"""Ревью #352 r5: неопределённый submit не ретраится как сеть.

``OutseeSubmitUncertain`` несёт ``network: True``. Если
``_is_transient_network_error`` отвечает True, шаг повторит платный POST.
"""

import httpx
import pytest

from app.bots.outsee_http import OutseeSubmitUncertain
from app.services import outsee_retry as mod


class _Client:
    posts: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        type(self).posts.append("httpx")
        raise httpx.ReadTimeout("read timed out")


@pytest.mark.asyncio
async def test_uncertain_submit_is_exactly_one_post(monkeypatch, tmp_path) -> None:
    import app.bots.outsee_http as oh

    _Client.posts = []
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(oh.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(oh, "outsee_api_configured", lambda: True)

    async def _prep(*_a, **_k):
        return _a[1]

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _prep)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr(
        "app.services.api_tracker_hook.check_api_allowed",
        lambda **_k: None,
    )

    with pytest.raises(OutseeSubmitUncertain) as ei:
        await mod.generate_image_with_retries(
            None,
            None,
            prompt="тихий двор",
            out_path=tmp_path / "frame.png",
            max_attempts_per_prompt=3,
            gpt_rewrite=False,
            model_slug="gpt-image-2",
            aspect_ratio="9:16",
            resolution="2K",
            project_id=13,
        )
    # Шаг, который ретраит по этому предикату, не должен слать второй POST.
    assert mod._is_transient_network_error(ei.value) is False
    assert _Client.posts == ["httpx"]
