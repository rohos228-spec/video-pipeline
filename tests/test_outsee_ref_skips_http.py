"""С реф-картинкой outsee_retry шлёт рефы в Outsee HTTP API как image_urls."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.outsee import GenerationResult
from app.services import outsee_retry as mod


@pytest.mark.asyncio
async def test_generate_with_reference_uses_http_api_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref = tmp_path / "c01.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 80)
    out = tmp_path / "c02.png"
    api_calls: list[dict] = []

    async def fake_api(prompt, out_path, **kwargs):
        api_calls.append({"prompt": prompt, "refs": kwargs.get("reference_images")})
        out_path.write_bytes(b"ok" * 50)
        return GenerationResult(
            file_path=out_path, raw_url="https://example/x.png", gen_id="g1"
        )

    class FakeOutsee:
        async def generate_image(self, *_a, **_k):
            raise AssertionError("CDP generate_image must not run when Outsee API on")

    async def fake_prepare(gpt, body, prefix, *, project_id=None):
        return body

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", fake_prepare)
    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_api_configured", lambda: True
    )
    monkeypatch.setattr("app.bots.outsee_http.generate_image", fake_api)

    result = await mod.generate_image_with_retries(
        FakeOutsee(),
        None,
        prompt="keep identity from ref",
        out_path=out,
        max_attempts_per_prompt=1,
        gpt_rewrite=False,
        reference_image=[ref],
        project_id=1,
        model_slug="gpt-image-2-vip",
    )
    assert result.file_path == out
    assert len(api_calls) == 1
    assert api_calls[0]["refs"] == [ref]


@pytest.mark.asyncio
async def test_outsee_http_generate_image_hosts_and_sends_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.bots import outsee_http as oh

    ref = tmp_path / "c01.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    out = tmp_path / "out.png"
    posted: list[dict] = []

    async def fake_host(url: str | None, **_kwargs):
        assert url and url.startswith("data:")
        return "https://example.test/ref.png"

    async def fake_post(path: str, body: dict):
        posted.append({"path": path, "body": body})
        return {"id": "task-1"}

    async def fake_poll(task_id: str, *, timeout: float = 600):
        return {"status": "completed", "result_url": "https://example.test/out.png"}

    async def fake_download(url: str, dest: Path):
        dest.write_bytes(b"png")

    monkeypatch.setattr(oh, "ensure_public_image_url", fake_host)
    monkeypatch.setattr(oh, "_post_generate", fake_post)
    monkeypatch.setattr(oh, "_poll_generation", fake_poll)
    monkeypatch.setattr(oh, "_download", fake_download)

    result = await oh.generate_image(
        "x",
        out,
        reference_images=[ref],
        model_slug="gpt-image-2",
    )
    assert result.file_path == out
    assert posted[0]["path"] == "/api/v1/images/generate"
    assert "reference_images" not in posted[0]["body"]
    assert posted[0]["body"]["image_urls"] == ["https://example.test/ref.png"]
