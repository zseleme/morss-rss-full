from unittest.mock import MagicMock, patch

import pytest

from morss.morss import (CUSTOM_EXTRACTORS, IMPERSONATE, cffi_get,
                         extract_investing_com)


class FakeResponse:
    def __init__(self, html, status_code=200):
        self.content = html.encode('utf-8')
        self.status_code = status_code


SAMPLE_AMP_HTML = """<!DOCTYPE html>
<html lang="pt"><head><title>Test</title></head><body>
<div class="WYSIWYG articlePage">
<p>Parágrafo principal do artigo de teste.</p>
<p>Segundo parágrafo com mais conteúdo relevante.</p>
<amp-img src="https://example.com/foto.jpg" width="800" height="450" layout="responsive" alt="Foto do artigo"></amp-img>
<amp-youtube data-videoid="dQw4w9WgXcQ" width="560" height="315" layout="responsive"></amp-youtube>
<div class="inlineBanner" id="inlineBannerForApp"><div>Baixe o App</div></div>
</div>
</body></html>"""


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_returns_content(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    result = extract_investing_com('https://br.investing.com/news/test-article-123')
    assert result is not None
    assert 'Parágrafo principal' in result


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_converts_amp_img(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    result = extract_investing_com('https://br.investing.com/news/test-article-123')
    assert '<img' in result
    assert 'amp-img' not in result


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_converts_amp_youtube(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    result = extract_investing_com('https://br.investing.com/news/test-article-123')
    assert 'youtube.com/embed/dQw4w9WgXcQ' in result
    assert 'amp-youtube' not in result


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_removes_banner(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    result = extract_investing_com('https://br.investing.com/news/test-article-123')
    assert 'inlineBanner' not in result


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_tries_regular_url_first(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    extract_investing_com('https://br.investing.com/news/test-article-123')
    assert mock_cffi.get.call_count == 1
    assert mock_cffi.get.call_args[0][0] == 'https://br.investing.com/news/test-article-123'


@patch('morss.morss.cffi_requests')
def test_extract_investing_com_falls_back_to_amp_url(mock_cffi):
    mock_cffi.get.side_effect = [
        FakeResponse('<html><body>no article here</body></html>'),
        FakeResponse(SAMPLE_AMP_HTML),
    ]
    result = extract_investing_com('https://br.investing.com/news/test-article-123')
    assert 'Parágrafo principal' in result
    called_url = mock_cffi.get.call_args[0][0]
    assert 'm.br.investing.com' in called_url
    assert 'ampMode=1' in called_url


@patch('morss.morss.cffi_requests')
def test_cffi_get_tries_next_fingerprint_when_blocked(mock_cffi):
    mock_cffi.get.side_effect = [FakeResponse('', 403), FakeResponse(SAMPLE_AMP_HTML)]
    resp = cffi_get('https://br.investing.com/news/test-article-123')
    assert resp is not None and resp.status_code == 200
    used = [call.kwargs['impersonate'] for call in mock_cffi.get.call_args_list]
    assert used == IMPERSONATE[:2]


@patch('morss.morss.cffi_requests')
def test_cffi_get_returns_none_when_all_blocked(mock_cffi):
    mock_cffi.get.return_value = FakeResponse('', 403)
    assert cffi_get('https://br.investing.com/news/test-article-123') is None
    assert extract_investing_com('https://br.investing.com/news/test-article-123') is None


def test_custom_extractor_registered():
    assert 'investing.com' in CUSTOM_EXTRACTORS


@patch('morss.morss.cffi_requests')
def test_cffi_get_does_not_retry_non_block_status(mock_cffi):
    mock_cffi.get.return_value = FakeResponse('', 404)
    assert cffi_get('https://br.investing.com/news/x') is None
    assert mock_cffi.get.call_count == 1


@patch('morss.morss.cffi_requests')
def test_cffi_get_respects_deadline(mock_cffi):
    import time
    assert cffi_get('https://br.investing.com/news/x', deadline=time.time() - 1) is None
    mock_cffi.get.assert_not_called()


@patch('morss.morss.cffi_requests')
def test_cffi_get_timeout_bounded_by_deadline(mock_cffi):
    import time
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    cffi_get('https://br.investing.com/news/x', deadline=time.time() + 3)
    assert 0 < mock_cffi.get.call_args.kwargs['timeout'] <= 3


@patch('morss.morss.cffi_requests')
def test_itemfill_remembers_failed_extraction(mock_cffi, monkeypatch):
    from morss import caching, morss
    monkeypatch.setattr(caching, 'default_cache', caching.CappedDict())
    mock_cffi.get.return_value = FakeResponse('', 403)

    item = MagicMock(link='https://br.investing.com/news/fail-1')
    options = morss.Options({})
    with patch('morss.morss.crawler.adv_get', side_effect=IOError):
        morss.ItemFill(item, options)
        calls = mock_cffi.get.call_count
        assert calls > 0
        morss.ItemFill(item, options)
    assert mock_cffi.get.call_count == calls  # second refresh skips the extractor


def test_sanitize_html_strips_active_content():
    from morss.morss import sanitize_html
    out = sanitize_html(
        '<div class="WYSIWYG"><p onclick="x()">texto</p>'
        '<img src="https://e.com/a.jpg" onerror="alert(1)" style="max-width:100%">'
        '<script>alert(2)</script><a href=" java\tscript:alert(3)">link</a>'
        '<iframe src="https://evil.com/x"></iframe>'
        '<iframe src="https://www.youtube.com/embed/abc" allowfullscreen=""></iframe>'
        '<svg><script>alert(4)</script></svg><!-- c --></div>')
    assert 'texto' in out and 'link' in out
    assert 'https://e.com/a.jpg' in out and 'max-width:100%' in out
    assert 'https://www.youtube.com/embed/abc' in out
    for bad in ('onclick', 'onerror', '<script', 'javascript', 'evil.com', '<svg', '<!--'):
        assert bad not in out


@patch('morss.morss.cffi_requests')
def test_itemfill_sanitizes_extracted_and_cached_content(mock_cffi, monkeypatch):
    from morss import caching, morss
    monkeypatch.setattr(caching, 'default_cache', caching.CappedDict())
    options = morss.Options({})

    mock_cffi.get.return_value = FakeResponse(
        '<div class="WYSIWYG"><p>ok</p><img src=x onerror="alert(1)"></div>')
    item = MagicMock(link='https://br.investing.com/news/xss-1')
    assert morss.ItemFill(item, options) is True
    assert 'onerror' not in item.content and 'ok' in item.content
    assert 'onerror' not in morss.custom_cache_get(item.link)

    # entry poisoned before sanitization existed
    morss.custom_cache_set('https://br.investing.com/news/xss-2', '<p onclick="x()">old</p>')
    item = MagicMock(link='https://br.investing.com/news/xss-2')
    assert morss.ItemFill(item, options, fast=True) is True
    assert 'onclick' not in item.content and 'old' in item.content


@pytest.mark.parametrize('host, expected', [
    ('investing.com', True),
    ('br.investing.com', True),
    ('m.br.investing.com', True),
    ('BR.Investing.COM', True),
    ('br.investing.com.', True),
    ('notinvesting.com', False),
    ('investing.com.evil.tld', False),
    ('evil.tld', False),
    (None, False),
])
def test_domain_matches(host, expected):
    from morss.morss import domain_matches
    assert domain_matches(host, 'investing.com') is expected


@patch('morss.morss.cffi_requests')
def test_itemfill_skips_extractor_for_lookalike_domain(mock_cffi, monkeypatch):
    from morss import caching, morss
    monkeypatch.setattr(caching, 'default_cache', caching.CappedDict())
    item = MagicMock(link='https://investing.com.evil.tld:8443/news/x')
    with patch('morss.morss.crawler.adv_get', side_effect=IOError):
        morss.ItemFill(item, morss.Options({}))
    mock_cffi.get.assert_not_called()


@pytest.mark.parametrize('value, expected', [
    ('chrome,safari', ['chrome', 'safari']),
    (' chrome , safari ,', ['chrome', 'safari']),
    ('firefox', ['firefox']),
    ('', ['chrome', 'safari']),
    (',, ', ['chrome', 'safari']),
    (None, ['chrome', 'safari']),
])
def test_parse_impersonate(value, expected):
    from morss.morss import parse_impersonate
    assert parse_impersonate(value) == expected
