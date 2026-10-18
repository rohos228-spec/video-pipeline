"""Добавка к dev-70: тридцать реплик пользователя. Dev-70 этим файлом не заменяется.

Семейство по смыслу. Вес на камере, мире и людях. Gold — только чанки,
в тексте которых есть ответ, плюс однострочное обоснование.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import HoldoutLeakError, _index_docs, _label, is_holdout_id, load_dev_expanded

DEV100_NAME = "precision_live_dev100.json"
DEV100_PATH = Path(__file__).resolve().parent / "datasets" / DEV100_NAME
EXTRA_COUNT = 30

METHOD = (
    "Реплика про задачу, симптом или цель, без имён нод, групп и номеров правил. "
    "Семейство по смыслу. Gold — только отвечающие part_key и однострочное обоснование. "
    "Retrieve не вызывался."
)

# gold: (corpus, source_id, part_key)
QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "d2-01",
        "family": "camera",
        "kind": "prompt",
        "text": "Ось действия внутри одной сцены можно перепрыгнуть?",
        "justification": "Ось действия внутри сцены не перескакивают, новый ракурс идёт по дуге той же стороны.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s2")],
    },
    {
        "id": "d2-02",
        "family": "camera",
        "kind": "prompt",
        "text": "Заваленный горизонт без причины — нормальный приём?",
        "justification": "Наклон кадра допустим редко и только с причиной, иначе это не приём.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s2")],
    },
    {
        "id": "d2-03",
        "family": "camera",
        "kind": "prompt",
        "text": "Длиннофокусная оптика сжимает задник или раздвигает комнату?",
        "justification": "Телеобъектив сжимает глубину, широкий угол её раздвигает.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s2")],
    },
    {
        "id": "d2-04",
        "family": "camera",
        "kind": "prompt",
        "text": "Давка и паника. Камеру оставлять мёртвой?",
        "justification": "Ссора, паника и толчея снимаются с рук, не неподвижным планом.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "d2-05",
        "family": "camera",
        "kind": "howto",
        "text": "Человека ведут под руки несколько секунд. Камера стоит или идёт с ним?",
        "justification": "Сопровождение ставят, когда тело ведут, тащат или оно убегает пару секунд.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "d2-06",
        "family": "camera",
        "kind": "prompt",
        "text": "Крупность писать по-русски, словами вроде дальнего плана?",
        "justification": "Крупность в этом шаге пишется только русскими формулировками, дальний план в их числе.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s4")],
    },
    {
        "id": "d2-07",
        "family": "camera",
        "kind": "prompt",
        "text": "Пик бита оставлять широким видом двора?",
        "justification": "Пик бита укрупняют до крупного плана, очень крупного или вставки, не оставляют общим.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s5")],
    },
    {
        "id": "d2-08",
        "family": "camera",
        "kind": "howto",
        "text": "Ролик и каждый новый мир открывать с ладони крупно?",
        "justification": "Первый бит ролика и каждый новый мир начинают с установочного широкого плана.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s5")],
    },
    {
        "id": "d2-09",
        "family": "camera",
        "kind": "prompt",
        "text": "Зачем камера поехала, можно не писать?",
        "justification": "Мотив движения обязателен: одна эмоция объясняет, зачем план поехал.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "d2-10",
        "family": "camera",
        "kind": "prompt",
        "text": "Композицию прошлого кадра скопировать пиксель в пиксель?",
        "justification": "Пространство сцены то же, но прошлый кадр один в один не копируют.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s5")],
    },
    {
        "id": "d2-11",
        "family": "characters",
        "kind": "prompt",
        "text": "Характер брать из поступков или дописывать биографию, которой в тексте нет?",
        "justification": "Характер — два–четыре слова из поступков в тексте, не отдельная биография.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s4")],
    },
    {
        "id": "d2-12",
        "family": "characters",
        "kind": "prompt",
        "text": "Номера людей идут по алфавиту имён?",
        "justification": "Номера идут по порядку появления в тексте, не по алфавиту.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s4")],
    },
    {
        "id": "d2-13",
        "family": "characters",
        "kind": "prompt",
        "text": "Двух разных людей оставить с одной причёской и одним лицом?",
        "justification": "Герои не должны путаться: копировать лицо и причёску нельзя.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "d2-14",
        "family": "characters",
        "kind": "prompt",
        "text": "В одежде достаточно слова «повседневная», без верха и обуви?",
        "justification": "Одежда — конкретный комплект, верх, низ и обувь, не общая отписка.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "d2-15",
        "family": "characters",
        "kind": "howto",
        "text": "Кадр пустой. Добавить кого-нибудь для заполнения?",
        "justification": "Персонажа «для красоты» или чтобы кто-то был в кадре заводить нельзя.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s2")],
    },
    {
        "id": "d2-16",
        "family": "characters",
        "kind": "prompt",
        "text": "Человека один раз назвали и у него нет ни шага, ни реплики. Карточка нужна?",
        "justification": "Карточку заводят, когда человека назвали, у него есть действие или реплика, либо текст прямо о нём.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s2")],
    },
    {
        "id": "d2-17",
        "family": "characters",
        "kind": "prompt",
        "text": "Площадь полна народа. Завести одну карточку «люди»?",
        "justification": "Толпа, группа и «люди» не персонаж, такую карточку не заводят.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s2")],
    },
    {
        "id": "d2-18",
        "family": "characters",
        "kind": "howto",
        "text": "В паспорте отметить, в каких кусках истории человек вообще виден?",
        "justification": "Для каждой сущности указывают, в каких фазах и сценах она появляется.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_characters", "s1")],
    },
    {
        "id": "d2-19",
        "family": "characters",
        "kind": "prompt",
        "text": "Безымянная группа без своего номера может быть главным объектом кадра?",
        "justification": "Массовка без номера не бывает главным объектом кадра, только фоном.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_characters", "s1")],
    },
    {
        "id": "d2-20",
        "family": "characters",
        "kind": "prompt",
        "text": "Пустой список лиц хуже, чем пачка безымянных?",
        "justification": "Пустой список лучше реестра статистов.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s2")],
    },
    {
        "id": "d2-21",
        "family": "world",
        "kind": "prompt",
        "text": "Сколько читаемых зон закладывать внутрь одного места?",
        "justification": "Внутри места две–четыре читаемые зоны, по ним ходят камера и действие.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-22",
        "family": "world",
        "kind": "prompt",
        "text": "Описание места одним словом «интерьер» сойдёт?",
        "justification": "Описание из одного слова, в том числе «интерьер», считается браком.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-23",
        "family": "world",
        "kind": "prompt",
        "text": "Стулья, чашки и прочие вещи комнаты перечислять в паспорте места?",
        "justification": "Предметы локации в паспорт места не пишут, это поле убрано.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-24",
        "family": "world",
        "kind": "prompt",
        "text": "Номер места заменить на более понятный?",
        "justification": "Номер места остаётся как во входном списке, его не переименовывают.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-25",
        "family": "world",
        "kind": "howto",
        "text": "Одно место из уже выданного списка можно молча пропустить?",
        "justification": "На каждый номер из входного списка пишут паспорт, пропускать нельзя.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-26",
        "family": "world",
        "kind": "prompt",
        "text": "Рядом с местом записывать, какая фраза закадра его включает?",
        "justification": "Триггеры закадра в паспорт места не пишут.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_world", "s2")],
    },
    {
        "id": "d2-27",
        "family": "world",
        "kind": "prompt",
        "text": "Глубину комнаты и материал стен можно не называть?",
        "justification": "Описание места включает стены, материал, цвет и глубину, не обходится без них.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-28",
        "family": "world",
        "kind": "prompt",
        "text": "Два угла одной комнаты — это два разных номера места?",
        "justification": "Одно пространство не дробят на два номера, смена зоны не новое место.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
    },
    {
        "id": "d2-29",
        "family": "world",
        "kind": "howto",
        "text": "Фон без постоянного номера. Ему нужен такой же полный паспорт, как остальным?",
        "justification": "Разовый фон без номера места не раздувают в локацию и не кладут в паспорта.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
        ],
    },
    {
        "id": "d2-30",
        "family": "world",
        "kind": "prompt",
        "text": "Подпись места уточнить, а номер оставить как пришёл?",
        "justification": "Короткое имя можно уточнить, номер остаётся тем же.",
        "gold": [("prompts", "n_excel_gpt_sd_world", "s2")],
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


def build_dev100_extra(docs: list[Any] | None = None) -> dict[str, Any]:
    catalog = list(docs) if docs is not None else _index_docs()
    if len(QUESTIONS) != EXTRA_COUNT:
        raise RuntimeError(f"ожидалось {EXTRA_COUNT} добавок, получилось {len(QUESTIONS)}")
    expected = [f"d2-{number:02d}" for number in range(1, EXTRA_COUNT + 1)]
    if [question["id"] for question in QUESTIONS] != expected:
        raise RuntimeError("id добавки должны идти d2-01 … без дыр")
    queries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for question in QUESTIONS:
        if is_holdout_id(str(question["id"])):
            raise HoldoutLeakError(question["id"])
        justification = str(question["justification"]).strip()
        if not justification or "\n" in justification:
            raise RuntimeError(f"{question['id']}: обоснование должно быть одной строкой")
        relevant = _select(catalog, list(question["gold"]))
        family = str(question["family"])
        counts[family] = counts.get(family, 0) + 1
        queries.append(
            {
                "id": question["id"],
                "family": family,
                "kind": question["kind"],
                "text": question["text"],
                "justification": justification,
                "source": ", ".join(
                    f"{item['corpus']}:{item['source_id']}:{item['part_key']}" for item in relevant
                ),
                "method": METHOD,
                "relevant": relevant,
            }
        )
    return {
        "role": "dev-100-extra",
        "base": "dev-70 = live-01..50 + hold-01..20",
        "families": counts,
        "label_rule": METHOD,
        "queries": queries,
    }


def load_dev100() -> list[dict[str, Any]]:
    """100 запросов: прежний dev-70 и добавка. Holdout сюда не входит."""
    base = load_dev_expanded()
    if len(base) != 70:
        raise RuntimeError(f"dev-70 разошёлся: {len(base)}")
    extra_doc = json.loads(DEV100_PATH.read_text(encoding="utf-8"))
    extra = list(extra_doc["queries"])
    leaked = [str(query["id"]) for query in [*base, *extra] if is_holdout_id(str(query["id"]))]
    if leaked:
        raise HoldoutLeakError("dev-100 видит holdout: " + ", ".join(leaked))
    if [str(query["id"]) for query in extra] != [f"d2-{number:02d}" for number in range(1, EXTRA_COUNT + 1)]:
        raise RuntimeError("добавка dev-100 не d2-01..d2-30")
    queries = [*base, *extra]
    if len(queries) != 100:
        raise RuntimeError(f"dev-100: ожидалось 100, получилось {len(queries)}")
    return queries


def write_dev100_file(*, rewrite: bool = False) -> dict[str, Any]:
    if DEV100_PATH.exists() and not rewrite:
        return json.loads(DEV100_PATH.read_text(encoding="utf-8"))
    document = build_dev100_extra()
    DEV100_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
