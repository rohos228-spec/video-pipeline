"""Holdout-6: реплики пользователя. Retrieve и метрики сюда не входят.

Пишется до правок структуры. Семейства те же, что у holdout-5, с перевесом
мира, людей, сборки и скелета. Gold — только отвечающие чанки.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import HOLDOUT6_NAME, _index_docs, _label

HOLDOUT6_PATH = Path(__file__).resolve().parent / "datasets" / HOLDOUT6_NAME
MIN_QUERIES = 40
WEIGHTED = ("world", "characters", "assemble", "skeleton")

METHOD = (
    "Реплика про задачу, симптом или цель, без имён нод, групп и номеров правил. "
    "Семейство по смыслу. Gold — только отвечающие part_key и однострочное обоснование. "
    "Retrieve и метрики не вызывались."
)

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "h6-01",
        "family": "world",
        "kind": "prompt",
        "text": "После перечня комнат можно дописать обычный поясняющий абзац?",
        "justification": "Ответ по комнатам — только машинный объект, поясняющего абзаца снаружи нет.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s3"),
            ("prompts", "n_excel_gpt_sd_world", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s6"),
        ],
    },
    {
        "id": "h6-02",
        "family": "world",
        "kind": "prompt",
        "text": "Поля про лампы и час суток из паспорта комнаты вычеркнуты?",
        "justification": "Освещение, свет и время суток в паспорт комнаты не пишут.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_world", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s6"),
        ],
    },
    {
        "id": "h6-03",
        "family": "world",
        "kind": "howto",
        "text": "Если перечень комнат пуст, в строке отчёта ставят ноль?",
        "justification": "Пустой перечень даёт пустой список и отчёт со счётчиком ноль.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s3"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s5"),
        ],
    },
    {
        "id": "h6-04",
        "family": "world",
        "kind": "prompt",
        "text": "Краткое имя комнаты разрешено слегка поправить, номер при этом не меняют?",
        "justification": "Имя можно уточнить, номер остаётся тем же, что пришёл во входе.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s4"),
        ],
    },
    {
        "id": "h6-05",
        "family": "world",
        "kind": "prompt",
        "text": "Коридор внутри той же квартиры — повод завести второй номер?",
        "justification": "Участок внутри одного помещения не получает второй номер.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s4"),
        ],
    },
    {
        "id": "h6-06",
        "family": "world",
        "kind": "howto",
        "text": "Номера комнат художник придумывает сам или берёт готовый перечень?",
        "justification": "Новые номера не выдумывают: паспорта пишут только по присланному перечню.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s1"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s3"),
        ],
    },
    {
        "id": "h6-07",
        "family": "world",
        "kind": "prompt",
        "text": "Цвет поверхностей в описании комнаты обязателен?",
        "justification": "Описание комнаты называет материал и цвет поверхностей.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s4"),
        ],
    },
    {
        "id": "h6-08",
        "family": "world",
        "kind": "prompt",
        "text": "У комнаты внутренние участки обязательны, или перечень участков может быть пуст?",
        "justification": "У каждой комнаты есть внутренние участки, пустой перечень участков — брак.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_world", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s6"),
        ],
    },
    {
        "id": "h6-09",
        "family": "characters",
        "kind": "prompt",
        "text": "Пометка, что человек вне кадра, раздувается до полноценного героя для картинок?",
        "justification": "Пометку «вне кадра» не раздувают в полноценного героя для картинок.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_characters", "s0")],
    },
    {
        "id": "h6-10",
        "family": "characters",
        "kind": "prompt",
        "text": "Характер укладывают в два–четыре слова, взятых из поступков?",
        "justification": "Характер — два–четыре слова из поступков в тексте.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s5"),
        ],
    },
    {
        "id": "h6-11",
        "family": "characters",
        "kind": "prompt",
        "text": "Двум людям можно отдать одинаковые волосы, лицо и костюм?",
        "justification": "Между любыми двумя карточками минимум два явных визуальных отличия.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s3"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s4"),
        ],
    },
    {
        "id": "h6-12",
        "family": "characters",
        "kind": "prompt",
        "text": "Цвет волос записывают как примету, которую по ходу ролика не меняют?",
        "justification": "В карточке фиксируют приметы, которые нельзя менять, включая цвет волос.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s3"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s4"),
        ],
    },
    {
        "id": "h6-13",
        "family": "characters",
        "kind": "howto",
        "text": "Человека «чтобы кто-то стоял в кадре» заводят отдельной карточкой?",
        "justification": "Карточку не заводят для красоты кадра и без прямой роли в тексте.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s3"),
        ],
    },
    {
        "id": "h6-14",
        "family": "characters",
        "kind": "prompt",
        "text": "В служебной строке ответа отдельно считают, скольким лицам облик додумали?",
        "justification": "В отчёте есть счётчик карточек, где облик додумали, потому что текст молчал.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s6"),
        ],
    },
    {
        "id": "h6-15",
        "family": "characters",
        "kind": "prompt",
        "text": "У каждого лица не меньше четырёх зримых примет?",
        "justification": "У каждой карточки не меньше четырёх зримых примет облика.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s6"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s7"),
        ],
    },
    {
        "id": "h6-16",
        "family": "characters",
        "kind": "howto",
        "text": "Сколько групп людей отбросили — это отдельное число в отчёте?",
        "justification": "В отчёте отдельно считают, сколько групп отбросили.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_characters", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_characters", "s6"),
        ],
    },
    {
        "id": "h6-17",
        "family": "assemble",
        "kind": "howto",
        "text": "Готовый пакет предназначен для записи в проект, и вызов допускается кусками?",
        "justification": "Итоговый пакет идёт на запись в проект, и пайплайн может звать сборку кусками.",
        "gold": [("prompts", "n_excel_gpt_sd_asm", "s0")],
    },
    {
        "id": "h6-18",
        "family": "assemble",
        "kind": "prompt",
        "text": "Связь с предыдущим отрезком и крючок к следующему, если они были, сохраняют?",
        "justification": "Если у отрезка уже есть связь с предыдущим и крючок к следующему, их оставляют.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_asm", "s4")],
    },
    {
        "id": "h6-19",
        "family": "assemble",
        "kind": "prompt",
        "text": "Ступени внутри отрезка сортируют по возрастанию номера и раздают кадрам по очереди, не кроша речь?",
        "justification": "Ступени идут по номеру, кадры назначают по порядку, текст речи по шотам не режут.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_asm", "s5")],
    },
    {
        "id": "h6-20",
        "family": "assemble",
        "kind": "prompt",
        "text": "Открытие отрезка — завязка, закрытие — расплата, и стык согласован с соседом?",
        "justification": "Первый кадр отрезка — завязка, последний — расплата, стык согласован с соседним отрезком.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_asm", "s5")],
    },
    {
        "id": "h6-21",
        "family": "assemble",
        "kind": "prompt",
        "text": "Все ступени схлопывать в одну и ту же фразу на каждом кадре?",
        "justification": "Ступени нельзя схлопнуть в одно и то же действие на каждый кадр отрезка.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_asm", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_asm", "s7"),
        ],
    },
    {
        "id": "h6-22",
        "family": "assemble",
        "kind": "howto",
        "text": "Если границы спорят, чья очередь раньше: карточка ячейки, цепь поступков или план показа?",
        "justification": "При споре границ сначала карточка ячейки, затем цепь поступков, и только потом план показа.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_asm", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_asm", "s3"),
        ],
    },
    {
        "id": "h6-23",
        "family": "camera",
        "kind": "prompt",
        "text": "Эмоция на лице важнее дверного замка, и неподвижный план нельзя ставить на всё подряд?",
        "justification": "Эмоция лица важнее замка, и неподвижность нельзя ставить на все планы.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s0")],
    },
    {
        "id": "h6-24",
        "family": "camera",
        "kind": "prompt",
        "text": "В пояснении к движению называют одну эмоцию, а не красоту приёма?",
        "justification": "Движение объясняют одной эмоцией, крутить красиво без эмоции нельзя.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h6-25",
        "family": "camera",
        "kind": "prompt",
        "text": "Сколько планов, столько ступеней, и комплект ракурсов лишние кадры не плодит?",
        "justification": "Число планов равно числу ступеней, комплект не размножает кадры.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s2")],
    },
    {
        "id": "h6-26",
        "family": "camera",
        "kind": "howto",
        "text": "Пику отдают самый сильный план, не трогая номер склейки и состав ступеней?",
        "justification": "Главному сдвигу отдают сильнейший план и не меняют номер склейки и набор ступеней.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s1")],
    },
    {
        "id": "h6-27",
        "family": "action",
        "kind": "prompt",
        "text": "Новое место, другой час или поворот смысла открывают новый кусок?",
        "justification": "Новое место, новое время или поворот смысла — новый кусок.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s3")],
    },
    {
        "id": "h6-28",
        "family": "action",
        "kind": "prompt",
        "text": "Цитата начала и цитата конца — от трёх до двенадцати слов и в тексте ровно один раз?",
        "justification": "Границы — дословные цитаты из трёх–двенадцати слов, каждая встречается один раз.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s3")],
    },
    {
        "id": "h6-29",
        "family": "action",
        "kind": "prompt",
        "text": "Кусок — цепь из трёх–семи видимых поступков тела или предмета, а не альбом застывших поз?",
        "justification": "Кусок — видимая цепь из трёх–семи фаз, не альбом статичных поз.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s4")],
    },
    {
        "id": "h6-30",
        "family": "shots",
        "kind": "howto",
        "text": "Этот шаг кладёт кадры в проект и сам связный текст не сочиняет?",
        "justification": "Шаг пишет кадры в проект и не генерирует прозу.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s0")],
    },
    {
        "id": "h6-31",
        "family": "shots",
        "kind": "prompt",
        "text": "Одна мысль в одном месте почти всегда даёт общий вид и покрытие, а не одиночную картинку?",
        "justification": "Одна сцена в одном месте почти всегда даёт мастер и покрытие, не один кадр.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s2")],
    },
    {
        "id": "h6-32",
        "family": "shots",
        "kind": "prompt",
        "text": "Нарезка речи на картинку держится около тридцати–пятидесяти знаков, если нет особого основания короче?",
        "justification": "Норма куска речи на кадр — от двадцати семи до пятидесяти четырёх знаков.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s3")],
    },
    {
        "id": "h6-33",
        "family": "script",
        "kind": "prompt",
        "text": "Сколько фактических сдвигов «было — стало», столько шагов: не всегда один и не всегда три?",
        "justification": "Число шагов равно фактическому числу изменений, это не всегда один и не всегда три.",
        "gold": [
            ("prompts", "n_excel_gpt_fw_script", "s2"),
            ("prompts", "n_excel_gpt_fw_script", "s3"),
        ],
    },
    {
        "id": "h6-34",
        "family": "skeleton",
        "kind": "howto",
        "text": "Один проход ставит карточку на каждую ячейку речи и не изготовляет сцены и не режет текст?",
        "justification": "Один вызов размечает каждую ячейку и не делает сцены и не режет текст.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_skeleton", "s0")],
    },
    {
        "id": "h6-35",
        "family": "skeleton",
        "kind": "prompt",
        "text": "Метка «начало» допустима только на самой первой карточке?",
        "justification": "Тип связи «начало» стоит только у первой карточки.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s7"),
        ],
    },
    {
        "id": "h6-36",
        "family": "skeleton",
        "kind": "prompt",
        "text": "Один сдвиг внутри ячейки — один будущий кадр, а текст ячейки здесь не распиливают?",
        "justification": "Один смысловой сдвиг — один слот будущего кадра, текст ячейки не режут.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s0"),
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s3"),
        ],
    },
    {
        "id": "h6-37",
        "family": "skeleton",
        "kind": "prompt",
        "text": "Вещь, которую надо узнать дальше, получает один сквозной номер на весь ролик?",
        "justification": "Узнаваемая дальше вещь получает один сквозной номер на весь ролик.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s4"),
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s7"),
        ],
    },
    {
        "id": "h6-38",
        "family": "skeleton",
        "kind": "prompt",
        "text": "В перечень повторяющихся мест разовый топоним без возврата не кладут?",
        "justification": "В перечень мест попадают только повторы; разовый топоним номера не получает.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s7"),
        ],
    },
    {
        "id": "h6-39",
        "family": "skeleton",
        "kind": "howto",
        "text": "Секунды карточки копируют из секунд ячейки, а не угадывают?",
        "justification": "Длительность карточки копируют из времени ячейки, не угадывают.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s6"),
            ("prompts", "n_excel_gpt_sd_cd_skeleton", "s7"),
        ],
    },
    {
        "id": "h6-40",
        "family": "rules",
        "kind": "rule",
        "text": "Исходники править и команды запускать нельзя, а новый узел и правимый html-отчёт — можно?",
        "justification": "Инструментов правки кода и команд нет, создавать узлы и правимые отчёты можно.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-8")],
    },
]


def _select(catalog: list[Any], gold: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    index = {(doc.corpus, doc.source_id, doc.part_key): doc for doc in catalog}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for triple in gold:
        if triple in seen:
            raise RuntimeError(f"повтор gold {triple}")
        seen.add(triple)
        doc = index.get(triple)
        if doc is None:
            raise RuntimeError(f"в каталоге нет {triple}")
        rows.append(_label(doc))
    rows.sort(key=lambda row: (row["corpus"], row["source_id"], row["part_key"]))
    return rows


def build_holdout6_document(docs: list[Any] | None = None) -> dict[str, Any]:
    catalog = list(docs) if docs is not None else _index_docs()
    if len(QUESTIONS) < MIN_QUERIES:
        raise RuntimeError(f"ожидалось не меньше {MIN_QUERIES}, получилось {len(QUESTIONS)}")
    expected = [f"h6-{number:02d}" for number in range(1, len(QUESTIONS) + 1)]
    if [question["id"] for question in QUESTIONS] != expected:
        raise RuntimeError("id holdout-6 должны идти h6-01 … без дыр")
    queries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for question in QUESTIONS:
        family = str(question["family"])
        kind = str(question["kind"])
        if kind == "rule" and family != "rules":
            raise RuntimeError(f"{question['id']}: rule вне rules")
        justification = str(question["justification"]).strip()
        if not justification or "\n" in justification:
            raise RuntimeError(f"{question['id']}: обоснование должно быть одной строкой")
        relevant = _select(catalog, list(question["gold"]))
        counts[family] = counts.get(family, 0) + 1
        queries.append(
            {
                "id": question["id"],
                "family": family,
                "kind": kind,
                "text": question["text"],
                "justification": justification,
                "source": ", ".join(
                    f"{item['corpus']}:{item['source_id']}:{item['part_key']}" for item in relevant
                ),
                "method": METHOD,
                "relevant": relevant,
            }
        )
    for family in WEIGHTED:
        if counts.get(family, 0) < 6:
            raise RuntimeError(f"{family}: меньше шести")
    light = [count for family, count in counts.items() if family not in WEIGHTED]
    if not light:
        raise RuntimeError("нет остальных семейств")
    ceiling = max(light)
    for family in WEIGHTED:
        if counts[family] <= ceiling:
            raise RuntimeError(f"{family} не тяжелее остальных")
    required = {"camera", "world", "characters", "action", "assemble", "shots", "script", "skeleton", "rules"}
    if set(counts) != required:
        raise RuntimeError(f"семейства {sorted(counts)} != {sorted(required)}")
    return {
        "role": "holdout-6",
        "families": counts,
        "label_rule": METHOD,
        "queries": queries,
    }


def load_holdout6_queries() -> list[dict[str, Any]]:
    from evals.orchestrator.frozen_datasets import assert_frozen

    assert_frozen(HOLDOUT6_PATH)
    document = json.loads(HOLDOUT6_PATH.read_text(encoding="utf-8"))
    queries = list(document["queries"])
    if len(queries) < MIN_QUERIES:
        raise RuntimeError(f"в holdout-6 {len(queries)} запросов, ожидалось не меньше {MIN_QUERIES}")
    return queries


def write_holdout6_file(*, rewrite: bool = False) -> dict[str, Any]:
    if HOLDOUT6_PATH.exists():
        from evals.orchestrator.frozen_datasets import FrozenDatasetError, assert_frozen

        assert_frozen(HOLDOUT6_PATH)
        if rewrite:
            raise FrozenDatasetError(f"{HOLDOUT6_NAME} read-only")
        return json.loads(HOLDOUT6_PATH.read_text(encoding="utf-8"))
    document = build_holdout6_document()
    HOLDOUT6_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
