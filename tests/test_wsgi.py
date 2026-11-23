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
