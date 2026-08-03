"""С реф-картинкой outsee_retry не ходит в HTTP API — только CDP attach."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.outsee import GenerationResult
from app.services import outsee_retry as mod


@pytest.mark.asyncio
async def test_generate_with_reference_uses_cdp_not_http_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ref = tmp_path / "c01.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 80)
    out = tmp_path / "c02.png"
    api_calls: list[str] = []
    cdp_calls: list[dict] = []

    async def fake_api(*_a, **_k):
        api_calls.append("hit")
        raise AssertionError("HTTP API must not be called when refs present")

    class FakeOutsee:
        async def generate_image(self, prompt: str, out_path: Path, **kwargs):
            cdp_calls.append({"prompt": prompt, "ref": kwargs.get("reference_image")})
            out_path.write_bytes(b"ok" * 50)
            return GenerationResult(
                file_path=out_path, raw_url="https://example/x.png", gen_id="g1"
            )

    async def fake_prepare(gpt, body, prefix, *, project_id=None):
        return body

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", fake_prepare)
    monkeypatch.setattr("app.bots.grsai.grsai_enabled", lambda: False)
    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_api_enabled_for_image", lambda: True
    )
    monkeypatch.setattr(
        "app.bots.outsee_http.generate_image", fake_api
    )

    result = await mod.generate_image_with_retries(
        FakeOutsee(),
        None,
        prompt="keep identity from ref",
        out_path=out,
        max_attempts_per_prompt=1,
        gpt_rewrite=False,
        reference_image=[ref],
        project_id=1,
    )
    assert result.file_path == out
    assert api_calls == []
    assert len(cdp_calls) == 1
    assert cdp_calls[0]["ref"] == [ref]


@pytest.mark.asyncio
async def test_try_http_image_skips_when_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bots.outsee import OutseeBot

    called = {"n": 0}

    async def boom(**_k):
        called["n"] += 1
        raise AssertionError("generate_image HTTP must not run")

    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_http_enabled", lambda: True
    )
    monkeypatch.setattr("app.bots.outsee_http.generate_image", boom)

    bot = OutseeBot(session=None)  # type: ignore[arg-type]
    res = await bot._try_http_image(
        prompt="x",
        out_path=Path("y.png"),
        reference_images=[Path("a.png")],
    )
    assert res is None
    assert called["n"] == 0
