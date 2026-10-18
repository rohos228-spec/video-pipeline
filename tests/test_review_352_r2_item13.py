"""Повторное ревью #352 №13: rewire не затирает свой промт и подпись."""

from __future__ import annotations

from app.services import node_groups as ng


def _meta(*, main: str, qc_label: str) -> dict:
    return {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [
                {
                    "id": "n_excel_gpt_fw_shots",
                    "type": "excel_gpt",
                    "position": {"x": 0, "y": 0},
                    "data": {"label": "GPT: сцены → кадры"},
                },
                {
                    "id": "n_excel_gpt_fw_qc",
                    "type": "excel_gpt",
                    "position": {"x": 1, "y": 0},
                    "data": {"label": qc_label},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n_excel_gpt_fw_shots",
                    "target": "n_excel_gpt_fw_qc",
                    "data": {"kind": "after"},
                }
            ],
        },
        "prompt_slot_variants": {
            "n_excel_gpt_fw_qc": {"main": main, "extra": "keep"},
        },
    }


def test_custom_qc_prompt_and_label_survive_rewire() -> None:
    meta = _meta(main="my_custom_qc", qc_label="Мой QC")
    assert ng.rewire_script_frames_qc_shots_to_qc(meta) is True
    slot = meta["prompt_slot_variants"]["n_excel_gpt_fw_qc"]
    assert slot["main"] == "my_custom_qc"
    assert slot["extra"] == "keep"
    qc = next(n for n in meta["canvas_graph"]["nodes"] if n["id"] == "n_excel_gpt_fw_qc")
    assert qc["data"]["label"] == "Мой QC"


def test_empty_qc_prompt_becomes_shots_qc_ru() -> None:
    meta = _meta(main="", qc_label="Мой QC")
    ng.rewire_script_frames_qc_shots_to_qc(meta)
    assert meta["prompt_slot_variants"]["n_excel_gpt_fw_qc"]["main"] == "shots_qc_ru"
    qc = next(n for n in meta["canvas_graph"]["nodes"] if n["id"] == "n_excel_gpt_fw_qc")
    assert qc["data"]["label"] == "Мой QC"
