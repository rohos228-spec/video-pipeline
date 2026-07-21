"""Тесты 5 механик поиска / сортировки / скачивания."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.montage_outsee_five import (
    DOWNLOAD_MECHANICS,
    HitCandidate,
    _parse_ids,
    _url_ts,
    sort_m5_pending_priority,
    search_m1_dom_scan,
)


def test_five_download_mechanics_registered() -> None:
    assert len(DOWNLOAD_MECHANICS) == 5
    names = [n for n, _ in DOWNLOAD_MECHANICS]
    assert names == [
        "d1_thumb_button",
        "d2_result_panel",
        "d3_dom_full_request",
        "d4_page_fetch",
        "d5_cascade",
    ]


def test_parse_ids_and_url_ts() -> None:
    got = _parse_ids("[ID: P13-F3-abcdef12] x [ID: P13-F3-abcdef12]-S2", project_id=13)
    assert len(got) == 2
    assert got[0][2] == 1 and got[1][2] == 2
    ts = _url_ts(
        "https://cdn/x/image_1780991092050_0_thumb.jpg?sig=1"
    )
    assert ts == 1780991092050


def test_sort_m5_pending_priority_orders() -> None:
    hits = [
        HitCandidate(1, 1, "aaaa1111", "[ID: P13-F1-aaaa1111]", "https://a/image_100_0_thumb.jpg", sources={"m1_dom"}),
        HitCandidate(2, 1, "bbbb2222", "[ID: P13-F2-bbbb2222]", "https://a/image_200_0_thumb.jpg", sources={"m2_click", "m1_dom"}),
        HitCandidate(3, 1, "cccc3333", "[ID: P13-F3-cccc3333]", "https://a/image_300_0_thumb.jpg", sources={"m3_text"}),
    ]
    ordered = sort_m5_pending_priority(
        hits,
        frame_filter={(2, 1), (3, 1)},
        pending_keys={(2, 1)},
    )
    assert [h.frame_number for h in ordered] == [2, 3]
    # pending F2 first
    assert ordered[0].frame_number == 2


@pytest.mark.asyncio
async def test_search_m1_dom_scan_parses() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=[
            {
                "src": "https://cdn/image_1_0_thumb.jpg",
                "text": "prompt [ID: P13-F4-aabbccdd]",
                "y": 10,
                "idx": 0,
            }
        ]
    )
    hits = await search_m1_dom_scan(page, 13, limit=10)
    assert len(hits) == 1
    assert hits[0].frame_number == 4
    assert "m1_dom" in hits[0].sources
