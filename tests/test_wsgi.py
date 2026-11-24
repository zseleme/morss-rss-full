import base64

import pytest

from morss.wsgi import cgi_dispatcher


def basic(user, password):
    return 'Basic ' + base64.b64encode(('%s:%s' % (user, password)).encode('utf-8')).decode('ascii')


def call(path, auth=None):
    environ = {'PATH_INFO': path, 'QUERY_STRING': ''}
    if auth:
        environ['HTTP_AUTHORIZATION'] = auth

    captured = {}

    def start_response(status, headers):
        captured['status'] = status
        captured['headers'] = dict(headers)

    def fallback_app(environ, start_response):
        start_response('200 OK', [])
        return [b'feed']

    body = b''.join(cgi_dispatcher(fallback_app)(environ, start_response))
    return captured['status'], captured['headers'], body


def test_status_disabled_without_password(monkeypatch):
    monkeypatch.delenv('STATUS_PASSWORD', raising=False)
    status, _, _ = call('/status')
    assert status.startswith('404')


def test_status_requires_auth(monkeypatch):
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    status, headers, body = call('/status')
    assert status.startswith('401')
    assert headers['www-authenticate'].startswith('Basic')
    assert b'status' not in body


@pytest.mark.parametrize('auth', [
    basic('admin', 'wrong'),
    basic('other', 's3cret'),
    'Basic not-base64!!',
    'Bearer s3cret',
])
def test_status_rejects_bad_credentials(monkeypatch, auth):
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    status, _, _ = call('/status', auth)
    assert status.startswith('401')


def test_status_accepts_valid_credentials(monkeypatch):
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    status, _, body = call('/status', basic('admin', 's3cret'))
    assert status.startswith('200')
    assert b'morss / status' in body


def test_status_custom_user(monkeypatch):
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    monkeypatch.setenv('STATUS_USER', 'zaiden')
    assert call('/status', basic('admin', 's3cret'))[0].startswith('401')
    assert call('/status', basic('zaiden', 's3cret'))[0].startswith('200')


def test_status_trailing_slash_is_protected(monkeypatch):
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    assert call('/status/')[0].startswith('401')


def test_status_escapes_logged_values(monkeypatch):
    from morss import wsgi
    monkeypatch.setenv('STATUS_PASSWORD', 's3cret')
    monkeypatch.setattr(wsgi, '_request_log', wsgi.deque([{
        'ts': 0, 'ok': True,
        'url': '/https://x/"><img src=x onerror=alert(1)>',
        'ip': '<script>alert(2)</script>',
    }]))
    status, _, body = call('/status', basic('admin', 's3cret'))
    assert status.startswith('200')
    assert b'<img src=x' not in body
    assert b'<script>alert' not in body
    assert b'&quot;&gt;&lt;img' in body
    assert b'&lt;script&gt;alert(2)' in body


SPOOFED = {'REMOTE_ADDR': '10.0.0.1', 'HTTP_CF_CONNECTING_IP': '6.6.6.6',
           'HTTP_X_FORWARDED_FOR': '6.6.6.6, 203.0.113.9', 'HTTP_FLY_CLIENT_IP': '198.51.100.7'}


def test_client_ip_ignores_proxy_headers_by_default(monkeypatch):
    from morss.wsgi import client_ip
    monkeypatch.delenv('CLIENT_IP_HEADER', raising=False)
    monkeypatch.delenv('FLY_APP_NAME', raising=False)
    assert client_ip(dict(SPOOFED)) == '10.0.0.1'


def test_client_ip_uses_fly_header_on_fly(monkeypatch):
    from morss.wsgi import client_ip
    monkeypatch.delenv('CLIENT_IP_HEADER', raising=False)
    monkeypatch.setenv('FLY_APP_NAME', 'morss')
    assert client_ip(dict(SPOOFED)) == '198.51.100.7'


