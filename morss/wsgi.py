# This file is part of morss
#
# Copyright (C) 2013-2020 pictuga <contact@pictuga.com>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

try:
    import cgitb
except ImportError:
    # Python 3.13+ removed cgitb
    import traceback
    class cgitb:
        @staticmethod
        def html(exc_info):
            return ('<html><body><pre>%s</pre></body></html>' % traceback.format_exception(*exc_info)).encode('utf-8')
import mimetypes
import os.path
import re
import sys
import time
import wsgiref.handlers
import wsgiref.simple_server
import wsgiref.util
from collections import deque

import lxml.etree

try:
    # python 2
    from urllib import unquote
except ImportError:
    # python 3
    from urllib.parse import unquote

from . import caching, crawler, readabilite
from .morss import (DELAY, TIMEOUT, FeedFetch, FeedFormat, FeedGather,
                    MorssException, Options, log)
from .util import data_path

PORT = int(os.getenv('PORT', 8000))

_start_time = time.time()
_request_log = deque(maxlen=50)  # last 50 feed requests


def parse_options(options):
    """ Turns ['md=True'] into {'md':True} """
    out = {}

    for option in options:
        split = option.split('=', 1)

        if len(split) > 1:
            out[split[0]] = unquote(split[1]).replace('|', '/') # | -> / for backward compatibility (and Apache)

        else:
            out[split[0]] = True

    return out


def request_uri(environ):
    if 'REQUEST_URI' in environ:
        # when running on Apache/uwsgi
        url = environ['REQUEST_URI']

    elif 'RAW_URI' in environ:
        # gunicorn
        url = environ['RAW_URI']

    else:
        # when using other servers
        url = environ['PATH_INFO']

        if environ['QUERY_STRING']:
            url += '?' + environ['QUERY_STRING']

    return url


def cgi_parse_environ(environ):
    # get options

    url = request_uri(environ)[1:]
    url = re.sub(r'^(cgi/)?(morss.py|main.py)/', '', url)

    if url.startswith(':'):
        parts = url.split('/', 1)
        raw_options = parts[0].split(':')[1:]
        url = parts[1] if len(parts) > 1 else ''

    else:
        raw_options = []

    # init
    options = Options(parse_options(raw_options))

    return (url, options)


def cgi_app(environ, start_response):
    url, options = cgi_parse_environ(environ)

    headers = {}

    # headers
    headers['status'] = '200 OK'
    headers['cache-control'] = 'max-age=%s' % DELAY
    headers['x-content-type-options'] = 'nosniff' # safari work around

    if options.cors:
        headers['access-control-allow-origin'] = '*'

    if options.format == 'html':
        headers['content-type'] = 'text/html'
    elif options.txt or options.silent:
        headers['content-type'] = 'text/plain'
    elif options.format == 'json':
        headers['content-type'] = 'application/json'
    elif options.callback:
        headers['content-type'] = 'application/javascript'
    elif options.format == 'csv':
        headers['content-type'] = 'text/csv'
        headers['content-disposition'] = 'attachment; filename="feed.csv"'
    else:
        headers['content-type'] = 'text/xml'

    headers['content-type'] += '; charset=utf-8'

    # get the work done
    url, rss = FeedFetch(url, options)

    start_response(headers['status'], list(headers.items()))

    rss = FeedGather(rss, url, options)
    out = FeedFormat(rss, options)

    if options.silent:
        return ['']

    else:
        return [out]


def middleware(func):
    " Decorator to turn a function into a wsgi middleware "
    # This is called when parsing the "@middleware" code

    def app_builder(app):
        # This is called when doing app = cgi_wrapper(app)

        def app_wrap(environ, start_response):
            # This is called when a http request is being processed

            return func(environ, start_response, app)

        return app_wrap

    return app_builder


