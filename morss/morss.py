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

import os
import re
import sys
import time
from datetime import datetime
from fnmatch import fnmatch

import lxml.etree
import lxml.html
from dateutil import tz

from . import caching, crawler, feeds, readabilite

try:
    # python 2
    from httplib import HTTPException
    from urlparse import parse_qs, urljoin, urlparse
except ImportError:
    # python 3
    from http.client import HTTPException
    from urllib.parse import parse_qs, urljoin, urlparse

import json
import subprocess

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

MAX_ITEM = int(os.getenv('MAX_ITEM', 5)) # cache-only beyond
MAX_TIME = int(os.getenv('MAX_TIME', 2)) # cache-only after (in sec)

LIM_ITEM = int(os.getenv('LIM_ITEM', 10)) # deletes what's beyond
LIM_TIME = float(os.getenv('LIM_TIME', 2.5)) # deletes what's after

DELAY = int(os.getenv('DELAY', 10 * 60)) # xml cache & ETag cache (in sec)
TIMEOUT = int(os.getenv('TIMEOUT', 4)) # http timeout (in sec)


class MorssException(Exception):
    pass


def log(txt):
    if 'DEBUG' in os.environ:
        if 'REQUEST_URI' in os.environ:
            # when running on Apache
            open('morss.log', 'a').write("%s\n" % repr(txt))

        else:
            # when using internal server or cli
            print(repr(txt), file=sys.stderr)


def len_html(txt):
    if len(txt):
        return len(lxml.html.fromstring(txt).text_content())

    else:
        return 0


def count_words(txt):
    if len(txt):
        return len(lxml.html.fromstring(txt).text_content().split())

    return 0


class Options:
    def __init__(self, options=None, **args):
        if len(args):
            self.options = args
            self.options.update(options or {})

        else:
            self.options = options or {}

    def __getattr__(self, key, default=None):
        if key in self.options:
            return self.options[key]

        else:
            return default

    def __setitem__(self, key, value):
        self.options[key] = value

    def __contains__(self, key):
        return key in self.options

    get = __getitem__ = __getattr__


def ItemFix(item, options, feedurl='/'):
    """ Improves feed items (absolute links, resolve feedburner links, etc) """

    # check unwanted uppercase title
    if item.title is not None and len(item.title) > 20 and item.title.isupper():
        item.title = item.title.title()

    # check if it includes link
    if not item.link:
        log('no link')
        return item

    # wikipedia daily highlight
    if fnmatch(feedurl, 'http*://*.wikipedia.org/w/api.php?*&feedformat=atom'):
        match = lxml.html.fromstring(item.desc).xpath('//b/a/@href')
        if len(match):
            item.link = match[0]
            log(item.link)

    # at user's election, use first <a>
    if options.firstlink and (item.desc or item.content):
        match = lxml.html.fromstring(item.desc or item.content).xpath('//a/@href')
        if len(match):
            item.link = match[0]
            log(item.link)

    # check relative urls
    item.link = urljoin(feedurl, item.link)

    # google translate
    if fnmatch(item.link, 'http://translate.google.*/translate*u=*'):
        item.link = parse_qs(urlparse(item.link).query)['u'][0]
        log(item.link)

    # google
    if fnmatch(item.link, 'http://www.google.*/url?q=*'):
        item.link = parse_qs(urlparse(item.link).query)['q'][0]
        log(item.link)

    # google news
    if fnmatch(item.link, 'http://news.google.com/news/url*url=*'):
        item.link = parse_qs(urlparse(item.link).query)['url'][0]
        log(item.link)

    # pocket
    if fnmatch(item.link, 'https://getpocket.com/redirect?url=*'):
        item.link = parse_qs(urlparse(item.link).query)['url'][0]
        log(item.link)

    # facebook
    if fnmatch(item.link, 'https://www.facebook.com/l.php?u=*'):
        item.link = parse_qs(urlparse(item.link).query)['u'][0]
        log(item.link)

    # feedburner FIXME only works if RSS...
    item.NSMAP['feedburner'] = 'http://rssnamespace.org/feedburner/ext/1.0'
    match = item.rule_str('feedburner:origLink')
    if match:
        item.link = match

    # feedsportal
    match = re.search('/([0-9a-zA-Z]{20,})/story01.htm$', item.link)
    if match:
        url = match.groups()[0].split('0')
        t = {'A': '0', 'B': '.', 'C': '/', 'D': '?', 'E': '-', 'F': '=',
             'G': '&', 'H': ',', 'I': '_', 'J': '%', 'K': '+', 'L': 'http://',
             'M': 'https://', 'N': '.com', 'O': '.co.uk', 'P': ';', 'Q': '|',
             'R': ':', 'S': 'www.', 'T': '#', 'U': '$', 'V': '~', 'W': '!',
             'X': '(', 'Y': ')', 'Z': 'Z'}
        item.link = ''.join([(t[s[0]] if s[0] in t else s[0]) + s[1:] for s in url[1:]])
        log(item.link)

    # reddit
    if urlparse(feedurl).netloc == 'www.reddit.com':
        match = lxml.html.fromstring(item.content).xpath('//a[text()="[link]"]/@href')
        if len(match):
            item.link = match[0]
            log(item.link)

    return item


