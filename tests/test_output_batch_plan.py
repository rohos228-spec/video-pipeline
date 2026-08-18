"""Pack GPT work by estimated output tokens before the first request."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.output_batch_plan import (
    OUTPUT_TOKEN_BUDGET,
    estimate_frame_output_tokens,
    pack_frames_for_output,
)


def _fr(n: int, *, prompt: str = "", **attrs) -> SimpleNamespace:
    return SimpleNamespace(
        number=n,
        uuid=f"u{n:03d}",
        image_prompt=prompt,
        attrs=attrs,
    )


def test_pack_splits_when_token_sum_exceeds_budget() -> None:
    frames = [_fr(i, prompt="а" * 800) for i in range(1, 21)]
    batches = pack_frames_for_output(
        frames, budget=4_000, count_tokens=lambda text: len(text)
    )
    assert len(batches) >= 2
    assert [fr.uuid for b in batches for fr in b] == [fr.uuid for fr in frames]
    for b in batches[:-1]:
        est = sum(estimate_frame_output_tokens(fr, count_tokens=lambda t: len(t)) for fr in b)
        assert est <= 4_000


def test_fat_frame_does_not_share_batch_with_another_fat() -> None:
    fat = "б" * 3_500
    frames = [_fr(1, prompt=fat), _fr(2, prompt=fat)]
    batches = pack_frames_for_output(
        frames, budget=4_000, count_tokens=lambda text: len(text)
    )
    assert [len(b) for b in batches] == [1, 1]


def test_existing_prompt_used_over_empty_attrs() -> None:
    fr = _fr(1, prompt="готовый промт " * 40, shot01_bg="короткий фон")
    a = estimate_frame_output_tokens(fr, count_tokens=lambda t: len(t))
    fr2 = _fr(2, prompt="", shot01_bg="короткий фон")
    b = estimate_frame_output_tokens(fr2, count_tokens=lambda t: len(t))
    assert a > b


def test_default_budget_is_under_128k() -> None:
    assert 50_000 <= OUTPUT_TOKEN_BUDGET <= 90_000


def test_plan_db_frames_slices_splits_fat_file(tmp_path) -> None:
    from app.services.output_batch_plan import plan_db_frames_slices

    frames = [
        {"uuid": f"u{i:03d}", "number": i, "промт_картинки": "я" * 800}
        for i in range(1, 21)
    ]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"source": "db_v2", "frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    slices = plan_db_frames_slices(
        [path], budget=4_000, count_tokens=lambda text: len(text)
    )
    assert slices is not None
    assert len(slices) >= 2
    total = 0
    for p in slices:
        data = json.loads(p.read_text(encoding="utf-8"))
        total += len(data["frames"])
    assert total == 20
