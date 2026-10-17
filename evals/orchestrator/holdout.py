"""Отложенный набор контура B. В подбор конфига не входит.

Dev-плюс — все 50 запросов ``precision_live_queries.json`` (и нечётные, и бывший test).
Этот файл — 20 новых запросов. Сетка, ``run_tune``, ``run_abc`` и пара профилей
его не открывают. Подписи релевантности — те же, что у живого набора: у семейства
все реальные чанки каталога и промпта двух нод, у правила один ``part_key``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.orchestrator.rag.store import chunk_id_for

HOLDOUT_NAME = "precision_live_holdout.json"
HOLDOUT_PATH = Path(__file__).resolve().parent / "datasets" / HOLDOUT_NAME

# Совпадает с ``precision_live._FAMILIES``. Импорт оттуда зациклил бы модули.
FAMILY_NODES: dict[str, tuple[str, str]] = {
    "camera": ("n_excel_gpt_sd_camera", "n_excel_gpt_sd_cd_camera"),
    "characters": ("n_excel_gpt_sd_characters", "n_excel_gpt_sd_cd_characters"),
    "world": ("n_excel_gpt_sd_world", "n_excel_gpt_sd_cd_world"),
    "action": ("n_excel_gpt_sd_action", "n_excel_gpt_sd_cd_action"),
    "assemble": ("n_excel_gpt_sd_asm", "n_excel_gpt_sd_cd_asm"),
    "shots": ("n_excel_gpt_fw_shots", "n_excel_gpt_fw_frames"),
    "script": ("n_excel_gpt_fw_script", "n_excel_gpt_fw_action"),
}

# Тексты не перефразируют live-01..live-50: другие пункты тех же промптов и правило 9.
QUESTIONS: list[dict[str, str]] = [
    {
        "id": "hold-01",
        "family": "assemble",
        "text": (
            "В частичной сборке куска ops пишутся только на uuid кадров этого куска, "
            "а полный список лиц возвращается лишь в первом куске?"
        ),
    },
    {
        "id": "hold-02",
        "family": "assemble",
        "text": (
            "Машинный контракт требует ровно одну операцию на uuid кадра. "
            "Из чего тогда складывается длительность сцены?"
        ),
    },
    {
        "id": "hold-03",
        "family": "assemble",
        "text": (
            "Как разложить ступени набора SET по кадрам одной сцены, "
            "не превращая колонку таблицы в отдельную сцену?"
        ),
    },
    {
        "id": "hold-04",
        "family": "assemble",
        "text": (
            "Если в паспорте героя пустая внешность, финальная склейка обязана "
            "дорисовать кастинг-look или имеет право вернуть error?"
        ),
    },
    {
        "id": "hold-05",
        "family": "assemble",
        "text": (
            "В сборщике со скелетом фазы идут по phase_index, "
            "и ракурс нельзя поднять выше цепи фаз?"
        ),
    },
    {
        "id": "hold-06",
        "family": "world",
        "text": (
            "Смена зоны внутри одного помещения — новая локация "
            "или те же два–четыре участка одного места?"
        ),
    },
    {
        "id": "hold-07",
        "family": "world",
        "text": (
            "Какие четыре свойства света надо назвать у места, "
            "и когда полная смена схемы света заводит отдельную локацию?"
        ),
    },
    {
        "id": "hold-08",
        "family": "world",
        "text": "Сколько конкретных вещей должно жить в локации, чтобы описание не схлопнулось в одно слово?",
    },
    {
        "id": "hold-09",
        "family": "world",
        "text": (
            "Хроно-вариант мира берёт только идентификаторы из locations_seed "
            "и при пустом seed отдаёт пустой список?"
        ),
    },
    {
        "id": "hold-10",
        "family": "world",
        "text": "Разовый фон без место_id можно завести как постоянное место или его нельзя класть в паспорт?",
    },
    {
        "id": "hold-11",
        "family": "camera",
        "text": "Какие шесть условий Боуэна проверяют на стыке, чтобы склейка не стала красивым повтором?",
    },
    {
        "id": "hold-12",
        "family": "camera",
        "text": "Когда уместен dissolve, а когда реплики диалога нельзя растворять и нужен cut?",
    },
    {
        "id": "hold-13",
        "family": "characters",
        "text": "Толпа, жители и безымянный прохожий заводятся как герои или их запрещено заводить?",
    },
    {
        "id": "hold-14",
        "family": "characters",
        "text": (
            "Чем два героя обязаны отличаться на экране, "
            "и какие якоря вроде шрама или очков нельзя менять между сценами?"
        ),
    },
    {
        "id": "hold-15",
        "family": "action",
        "text": (
            "Фаза «подъезд встречает» или «камера вводит» годится в цепь, "
            "или каждая фаза обязана быть видимым движением тела и предмета?"
        ),
    },
    {
        "id": "hold-16",
        "family": "action",
        "text": "Диалог берёт continuity и cut, а сжатие дня — associative и dissolve?",
    },
    {
        "id": "hold-17",
        "family": "shots",
        "text": (
            "У кадров одного места кто получает parent_id null, "
            "а кто ссылается на первый кадр сетапа, а не на предыдущий шот?"
        ),
    },
    {
        "id": "hold-18",
        "family": "shots",
        "text": "Один кадр на ячейку допустим только для титра или имени без поступка, иначе обычно нужно два–четыре?",
    },
    {
        "id": "hold-19",
        "family": "script",
        "text": (
            "Если склеить скобки под сценами и убрать только переносы строк, "
            "получится вся ячейка, включая запятые и имя в начале?"
        ),
    },
    {
        "id": "hold-20",
        "family": "rules",
        "part_key": "rule-9",
        "text": "Перед запуском ноды план обязан прочитать код студии и рантайм-определение, или можно сразу жать run?",
    },
]


class HoldoutLeakError(ValueError):
    """Тюнинг открыл отложенный набор или чужой query_id."""


def is_holdout_id(query_id: str) -> bool:
    return str(query_id).startswith("hold-")


def dev_plus_ids() -> list[str]:
    """Все 50 старых запросов. Бывший чётный test тоже здесь."""
    return [f"live-{number:02d}" for number in range(1, 51)]


def assert_tuning_path(path: Path) -> Path:
    """Путь holdout для сетки и подбора закрыт."""
    if Path(path).name == HOLDOUT_NAME:
        raise HoldoutLeakError(f"тюнинг не читает {HOLDOUT_NAME}")
    return Path(path)


def read_for_tuning(path: Path) -> str:
    """Чтение датасета из инструментов подбора. Holdout — ошибка, не фильтр."""
    target = assert_tuning_path(path)
    return target.read_text(encoding="utf-8")


def require_dev_plus(query_ids: list[str]) -> list[str]:
    """Подбор принимает только live-01..live-50. Holdout и прочие id — ошибка."""
    allowed = set(dev_plus_ids())
    leaked = [str(query_id) for query_id in query_ids if str(query_id) not in allowed]
    if leaked:
        raise HoldoutLeakError("подбор видит не dev-плюс: " + ", ".join(leaked))
    return [str(query_id) for query_id in query_ids]


def _index_docs() -> list[Any]:
    from app.orchestrator.rag.loaders import load_nodes_and_prompts, load_rules
    from app.project_root import find_project_root

    root = Path(find_project_root())
    nodes, prompts, _skipped = load_nodes_and_prompts(repo_root=root, node_store=None, scan_git=True)
    rules = load_rules(root / "docs" / "fable" / "user_rules.md")
    return [*nodes, *prompts, *rules]


def _label(doc: Any) -> dict[str, Any]:
    chunk_id = chunk_id_for(doc.corpus, doc.source_id, doc.part_key)
    row: dict[str, Any] = {
        "corpus": doc.corpus,
        "source_id": doc.source_id,
        "part_key": doc.part_key,
        "chunk_id": chunk_id,
        "grade": 1,
        "from": [chunk_id],
    }
    return row


def relevant_chunks(question: dict[str, str], docs: list[Any]) -> list[dict[str, Any]]:
    """Семейство — все чанки двух нод. Правило — один part_key."""
    family = question["family"]
    if family == "rules":
        part_key = question["part_key"]
        matched = [doc for doc in docs if doc.corpus == "rules" and doc.part_key == part_key]
    else:
        keys = set(FAMILY_NODES[family])
        matched = [doc for doc in docs if doc.source_id in keys]
    if not matched:
        raise RuntimeError(f"в индексе нет чанков для {question['id']}")
    rows = [_label(doc) for doc in matched]
    rows.sort(key=lambda row: (row["corpus"], row["source_id"], row["part_key"]))
    return rows


def build_holdout_document(docs: list[Any] | None = None) -> dict[str, Any]:
    catalog = list(docs) if docs is not None else _index_docs()
    if len(QUESTIONS) != 20:
        raise RuntimeError(f"ожидалось 20 запросов, получилось {len(QUESTIONS)}")
    queries = []
    for question in QUESTIONS:
        queries.append(
            {
                "id": question["id"],
                "family": question["family"],
                "text": question["text"],
                "relevant": relevant_chunks(question, catalog),
            }
        )
    return {
        "role": "holdout",
        "dev_plus": "live-01..live-50, включая бывший чётный test",
        "label_rule": (
            "семейство: все реальные чанки каталога и промпта двух нод; "
            "правило: один part_key. Grade 1. Перефраз live-01..live-50 не используется."
        ),
        "queries": queries,
    }


def write_holdout_file(*, rewrite: bool = False) -> dict[str, Any]:
    """Существующий файл только сверяется. Перезапись запрещена."""
    if HOLDOUT_PATH.exists():
        from evals.orchestrator.frozen_datasets import FrozenDatasetError, assert_frozen

        assert_frozen(HOLDOUT_PATH)
        if rewrite:
            raise FrozenDatasetError(f"{HOLDOUT_NAME} read-only")
        return json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    document = build_holdout_document()
    HOLDOUT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document