CUSTOM_EXTRACTORS = {}

# Browser fingerprints tried in order. Cloudflare blocks outdated ones (e.g.
# 'chrome120' now gets a 403), so prefer the aliases that track the newest
# browser version shipped with the installed curl_cffi.
IMPERSONATE = os.getenv('IMPERSONATE', 'chrome,safari').split(',')

# Overall time budget (in sec) for one custom extraction, across every URL and
# fingerprint it tries, so a blocked site can't stall the whole feed.
EXTRACTOR_TIMEOUT = float(os.getenv('EXTRACTOR_TIMEOUT', 10))

# Statuses worth retrying with another fingerprint (Cloudflare blocks/challenges).
CFFI_RETRY_STATUS = (403, 503)

CUSTOM_CACHE_PREFIX = 'custom-extractor:'
CUSTOM_FAIL_PREFIX = 'custom-extractor-fail:'


def domain_matches(host, domain):
    """ True if `host` is `domain` or one of its subdomains """
    host = (host or '').lower().rstrip('.')
    domain = domain.lower()
    return host == domain or host.endswith('.' + domain)


def custom_extractor(domain_pattern):
    """ Decorator to register a custom extractor for a domain pattern """
    def decorator(func):
        CUSTOM_EXTRACTORS[domain_pattern] = func
        return func
    return decorator


def cffi_get(url, deadline=None):
    """ GET with curl_cffi, trying each browser fingerprint until one is not
    blocked, without going past `deadline` (a time.time() value; defaults to
    EXTRACTOR_TIMEOUT from now). Returns the response, or None on failure. """
    if cffi_requests is None:
        return None

    if deadline is None:
        deadline = time.time() + EXTRACTOR_TIMEOUT

    for impersonate in IMPERSONATE:
        remaining = deadline - time.time()
        if remaining <= 0:
            log('cffi deadline exceeded')
            return None

        try:
            resp = cffi_requests.get(url, impersonate=impersonate.strip(), timeout=remaining,
                                     headers={'Accept-Language': 'pt-BR,pt;q=0.9'})
        except Exception as e:
            log('cffi %s error: %r' % (impersonate, e))
            continue

        if resp.status_code == 200:
            return resp

        log('cffi %s status %s' % (impersonate, resp.status_code))

        if resp.status_code not in CFFI_RETRY_STATUS:
            # not a bot block (e.g. 404): another fingerprint won't help
            return None

    return None


def custom_cache_get(url):
    try:
        data = caching.default_cache[CUSTOM_CACHE_PREFIX + url]
    except KeyError:
        return None

    if isinstance(data, bytes):
        data = data.decode('utf-8')

    return data or None


def custom_cache_set(url, content):
    try:
        caching.default_cache[CUSTOM_CACHE_PREFIX + url] = content.encode('utf-8')
    except Exception as e:
        log('custom cache error: %r' % e)


