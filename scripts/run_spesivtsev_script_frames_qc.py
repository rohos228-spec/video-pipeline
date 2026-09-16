"""Первый шаг: сырой apply-ops ноды сценариста + fill_bit_spans. Без своих битов."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.services.scene_shot_grammar import bits_cover_vo, fill_bit_spans
from app.services.shots_report import render_bits_check_table

REPLY = Path("/opt/cursor/artifacts/fw_script_node/gpt_reply.txt")
FRAMES = Path("/opt/cursor/artifacts/fw_script_node/db_frames.json")


def load_node_bits(reply_path: Path = REPLY) -> tuple[str, list[dict]]:
    db = json.loads(FRAMES.read_text(encoding="utf-8"))
    vo = str((db["frames"][0].get("voiceover_text") or "")).strip()
    payload = json.loads(reply_path.read_text(encoding="utf-8"))
    ops = payload.get("ops") or []
    if not ops:
        raise SystemExit("в ответе нет ops")
    bits = list((ops[0].get("fields") or {}).get("биты") or [])
    if not bits:
        raise SystemExit("в ответе нет биты")
    return vo, bits


def _first_step_html(model: dict) -> str:
    bits_table = render_bits_check_table(model)
    return f"""<!doctype html><html lang=ru><meta charset=utf-8>
<title>шаг 1 · сырой ответ</title>
<style>
body{{font:16px/1.45 system-ui,Segoe UI,sans-serif;margin:16px;color:#111;background:#fff}}
h1{{font-size:22px;margin:0 0 8px}}
.meta{{color:#333;margin:0 0 14px;max-width:1100px}}
pre{{white-space:pre-wrap;background:#f6f6f6;border:1px solid #ddd;padding:10px;font-size:13px}}
table{{border-collapse:collapse;width:100%}}
table.bits-node{{min-width:1400px;font-size:16px}}
th,td{{border:1px solid #bbb;vertical-align:top;padding:10px 10px}}
th{{background:#ececec;text-align:left}}
.vo-bit{{color:#555;margin-top:4px}}
</style>
<h1>Шаг 1 · сырой JSON модели</h1>
<p class=meta>Промт <code>script_writer_ru.md</code> + <code>db_frames.json</code>.
Поля бита не правились. <code>закадр</code> ниже — только <code>fill_bit_spans</code>.
· {len(model.get('bits') or [])} битов</p>
<pre>{json.dumps(model.get('raw_ops'), ensure_ascii=False, indent=2)}</pre>
<h1>Закадр по якорям (код, не модель)</h1>
{bits_table}
"""


def run() -> dict:
    vo, bits = load_node_bits()
    raw_bits = copy.deepcopy(bits)
    filled = fill_bit_spans(vo, bits)
    return {
        "vo": vo,
        "bits": filled,
        "raw_ops": {
            "ops": [
                {
                    "frame_uuid": "8f3c1a2b-4d5e-4678-9abc-def012345678",
                    "fields": {"биты": raw_bits},
                }
            ]
        },
        "cover": bits_cover_vo(vo, filled),
        "qc": None,
    }


def main() -> None:
    model = run()
    out_dir = Path("/opt/cursor/artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "spesivtsev_step1_bits.html"
    json_path = out_dir / "spesivtsev_step1_bits.json"
    json_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_first_step_html(model), encoding="utf-8")
    print("bits:", len(model["bits"]))
    print("cover:", model["cover"])
    print("wrote", html_path)


if __name__ == "__main__":
    main()