@pytest.mark.parametrize('header, expected', [
    ('CF-Connecting-IP', '6.6.6.6'),
    ('X-Forwarded-For', '203.0.113.9'),  # rightmost hop, not the client-supplied one
])
def test_client_ip_configured_header(monkeypatch, header, expected):
    from morss.wsgi import client_ip
    monkeypatch.setenv('CLIENT_IP_HEADER', header)
    assert client_ip(dict(SPOOFED)) == expected


def test_client_ip_falls_back_when_header_missing(monkeypatch):
    from morss.wsgi import client_ip
    monkeypatch.setenv('CLIENT_IP_HEADER', 'CF-Connecting-IP')
    assert client_ip({'REMOTE_ADDR': '10.0.0.1'}) == '10.0.0.1'


def test_error_page_escapes_url_and_error(monkeypatch):
    from morss import wsgi

    def failing_app(environ, start_response):
        raise ValueError('<img src=x onerror=alert(1)>')

    captured = {}

    def start_response(status, headers, exc_info=None):
        captured['status'] = status

    environ = {'PATH_INFO': '/"><script>alert(2)</script>', 'QUERY_STRING': '',
               'REQUEST_URI': '/"><script>alert(2)</script>'}
    body = b''.join(wsgi.cgi_error_handler(failing_app)(environ, start_response))
    assert captured['status'].startswith('404')
    assert b'<img src=x' not in body and b'<script>alert(2)' not in body
    assert b'&lt;img src=x onerror=alert(1)&gt;' in body
    assert b'&lt;script&gt;alert(2)' in body


BRIDGE_QS = 'action=display&bridge=Instagram&context=Username&u=%s&format=Atom'


def parse(path):
    from morss.wsgi import cgi_parse_environ
    return cgi_parse_environ({'PATH_INFO': path, 'RAW_URI': path, 'QUERY_STRING': ''})


@pytest.mark.parametrize('path', ['/instagram/bruna.theoneimoveis', '/instagram/bruna.theoneimoveis/'])
def test_instagram_shortcut(monkeypatch, path):
    monkeypatch.delenv('RSSBRIDGE_URL', raising=False)
    url, options = parse(path)
    assert url == 'https://rss-bridge.org/bridge01/?' + BRIDGE_QS % 'bruna.theoneimoveis'
    assert options.proxy is True


def test_instagram_shortcut_keeps_options(monkeypatch):
    monkeypatch.delenv('RSSBRIDGE_URL', raising=False)
    url, options = parse('/:format=json:force/instagram/nasa')
    assert url.endswith(BRIDGE_QS % 'nasa')
    assert options.format == 'json' and options.force is True and options.proxy is True


@pytest.mark.parametrize('bridge, expected', [
    ('https://bridge.example.com/', 'https://bridge.example.com/?'),
    ('https://bridge.example.com/index.php?x=1', 'https://bridge.example.com/index.php?x=1&'),
])
def test_instagram_shortcut_custom_bridge(monkeypatch, bridge, expected):
    monkeypatch.setenv('RSSBRIDGE_URL', bridge)
    url, _ = parse('/instagram/nasa')
    assert url == expected + BRIDGE_QS % 'nasa'


@pytest.mark.parametrize('username', ['', 'a&format=Html', 'a b', 'x' * 31, '..%2F'])
def test_instagram_shortcut_rejects_invalid_username(username):
    from morss.morss import MorssException
    with pytest.raises(MorssException):
        parse('/instagram/' + username)


@pytest.mark.parametrize('path', ['/br.investing.com/rss/news_285.rss', '/https://instagram.com/nasa',
                                  '/www.instagram.com/nasa', '/instagram/nasa/reels'])
def test_other_urls_untouched(path):
    url, options = parse(path)
    assert url == path[1:]
    assert not options.proxy


def test_instagram_invalid_username_returns_error_page():
    from morss.wsgi import application
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured['status'] = status

    environ = {'PATH_INFO': '/instagram/a&b', 'RAW_URI': '/instagram/a&b', 'QUERY_STRING': ''}
    body = b''.join(application(environ, start_response))
    assert captured['status'].startswith('404')
    assert b'Invalid Instagram username' in body
