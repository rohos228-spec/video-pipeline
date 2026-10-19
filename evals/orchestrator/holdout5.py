"""Holdout-5: реплики пользователя. Retrieve и метрики сюда не входят.

Пишется до подбора на dev-100. Семейство по смыслу. Камера, мир и люди —
не меньше шести. Gold — только отвечающие чанки и однострочное обоснование.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import HOLDOUT5_NAME, _index_docs, _label

HOLDOUT5_PATH = Path(__file__).resolve().parent / "datasets" / HOLDOUT5_NAME
MIN_QUERIES = 40

METHOD = (
    "Реплика про задачу, симптом или цель, без имён нод, групп и номеров правил. "
    "Семейство по смыслу. Gold — только отвечающие part_key и однострочное обоснование. "
    "Retrieve и метрики не вызывались."
)

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "h5-01",
        "family": "camera",
        "kind": "prompt",
        "text": "Взгляд переходит с судьи на скамью. Камера стоит или ведёт панораму?",
        "justification": "Переход взгляда от одного человека к другому снимают панорамой.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h5-02",
        "family": "camera",
        "kind": "prompt",
        "text": "От рук с уликой поднять взгляд к лицу. Это наклон или наезд?",
        "justification": "От рук с уликой вверх к лицу указан наклон, не наезд.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h5-03",
        "family": "camera",
        "kind": "prompt",
        "text": "Стыд нарастает на лице. Камеру отвезти шире?",
        "justification": "Нарастающая эмоция и приближение к лицу — наезд, не отъезд.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h5-04",
        "family": "camera",
        "kind": "howto",
        "text": "Герой понял масштаб и выбрался из тесноты. Чем двигать камеру?",
        "justification": "Осознание масштаба и выход из тесноты снимают отъездом.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h5-05",
        "family": "camera",
        "kind": "prompt",
        "text": "Поля плана можно отдать списком объектов, не строками?",
        "justification": "Все поля ответа — обычные строки, объекты запрещены.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s6")],
    },
    {
        "id": "h5-06",
        "family": "camera",
        "kind": "howto",
        "text": "На фазу вышло чуть меньше двух секунд. Из-за этого рушить весь план?",
        "justification": "Полторы–две секунды на фазу не повод для ошибки, план всё равно строится один к одному.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s2")],
    },
    {
        "id": "h5-07",
        "family": "camera",
        "kind": "prompt",
        "text": "Кто в кадре: писать внутренний номер или имя человека?",
        "justification": "Если субъект указан, в кадре пишут имя или роль человека.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s5")],
    },
    {
        "id": "h5-08",
        "family": "world",
        "kind": "prompt",
        "text": "Паспорт места заодно выбирает героя и крупность?",
        "justification": "Шаг мест не решает персонажей, сцены, камеру, свет и стиль.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s1")],
    },
    {
        "id": "h5-09",
        "family": "world",
        "kind": "howto",
        "text": "Список помещений не пришёл. Комнаты брать из воздуха?",
        "justification": "Нет входного списка — пустой ответ, места не выдумывают.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
        ],
    },
    {
        "id": "h5-10",
        "family": "world",
        "kind": "prompt",
        "text": "Короткое имя места должно быть абзацем или хватит пары слов?",
        "justification": "Имя места короткое, абзац туда не кладут.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "h5-11",
        "family": "world",
        "kind": "prompt",
        "text": "Каждое выданное место в ответе встречается ровно один раз?",
        "justification": "Каждый номер из входного списка есть в ответе ровно один раз, без пропусков и двойников.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_world", "s4"),
        ],
    },
    {
        "id": "h5-12",
        "family": "world",
        "kind": "howto",
        "text": "Готовый список мест сразу пишется в проект или его только отдают сборке?",
        "justification": "Ответ уходит сборке, сам шаг мест в проект ничего не пишет.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_world", "s2")],
    },
    {
        "id": "h5-13",
        "family": "world",
        "kind": "howto",
        "text": "Шаг про комнаты может подвинуть, где текст сцены начинается?",
        "justification": "Шаг мест не задаёт границы сцен и не спорит о начале и конце текста.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_world", "s2")],
    },
    {
        "id": "h5-14",
        "family": "world",
        "kind": "prompt",
        "text": "Двойник уже существующего номера места — это нормально, если описание другое?",
        "justification": "Номера из входного списка не дублируют, на каждый номер один паспорт.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_world", "s1")],
    },
    {
        "id": "h5-15",
        "family": "characters",
        "kind": "prompt",
        "text": "Возраст в облике можно пропустить, раз есть родинка?",
        "justification": "В облике обязательна возрастная вилка, родинка её не заменяет.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "h5-16",
        "family": "characters",
        "kind": "prompt",
        "text": "Про волосы хватит слова «тёмные», без длины?",
        "justification": "Волосы описывают цветом, длиной и укладкой, одного цвета мало.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "h5-17",
        "family": "characters",
        "kind": "prompt",
        "text": "Человек сменил куртку. Это уже второй номер в реестре?",
        "justification": "Один человек — один номер на весь ролик, смена одежды номер не удваивает.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s4")],
    },
    {
        "id": "h5-18",
        "family": "characters",
        "kind": "prompt",
        "text": "Телосложение в карточке по желанию?",
        "justification": "Телосложение входит в обязательный минимум облика.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "h5-19",
        "family": "characters",
        "kind": "howto",
        "text": "Перед списком людей можно абзацем объяснить, кого не включил?",
        "justification": "Ответ по людям — только JSON, без поясняющей прозы.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s5")],
    },
    {
        "id": "h5-20",
        "family": "characters",
        "kind": "prompt",
        "text": "Форму из текста выбросить и нарисовать другой костюм с нуля?",
        "justification": "Черту, которую дал текст, включая форму, берут как якорь, а не выбрасывают.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "h5-21",
        "family": "characters",
        "kind": "prompt",
        "text": "Паспорта уже собраны. Имеет смысл там же перекроить границы текста?",
        "justification": "Шаг паспортов не задаёт границы сцен и не спорит о начале и конце.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_characters", "s1")],
    },
    {
        "id": "h5-22",
        "family": "action",
        "kind": "prompt",
        "text": "Шаг «мы видим подъезд» — годное звено цепи?",
        "justification": "Камера-нарратив вроде «мы видим» запрещён, нужно видимое действие тела или предмета.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s4")],
    },
    {
        "id": "h5-23",
        "family": "action",
        "kind": "prompt",
        "text": "Скачок через день и смена города. Это непрерывное действие или ассоциация?",
        "justification": "Сжатие времени и смена места — ассоциативная схема, не непрерывность.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s5")],
    },
    {
        "id": "h5-24",
        "family": "action",
        "kind": "prompt",
        "text": "В одной фазе сразу два глагола: открыл и подписал. Так оставлять?",
        "justification": "Одна фаза — один глагол-удар, не два действия сразу.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s5")],
    },
    {
        "id": "h5-25",
        "family": "action",
        "kind": "howto",
        "text": "Весь ролик в одной комнате, хотя по тексту есть больница и улица. Нормально?",
        "justification": "Места должны различаться от сцены к сцене, одна комната на весь ролик не годится.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s2")],
    },
    {
        "id": "h5-26",
        "family": "action",
        "kind": "prompt",
        "text": "Единственное событие сцены — прогулка на лифте. Это действие?",
        "justification": "Лифт-прогулка как единственное действие запрещена.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s3")],
    },
    {
        "id": "h5-27",
        "family": "assemble",
        "kind": "prompt",
        "text": "Черновик границ можно разрезать, чтобы попасть во время планов?",
        "justification": "Черновик границ действия можно и нужно резать, чтобы сцены совпали с планами и их временем.",
        "gold": [("prompts", "n_excel_gpt_sd_asm", "s3")],
    },
    {
        "id": "h5-28",
        "family": "assemble",
        "kind": "howto",
        "text": "Соседний кусок ещё не собран. Его кадры закрывать в этом ответе?",
        "justification": "Частичная сборка не покрывает весь ролик, соседние куски соберут остальное.",
        "gold": [("prompts", "n_excel_gpt_sd_asm", "s1")],
    },
    {
        "id": "h5-29",
        "family": "assemble",
        "kind": "prompt",
        "text": "Дочерний план получает собственный кусок озвучки?",
        "justification": "Текст озвучки остаётся у родителя, дочерние планы — картинка без нарезки текста.",
        "gold": [("prompts", "n_excel_gpt_sd_asm", "s5")],
    },
    {
        "id": "h5-30",
        "family": "assemble",
        "kind": "prompt",
        "text": "Последний кадр длинной сцены можно оставить таким же широким входом?",
        "justification": "Первый кадр сцены — завязка, последний — развязка, не повтор входа.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_asm", "s5")],
    },
    {
        "id": "h5-31",
        "family": "shots",
        "kind": "prompt",
        "text": "Запятую в перечислении выносить отдельным кадром?",
        "justification": "Запятая в списке не основание для отдельного кадра, кусок клеят к соседу.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s3")],
    },
    {
        "id": "h5-32",
        "family": "shots",
        "kind": "prompt",
        "text": "Первый кадр нового места. У него есть родитель?",
        "justification": "Первый кадр нового места — мастер без родителя, следующие того же места на него ссылаются.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s4")],
    },
    {
        "id": "h5-33",
        "family": "shots",
        "kind": "prompt",
        "text": "В описании клипа можно написать, что затем кадр меняется?",
        "justification": "Клип — один непрерывный дубль, фраза про смену кадра запрещена.",
        "gold": [("prompts", "n_excel_gpt_fw_frames", "s5")],
    },
    {
        "id": "h5-34",
        "family": "shots",
        "kind": "howto",
        "text": "Кадр без дословной детали из своей озвучки. Это уже брак?",
        "justification": "В промпт обязана попасть дословная деталь из текста этого кадра, иначе это открытка.",
        "gold": [("prompts", "n_excel_gpt_fw_frames", "s2")],
    },
    {
        "id": "h5-35",
        "family": "script",
        "kind": "prompt",
        "text": "Фраза «шёл такой-то год» без сдвига. Ей отдельный смысловой шаг?",
        "justification": "Служебная экспозиция без сдвига клеится к соседнему шагу и отдельно не живёт.",
        "gold": [("prompts", "n_excel_gpt_fw_script", "s2")],
    },
    {
        "id": "h5-36",
        "family": "script",
        "kind": "prompt",
        "text": "Сцену можно описать абзацем прозы вместо двух коротких строк?",
        "justification": "На сцену две строки: кто что делает и дословный кусок, не абзац-рассказ.",
        "gold": [("prompts", "n_excel_gpt_fw_action", "s2")],
    },
    {
        "id": "h5-37",
        "family": "skeleton",
        "kind": "howto",
        "text": "Две соседние ячейки озвучки склеить в одну карточку разметки?",
        "justification": "На каждый номер списка своя карточка, склейки и пропуски запрещены.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_skeleton", "s1")],
    },
    {
        "id": "h5-38",
        "family": "rules",
        "kind": "rule",
        "text": "Объём работы назван, а срок без единой цифры. Так отвечать?",
        "justification": "Срок дают числом секунд, минут или часов, формулировка без числа не используется.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-10")],
    },
    {
        "id": "h5-39",
        "family": "rules",
        "kind": "rule",
        "text": "Отчёт уже создан. При следующей правке его снова чистить?",
        "justification": "Отчёт чистят и при создании, и при каждом обновлении.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-11")],
    },
    {
        "id": "h5-40",
        "family": "rules",
        "kind": "rule",
        "text": "План отвергли. Теперь можно задать один вопрос?",
        "justification": "Вопрос уместен, если план отвергнут или шаг требует разрешения.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-12")],
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


def build_holdout5_document(docs: list[Any] | None = None) -> dict[str, Any]:
    catalog = list(docs) if docs is not None else _index_docs()
    if len(QUESTIONS) < MIN_QUERIES:
        raise RuntimeError(f"ожидалось не меньше {MIN_QUERIES}, получилось {len(QUESTIONS)}")
    expected = [f"h5-{number:02d}" for number in range(1, len(QUESTIONS) + 1)]
    if [question["id"] for question in QUESTIONS] != expected:
        raise RuntimeError("id holdout-5 должны идти h5-01 … без дыр")
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
    for family in ("camera", "world", "characters"):
        if counts.get(family, 0) < 6:
            raise RuntimeError(f"{family}: меньше шести")
    return {
        "role": "holdout-5",
        "families": counts,
        "label_rule": METHOD,
        "queries": queries,
    }


def load_holdout5_queries() -> list[dict[str, Any]]:
    from evals.orchestrator.frozen_datasets import assert_frozen

    assert_frozen(HOLDOUT5_PATH)
    document = json.loads(HOLDOUT5_PATH.read_text(encoding="utf-8"))
    queries = list(document["queries"])
    if len(queries) < MIN_QUERIES:
        raise RuntimeError(f"в holdout-5 {len(queries)} запросов, ожидалось не меньше {MIN_QUERIES}")
    return queries


def write_holdout5_file(*, rewrite: bool = False) -> dict[str, Any]:
    if HOLDOUT5_PATH.exists():
        from evals.orchestrator.frozen_datasets import FrozenDatasetError, assert_frozen

        assert_frozen(HOLDOUT5_PATH)
        if rewrite:
            raise FrozenDatasetError(f"{HOLDOUT5_NAME} read-only")
        return json.loads(HOLDOUT5_PATH.read_text(encoding="utf-8"))
    document = build_holdout5_document()
    HOLDOUT5_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