# Custom extractors return raw page HTML, which the HTML view (www/sheet.xsl)
# injects with innerHTML on the morss origin: strip anything that can run code.
SANITIZE_DROP_TAGS = {'script', 'style', 'link', 'meta', 'base', 'object', 'embed',
                      'applet', 'frame', 'frameset', 'form', 'input', 'button',
                      'textarea', 'select', 'noscript', 'template', 'svg', 'math'}
SANITIZE_ATTRIBUTES = {'href', 'src', 'alt', 'title', 'width', 'height', 'style',
                       'class', 'colspan', 'rowspan', 'frameborder', 'allowfullscreen'}
SANITIZE_URL_ATTRIBUTES = ('href', 'src')
SANITIZE_IFRAME_SRC = ('https://www.youtube.com/embed/', 'https://www.youtube-nocookie.com/embed/')


def sanitize_html(content):
    """ Returns `content` without scripts, event handlers, javascript: URLs
    and embeds (except YouTube iframes), or None if nothing is left """
    try:
        root = lxml.html.fragment_fromstring(content, create_parent='div')
    except (lxml.etree.ParserError, ValueError):
        return None

    for el in list(root.iter()):
        if el is root:
            continue

        if not isinstance(el.tag, str):
            # comments, processing instructions
            el.drop_tree()
            continue

        tag = el.tag.lower()

        if tag in SANITIZE_DROP_TAGS or (tag == 'iframe' and not el.get('src', '').startswith(SANITIZE_IFRAME_SRC)):
            el.drop_tree()
            continue

        for attr in list(el.attrib):
            value = re.sub(r'[\s\x00-\x1f]', '', el.attrib[attr]).lower()

            if (attr.lower() not in SANITIZE_ATTRIBUTES
                    or (attr.lower() in SANITIZE_URL_ATTRIBUTES and value.startswith(('javascript:', 'vbscript:', 'data:')))
                    or (attr.lower() == 'style' and ('expression(' in value or 'javascript:' in value or 'url(' in value))):
                del el.attrib[attr]

    return lxml.html.tostring(root, encoding='unicode')


def custom_failed_recently(url):
    """ True if extracting `url` failed less than DELAY seconds ago """
    try:
        data = caching.default_cache[CUSTOM_FAIL_PREFIX + url]
        return data is not None and time.time() - float(data) < DELAY
    except (KeyError, ValueError, TypeError):
        return False


def custom_failed_set(url):
    try:
        caching.default_cache[CUSTOM_FAIL_PREFIX + url] = str(time.time()).encode('ascii')
    except Exception as e:
        log('custom cache error: %r' % e)


@custom_extractor('investing.com')
def extract_investing_com(url, deadline=None):
    """ Extract article body from investing.com (article in div.WYSIWYG*).
    Uses curl_cffi to impersonate a browser TLS fingerprint (bypasses Cloudflare
    bot detection). Tries the regular page first, then the AMP version
    (m.br.investing.com/...?ampMode=1). """
    try:
        from bs4 import BeautifulSoup

        if deadline is None:
            deadline = time.time() + EXTRACTOR_TIMEOUT

        parsed = urlparse(url)
        amp_netloc = 'm.' + parsed.netloc if not parsed.netloc.startswith('m.') else parsed.netloc
        amp_url = parsed._replace(netloc=amp_netloc, query='ampMode=1').geturl()

        soup = el = None
        for candidate in (url, amp_url):
            resp = cffi_get(candidate, deadline)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.content, 'lxml')
            el = soup.select_one('div[class*="WYSIWYG"]')
            if el:
                break

        if not el:
            return None

        # remove app-promo banners
        for tag in el.select('.inlineBanner, .bannerImg'):
            tag.decompose()

        # amp-img -> img
        for amp_img in el.select('amp-img'):
            img = soup.new_tag('img', src=amp_img.get('src', ''),
                               width=amp_img.get('width', ''), height=amp_img.get('height', ''),
                               style='max-width:100%;height:auto')
            amp_img.replace_with(img)

        # amp-youtube -> iframe embed
        for amp_yt in el.select('amp-youtube'):
            vid = amp_yt.get('data-videoid', '')
            if vid:
                iframe = soup.new_tag('iframe',
                    src=f'https://www.youtube.com/embed/{vid}',
                    width=amp_yt.get('width', '560'), height=amp_yt.get('height', '315'),
                    frameborder='0', allowfullscreen='')
                amp_yt.replace_with(iframe)
            else:
                amp_yt.decompose()

        return str(el)
    except Exception:
        return None


