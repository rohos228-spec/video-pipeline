"""Прогон группы script_frames_qc на закадре Спесивцева → HTML-отчёт."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.scene_shot_grammar import (
    apply_grammar_to_ops,
    bits_cover_vo,
    fill_bit_spans,
    merge_same_place_scenes,
    shots_grammar_reason,
)
from app.services.shots_report import render_all_nodes_one_table, render_group_run_html

VO = (
    "Алекса́ндр Спеси́вцев, сын Людми́лы Спеси́вцевой, рос в семье, где мать "
    "занимала центральное место. Она защищала его от внешнего мира, принимала "
    "решения за него и всё сильнее замыкала семейную жизнь вокруг себя и сына. "
    "Отец постепенно оказался на периферии этой истории, а сестра Наде́жда жила "
    "рядом, хотя впоследствии суд не установил её участия в преступлениях. "
    "Для окружающих семья выглядела тяжёлой, конфликтной и неблагополучной, "
    "однако само по себе это ещё не означало, что внутри совершаются преступления. "
    "Психиатрический диагноз Александра становился удобным объяснением многих "
    "странностей: если человек болен, значит, за ним должны наблюдать врачи. "
    "На практике ответственность оказалась разделена между больницей, семьёй "
    "и милицией. В 1992 году Александра направили в Орло́вскую "
    "специализированную психиатрическую больницу, а в 1995 году он вернулся "
    "в Новокузне́цк. При этом документы содержали противоречия: по одним сведениям "
    "он уже находился дома, по другим мог продолжать числиться в стационаре. "
    "Позже именно эта путаница осложнила поиски и проверку информации о его "
    "местонахождении."
)

BITS = [
    {"порядок": 1, "изменение": "мать → центр", "якорь": "Алекса́ндр Спеси́вцев"},
    {"порядок": 2, "изменение": "мир → закрыт", "якорь": "Она защищала его"},
    {"порядок": 3, "изменение": "отец → периферия", "якорь": "Отец постепенно"},
    {"порядок": 4, "изменение": "сестра → рядом", "якорь": "а сестра Наде́жда"},
    {"порядок": 5, "изменение": "снаружи → конфликт", "якорь": "Для окружающих"},
    {"порядок": 6, "изменение": "внутри → не видно", "якорь": "однако само по себе"},
    {"порядок": 7, "изменение": "диагноз → объяснение", "якорь": "Психиатрический диагноз"},
    {"порядок": 8, "изменение": "ответственность → трое", "якорь": "На практике"},
    {"порядок": 9, "изменение": "дом → Орловская", "якорь": "В 1992 году"},
    {"порядок": 10, "изменение": "Орловская → Новокузнецк", "якорь": "а в 1995 году"},
    {"порядок": 11, "изменение": "бумаги → две записи", "якорь": "При этом документы"},
    {"порядок": 12, "изменение": "поиск → путаница", "якорь": "Позже именно эта путаница"},
]

BIT_PLACE = {
    1: "квартира Спесивцевых",
    2: "квартира Спесивцевых",
    3: "квартира Спесивцевых",
    4: "квартира Спесивцевых",
    5: "окна во двор",
    6: "окна во двор",
    7: "кабинет врача",
    8: "кабинет врача",
    9: "Орловская больница",
    10: "Новокузнецк",
    11: "Новокузнецк",
    12: "Новокузнецк",
}

BIT_STEP = {
    1: "дверь закрыта на засов → ключ лежит на столе",
    2: "окно закрыто шторой → пустая тарелка стоит у края",
    3: "стул отодвинут от стола",
    4: "фото стоит на полке",
    5: "соседи смотрят на окна → забор стоит вдоль улицы",
    6: "калитка стоит закрытой → окна дома стоят тёмными",
    7: "карта лежит на столе врача → три папки лежат в ряд",
    8: "лампа горит над бланком → штамп лежит на бланке → три стула стоят у стены",
    9: "коридор больницы стоит пустой → дверь палаты закрыта",
    10: "сумка стоит у порога",
    11: "две справки лежат рядом → папка открыта на двух датах",
    12: "карта лежит с двумя адресами → календарь лежит открытым → печать лежит на двух справках",
}


def _one_table_html(model: dict) -> str:
    qc = "ок" if not model.get("qc") else str(model.get("qc"))
    table = render_all_nodes_one_table(model)
    return f"""<!doctype html><html lang=ru><meta charset=utf-8>
