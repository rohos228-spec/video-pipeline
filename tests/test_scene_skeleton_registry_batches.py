"""Каркас: персонажи одним запросом, сцены тремя пачками по фрагментам границ."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from app.services import scene_skeleton_batches as skb


def test_scene_batches_cut_only_on_boundary_fragments() -> None:
    assert skb.fragment_ranges(39, 3) == [(0, 13), (13, 26), (26, 39)]
    assert skb.fragment_ranges(10, 3) == [(0, 4), (4, 7), (7, 10)]
    assert skb.fragment_ranges(2, 3) == [(0, 1), (1, 2)]
    assert skb.fragment_ranges(0, 3) == []


@pytest.mark.asyncio
async def test_registry_prepass_is_one_batch(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_call(user, system, tag, trace_dir):
        calls.append(tag)
        assert "весь фильм" in user
        assert "1–10" in user
        return json.dumps(
            {
                "база_персонажей": [
                    {"id": "c01", "имя": "Иван Петров", "фрагменты": [1, 9]},
                    {
                        "id": "c02",
                        "имя": "Иван Петров — в форме (вариация c01)",
                        "фрагменты": [10],
                    },
                ],
                "предметы": [{"id": "p01", "имя": "пистолет"}],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(skb, "_call", fake_call)
    frags = [f"фрагмент {i}" for i in range(10)]

    chars, items = await skb.run_registry_prepass(frags, "", tmp_path)

    assert [c.split("_a")[0] for c in calls] == ["A_registry_r01"]
    bases = [c for c in chars if not str(c.get("правила") or "").strip()]
    variations = [c for c in chars if str(c.get("правила") or "").strip()]
    assert len(bases) == 1
    assert bases[0]["фрагменты"] == [1, 9]
    assert len(variations) == 1
    assert variations[0]["правила"] == bases[0]["id"]
    assert variations[0]["фрагменты"] == [10]
    assert len(items) == 1
    assert items[0]["имя"] == "пистолет"


@pytest.mark.asyncio
async def test_scene_batch_retries_network_before_giving_up(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_call(user, system, tag, trace_dir):
        calls.append(tag)
        if len(calls) < 3:
            raise RuntimeError("ConnectError: All connection attempts failed")
        return json.dumps(
            {"сцены": [{"номер": 1, "сцена": "он стоит у двери", "закадр": "текст"}]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(skb, "_call", fake_call)
    monkeypatch.setattr(skb, "SCENE_RETRY_S", 0)
    rep: dict = {"split_after_fail": []}
    out = await skb._run_one_batch("b01", 0, 1, ["текст"], "", tmp_path, [], [], rep)
    assert [c.rsplit("_", 1)[-1] for c in calls] == ["a1", "a2", "a3"]
    assert rep["split_after_fail"] == []
    assert out[0][4][0]["сцена"] == "он стоит у двери"


@pytest.mark.asyncio
async def test_scene_batch_is_not_split_after_network_error(tmp_path, monkeypatch):
    calls: list[str] = []

    async def fake_call(user, system, tag, trace_dir):
        calls.append(tag)
        if tag.endswith("_a1"):
            raise RuntimeError("HTTP 524")
        return json.dumps(
            {
                "сцены": [
                    {"номер": 1, "сцена": "раз", "закадр": "а"},
                    {"номер": 2, "сцена": "два", "закадр": "б"},
                    {"номер": 3, "сцена": "три", "закадр": "в"},
                    {"номер": 4, "сцена": "четыре", "закадр": "г"},
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(skb, "_call", fake_call)
    monkeypatch.setattr(skb, "SCENE_RETRY_S", 0)
    rep: dict = {"split_after_fail": []}
    out = await skb._run_one_batch("b01", 0, 4, ["а", "б", "в", "г"], "", tmp_path, [], [], rep)
    assert calls == ["B_scenes_b01_a1", "B_scenes_b01_a2"]
    assert rep["split_after_fail"] == []
    assert len(out) == 1
    assert len(out[0][4]) == 4


@pytest.mark.asyncio
async def test_skeleton_sends_one_batch_at_a_time(tmp_path, monkeypatch):
    inflight = 0
    max_seen = 0

    async def fake_call(user, system, tag, trace_dir):
        nonlocal inflight, max_seen
        inflight += 1
        max_seen = max(max_seen, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        if tag.startswith("A_registry"):
            return json.dumps(
                {
                    "база_персонажей": [
                        {"id": "c01", "имя": "Иван", "фрагменты": [1]},
                    ],
                    "предметы": [],
                },
                ensure_ascii=False,
            )
        count = int(re.search(r"Напиши ровно (\d+)", user).group(1))
        return json.dumps(
            {
                "сцены": [
                    {"номер": n, "сцена": "действие", "закадр": "x"}
                    for n in range(1, count + 1)
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(skb, "_call", fake_call)
    monkeypatch.setattr(skb, "SCENE_RETRY_S", 0)
    flag = Path("data/_resend_skeleton_pack.flag")
    backup = flag.read_bytes() if flag.is_file() else None
    try:
        _doc, rep = await skb.run_skeleton_batches(
            [f"фрагмент {i}" for i in range(16)], "", tmp_path
        )
    finally:
        if backup is None:
            if flag.is_file():
                flag.unlink()
        else:
            flag.write_bytes(backup)

    assert max_seen == 1
    assert rep["mode"] == "registry 1 batch, scenes 3 by boundary fragments"
    assert [b["fragments"] for b in rep["batches"]] == ["1-6", "7-11", "12-16"]