@custom_extractor('moneytimes.com.br')
def extract_moneytimes(url, deadline=None):
    """ Extract article body from moneytimes.com.br.
    Selectors: div.single_block_news_image (lead image) + div.single_block_news_text (body). """
    try:
        from bs4 import BeautifulSoup

        resp = cffi_get(url, deadline)
        if resp is None:
            return None

        soup = BeautifulSoup(resp.content, 'lxml')

        img_el  = soup.select_one('div.single_block_news_image')
        text_el = soup.select_one('div.single_block_news_text')

        if not text_el:
            return None

        # remove social/promo blocks inside the text
        for tag in text_el.select('div[class*="single_meta"], div[class*="socials"], .mt-yt-btn'):
            tag.decompose()

        parts = []
        if img_el:
            parts.append(str(img_el))
        parts.append(str(text_el))
        return ''.join(parts)
    except Exception:
        return None


def ItemFill(item, options, feedurl='/', fast=False):
    """ Returns True when it has done its best """

    if not item.link:
        log('no link')
        return True

    log(item.link)

    # custom domain extractors
    host = urlparse(item.link).hostname
    for domain, extractor in CUSTOM_EXTRACTORS.items():
        if domain_matches(host, domain):
            # articles don't change once published: reuse previous extractions,
            # including in fast (cache-only) mode
            content = custom_cache_get(item.link) if not options.force else None

            if content is None and not fast and (options.force or not custom_failed_recently(item.link)):
                content = extractor(item.link, time.time() + EXTRACTOR_TIMEOUT)
                if content:
                    content = sanitize_html(content)
                if content:
                    custom_cache_set(item.link, content)
                else:
                    # don't pay the full timeout again on every refresh
                    custom_failed_set(item.link)

            if content:
                # also covers entries cached before sanitization existed
                item.content = sanitize_html(content) or ''
                return True
            break  # domain matched but extraction failed, fall through to default

    # download

    if fast or options.cache:
        # force cache, don't fetch
        policy = 'offline'

    elif options.force:
        # force refresh
        policy = 'refresh'

    else:
        policy = None

    try:
        req = crawler.adv_get(url=item.link, policy=policy, force_min=24*60*60, timeout=TIMEOUT)

    except (IOError, HTTPException) as e:
        log('http error')
        return False # let's just delete errors stuff when in cache mode

    if req['contenttype'] not in crawler.MIMETYPE['html'] and req['contenttype'] != 'text/plain':
        log('non-text page')
        return True

    if not req['data']:
        log('empty page')
        return True

    out = readabilite.get_article(req['data'], url=req['url'], encoding_in=req['encoding'], encoding_out='unicode', xpath=options.xpath)

    if out is not None:
        item.content = out

    if options.resolve:
        item.link = req['url']

    return True


def ItemBefore(item, options):
    # return None if item deleted

    if options.search:
        if options.search not in item.title:
            item.remove()
            return None

    return item


def ItemAfter(item, options):
    if options.clip and item.desc and item.content:
        item.content = item.desc + "<br/><br/><hr/><br/><br/>" + item.content
        del item.desc

    if options.nolink and item.content:
        content = lxml.html.fromstring(item.content)
        for link in content.xpath('//a'):
            log(link.text_content())
            link.drop_tag()
        item.content = lxml.etree.tostring(content, method='html')

    if options.noref:
        item.link = ''

    return item