@middleware
def cgi_file_handler(environ, start_response, app):
    " Simple HTTP server to serve static files (.html, .css, etc.) "

    url = request_uri(environ)[1:]

    if url == '':
        url = 'index.html'

    if re.match(r'^/?([a-zA-Z0-9_-][a-zA-Z0-9\._-]+/?)*$', url):
        # if it is a legitimate url (no funny relative paths)
        try:
            path = data_path('www', url)
            f = open(path, 'rb')

        except IOError:
            # problem with file (cannot open or not found)
            pass

        else:
            # file successfully open
            headers = {}
            headers['status'] = '200 OK'
            headers['content-type'] = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            start_response(headers['status'], list(headers.items()))
            return wsgiref.util.FileWrapper(f)

    # regex didn't validate or no file found
    return app(environ, start_response)


def cgi_get(environ, start_response):
    url, options = cgi_parse_environ(environ)

    # get page
    if options['get'] in ('page', 'article'):
        req = crawler.adv_get(url=url, timeout=TIMEOUT)

        if req['contenttype'] in crawler.MIMETYPE['html']:
            if options['get'] == 'page':
                html = readabilite.parse(req['data'], encoding=req['encoding'])
                html.make_links_absolute(req['url'])

                kill_tags = ['script', 'iframe', 'noscript']

                for tag in kill_tags:
                    for elem in html.xpath('//'+tag):
                        elem.getparent().remove(elem)

                output = lxml.etree.tostring(html.getroottree(), encoding='utf-8', method='html')

            else: # i.e. options['get'] == 'article'
                output = readabilite.get_article(req['data'], url=req['url'], encoding_in=req['encoding'], encoding_out='utf-8', debug=options.debug)

        elif req['contenttype'] in crawler.MIMETYPE['xml'] + crawler.MIMETYPE['rss'] + crawler.MIMETYPE['json']:
            output = req['data']

        else:
            raise MorssException('unsupported mimetype')

    else:
        raise MorssException('no :get option passed')

    # return html page
    headers = {'status': '200 OK', 'content-type': req['contenttype'], 'X-Frame-Options': 'SAMEORIGIN'} # SAMEORIGIN to avoid potential abuse
    start_response(headers['status'], list(headers.items()))
    return [output]


def cgi_status(environ, start_response):
    from . import caching as _caching

    uptime_s = int(time.time() - _start_time)
    h, m, s = uptime_s // 3600, (uptime_s % 3600) // 60, uptime_s % 60
    uptime_str = '%dh %02dm %02ds' % (h, m, s)

    cache = _caching.default_cache
    cache_type = type(cache).__name__
    try:
        cache_items = len(cache)
        cache_max = _caching.CACHE_SIZE
        cache_info = '%d / %d items' % (cache_items, cache_max)
    except Exception:
        cache_info = 'n/a'

    rows = ''
    for entry in reversed(_request_log):
        status_color = '#2ecc71' if entry['ok'] else '#e74c3c'
        status_label = 'ok' if entry['ok'] else 'erro'
        ts = time.strftime('%H:%M:%S', time.localtime(entry['ts']))
        url = entry['url'][:70] + ('...' if len(entry['url']) > 70 else '')
        rows += (
            '<tr>'
            '<td style="white-space:nowrap">%s</td>'
            '<td style="color:#aaa;white-space:nowrap">%s</td>'
            '<td style="max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="%s">%s</td>'
            '<td><span style="color:%s">%s</span></td>'
            '</tr>'
        ) % (ts, entry['ip'], entry['url'], url, status_color, status_label)

    if not rows:
        rows = '<tr><td colspan="4" style="color:#888">Nenhum feed processado ainda.</td></tr>'

    html = '''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>morss – status</title>
  <style>
    body { font-family: monospace; max-width: 800px; margin: 40px auto; padding: 0 20px; background: #111; color: #eee; }
    h1 { color: #aaa; font-size: 1.2em; margin-bottom: 30px; }
    .cards { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 30px; }
    .card { background: #1e1e1e; border: 1px solid #333; border-radius: 6px; padding: 16px 24px; min-width: 160px; }
    .card .label { font-size: .75em; color: #888; text-transform: uppercase; margin-bottom: 4px; }
    .card .value { font-size: 1.4em; color: #fff; }
    table { width: 100%%; border-collapse: collapse; font-size: .85em; }
    th { text-align: left; color: #888; border-bottom: 1px solid #333; padding: 6px 8px; }
    td { padding: 6px 8px; border-bottom: 1px solid #222; }
    tr:last-child td { border-bottom: none; }
    a { color: #888; text-decoration: none; }
  </style>
</head>
<body>
  <h1>morss / status</h1>
  <div class="cards">
    <div class="card"><div class="label">Uptime</div><div class="value">%s</div></div>
    <div class="card"><div class="label">Cache</div><div class="value" style="font-size:1em">%s</div></div>
    <div class="card"><div class="label">Tipo de cache</div><div class="value" style="font-size:.9em">%s</div></div>
    <div class="card"><div class="label">Requests (memória)</div><div class="value">%d</div></div>
  </div>
  <table>
    <thead><tr><th>Hora</th><th>IP</th><th>Feed</th><th>Status</th></tr></thead>
    <tbody>%s</tbody>
  </table>
  <p style="margin-top:24px"><a href="/">← voltar</a></p>
</body>
</html>''' % (uptime_str, cache_info, cache_type, len(_request_log), rows)

    start_response('200 OK', [('content-type', 'text/html; charset=utf-8')])
    return [html.encode('utf-8')]


