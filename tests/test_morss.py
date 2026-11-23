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