def FeedFetch(url, options):
    # fetch feed
    delay = DELAY

    if options.cache:
        policy = 'offline'

    elif options.force:
        policy = 'refresh'

    else:
        policy = None

    try:
        req = crawler.adv_get(url=url, post=options.post, follow=('rss' if not options.items else None), policy=policy, force_min=5*60, force_max=60*60, timeout=TIMEOUT)

    except (IOError, HTTPException):
        raise MorssException('Error downloading feed')

    if options.items:
        # using custom rules
        ruleset = {}

        ruleset['items'] = options.items

        if options.mode:
            ruleset['mode'] = options.mode

        ruleset['title'] = options.get('title', '//head/title')
        ruleset['desc'] = options.get('desc', '//head/meta[@name="description"]/@content')

        ruleset['item_title'] = options.get('item_title', '.')
        ruleset['item_link'] = options.get('item_link', '(.|.//a|ancestor::a)/@href')

        if options.item_content:
            ruleset['item_content'] = options.item_content

        if options.item_time:
            ruleset['item_time'] = options.item_time

        rss = feeds.parse(req['data'], encoding=req['encoding'], ruleset=ruleset)
        rss = rss.convert(feeds.FeedXML)

    else:
        try:
            rss = feeds.parse(req['data'], url=url, encoding=req['encoding'])
            rss = rss.convert(feeds.FeedXML)
                # contains all fields, otherwise much-needed data can be lost

        except TypeError:
            log('random page')
            log(req['contenttype'])
            raise MorssException('Link provided is not a valid feed')

    return req['url'], rss


def FeedGather(rss, url, options):
    size = len(rss.items)
    start_time = time.time()

    # custom settings
    lim_item = LIM_ITEM
    lim_time = LIM_TIME
    max_item = MAX_ITEM
    max_time = MAX_TIME

    if options.cache:
        max_time = 0

    # sort
    sorted_items = list(rss.items)

    if options.order == 'last':
    # `first` does nothing from a practical standpoint, so only `last` needs
    # to be addressed
        sorted_items = reversed(sorted_items)

    elif options.order in ['newest', 'oldest']:
        now = datetime.now(tz.tzutc())
        sorted_items = sorted(sorted_items, key=lambda x:x.updated or x.time or now) # oldest to newest

        if options.order == 'newest':
            sorted_items = reversed(sorted_items)

    for i, item in enumerate(sorted_items):
        # hard cap
        if time.time() - start_time > lim_time >= 0 or i + 1 > lim_item >= 0:
            log('dropped')
            item.remove()
            continue

        item = ItemBefore(item, options)

        if item is None:
            continue

        item = ItemFix(item, options, url)

        # soft cap
        if time.time() - start_time > max_time >= 0 or i + 1 > max_item >= 0:
            if not options.proxy:
                if ItemFill(item, options, url, True) is False:
                    item.remove()
                    continue

        else:
            if not options.proxy:
                ItemFill(item, options, url)

        item = ItemAfter(item, options)

    if options.ad:
        new = rss.items.append()
        new.title = "Are you hungry?"
        new.desc = "Eat some Galler chocolate :)"
        new.link = "http://www.galler.com/"
        new.time = "5 Oct 2013 22:42"

    log(len(rss.items))
    log(time.time() - start_time)

    return rss


def FeedFormat(rss, options, encoding='utf-8'):
    if options.callback:
        if re.match(r'^[a-zA-Z0-9\.]+$', options.callback) is not None:
            out = '%s(%s)' % (options.callback, rss.tojson(encoding='unicode'))
            return out if encoding == 'unicode' else out.encode(encoding)

        else:
            raise MorssException('Invalid callback var name')

    elif options.format == 'json':
        if options.indent:
            return rss.tojson(encoding=encoding, indent=4)

        else:
            return rss.tojson(encoding=encoding)

    elif options.format == 'csv':
        return rss.tocsv(encoding=encoding)

    elif options.format == 'html':
        if options.indent:
            return rss.tohtml(encoding=encoding, pretty_print=True)

        else:
            return rss.tohtml(encoding=encoding)

    else: # i.e. format == 'rss'
        if options.indent:
            return rss.torss(xml_declaration=(not encoding == 'unicode'), encoding=encoding, pretty_print=True)

        else:
            return rss.torss(xml_declaration=(not encoding == 'unicode'), encoding=encoding)


def process(url, cache=None, options=None):
    if not options:
        options = []

    options = Options(options)

    if cache:
        caching.default_cache = caching.DiskCacheHandler(cache)

    url, rss = FeedFetch(url, options)
    rss = FeedGather(rss, url, options)

    return FeedFormat(rss, options, 'unicode')
