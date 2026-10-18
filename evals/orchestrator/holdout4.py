"""Holdout-4: реплики пользователя студии. Retrieve и метрики сюда не входят.

Семейство — по смыслу вопроса. Gold — только те чанки каталога, чей текст
отвечает на вопрос, с однострочным обоснованием. Все чанки двух нод сюда
не копируются.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import HOLDOUT4_NAME, _index_docs, _label

HOLDOUT4_PATH = Path(__file__).resolve().parent / "datasets" / HOLDOUT4_NAME
MIN_QUERIES = 32

METHOD = (
    "Реплика сформулирована как задача, симптом или цель, без имён нод, групп и номеров правил. "
    "Семейство назначено по смыслу. "
    "Gold — только part_key, в тексте которых есть ответ; обоснование записано в justification. "
    "Retrieve и метрики не вызывались."
)

# gold: (corpus, source_id, part_key). Не все чанки ноды.
QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "h4-01",
        "family": "camera",
        "kind": "prompt",
        "text": "Куда класть воздух, если герой смотрит влево?",
        "justification": "В композиции воздух ставят перед взглядом, не за спиной.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s2")],
    },
    {
        "id": "h4-02",
        "family": "camera",
        "kind": "prompt",
        "text": "Съёмка снизу — это угроза или просто красивый угол?",
        "justification": "Там же низкий угол означает силу или угрозу, а не украшение.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s2")],
    },
    {
        "id": "h4-03",
        "family": "camera",
        "kind": "prompt",
        "text": "Весь ролик со штатива. Какая доля неподвижных планов ещё терпима?",
        "justification": "Неподвижный план не дефолт: доля static не выше сорока пяти процентов.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s3")],
    },
    {
        "id": "h4-04",
        "family": "camera",
        "kind": "prompt",
        "text": "На развязке наезжать к лицу или оставлять общий вид?",
        "justification": "На развязке указан наезд, а в последовательности её снимают ближе.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_cd_camera", "s3"),
            ("prompts", "n_excel_gpt_sd_cd_camera", "s5"),
        ],
    },
    {
        "id": "h4-05",
        "family": "camera",
        "kind": "howto",
        "text": "Фаз уже семь. Сколько планов отдать и можно ли две фазы склеить в один?",
        "justification": "Число планов равно числу фаз, две фазы в один план не склеивают.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s2")],
    },
    {
        "id": "h4-06",
        "family": "camera",
        "kind": "prompt",
        "text": "Вход в новое место снимать шире, а жест и реакцию второго человека следующим кадром?",
        "justification": "Последовательность: вход шире, дальше жест и реакция второго человека.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s5")],
    },
    {
        "id": "h4-07",
        "family": "camera",
        "kind": "prompt",
        "text": "Главный удар сцены снимать тем же планом, что и вход?",
        "justification": "Главному удару сцены положен самый сильный план, не план входа.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_camera", "s1")],
    },
    {
        "id": "h4-08",
        "family": "camera",
        "kind": "howto",
        "text": "Длинная ячейка озвучки. Когда набор планов можно не выбирать?",
        "justification": "Набор пропускают, только если ни один не подходит, и серия пропусков — брак.",
        "gold": [("prompts", "n_excel_gpt_sd_camera", "s5")],
    },
    {
        "id": "h4-09",
        "family": "characters",
        "kind": "prompt",
        "text": "В тексте нет ни имён, ни поступков. Что отдавать вместо выдуманных лиц?",
        "justification": "Нет индивидуального героя — пустой список и ошибка, не выдуманные лица.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s2")],
    },
    {
        "id": "h4-10",
        "family": "characters",
        "kind": "prompt",
        "text": "В тексте молчат про лицо и костюм. Добирать облик под эпоху или оставлять карточку пустой?",
        "justification": "Пустая внешность запрещена: облик, волосы и конкретный костюм добирают под эпоху.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s3")],
    },
    {
        "id": "h4-11",
        "family": "characters",
        "kind": "prompt",
        "text": "Павел и механик — это два человека?",
        "justification": "Разные имена одного человека — одна карточка и один номер.",
        "gold": [("prompts", "n_excel_gpt_sd_characters", "s4")],
    },
    {
        "id": "h4-12",
        "family": "characters",
        "kind": "howto",
        "text": "Карточки людей уже есть, а шаг всё равно двигает границы сцен. Так надо?",
        "justification": "Шаг паспортов не задаёт границы сцен и не спорит, где текст начинается.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_characters", "s1")],
    },
    {
        "id": "h4-13",
        "family": "world",
        "kind": "prompt",
        "text": "В ответе всплыл номер места, которого не было во входном списке. Это брак?",
        "justification": "Новый номер места заводить нельзя: паспорта пишут только по уже выданному списку.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s1"),
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
        ],
    },
    {
        "id": "h4-14",
        "family": "world",
        "kind": "prompt",
        "text": "Мест на входе нет. Придумать квартиру или вернуть пусто?",
        "justification": "Пустой список мест на входе даёт пустой ответ, комнату не выдумывают.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
        ],
    },
    {
        "id": "h4-15",
        "family": "world",
        "kind": "prompt",
        "text": "Двор мелькнул за окном один раз. Заводить ему постоянное место?",
        "justification": "Разовый фон не превращают в постоянное место.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s1"),
        ],
    },
    {
        "id": "h4-16",
        "family": "world",
        "kind": "prompt",
        "text": "В паспорт места писать свет и время суток?",
        "justification": "Свет и время суток в паспорт места не пишут.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_world", "s2"),
            ("prompts", "n_excel_gpt_sd_cd_world", "s2"),
        ],
    },
    {
        "id": "h4-17",
        "family": "world",
        "kind": "howto",
        "text": "Шаг про места переписывает, где сцена начинается. Ему это можно?",
        "justification": "Шаг мест не задаёт границы сцен и не спорит о начале и конце текста.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_world", "s2")],
    },
    {
        "id": "h4-18",
        "family": "action",
        "kind": "prompt",
        "text": "Фраза «усталость сгущается», а человек в кадре не шевелится. Это шаг действия?",
        "justification": "Метафора и статика без жеста не шаг: нужно видимое действие тела или предмета.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s4")],
    },
    {
        "id": "h4-19",
        "family": "action",
        "kind": "prompt",
        "text": "Две линии идут одновременно. Чем их стыковать?",
        "justification": "Одновременные линии собирают схемой parallel и стыком cut или smash.",
        "gold": [("prompts", "n_excel_gpt_sd_action", "s5")],
    },
    {
        "id": "h4-20",
        "family": "action",
        "kind": "prompt",
        "text": "Почти вся история крутится вокруг одной двери, жесты разные. Так можно?",
        "justification": "Почти каждая сцена про одну и ту же дверь запрещена, даже если жест разный.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s3")],
    },
    {
        "id": "h4-21",
        "family": "action",
        "kind": "prompt",
        "text": "Герой кивает и смотрит. Этого хватает на отдельную фазу?",
        "justification": "Кивок и взгляд как единственное действие фазы запрещены.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s3")],
    },
    {
        "id": "h4-22",
        "family": "action",
        "kind": "howto",
        "text": "В ячейке сорок два символа озвучки. Как из длины текста получить секунды и число разных фаз?",
        "justification": "Четырнадцать символов озвучки — секунда, фазы примерно по три секунды, длиннее шести секунд — минимум три фазы.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s4")],
    },
    {
        "id": "h4-23",
        "family": "action",
        "kind": "prompt",
        "text": "В одной сцене два года и два места. Оставлять?",
        "justification": "Одна сцена — одно место; смена года или места открывает новую сцену.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_action", "s5")],
    },
    {
        "id": "h4-24",
        "family": "assemble",
        "kind": "prompt",
        "text": "Сцен четыре, планов девять, строк кадров двенадцать. Сборку из-за этого останавливать?",
        "justification": "Разное число сцен, планов и кадров — не повод отказывать в сборке.",
        "gold": [("prompts", "n_excel_gpt_sd_asm", "s3")],
    },
    {
        "id": "h4-25",
        "family": "assemble",
        "kind": "howto",
        "text": "У сцены с несколькими кадрами первый и последний что должны делать?",
        "justification": "Первый кадр такой сцены — завязка, последний — развязка.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_asm", "s5")],
    },
    {
        "id": "h4-26",
        "family": "assemble",
        "kind": "prompt",
        "text": "При сборке резать текст озвучки по отдельным планам?",
        "justification": "Текст озвучки по планам не режут: он остаётся у сцены, дочерние планы только картинка.",
        "gold": [
            ("prompts", "n_excel_gpt_sd_asm", "s5"),
            ("prompts", "n_excel_gpt_sd_cd_asm", "s5"),
        ],
    },
    {
        "id": "h4-27",
        "family": "script",
        "kind": "prompt",
        "text": "В одной ячейке имя и сразу поворот. Это один смысловой шаг или два, даже если вместе коротко?",
        "justification": "Имя и поворот в одной ячейке — два смысловых шага, даже если текст короткий.",
        "gold": [("prompts", "n_excel_gpt_fw_script", "s2")],
    },
    {
        "id": "h4-28",
        "family": "script",
        "kind": "prompt",
        "text": "Новый номер сцены на каждую придуманную комнату?",
        "justification": "Новый номер — новый смысловой шаг или реальная смена места, не комнату на каждую фразу.",
        "gold": [("prompts", "n_excel_gpt_fw_action", "s2")],
    },
    {
        "id": "h4-29",
        "family": "skeleton",
        "kind": "howto",
        "text": "В нумерации карточек дырка: четвёртый номер есть, третьего нет. Так можно?",
        "justification": "На каждый номер списка нужна своя карточка, пропуски запрещены.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_skeleton", "s1")],
    },
    {
        "id": "h4-30",
        "family": "skeleton",
        "kind": "prompt",
        "text": "Три видимых сдвига в одной ячейке записали одним слотом. Так и задумано?",
        "justification": "Слотов столько, сколько сдвигов: три сдвига — три будущих кадра, не один слот.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_skeleton", "s3")],
    },
    {
        "id": "h4-31",
        "family": "skeleton",
        "kind": "prompt",
        "text": "Место назвали один раз и к нему не возвращаются. Давать постоянный номер?",
        "justification": "Место без возврата не получает постоянный номер, это разовый фон.",
        "gold": [("prompts", "n_excel_gpt_sd_cd_skeleton", "s5")],
    },
    {
        "id": "h4-32",
        "family": "shots",
        "kind": "prompt",
        "text": "Кусок озвучки на кадр короче двух слов. Когда его можно не склеивать с соседом?",
        "justification": "Короткий кусок оставляют отдельно только для имени, титра или ударной детали.",
        "gold": [("prompts", "n_excel_gpt_fw_shots", "s3")],
    },
    {
        "id": "h4-33",
        "family": "shots",
        "kind": "prompt",
        "text": "Кадр гладко продолжает предыдущий, а его текст уже о другом. Что важнее?",
        "justification": "Кадр решает его собственный текст, продолжение предыдущей картинки вторично.",
        "gold": [("prompts", "n_excel_gpt_fw_frames", "s2")],
    },
    {
        "id": "h4-34",
        "family": "shots",
        "kind": "prompt",
        "text": "Ролик кадра можно смонтировать из двух планов?",
        "justification": "Ролик кадра — один непрерывный дубль без склейки и смены плана.",
        "gold": [("prompts", "n_excel_gpt_fw_frames", "s5")],
    },
    {
        "id": "h4-35",
        "family": "shots",
        "kind": "howto",
        "text": "Проверка переписала кадры, к которым не было претензий. Так можно?",
        "justification": "Кадры без нарушения не переписывают, правка только там, где проверка нашла брак.",
        "gold": [("prompts", "n_excel_gpt_fw_qc", "s4")],
    },
    {
        "id": "h4-36",
        "family": "shots",
        "kind": "prompt",
        "text": "Воспоминание рисовать как случившийся факт?",
        "justification": "Воспоминание рисуют как версию: зерно, дефокус, обесцвечивание, не как прямой факт.",
        "gold": [("prompts", "n_excel_gpt_fw_frames", "s4")],
    },
    {
        "id": "h4-37",
        "family": "rules",
        "kind": "rule",
        "text": "Хватит «потом». Сколько минут займёт этот кусок?",
        "justification": "Срок называют числом минут, секунд или часов на названный объём, без «потом».",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-10")],
    },
    {
        "id": "h4-38",
        "family": "rules",
        "kind": "rule",
        "text": "Итог хочу править прямо на странице, а не скачивать файл.",
        "justification": "Итог — правимый отчёт на странице, файлы отчёта статикой не раздают.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-11")],
    },
    {
        "id": "h4-39",
        "family": "rules",
        "kind": "rule",
        "text": "Не спрашивай «точно?» на каждом шаге.",
        "justification": "Лишние подтверждения не задают: вопрос только если шаг требует разрешения или план отвергнут.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-12")],
    },
    {
        "id": "h4-40",
        "family": "rules",
        "kind": "rule",
        "text": "Чтобы числа сошлись, половину сцен предлагают удалить. Это починка?",
        "justification": "Удаление сцен не починка: это блокирующее нарушение, нужен откат.",
        "gold": [("rules", "docs/fable/user_rules.md", "rule-3")],
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


def build_holdout4_document(docs: list[Any] | None = None) -> dict[str, Any]:
    catalog = list(docs) if docs is not None else _index_docs()
    if len(QUESTIONS) < MIN_QUERIES:
        raise RuntimeError(f"ожидалось не меньше {MIN_QUERIES} вопросов, получилось {len(QUESTIONS)}")
    expected_ids = [f"h4-{number:02d}" for number in range(1, len(QUESTIONS) + 1)]
    if [question["id"] for question in QUESTIONS] != expected_ids:
        raise RuntimeError("id holdout-4 должны идти h4-01, h4-02, … без дыр")
    queries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for question in QUESTIONS:
        family = str(question["family"])
        kind = str(question["kind"])
        if kind not in {"howto", "prompt", "rule"}:
            raise RuntimeError(f"{question['id']}: неизвестный kind {kind}")
        if kind == "rule" and family != "rules":
            raise RuntimeError(f"{question['id']}: rule вне семейства rules")
        justification = str(question["justification"]).strip()
        if not justification or "\n" in justification:
            raise RuntimeError(f"{question['id']}: обоснование должно быть одной строкой")
        relevant = _select(catalog, list(question["gold"]))
        if not relevant:
            raise RuntimeError(f"{question['id']}: пустой gold")
        counts[family] = counts.get(family, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
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
    if set(kinds) != {"howto", "prompt", "rule"}:
        raise RuntimeError(f"нужны kind howto, prompt и rule, есть {sorted(kinds)}")
    return {
        "role": "holdout-4",
        "kind": "реплики пользователя: как сделать, что говорит инструкция шага, правила",
        "families": counts,
        "kinds": kinds,
        "label_rule": METHOD,
        "queries": queries,
    }


def load_holdout4_queries() -> list[dict[str, Any]]:
    """Финальный замер позже. Тюнинг этот путь не читает."""
    from evals.orchestrator.frozen_datasets import assert_frozen

    assert_frozen(HOLDOUT4_PATH)
    document = json.loads(HOLDOUT4_PATH.read_text(encoding="utf-8"))
    queries = list(document["queries"])
    if len(queries) < MIN_QUERIES:
        raise RuntimeError(f"в holdout-4 {len(queries)} запросов, ожидалось не меньше {MIN_QUERIES}")
    return queries


def write_holdout4_file(*, rewrite: bool = False) -> dict[str, Any]:
    """Существующий файл только сверяется. Перезапись запрещена."""
    if HOLDOUT4_PATH.exists():
        from evals.orchestrator.frozen_datasets import FrozenDatasetError, assert_frozen

        assert_frozen(HOLDOUT4_PATH)
        if rewrite:
            raise FrozenDatasetError(f"{HOLDOUT4_NAME} read-only")
        return json.loads(HOLDOUT4_PATH.read_text(encoding="utf-8"))
    document = build_holdout4_document()
    HOLDOUT4_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