<title>script_frames_qc · одна таблица</title>
<style>
body{{font:13px/1.35 system-ui,Segoe UI,sans-serif;margin:16px;color:#111;background:#fff}}
h1{{font-size:20px;margin:0 0 6px}}
.meta{{color:#444;margin:0 0 12px}}
table{{border-collapse:collapse;width:100%;min-width:3200px}}
th,td{{border:1px solid #bbb;vertical-align:top;padding:8px 8px}}
th{{background:#ececec;text-align:left}}
.vo-bit{{color:#555;margin-top:4px}}
</style>
<h1>script_frames_qc · одна таблица всех нод</h1>
<p class=meta>1 сценарист · 2 проверка · 3 действие · 4 кадры-шаги · 5 QC · 6 отчёт=эта таблица
· {len(model.get('bits') or [])} битов · {len(model.get('shots') or [])} кадров · QC: {qc}
· бит = слово/словосочетание было→стало; закадр бита = покрытие, не ярлык
· закадр кадра режется по точке или запятой</p>
{table}
"""


def run() -> dict:
    vo = " ".join(VO.split())
    bits = fill_bit_spans(vo, BITS)
    if not bits_cover_vo(vo, bits):
        glued = " ".join(str(b.get("закадр") or "") for b in bits)
        raise SystemExit(f"биты не покрывают VO\n{glued!r}\n{vo!r}")
    for bit in bits:
        change = str(bit.get("изменение") or "")
        span = str(bit.get("закадр") or "")
        if "→" not in change:
            raise SystemExit(f"бит {bit.get('порядок')} без было→стало: {change!r}")
        if change.strip() in span:
            raise SystemExit(f"бит {bit.get('порядок')} скопировал закадр: {change!r}")

    lines: list[str] = []
    for b in bits:
        n = int(b["порядок"])
        place = BIT_PLACE[n]
        step = BIT_STEP[n]
        chunk = str(b.get("закадр") or "").strip()
        lines.append(f"{n}. {place} — {step}")
        lines.append(f"({chunk})")
    raw_action = "\n".join(lines)
    action = merge_same_place_scenes(raw_action)

    ops = [{"frame_uuid": "seed-spesivtsev", "fields": {"кадры": [], "главное_действие": action}}]
    frames = [
        {
            "uuid": "seed-spesivtsev",
            "number": 1,
            "voiceover_text": vo,
            "main_action": action,
        }
    ]
    apply_grammar_to_ops(ops, frames)
    shots = ops[0]["fields"]["кадры"]
    qc = shots_grammar_reason(shots, vo, "seed-spesivtsev")
    return {
        "vo": vo,
        "bits": bits,
        "action_raw": raw_action,
        "action": action,
        "shots": shots,
        "qc": qc,
    }


def main() -> None:
    model = run()
    out_dir = Path("/opt/cursor/artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "spesivtsev_script_frames_qc.json"
    html_path = out_dir / "spesivtsev_shots_report.html"
    one_path = out_dir / "spesivtsev_one_table.html"
    json_path.write_text(
        json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_path.write_text(
        render_group_run_html(
            model,
            slug="spesivtsev",
            title="Отчёт группы нод · Спесивцев",
        ),
        encoding="utf-8",
    )
    one_path.write_text(_one_table_html(model), encoding="utf-8")
    print("qc:", model["qc"])
    print("shots:", len(model["shots"]))
    print("bits:", len(model["bits"]))
    print("wrote", html_path)
    print("wrote", one_path)
    print("wrote", json_path)


if __name__ == "__main__":
    main()
