import pytest
import httpx
import respx

from invoicer.signwell import SignWellClient, SignWellError


@respx.mock
def test_download_pdf_returns_bytes():
    pdf_bytes = b"%PDF-stub"
    url = "https://cdn.signwell.com/signed/invoice.pdf"
    respx.get(url).mock(return_value=httpx.Response(200, content=pdf_bytes))

    client = SignWellClient(api_key="test-key-1234567890")
    result = client.download_pdf(url)
    assert result == pdf_bytes


@respx.mock
def test_download_pdf_raises_on_non_200():
    url = "https://cdn.signwell.com/signed/missing.pdf"
    respx.get(url).mock(return_value=httpx.Response(404, text="Not found"))

    client = SignWellClient(api_key="test-key-1234567890")
    with pytest.raises(SignWellError, match="404"):
        client.download_pdf(url)
