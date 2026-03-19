from unittest.mock import MagicMock, patch

import pytest

from morss.morss import CUSTOM_EXTRACTORS, extract_investing_com


class FakeResponse:
    def __init__(self, html):
        self.content = html.encode('utf-8')
        self.status_code = 200


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
def test_extract_investing_com_uses_amp_url(mock_cffi):
    mock_cffi.get.return_value = FakeResponse(SAMPLE_AMP_HTML)
    extract_investing_com('https://br.investing.com/news/test-article-123')
    called_url = mock_cffi.get.call_args[0][0]
    assert 'm.br.investing.com' in called_url
    assert 'ampMode=1' in called_url


def test_custom_extractor_registered():
    assert 'investing.com' in CUSTOM_EXTRACTORS
