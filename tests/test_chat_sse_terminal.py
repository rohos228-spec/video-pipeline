"""Protocol completion must not depend on a gateway closing its socket."""
import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import gpt_api as ga


class OpenStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield (event + '\n\n').encode()
        await asyncio.Future()  # gateway keeps the connection open


@pytest.mark.asyncio
@pytest.mark.parametrize('hint', [False, True])
async def test_terminal_event_does_not_wait_for_socket_close(monkeypatch, hint):
    payload = {'id': 'chatcmpl-kmt-hint' if hint else 'normal',
               'choices': [{'delta': {'content': 'OK'}, 'finish_reason': 'stop'}]}
    events = ['data: ' + json.dumps(payload), 'data: [DONE]']
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, stream=OpenStream(events), headers={'content-type': 'text/event-stream'}))
    monkeypatch.setattr(ga, '_async_client', lambda **kw: httpx.AsyncClient(transport=transport))
    salvage = AsyncMock()
    monkeypatch.setattr(ga, '_chat_completions_nostream', salvage)
    call = ga._chat_completions_stream(url='https://example.test/v1/chat/completions',
        headers={}, body={}, timeout=30, use_model='test')
    if hint:
        with pytest.raises(ga.GptApiError) as exc:
            await asyncio.wait_for(call, 0.5)
        assert not exc.value.retryable
        assert exc.value.context['error_kind'] == 'provider_hint'
    else:
        result = await asyncio.wait_for(call, 0.5)
        assert result.text == 'OK'
        assert not result.raw.get('sse_interrupt')
    salvage.assert_not_awaited()


def test_fatal_hint_not_salvaged_as_partial_text():
    lines = ['data: ' + json.dumps({'choices': [{'delta': {'content': 'partial'}}]}),
             'data: ' + json.dumps({'id': 'chatcmpl-kmt-hint',
                                   'choices': [{'delta': {'content': 'forbidden'}}]})]
    with pytest.raises(ga.GptApiError):
        ga.parse_chat_completions_sse_lines(lines)


@pytest.mark.asyncio
async def test_52pay_sol_uses_verified_nonstream_path(monkeypatch):
    result = ga.GptChatResult(text='OK', model='gpt-6.1-sol', finish_reason='stop')
    nostream = AsyncMock(return_value=result)
    monkeypatch.setattr(ga, '_chat_completions_nostream', nostream)
    body = {'model': 'gpt-6.1-sol', 'messages': [], 'reasoning_effort': 'high'}
    got = await ga._chat_completions_stream(
        url='https://api.52pay.com/v1/chat/completions', headers={},
        body=body, timeout=90, use_model='gpt-6.1-sol')
    assert got is result
    assert nostream.await_args.kwargs['body'] == body
    assert nostream.await_count == 1


@pytest.mark.asyncio
async def test_52pay_timeout_does_not_restart_request(monkeypatch):
    nostream = AsyncMock(side_effect=httpx.ReadTimeout('stalled'))
    monkeypatch.setattr(ga, '_chat_completions_nostream', nostream)
    with pytest.raises(ga.GptApiError) as exc:
        await ga._chat_completions_stream(
            url='https://api.52pay.com/v1/chat/completions', headers={},
            body={}, timeout=90, use_model='gpt-6.1-sol')
    assert not exc.value.retryable
    assert exc.value.context['error_kind'] == 'provider_timeout'
    assert nostream.await_count == 1

