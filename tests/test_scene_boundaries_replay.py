from pathlib import Path

import pytest

from app.services import scene_boundaries as sb


def test_complete_duplicate_only_is_repaired():
    vo = "Первое предложение. Второе предложение."
    fragments = ["Первое предложение. ", "Второе предложение."]
    fixed, copies = sb.remove_repeated_complete_segmentation(vo, fragments * 2, " ")
    assert fixed == fragments
    assert copies == 2
    assert sb.check_seg(vo, fixed, "")["strict_ws_equal"]


@pytest.mark.parametrize("frags,outside", [
    (["Первое предложение.", "Чужой текст."], ""),
    (["Первое предложение."], "пояснение"),
    (["[Первое предложение."], ""),
    (["Первое предложение. Первое предложение."], ""),
])
def test_no_heuristic_repair_of_partial_or_invalid_output(frags, outside):
    fixed, copies = sb.remove_repeated_complete_segmentation("Первое предложение.", frags, outside)
    assert fixed == frags
    assert copies == 1


def test_intentional_repeated_source_sentence_is_preserved():
    vo = "Он ушёл. Он ушёл."
    frags = ["Он ушёл. ", "Он ушёл."]
    assert sb.remove_repeated_complete_segmentation(vo, frags, "") == (frags, 1)


@pytest.mark.asyncio
async def test_node_accepts_exact_complete_replay_without_paid_retry(tmp_path, monkeypatch):
    vo = "Начало истории. Конец истории."
    async def call(*args):
        return "[Начало истории. ][Конец истории.][Начало истории. ][Конец истории.]"
    monkeypatch.setattr(sb, "call_opus", call)
    fragments, report = await sb.run_boundaries(vo, tmp_path)
    assert "".join(fragments) == vo
    assert len(fragments) == 2
    assert report["check_passed"]
    assert not report["retried"]
    assert report["tries"][0]["duplicate_complete_copies_removed"] == 1


@pytest.mark.asyncio
async def test_retry_includes_failure_feedback_and_never_accepts_invented_story(tmp_path, monkeypatch):
    calls = []
    async def call(user, *args):
        calls.append(user)
        return "[Совершенно чужая история.]"
    monkeypatch.setattr(sb, "call_opus", call)
    _fragments, report = await sb.run_boundaries("Настоящий исходный текст.", tmp_path)
    assert len(calls) == 2
    assert "Предыдущая попытка НЕ прошла" in calls[1]
    assert "Настоящий исходный текст." in calls[1]
    assert not report["check_passed"]


def test_real_failed_response_replay_if_present():
    root = Path(__file__).resolve().parents[1]
    trace = root / "data/videos/test-031126/node_trace/n_cinema_excel_gpt_fw_boundaries/20261103_112728"
    response = trace / "A_seg_a2.resp.txt"
    request = trace / "A_seg_a2.req.txt"
    if not response.is_file() or not request.is_file():
        pytest.skip("Local incident trace is not committed")
    vo = request.read_text(encoding="utf-8").split("===ТЕКСТ_НАЧАЛО===", 1)[1].split("===ТЕКСТ_КОНЕЦ===", 1)[0].strip()
    raw = response.read_text(encoding="utf-8")
    frags, outside = sb.parse_seg(raw)
    assert not sb.check_seg(vo, frags, outside)["strict_ws_equal"]
    fixed, copies = sb.remove_repeated_complete_segmentation(vo, frags, outside)
    assert copies == 2
    assert sb.check_seg(vo, fixed, outside)["strict_ws_equal"]
    assert "".join(sb.project_to_source(vo, fixed, key=sb.nz)) == vo