dispatch_table = {
    'get': cgi_get,
    }


@middleware
def cgi_dispatcher(environ, start_response, app):
    if request_uri(environ) == '/status':
        return cgi_status(environ, start_response)

    url, options = cgi_parse_environ(environ)

    for key in dispatch_table.keys():
        if key in options:
            return dispatch_table[key](environ, start_response)

    return app(environ, start_response)


@middleware
def cgi_request_logger(environ, start_response, app):
    url, options = cgi_parse_environ(environ)
    if not url or request_uri(environ) in ('/', '/status', '/logo.svg', '/index.html'):
        return app(environ, start_response)

    # real IP: Cloudflare passes it in HTTP_CF_CONNECTING_IP
    ip = (environ.get('HTTP_CF_CONNECTING_IP')
          or environ.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
          or environ.get('REMOTE_ADDR', '?'))

    ok = True
    try:
        result = app(environ, start_response)
    except Exception:
        ok = False
        raise
    finally:
        _request_log.append({'ts': time.time(), 'url': url, 'ok': ok, 'ip': ip})

    return result


@middleware
def cgi_error_handler(environ, start_response, app):
    try:
        return app(environ, start_response)

    except (KeyboardInterrupt, SystemExit):
        raise

    except Exception as e:
        headers = {'status': '404 Not Found', 'content-type': 'text/html', 'x-morss-error': repr(e)}
        start_response(headers['status'], list(headers.items()), sys.exc_info())
        log('ERROR: %s' % repr(e))
        return [cgitb.html(sys.exc_info())]


@middleware
def cgi_encode(environ, start_response, app):
    out = app(environ, start_response)
    return [x if isinstance(x, bytes) else str(x).encode('utf-8') for x in out]


application = cgi_app
application = cgi_request_logger(application)
application = cgi_file_handler(application)
application = cgi_dispatcher(application)
application = cgi_error_handler(application)
application = cgi_encode(application)


def cgi_handle_request():
    app = cgi_app
    app = cgi_dispatcher(app)
    app = cgi_error_handler(app)
    app = cgi_encode(app)

    wsgiref.handlers.CGIHandler().run(app)


class WSGIRequestHandlerRequestUri(wsgiref.simple_server.WSGIRequestHandler):
    def get_environ(self):
        env = wsgiref.simple_server.WSGIRequestHandler.get_environ(self)
        env['REQUEST_URI'] = self.path
        return env


def cgi_start_server():
    caching.default_cache.autotrim()

    print('Serving http://localhost:%s/' % PORT)
    httpd = wsgiref.simple_server.make_server('', PORT, application, handler_class=WSGIRequestHandlerRequestUri)
    httpd.serve_forever()


if 'gunicorn' in os.getenv('SERVER_SOFTWARE', ''):
    caching.default_cache.autotrim()
