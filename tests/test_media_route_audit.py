"""Маршрут нативной генерации картинки: куда реально уходит слаг модели Kie (docs/MEDIA_ROUTE_AUDIT.md).

Без сети, без платных вызовов, без БД: оба провайдерских шва подменены. Тесты закрепляют ТЕКУЩЕЕ поведение
`outsee_retry.generate_image_with_retries` вне управляемого режима: ветка Kie (`generate_kie_image`) стоит внутри
`if use_outsee_api`, а `use_outsee_api = backend == "outsee" and ...`, поэтому для слага Kie она недостижима.
Если маршрут починят, эти тесты упадут: это сигнал пересмотреть уровень `items`/`images`/`hero` в покрытии нод.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.bots.outsee import GenerationResult
from app.services import media_route, outsee_retry

KIE_IMAGE_SLUGS = ("flux-2-pro", "seedream-5-pro", "z-image", "qwen3-image")
OUTSEE_IMAGE_SLUGS = ("gpt-image-2-vip", "nano-banana-2", "nano-banana-2-lite")
APP = Path(__file__).resolve().parents[1] / "app"


class _OutseeSeam:
    def __init__(self) -> None:
        self.slugs: list[str | None] = []

    async def generate_image(self, prompt, out_path, **kwargs):
        self.slugs.append(kwargs.get("model_slug"))
        return GenerationResult(file_path=out_path, raw_url=None, gen_id=None)


def test_provider_table_names_four_kie_image_models_and_three_outsee_ones():
    assert {s: media_route.image_provider_for(s) for s in KIE_IMAGE_SLUGS} == {s: "kie" for s in KIE_IMAGE_SLUGS}
    assert {s: media_route.image_provider_for(s) for s in OUTSEE_IMAGE_SLUGS} == {s: "outsee" for s in OUTSEE_IMAGE_SLUGS}


@pytest.mark.parametrize("slug", KIE_IMAGE_SLUGS)
async def test_a_kie_slug_goes_to_the_outsee_browser_seam_not_to_kie_in_the_native_route(slug):
    kie = AsyncMock(side_effect=AssertionError("kie seam reached"))
    seam = _OutseeSeam()
    with patch("app.bots.kie_http.generate_kie_image", kie), \
         patch("app.bots.outsee_http.outsee_api_configured", return_value=False):
        await outsee_retry.generate_image_with_retries(
            seam, None, prompt="x" * 150, out_path=Path("predmet1_abcdef12.png"),
            model_slug=slug, project_id=None, gpt_rewrite=False)
    assert seam.slugs == [slug]
    kie.assert_not_called()


@pytest.mark.parametrize("slug", KIE_IMAGE_SLUGS)
async def test_a_kie_slug_with_an_outsee_key_and_no_browser_bot_never_reaches_kie_either(slug):
    """The images step passes outsee=None when an Outsee key is set (HTTP primary). A Kie slug then has no seam
    at all: the call fails on None instead of submitting to Kie. Documents the defect, not a wanted behaviour."""
    kie = AsyncMock(side_effect=AssertionError("kie seam reached"))
    api = AsyncMock(side_effect=AssertionError("outsee api reached"))
    with patch("app.bots.kie_http.generate_kie_image", kie), \
         patch("app.bots.outsee_http.generate_image", api), \
         patch("app.bots.outsee_http.outsee_api_configured", return_value=True):
        with pytest.raises(AttributeError):
            await outsee_retry.generate_image_with_retries(
                None, None, prompt="x" * 150, out_path=Path("frame_001_abcdef12.png"),
                model_slug=slug, project_id=None, gpt_rewrite=False)
    kie.assert_not_called()
    api.assert_not_called()


def test_the_only_production_caller_of_generate_kie_image_is_the_unreachable_branch_and_the_managed_one():
    """No step calls the Kie image seam directly: the native image ledger rows (frame_/hero_/predmet) can only
    come from outsee_retry, whose Kie branch is unreachable outside the synthetic managed route."""
    callers = []
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        if "generate_kie_image" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "generate_kie_image":
                callers.append(path.relative_to(APP).as_posix())
    assert sorted(set(callers)) == ["services/outsee_retry.py"]
    assert len(callers) == 2  # managed branch + the unreachable native branch
