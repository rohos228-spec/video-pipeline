import httpx
import pytest
from app.services.gpt_api import _stream_should_salvage_nostream

@pytest.mark.parametrize('exc', [TimeoutError(), httpx.ReadTimeout('timeout'), httpx.ConnectTimeout('timeout'), httpx.PoolTimeout('timeout')])
def test_exhausted_timeout_never_starts_second_request(exc):
    assert not _stream_should_salvage_nostream(exc)


def test_non_timeout_stream_salvage_still_supported():
    assert _stream_should_salvage_nostream(httpx.RemoteProtocolError('broken SSE'))
    assert _stream_should_salvage_nostream(None)
