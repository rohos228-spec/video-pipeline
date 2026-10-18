"""Контур B: hit@5 и nDCG@8, мультиязычный реранкер и перебор гибрида.

Не для CI. Запуск качает веса в стандартный кэш Hugging Face
(``~/.cache/huggingface`` или ``HF_HOME`` / ``FASTEMBED_CACHE_PATH``), не в ``data/``.
Сеть — только Hugging Face за весами. Провайдеры вроде 52pay не вызываются.

Приёмка: hit@5 ≥ 0.9 и nDCG@8 ≥ 0.65 (временный порог, пересмотр на R5).
precision@8, recall@8 и hit@8 — для сведения.

Корпуса: ``docs/fable/user_rules.md``, ноды из ``NODE_GROUPS`` и
``templates/node_groups`` (на этом main каталога нет — файл перекрыл бы код),
промпты, которые эти ноды читают из ``templates/excel_gpt_agents`` и
``prompts/scene_design``.

Запросы — перефразы без дословных якорей. Релевантность размечена по чанкам
заранее: семейство агента (каталог + три части промпта у двух вариантов)
или одно правило.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.orchestrator.rag.embedder import build_embedder
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.rerank import Reranker, build_reranker

HIT_AT_5_MIN = 0.9
# временный, пересмотр на R5
NDCG_AT_8_MIN = 0.65
P95_MAX_S = 1.2

ROOT = Path(__file__).resolve().parents[2]
QUERY_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_queries.json"
REPORT_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_report.md"
PASSES_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_passes.json"
PASS_COUNT = 3

_FAMILIES: dict[str, tuple[str, str]] = {
    "camera": ("n_excel_gpt_sd_camera", "n_excel_gpt_sd_cd_camera"),
    "characters": ("n_excel_gpt_sd_characters", "n_excel_gpt_sd_cd_characters"),
    "world": ("n_excel_gpt_sd_world", "n_excel_gpt_sd_cd_world"),
    "action": ("n_excel_gpt_sd_action", "n_excel_gpt_sd_cd_action"),
    "assemble": ("n_excel_gpt_sd_asm", "n_excel_gpt_sd_cd_asm"),
    "shots": ("n_excel_gpt_fw_shots", "n_excel_gpt_fw_frames"),
    "script": ("n_excel_gpt_fw_script", "n_excel_gpt_fw_action"),
}

_FAMILY_QUESTIONS: dict[str, list[str]] = {
    "camera": [
        "Какими крупностями снимать и когда камера едет, а не стоит на месте?",
        "Нужны оба описания постановки кадра: спокойный агент и вариант, где на фазу действия свой шот.",
        "Где расписаны общий, средний и крупный план плюс панорама?",
        "Хочу инструкцию оператору: композиция, наезд, отъезд, не телепорт.",
        "Как выбирать план кадра, если сцена только открывается и если уже идёт действие?",
        "Покажи материалы по съёмке: и обычный срез камеры, и динамический по фазам.",
    ],
    "characters": [
        "Кто ведёт реестр героев и как описывать внешность с одеждой?",
        "Нужны карточки персонажей: и обычный кастинг, и вариант под смену состояний.",
        "Где сказано, как завести героя, не путая его с местом и камерой?",
        "Как собрать полный облик персонажа по закадру, чтобы потом не поехал?",
        "Покажи оба промпта про людей в кадре: базовый и с динамикой характера.",
        "Кто отвечает за то, какие герои вообще есть в ролике и как они выглядят?",
    ],
    "world": [
        "Кто придумывает места и интерьеры, не добавляя локации от себя?",
        "Где правила про локации: брать только то, что уже наметили, и расписать обстановку?",
        "Нужны оба текста про мир: обычный художник места и вариант с несколькими мирами.",
        "Как описывать пространство сцены, если закадр сам не назвал комнату?",
        "Покажи инструкции по местам действия, не по людям и не по камере.",
        "Где агент, который оформляет локации и проверяет, что ни одна не потерялась?",
    ],
    "action": [
        "Кто режет закадр на сцены и цепь поступков, не трогая камеру?",
        "Как понять, где сцена начинается и кончается по озвучке, а не по ячейке таблицы?",
        "Нужны оба промпта про действие: базовый и тот, где у сцены есть фазы.",
        "Где сказано, что одна ячейка текста не равна одной сцене и не равна одному кадру?",
        "Как строить шаги действия внутри сцены, если кадров потом будет несколько?",
        "Покажи материалы режиссёра постановки: границы сцен и что в них происходит.",
    ],
    "assemble": [
        "Кто собирает срезы агентов в итоговые сцены, когда они спорят?",
        "Как разрешать конфликт: закадр важнее, чем догадка камеры или мира?",
        "Нужны оба сборщика: обычный и тот, что склеивает скелет с фазами.",
        "Где финальная сборка JSON сцен из нескольких промежуточных ответов?",
        "Покажи промпты, которые ничего не придумывают заново, а только стыкуют чужие срезы.",
        "Кто выпускает цельный пакет сцен после того, как герои, места и действие уже есть?",
    ],
    "shots": [
        "Как из готового действия нарезать кадры и не потерять текст озвучки?",
        "Где правила покрытия сцены несколькими кадрами, а не одним слайдом?",
        "Нужен и агент нарезки кадров, и агент, который пишет промпты картинок по ним.",
        "Как делить закадр между кадрами короткой фразой, чтобы сумма совпала с ячейкой?",
        "Покажи, кто ставит план, ракурс и видимое действие в списке кадров.",
        "Где непрерывность картинки внутри одной сцены и запрет тащить позу из соседней?",
    ],
    "script": [
        "Кто дробит закадр на смысловые биты, и кто потом собирает из них главное действие?",
        "Как решить, сколько изменений в ячейке, а не ставить один бит на всё?",
        "Нужны оба шага сценария: разметка битов и цепь действий по ним.",
        "Где сказано не склеивать ячейки озвучки и не терять куски текста?",
        "Покажи промпт про биты и промпт, который превращает их в поступки сцены.",
        "Как из текста за кадром получить сцены с действием, не придумывая новую озвучку?",
    ],
}

_RULES: list[tuple[str, str]] = [
    (
        "rule-1",
        "На сколько разносить одновременные пачки запросов к модели, чтобы они не стартовали в одну секунду?",
    ),
    ("rule-2", "Можно ли для внутренних ходов и для нод опустить глубину рассуждения, или пока рано?"),
    ("rule-3", "Если кадров получилось слишком много, можно просто стереть лишние сцены?"),
    (
        "rule-4",
        "Какая модель стоит на нодах пайплайна и через какого провайдера, пока не подтвердили замену?",
    ),
    ("rule-5", "Соседние почти одинаковые кадры надо обязательно резать на два или их можно объединить?"),
    ("rule-6", "Что должно стоять в раскадровке раньше реплики: деталь или широкий вид места?"),
    ("rule-7", "Сколько символов озвучки положено на одну сцену и можно ли при нарезке потерять хвост?"),
    ("rule-8", "Оркестратор может сам поправить код и запустить тесты или ему можно только ноды и отчёты?"),
]


def _family_chunks(keys: tuple[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in keys:
        rows.append({"corpus": "nodes", "source_id": key, "part_key": "catalog"})
        for part in ("s0", "s1", "s2"):
            rows.append({"corpus": "prompts", "source_id": key, "part_key": part})
    return rows


def build_queries() -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    number = 1
    for family, questions in _FAMILY_QUESTIONS.items():
        relevant = _family_chunks(_FAMILIES[family])
        for text in questions:
            queries.append(
                {
                    "id": f"live-{number:02d}",
                    "family": family,
                    "text": text,
                    "relevant": relevant,
                }
            )
            number += 1
    for part_key, text in _RULES:
        queries.append(
            {
                "id": f"live-{number:02d}",
                "family": "rules",
                "text": text,
                "relevant": [{"corpus": "rules", "part_key": part_key}],
            }
        )
        number += 1
    if len(queries) != 50:
        raise RuntimeError(f"ожидалось 50 запросов, получилось {len(queries)}")
    return queries


def _is_relevant(chunk: Any, query: dict[str, Any]) -> bool:
    """Прошлый скоринг семейств: любой чанк source_id, не part_key.

    Так посчитан nDCG 0.544 после max_parts=3. Буквальные метки файла — отдельно,
    ``_is_relevant_literal``.
    """
    if query.get("family") == "rules":
        return chunk.corpus == "rules" and any(
            item["part_key"] == chunk.metadata.get("part_key") for item in query["relevant"]
        )
    family_ids = {item["source_id"] for item in query["relevant"] if item.get("source_id")}
    return chunk.source_id in family_ids


def _is_relevant_literal(chunk: Any, query: dict[str, Any]) -> bool:
    """Старая разметка как в файле: corpus + source_id + part_key. Правило — по part_key."""
    part_key = chunk.metadata.get("part_key")
    for item in query.get("relevant") or []:
        if query.get("family") == "rules" or item.get("corpus") == "rules":
            if chunk.corpus == "rules" and item.get("part_key") == part_key:
                return True
            continue
        if (
            item.get("corpus") == chunk.corpus
            and item.get("source_id") == chunk.source_id
            and item.get("part_key") == part_key
        ):
            return True
    return False


def _is_relevant_transferred(chunk: Any, query: dict[str, Any]) -> bool:
    """Новая разметка: chunk_id с grade > 0."""
    return any(
        item.get("chunk_id") == chunk.chunk_id and int(item.get("grade") or 0) > 0
        for item in query.get("relevant") or []
    )


def _relevant_source_ids(query: dict[str, Any], *, literal: bool) -> set[str]:
    if literal and query.get("family") == "rules":
        return set()
    return {str(item["source_id"]) for item in query.get("relevant") or [] if item.get("source_id")}


def _doc_ndcg(found: list[Any], relevant_ids: set[str], indexed_ids: set[str]) -> float:
    """nDCG@8 по документам: выдача схлопнута по source_id, остаётся лучший чанк."""
    seen: list[str] = []
    for chunk in found:
        if chunk.source_id not in seen:
            seen.append(chunk.source_id)
    flags = [source_id in relevant_ids for source_id in seen]
    return _ndcg_at_8(flags, len(relevant_ids & indexed_ids))


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    weight = rank - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _ndcg_at_8(flags: list[bool], n_relevant: int) -> float:
    """Бинарный nDCG@8: DCG = Σ rel_i / log2(i+1), IDCG — идеальный префикс."""
    dcg = 0.0
    for index, rel in enumerate(flags[:8], start=1):
        if rel:
            dcg += 1.0 / math.log2(index + 1)
    ideal = min(8, n_relevant)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def measure(
    queries: list[dict[str, Any]],
    engine: RagEngine,
    *,
    rerank: bool,
    rrf_k: int = 60,
    rrf_weights: tuple[float, float] | None = None,
    bm25_scope: str = "full",
    rerank_pool: int = 20,
    transferred: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Метрики, задержка и top-8 по запросам. Холодная загрузка весов не входит."""
    indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    transferred_by_id = {query["id"]: query for query in (transferred or [])}
    if transferred is not None:
        missing = [query["id"] for query in queries if query["id"] not in transferred_by_id]
        if missing:
            raise RuntimeError(f"в перенесённой разметке нет запросов: {missing}")
    labeled_by_query = [sum(1 for chunk in indexed if _is_relevant(chunk, query)) for query in queries]
    literal_counts = [sum(1 for chunk in indexed if _is_relevant_literal(chunk, query)) for query in queries]
    new_counts = [
        sum(1 for chunk in indexed if _is_relevant_transferred(chunk, transferred_by_id[query["id"]]))
        if transferred is not None
        else 0
        for query in queries
    ]
    indexed_source_ids = {chunk.source_id for chunk in indexed}
    rule_source_ids = {chunk.source_id for chunk in indexed if chunk.corpus == "rules"}
    kwargs = {
        "rerank": rerank,
        "rrf_k": rrf_k,
        "bm25_scope": bm25_scope,
        "rerank_pool": rerank_pool,
    }
    if rrf_weights is not None:
        kwargs["rrf_weights"] = rrf_weights
    engine.retrieve(queries[0]["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
    source_ndcgs: list[float] = []
    literal_ndcgs: list[float] = []
    new_ndcgs: list[float] = []
    doc_source_ndcgs: list[float] = []
    doc_literal_ndcgs: list[float] = []
    doc_new_ndcgs: list[float] = []
    precisions: list[float] = []
    hit5_source = 0
    hit5_literal = 0
    hit5_new = 0
    latencies: list[float] = []
    details: list[dict[str, Any]] = []
    for query, source_n, literal_n, new_n in zip(
        queries, labeled_by_query, literal_counts, new_counts, strict=True
    ):
        started = time.perf_counter()
        found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
        latencies.append(time.perf_counter() - started)
        source_flags = [_is_relevant(chunk, query) for chunk in found]
        literal_flags = [_is_relevant_literal(chunk, query) for chunk in found]
        new_query = transferred_by_id.get(query["id"])
        new_flags = (
            [_is_relevant_transferred(chunk, new_query) for chunk in found] if new_query is not None else []
        )
        source_ndcgs.append(_ndcg_at_8(source_flags, source_n))
        literal_ndcgs.append(_ndcg_at_8(literal_flags, literal_n))
        if new_query is not None:
            new_ndcgs.append(_ndcg_at_8(new_flags, new_n))
        if any(source_flags[:5]):
            hit5_source += 1
        if any(literal_flags[:5]):
            hit5_literal += 1
        if new_flags and any(new_flags[:5]):
            hit5_new += 1
        literal_sources = _relevant_source_ids(query, literal=True)
        if query.get("family") == "rules":
            literal_sources = set(rule_source_ids)
            source_sources = set(rule_source_ids)
        else:
            source_sources = _relevant_source_ids(query, literal=False)
        doc_source_ndcgs.append(_doc_ndcg(found, source_sources, indexed_source_ids))
        doc_literal_ndcgs.append(_doc_ndcg(found, literal_sources, indexed_source_ids))
        if new_query is not None:
            new_sources = _relevant_source_ids(new_query, literal=True)
            if new_query.get("family") == "rules":
                new_sources = set(rule_source_ids)
            doc_new_ndcgs.append(_doc_ndcg(found, new_sources, indexed_source_ids))
        primary_flags = new_flags if new_query is not None else source_flags
        matched = sum(1 for flag in primary_flags if flag)
        precisions.append(matched / 8)
        details.append(
            {
                "id": query["id"],
                "hit5": any(primary_flags[:5]),
                "top": [
                    {
                        "corpus": chunk.corpus,
                        "source_id": chunk.source_id,
                        "part_key": chunk.metadata.get("part_key"),
                        "title": chunk.title,
                        "node_key": chunk.metadata.get("node_key"),
                        "group_role": chunk.metadata.get("group_role"),
                    }
                    for chunk in found
                ],
            }
        )
    count = len(queries)
    use_new = transferred is not None
    metrics = {
        "hit_at_5": (hit5_new if use_new else hit5_source) / count,
        "ndcg_at_8": sum(new_ndcgs if use_new else source_ndcgs) / count,
        "ndcg_at_8_source": sum(source_ndcgs) / count,
        "hit_at_5_source": hit5_source / count,
        "ndcg_at_8_old": sum(literal_ndcgs) / count,
        "hit_at_5_old": hit5_literal / count,
        "ndcg_doc_old": sum(doc_literal_ndcgs) / count,
        "ndcg_doc_source": sum(doc_source_ndcgs) / count,
        "precision_at_8": sum(precisions) / count,
        "p50_s": _percentile(latencies, 50),
        "p95_s": _percentile(latencies, 95),
    }
    if use_new:
        metrics["ndcg_at_8_new"] = sum(new_ndcgs) / count
        metrics["hit_at_5_new"] = hit5_new / count
        metrics["ndcg_doc_new"] = sum(doc_new_ndcgs) / count
    return metrics, details


def _passes(metrics: dict[str, float]) -> bool:
    return metrics["hit_at_5"] >= HIT_AT_5_MIN and metrics["ndcg_at_8"] >= NDCG_AT_8_MIN


def _rank_key(row: tuple[str, str, dict[str, float]]) -> tuple[float, float, float]:
    return (row[2]["ndcg_at_8"], row[2]["hit_at_5"], -row[2]["p95_s"])


def _is_rerank_key(key: str) -> bool:
    return "+jina+" in key or "+bge-m3+" in key


class SelectionLeakError(ValueError):
    """В подбор попал id вне dev-70 (live-01..50, hold-01..20) или holdout-3."""


def require_dev_only(query_ids: list[str]) -> list[str]:
    """Подбор принимает 70 id dev. Holdout-3 и прочие id — ошибка, не фильтр.

    Бывший чётный test и бывший holdout-2 входят в dev. Отложенный набор ``h3-*`` не входит.
    """
    from evals.orchestrator.holdout import HoldoutLeakError, require_dev_plus

    try:
        return require_dev_plus(query_ids)
    except HoldoutLeakError as exc:
        raise SelectionLeakError(str(exc)) from exc


def dev_only_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Срез для подбора: id из dev-70. Holdout-3 в списке — ошибка, не отбрасывание."""
    from evals.orchestrator.holdout import HoldoutLeakError, dev_plus_ids, is_holdout_id

    leaked = [str(query["id"]) for query in queries if is_holdout_id(str(query["id"]))]
    if leaked:
        raise HoldoutLeakError("подбор видит holdout: " + ", ".join(leaked))
    allowed = set(dev_plus_ids())
    dev = [query for query in queries if str(query["id"]) in allowed]
    require_dev_only([str(query["id"]) for query in dev])
    return dev


def select_on_dev(
    runs: list[tuple[str, str, dict[str, float]]],
    query_ids: list[str],
) -> tuple[str, str]:
    """``choose_default`` только после проверки, что id — dev."""
    require_dev_only(query_ids)
    return choose_default(runs)


def choose_default(runs: list[tuple[str, str, dict[str, float]]]) -> tuple[str, str]:
    """По новой разметке: hit@5 ≥ 0.9, p95 ≤ 1.2 с, максимальный чанковый nDCG@8.

    Порог nDCG@8 0.65 здесь не отбор, а приёмка: его не снижаем.
    Если никто не уложился в hit@5 и p95, ключ пустой и текст говорит «нет дефолта».
    """
    eligible = [
        row
        for row in runs
        if row[2]["p95_s"] <= P95_MAX_S and row[2]["hit_at_5"] >= HIT_AT_5_MIN
    ]
    if not eligible:
        return "", (
            "Нет дефолта: ни один вариант не уложился в "
            f"hit@5 ≥ {HIT_AT_5_MIN} и p95 ≤ {P95_MAX_S:.1f} с."
        )
    chosen = max(eligible, key=_rank_key)
    why = (
        f"По новой разметке среди hit@5 ≥ {HIT_AT_5_MIN} и p95 ≤ {P95_MAX_S:.1f} с "
        f"лучший nDCG@8 у «{chosen[1]}»: {chosen[2]['ndcg_at_8']:.4f}, "
        f"hit@5={chosen[2]['hit_at_5']:.4f}, p95={chosen[2]['p95_s']:.3f} с."
    )
    return chosen[0], why


def render_report(
    rows: list[tuple[str, str, dict[str, float]]],
    *,
    chosen_key: str,
    why: str,
    misses: list[str] | None = None,
) -> str:
    lines = [
        "# Контур B: hit@5 и nDCG@8 на живых корпусах",
        "",
        "Закрытый набор precision@8 остаётся регрессионным тестом. Приёмка на этих 50 перефразах: "
        f"**hit@5 ≥ {HIT_AT_5_MIN}** и **nDCG@8 ≥ {NDCG_AT_8_MIN}**. "
        "Порог nDCG@8 не снижался.",
        "",
        "Модели локальные, CPU. e5 — официальный ONNX intfloat, префиксы `query: ` и `passage: `. "
        "Реранкеры — квантованный ONNX `jinaai/jina-reranker-v2-base-multilingual` "
        "(тот же файл, что в каталоге FastEmbed) и `BAAI/bge-reranker-v2-m3`. "
        "Пул 10 или 20, пара обрезана до 256 или 384 токенов, в чанк идут заголовок и начало. "
        "Батч onnxruntime, intra_op = число ядер. "
        "CI этот прогон не запускает: hashing, без реранкера, веса не качаются. "
        "Кэш весов — каталог Hugging Face, не `data/`. Живых вызовов провайдера нет.",
        "",
        "Корпуса: правила `docs/fable/user_rules.md`; ноды `NODE_GROUPS` "
        "(каталога `templates/node_groups` на этом main нет); промпты этих нод "
        "из `templates/excel_gpt_agents` и `prompts/scene_design`.",
        "",
        "В текст чанка ноды и в metadata добавлены `node_key`, `title` и роль в группе. "
        "Явного поля role у `GroupNodeSpec` нет: роль — это `description` и позиция "
        "в `NodeGroupDef.nodes` (`app/services/node_groups.py`).",
        "",
        "50 запросов — перефразы без имён нод, без `R-*` и без дословных заголовков правил. "
        "42 запроса: релевантен любой чанк двух нод семейства. 8 запросов: одно правило.",
        "",
        "nDCG@8 бинарный: DCG = Σ rel_i / log2(i+1), IDCG — идеальная выдача min(8, |релевантные|). "
        "hit@5 — доля запросов, где в top-5 есть хотя бы один релевантный. "
        "Задержка — `retrieve` на запрос после одного прогрева; холодная загрузка весов не входит. "
        "p50/p95 — линейная интерполяция по 50 замерам.",
        "",
        "| Вариант | hit@5 новая | nDCG@8 новая | hit@5 старая | nDCG@8 старая | "
        "nDCG@8 док новая | nDCG@8 док старая | hit@5 source_id | nDCG@8 source_id | p50, с | p95, с | приёмка |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for _key, label, metrics in rows:
        mark = "да" if _passes(metrics) else "нет"
        lines.append(
            f"| {label} | {metrics['hit_at_5']:.4f} | {metrics['ndcg_at_8']:.4f} | "
            f"{metrics.get('hit_at_5_old', 0.0):.4f} | "
            f"{metrics.get('ndcg_at_8_old', metrics['ndcg_at_8']):.4f} | "
            f"{metrics.get('ndcg_doc_new', 0.0):.4f} | {metrics.get('ndcg_doc_old', 0.0):.4f} | "
            f"{metrics.get('hit_at_5_source', 0.0):.4f} | "
            f"{metrics.get('ndcg_at_8_source', metrics['ndcg_at_8']):.4f} | "
            f"{metrics['p50_s']:.3f} | {metrics['p95_s']:.3f} | {mark} |"
        )
    matched = next(((key, label, metrics) for key, label, metrics in rows if key == chosen_key), None)
    ceiling = rows[0][2].get("ceiling") if rows else None
    ceiling_lines = (
        ["", f"Потолок precision@8 по разметке: **{ceiling:.4f}**."]
        if isinstance(ceiling, (int, float))
        else []
    )
    if matched is None:
        default_lines = [
            *ceiling_lines,
            "",
            f"Дефолт: **нет дефолта**. {why}",
            "",
        ]
    else:
        key, chosen_label, chosen = matched
        default_lines = [
            *ceiling_lines,
            "",
            f"Дефолт контура B: **{chosen_label}** (ключ `{key}`). {why}",
            "",
            "Критерии выбранного дефолта: "
            f"hit@5 = {chosen['hit_at_5']:.4f} (порог {HIT_AT_5_MIN}), "
            f"nDCG@8 = {chosen['ndcg_at_8']:.4f} (порог {NDCG_AT_8_MIN}). "
            + ("Оба порога взяты." if _passes(chosen) else "Пороги не взяты."),
            "",
        ]
    lines.extend(default_lines)
    lines.extend(
        [
            "Гибрид по умолчанию в коде ретривера — RRF k=60, веса 1/1, BM25 по всему тексту. "
            "Без `ORCH_RAG_PROFILE=contour-b` `get_engine()` остаётся на hashing и реранкер не включает. "
            "Реранкер выключается `ORCH_RAG_RERANK=0` или `retrieve(..., rerank=False)`.",
            "",
            "## Запросы, где hit@5 промахнулись все варианты",
            "",
        ]
    )
    if misses:
        lines.extend(misses)
    else:
        lines.append("Таких запросов нет.")
    lines.append("")
    return "\n".join(lines)


def _engine(db_path: Path, embedder: Any, reranker: Reranker | None) -> RagEngine:
    return RagEngine(
        db_path=db_path,
        embedder=embedder,
        reranker=reranker,
        rules_path=ROOT / "docs" / "fable" / "user_rules.md",
        repo_root=ROOT,
        scan_git=True,
        project_db=None,
    )


def _prefix_ok() -> bool:
    """Живые веса не грузит: проверяет, что обёртка e5 ставит префиксы."""
    from app.orchestrator.rag.embedder import PASSAGE_PREFIX, QUERY_PREFIX, E5PrefixEmbedder

    class _Probe:
        embedder_id = "probe"

        def __init__(self) -> None:
            self.last: list[str] = []

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.last = list(texts)
            return [[0.0] * 8 for _ in texts]

    probe = _Probe()
    wrapper = E5PrefixEmbedder(probe)
    wrapper.embed_query("проверка префикса")
    query_ok = probe.last == [QUERY_PREFIX + "проверка префикса"]
    wrapper.embed_passages(["текст документа"])
    passage_ok = probe.last == [PASSAGE_PREFIX + "текст документа"]
    return query_ok and passage_ok


def _hybrid_grid(embedder_kind: str) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for rrf_k in (20, 60):
        for lexical, vector in ((0.5, 1.5), (1.0, 2.0)):
            for scope in ("full", "head"):
                scope_label = "весь текст" if scope == "full" else "title+key+роль"
                key = f"{embedder_kind}+k{rrf_k}+w{lexical}/{vector}+{scope}"
                label = (
                    f"{embedder_kind} гибрид k={rrf_k} веса {lexical}/{vector} BM25 {scope_label}"
                )
                rows.append(
                    (
                        key,
                        label,
                        {
                            "rerank": False,
                            "rrf_k": rrf_k,
                            "rrf_weights": (lexical, vector),
                            "bm25_scope": scope,
                        },
                    )
                )
    return rows


def _rerank_grid(embedder_kind: str, base: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    lexical, vector = base["rrf_weights"]
    for model in ("jina", "bge-m3"):
        for length in (256, 384):
            for pool in (10, 20):
                key = f"{embedder_kind}+{model}+p{pool}+t{length}"
                label = (
                    f"{embedder_kind} + {model} топ-{pool} {length} ток. "
                    f"(база k={base['rrf_k']} веса {lexical}/{vector} BM25 {base['bm25_scope']})"
                )
                spec = dict(base)
                spec.update({"rerank": True, "rerank_pool": pool, "model": model, "max_length": length})
                rows.append((key, label, spec))
    return rows


def _index_catalog(engine: RagEngine) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for chunk in engine.store.fetch_chunks(["rules", "nodes", "prompts"]):
        rows.append(
            {
                "corpus": chunk.corpus,
                "source_id": chunk.source_id,
                "part_key": str(chunk.metadata.get("part_key") or ""),
                "title": chunk.title,
                "node_key": str(chunk.metadata.get("node_key") or ""),
                "group_role": str(chunk.metadata.get("group_role") or ""),
            }
        )
    return rows


def _miss_lines(
    queries: list[dict[str, Any]],
    saved: dict[str, list[dict[str, Any]]],
) -> list[str]:
    if not saved:
        return []
    keys = list(saved)
    sample_key = next((key for key in keys if key.startswith("e5-large+k")), keys[0])
    lines: list[str] = []
    for index, query in enumerate(queries):
        if any(saved[key][index]["hit5"] for key in keys):
            continue
        lines.append(f"- `{query['id']}` ({query['family']}): {query['text']}")
        top = saved[sample_key][index]["top"][:5]
        for item in top:
            lines.append(
                f"  - {item['corpus']} `{item['source_id']}` {item.get('part_key') or ''} "
                f"{item.get('title') or ''}"
            )
    return lines


def run_profile_pair(out: Path | None = None) -> dict[str, Any]:
    """Два варианта на текущей разметке: дефолт bge-m3 и тот же гибрид без реранкера.

    Запросы не переписывает. Рейтинги пишет во временный файл, не в репозиторий.
    """
    from evals.orchestrator.holdout import load_dev_queries, read_for_tuning
    from evals.orchestrator.label_transfer import TRANSFERRED_PATH

    queries = load_dev_queries()
    require_dev_only([str(query["id"]) for query in queries])
    labeled = {
        str(query["id"]): query for query in json.loads(read_for_tuning(TRANSFERRED_PATH))["queries"]
    }
    transferred = [labeled.get(str(query["id"]), query) for query in queries]
    report_path = out or REPORT_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-pair-"))
    runs: list[tuple[str, str, dict[str, float]]] = []
    saved: dict[str, list[dict[str, Any]]] = {}
    try:
        embedder = build_embedder("e5-large")
        plain = _engine(temp_dir / "orch_rag.db", embedder, None)
        try:
            plain.reindex(["rules", "nodes", "prompts"])
            print("index chunks", len(_index_catalog(plain)), flush=True)
            metrics, details = measure(
                queries,
                plain,
                rerank=False,
                rrf_k=20,
                rrf_weights=(0.5, 1.5),
                bm25_scope="full",
                transferred=transferred,
            )
        finally:
            plain.close()
        runs.append(("e5-large+k20+w0.5/1.5+full", "e5-large гибрид k=20 веса 0.5/1.5 BM25 весь текст", metrics))
        saved[runs[-1][0]] = details
        print(runs[-1][1], {k: round(v, 4) for k, v in metrics.items()}, flush=True)
        reranker = build_reranker("bge-m3", max_length=256)
        ranked = _engine(temp_dir / "orch_rag.db", embedder, reranker)
        try:
            ranked.reindex(["rules", "nodes", "prompts"])
            metrics, details = measure(
                queries,
                ranked,
                rerank=True,
                rrf_k=20,
                rrf_weights=(0.5, 1.5),
                bm25_scope="full",
                rerank_pool=20,
                transferred=transferred,
            )
        finally:
            ranked.close()
        runs.append(
            (
                "e5-large+bge-m3+p20+t256",
                "e5-large + bge-m3 топ-20 256 ток. (база k=20 веса 0.5/1.5 BM25 full)",
                metrics,
            )
        )
        saved[runs[-1][0]] = details
        print(runs[-1][1], {k: round(v, 4) for k, v in metrics.items()}, flush=True)
        chosen_key, why = select_on_dev(runs, [str(query["id"]) for query in queries])
        misses = _miss_lines(queries, saved)
        text = render_report(runs, chosen_key=chosen_key, why=why, misses=misses)
        ambiguous_path = TRANSFERRED_PATH.with_name("precision_live_label_ambiguous.json")
        ambiguous = json.loads(ambiguous_path.read_text(encoding="utf-8"))
        ambiguous_lines = [
            "",
            f"Неоднозначные метки, вручную не назначенные: **{ambiguous['count']}**.",
            "",
            "| query_id | старый chunk_id | причина |",
            "|---|---|---|",
        ]
        for item in ambiguous["items"]:
            ambiguous_lines.append(
                f"| `{item['query_id']}` | `{item['old_chunk_id']}` | {item['reason']} |"
            )
        text += (
            "\nЧанкование промптов: `split_prompt(max_parts=3)`. "
            "Гипотеза подтвердилась для текста промптов: ни один из 252 фрагментов "
            "с part_key s0–s2 не сохранил тот же body, что при `max_parts=8`. "
            "Опубликованный nDCG 0.544 считался по source_id (колонка source_id), не по part_key. "
            "Правило переноса зафиксировано коммитом `1396ea43` "
            "(`precision_live_label_transfer.md` и `label_transfer.py`) до этого прогона "
            "и после него не менялось. Метрики до фиксации правила не считались. "
            "Порог nDCG@8 0.65 не снижался. "
            "hit@5 и `nDCG@8 новая` — перенесённые chunk_id. "
            "hit@5 и `nDCG@8 старая` — буквальные part_key исходного файла на новой нарезке. "
            "Колонки source_id — прошлый скоринг семейств (любой чанк source_id). "
            "nDCG по документам — выдача без повторов source_id, порога нет. "
            "Приёмка смотрит hit@5 и nDCG@8 новой разметки. "
            "live-29 вручную не правился.\n"
            + "\n".join(ambiguous_lines)
            + "\n"
        )
        report_path.write_text(text, encoding="utf-8")
        detail_path = temp_dir / "precision_live_rankings.json"
        detail_path.write_text(
            json.dumps({"chosen": chosen_key, "runs": {key: rows for key, rows in saved.items()}}, ensure_ascii=False),
            encoding="utf-8",
        )
        summary = {
            "chosen": chosen_key,
            "why": why,
            "metrics": {key: metrics for key, _label, metrics in runs},
        }
        print(json.dumps({"chosen": chosen_key, "why": why, "metrics": summary["metrics"]}, ensure_ascii=False), flush=True)
        return summary
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# Вариант B: max_parts=3, перенесённые chunk_id, коммит 4b447232. Здесь не переигрывался.
_VARIANT_B: list[dict[str, Any]] = [
    {
        "variant": "B",
        "system": "гибрид без реранкера",
        "hit_at_5": 0.82,
        "ndcg_at_8": 0.45738356018163073,
        "ndcg_doc": 0.7320796529700101,
        "p95_s": 0.057675631799338586,
    },
    {
        "variant": "B",
        "system": "bge-m3 топ-20 / 256",
        "hit_at_5": 0.82,
        "ndcg_at_8": 0.45740439515157605,
        "ndcg_doc": 0.6739302109861469,
        "p95_s": 1.4471942315503838,
    },
]


def _label_hit(chunk: Any, query: dict[str, Any], mode: str) -> bool:
    if mode == "chunk":
        return _is_relevant_transferred(chunk, query)
    return _is_relevant_literal(chunk, query)


def measure_labelings(
    engine: RagEngine,
    labelings: list[tuple[str, list[dict[str, Any]], str]],
    *,
    rerank: bool,
    rrf_k: int,
    rrf_weights: tuple[float, float],
    bm25_scope: str,
    rerank_pool: int,
    aggregate: str = "none",
) -> dict[str, dict[str, float]]:
    """Один проход retrieve, несколько разметок. Задержка от разметки не зависит."""
    indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    indexed_source_ids = {chunk.source_id for chunk in indexed}
    rule_source_ids = {chunk.source_id for chunk in indexed if chunk.corpus == "rules"}
    counts = {
        name: [sum(1 for chunk in indexed if _label_hit(chunk, query, mode)) for query in queries]
        for name, queries, mode in labelings
    }
    kwargs = {
        "rerank": rerank,
        "rrf_k": rrf_k,
        "rrf_weights": rrf_weights,
        "bm25_scope": bm25_scope,
        "rerank_pool": rerank_pool,
        "aggregate": aggregate,
    }
    anchor = labelings[0][1][0]["text"]
    engine.retrieve(anchor, ["rules", "nodes", "prompts"], top_k=8, **kwargs)
    latencies: list[float] = []
    scores: dict[str, dict[str, Any]] = {
        name: {"ndcg": [], "doc": [], "hit5": 0} for name, _queries, _mode in labelings
    }
    width = len(labelings[0][1])
    for index in range(width):
        text = labelings[0][1][index]["text"]
        started = time.perf_counter()
        found = engine.retrieve(text, ["rules", "nodes", "prompts"], top_k=8, **kwargs)
        latencies.append(time.perf_counter() - started)
        for name, queries, mode in labelings:
            query = queries[index]
            flags = [_label_hit(chunk, query, mode) for chunk in found]
            scores[name]["ndcg"].append(_ndcg_at_8(flags, counts[name][index]))
            if any(flags[:5]):
                scores[name]["hit5"] += 1
            if query.get("family") == "rules":
                sources = set(rule_source_ids)
            else:
                sources = _relevant_source_ids(query, literal=True)
            scores[name]["doc"].append(_doc_ndcg(found, sources, indexed_source_ids))
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    metrics: dict[str, dict[str, float]] = {}
    for name, _queries, _mode in labelings:
        bucket = scores[name]
        metrics[name] = {
            "hit_at_5": bucket["hit5"] / width,
            "ndcg_at_8": sum(bucket["ndcg"]) / width,
            "ndcg_doc": sum(bucket["doc"]) / width,
            "p50_s": p50,
            "p95_s": p95,
        }
    return metrics


def _abc_row(variant: str, system: str, metrics: dict[str, float]) -> dict[str, Any]:
    return {
        "variant": variant,
        "system": system,
        "hit_at_5": metrics["hit_at_5"],
        "ndcg_at_8": metrics["ndcg_at_8"],
        "ndcg_doc": metrics["ndcg_doc"],
        "p95_s": metrics["p95_s"],
    }


def render_abc(
    rows: list[dict[str, Any]],
    *,
    chosen_key: str,
    why: str,
    ambiguous: dict[str, Any],
    index_chunks: int,
) -> str:
    lines = [
        "# Контур B: три нарезки",
        "",
        "Порог nDCG@8 **0.65 не снижался**. Приёмка и выбор дефолта в `profile.py` — "
        "только вариант C (8 частей, метки s0–s7): hit@5 ≥ 0.9, "
        f"p95 ≤ {P95_MAX_S:.1f} с, "
        "максимальный nDCG@8. Варианты A и B в отбор не входят.",
        "",
        f"Индекс A и C: `split_prompt(max_parts=8)`, чанков {index_chunks}. "
        "Обе системы — e5-large, RRF k=20, веса 0.5/1.5, BM25 по всему тексту. "
        "Реранкер — bge-m3, пул 20, 256 токенов. "
        "p95 у A и C одной системы совпадает: разметка не меняет выдачу.",
        "",
        "B — уже посчитанный результат `max_parts=3` на перенесённых chunk_id "
        "(коммит `4b447232`). В этом прогоне не переигрывался.",
        "",
        "| Вариант | Система | hit@5 | nDCG@8 | nDCG@8 док | p95, с |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['variant']} | {row['system']} | {row['hit_at_5']:.4f} | "
            f"{row['ndcg_at_8']:.4f} | {row['ndcg_doc']:.4f} | {row['p95_s']:.3f} |"
        )
    c_rows = [row for row in rows if row["variant"] == "C"]
    passed = [
        row
        for row in c_rows
        if row["hit_at_5"] >= HIT_AT_5_MIN and row["ndcg_at_8"] >= NDCG_AT_8_MIN and row["p95_s"] <= P95_MAX_S
    ]
    lines.extend(
        [
            "",
            f"Дефолт по варианту C: **{chosen_key or 'нет дефолта'}**. {why}",
            "",
            (
                "Порог 0.65 на варианте C взят."
                if passed
                else "Порог 0.65 на варианте C не взят и не снижался."
            ),
            "",
            "A — буквальные part_key файла (каталог и s0–s2). "
            "C — `precision_live_labels_s0_s7.json`: s0–s2 тем же предикатом, что `1396ea43`, "
            "на нарезке из 8 частей; s3–s7 — уточнение `19d050c2`, записанное до этого прогона. "
            "Правило `1396ea43` после того коммита не менялось. live-29 вручную не правился.",
            "",
            f"Неоднозначные метки, вручную не назначенные: **{ambiguous['count']}**.",
            "",
            "| query_id | старый chunk_id | причина |",
            "|---|---|---|",
        ]
    )
    for item in ambiguous["items"]:
        lines.append(f"| `{item['query_id']}` | `{item['old_chunk_id']}` | {item['reason']} |")
    lines.append("")
    return "\n".join(lines)


def run_abc(out: Path | None = None) -> dict[str, Any]:
    """A и C на нарезке из 8 частей. B подставляется из прогона 4b447232."""
    from evals.orchestrator.holdout import load_dev_expanded, load_dev_queries, read_for_tuning
    from evals.orchestrator.label_transfer import AMBIGUOUS_PATH

    queries = load_dev_queries()
    expanded = load_dev_expanded()
    read_for_tuning(AMBIGUOUS_PATH)
    dev_ids = [str(query["id"]) for query in queries]
    require_dev_only(dev_ids)
    if dev_ids != [str(query["id"]) for query in expanded]:
        raise RuntimeError("порядок dev-запросов в разметке s0–s7 разошёлся с исходным файлом")
    ambiguous = json.loads(AMBIGUOUS_PATH.read_text(encoding="utf-8"))
    report_path = out or REPORT_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-abc-"))
    labelings = [
        ("A", queries, "literal"),
        ("C", expanded, "chunk"),
    ]
    try:
        embedder = build_embedder("e5-large")
        plain = _engine(temp_dir / "orch_rag.db", embedder, None)
        try:
            plain.reindex(["rules", "nodes", "prompts"])
            index_chunks = len(_index_catalog(plain))
            print("index chunks", index_chunks, flush=True)
            hybrid = measure_labelings(
                plain,
                labelings,
                rerank=False,
                rrf_k=20,
                rrf_weights=(0.5, 1.5),
                bm25_scope="full",
                rerank_pool=20,
            )
        finally:
            plain.close()
        print("hybrid", {key: {k: round(v, 4) for k, v in row.items()} for key, row in hybrid.items()}, flush=True)
        reranker = build_reranker("bge-m3", max_length=256)
        ranked = _engine(temp_dir / "orch_rag.db", embedder, reranker)
        try:
            ranked.reindex(["rules", "nodes", "prompts"])
            reranked = measure_labelings(
                ranked,
                labelings,
                rerank=True,
                rrf_k=20,
                rrf_weights=(0.5, 1.5),
                bm25_scope="full",
                rerank_pool=20,
            )
        finally:
            ranked.close()
        print("rerank", {key: {k: round(v, 4) for k, v in row.items()} for key, row in reranked.items()}, flush=True)
        rows = [
            _abc_row("A", "гибрид без реранкера", hybrid["A"]),
            _abc_row("A", "bge-m3 топ-20 / 256", reranked["A"]),
            *_VARIANT_B,
            _abc_row("C", "гибрид без реранкера", hybrid["C"]),
            _abc_row("C", "bge-m3 топ-20 / 256", reranked["C"]),
        ]
        c_runs = [
            ("e5-large+k20+w0.5/1.5+full", "C гибрид без реранкера", hybrid["C"]),
            ("e5-large+bge-m3+p20+t256", "C bge-m3 топ-20 / 256", reranked["C"]),
        ]
        chosen_key, why = select_on_dev(c_runs, dev_ids)
        text = render_abc(
            rows,
            chosen_key=chosen_key,
            why=why,
            ambiguous=ambiguous,
            index_chunks=index_chunks,
        )
        report_path.write_text(text, encoding="utf-8")
        summary = {"chosen": chosen_key, "why": why, "rows": rows, "index_chunks": index_chunks}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return summary
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _pick_dev(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Только dev: hit@5 ≥ 0.9, грубый p95 ≤ 1.2 с, максимальный nDCG@8."""
    eligible = [row for row in rows if row["hit_at_5"] >= HIT_AT_5_MIN and row["p95_s"] <= 1.2]
    pool = eligible or [row for row in rows if row["hit_at_5"] >= HIT_AT_5_MIN] or rows
    return max(pool, key=lambda row: (row["ndcg_at_8"], row["hit_at_5"], -row["p95_s"]))


def record_prod_p95(
    samples: list[float],
    *,
    config: dict[str, Any],
    commit: str,
    hardware: dict[str, Any],
) -> dict[str, Any]:
    """Медиана трёх холодных замеров. Без замеров число не из чего взять."""
    if len(samples) != 3:
        raise ValueError("p95 прод-пути — медиана трёх замеров, не литерал")
    ordered = [float(item) for item in samples]
    if any(item <= 0 for item in ordered):
        raise ValueError("замер p95 пустой")
    return {
        "samples_s": ordered,
        "median_s": _median(ordered),
        "min_s": min(ordered),
        "max_s": max(ordered),
        "config": config,
        "commit": commit,
        "hardware": hardware,
        "cold_query_vectors": True,
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def run_tune() -> dict[str, Any]:
    """Подбор только на dev-70. Holdout-3 не открывается."""
    from evals.orchestrator.holdout import load_dev_expanded

    dev = load_dev_expanded()
    require_dev_only([str(query["id"]) for query in dev])
    configs: list[dict[str, Any]] = [
        {"aggregate": "none", "rerank_pool": 20, "lexical": 0.5, "vector": 1.5},
    ]
    for aggregate in ("none", "prompt_pool", "prompt_expand"):
        for pool in (8, 12, 16):
            for lexical, vector in ((0.5, 1.5), (0.3, 1.7), (1.0, 2.0)):
                configs.append(
                    {"aggregate": aggregate, "rerank_pool": pool, "lexical": lexical, "vector": vector}
                )
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-tune-"))
    dev_rows: list[dict[str, Any]] = []
    try:
        embedder = build_embedder("e5-large")
        reranker = build_reranker("bge-m3", max_length=256)
        engine = _engine(temp_dir / "orch_rag.db", embedder, reranker)
        try:
            engine.reindex(["rules", "nodes", "prompts"])
            print("index chunks", len(_index_catalog(engine)), flush=True)
            engine.retrieve(dev[0]["text"], ["rules", "nodes", "prompts"], top_k=8, rerank=False)
            for config in configs:
                engine._query_vectors.clear()
                metrics = measure_labelings(
                    engine,
                    [("C", dev, "chunk")],
                    rerank=True,
                    rrf_k=20,
                    rrf_weights=(config["lexical"], config["vector"]),
                    bm25_scope="full",
                    rerank_pool=config["rerank_pool"],
                    aggregate=config["aggregate"],
                )["C"]
                row = {**config, **metrics}
                dev_rows.append(row)
                print(
                    "DEV",
                    config["aggregate"],
                    config["rerank_pool"],
                    config["lexical"],
                    config["vector"],
                    {key: round(metrics[key], 4) for key in ("hit_at_5", "ndcg_at_8", "ndcg_doc", "p95_s")},
                    flush=True,
                )
            chosen = _pick_dev(dev_rows)
            print("CHOSEN_BEFORE_TEST", json.dumps(chosen, ensure_ascii=False), flush=True)
            held_out: dict[str, dict[str, float]] = {}
            for name, subset in (("dev", dev),):
                engine._query_vectors.clear()
                held_out[name] = measure_labelings(
                    engine,
                    [("C", subset, "chunk")],
                    rerank=True,
                    rrf_k=20,
                    rrf_weights=(chosen["lexical"], chosen["vector"]),
                    bm25_scope="full",
                    rerank_pool=chosen["rerank_pool"],
                    aggregate=chosen["aggregate"],
                )["C"]
                print("SCORE", name, {key: round(held_out[name][key], 4) for key in held_out[name]}, flush=True)
            kwargs = {
                "rerank": True,
                "rrf_k": 20,
                "rrf_weights": (chosen["lexical"], chosen["vector"]),
                "bm25_scope": "full",
                "rerank_pool": chosen["rerank_pool"],
                "aggregate": chosen["aggregate"],
            }
            run_p95: dict[str, list[float]] = {"dev": []}
            for _run in range(3):
                engine._query_vectors.clear()
                engine.retrieve(dev[0]["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
                samples: list[float] = []
                for query in dev:
                    started = time.perf_counter()
                    engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
                    samples.append(time.perf_counter() - started)
                run_p95["dev"].append(_percentile(samples, 95))
            latency = {name: _median(values) for name, values in run_p95.items()}
            print("P95_MEDIAN", {key: round(value, 4) for key, value in latency.items()}, flush=True)
            summary = {"chosen": chosen, "dev_grid": dev_rows, "scores": held_out, "p95_median": latency}
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return summary
        finally:
            engine.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def contour_c_acceptance(test_ndcg: float) -> dict[str, Any]:
    """Порог 0.65 не снижается. Ниже — конфиг C экспериментальный, «R5 не достигнут»."""
    met = test_ndcg >= NDCG_AT_8_MIN
    return {
        "threshold": NDCG_AT_8_MIN,
        "test_ndcg_at_8": test_ndcg,
        "experimental": not met,
        "mark": "" if met else "R5 не достигнут",
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {"hit_at_5": 0.0, "ndcg_at_8": 0.0, "ndcg_doc": 0.0, "p95_s": 0.0, "n": 0}
    count = len(rows)
    return {
        "hit_at_5": sum(1 for row in rows if row["hit5"]) / count,
        "ndcg_at_8": sum(row["ndcg"] for row in rows) / count,
        "ndcg_doc": sum(row["ndcg_doc"] for row in rows) / count,
        "p95_s": _percentile([row["latency_s"] for row in rows], 95),
        "n": count,
    }


def group_slices(
    rows: list[dict[str, Any]],
    dev_ids: list[str],
    test_ids: list[str],
) -> dict[str, dict[str, float]]:
    by_id = {row["id"]: row for row in rows}
    slices: dict[str, dict[str, float]] = {
        "dev": summarize_rows([by_id[query_id] for query_id in dev_ids]),
        "test": summarize_rows([by_id[query_id] for query_id in test_ids]),
        "all": summarize_rows(rows),
    }
    families: list[str] = []
    for row in rows:
        family = str(row["family"])
        if family not in families:
            families.append(family)
    for family in families:
        slices[family] = summarize_rows([row for row in rows if row["family"] == family])
    return slices


def aggregate_pass_slices(passes: list[dict[str, dict[str, float]]]) -> dict[str, Any]:
    """Медиана hit@5 и nDCG по проходам. Совпадение — по этим двум, не по часам p95."""
    if len(passes) != PASS_COUNT:
        raise ValueError(f"ожидалось {PASS_COUNT} прохода, получено {len(passes)}")
    names = list(passes[0])
    identical = True
    median: dict[str, dict[str, float]] = {}
    spread: dict[str, dict[str, dict[str, float]]] = {}
    for name in names:
        hits = [item[name]["hit_at_5"] for item in passes]
        ndcgs = [item[name]["ndcg_at_8"] for item in passes]
        docs = [item[name]["ndcg_doc"] for item in passes]
        latencies = [item[name]["p95_s"] for item in passes]
        if len(set(hits)) != 1 or len(set(ndcgs)) != 1:
            identical = False
        median[name] = {
            "hit_at_5": _median(hits),
            "ndcg_at_8": _median(ndcgs),
            "ndcg_doc": _median(docs),
            "p95_s": _median(latencies),
            "n": passes[0][name]["n"],
        }
        spread[name] = {
            "hit_at_5": {"min": min(hits), "max": max(hits), "median": _median(hits)},
            "ndcg_at_8": {"min": min(ndcgs), "max": max(ndcgs), "median": _median(ndcgs)},
            "p95_s": {"min": min(latencies), "max": max(latencies), "median": _median(latencies)},
        }
    return {
        "pass_count": PASS_COUNT,
        "identical": identical,
        "passes": passes,
        "median": median,
        "spread": spread,
    }


def write_pass_artifact(artifact: Path, payload: dict[str, Any]) -> None:
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def measure_chunk_rows(
    engine: RagEngine,
    queries: list[dict[str, Any]],
    *,
    rerank: bool,
    rrf_k: int,
    rrf_weights: tuple[float, float],
    bm25_scope: str,
    rerank_pool: int,
    aggregate: str,
    rerank_fusion: str = "replace",
    rerank_fusion_weights: tuple[float, float] = (1.0, 1.0),
    bm25_protect: float = 0.0,
    family_boost: float = 1.0,
    corpora: list[str] | None = None,
    record_top5: bool = False,
) -> list[dict[str, Any]]:
    """Один проход. Прогрев не входит в задержку. Метки — chunk_id варианта C."""
    selected = list(corpora or ["rules", "nodes", "prompts"])
    indexed = engine.store.fetch_chunks(selected)
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    counts = [sum(1 for chunk in indexed if _is_relevant_transferred(chunk, query)) for query in queries]
    indexed_source_ids = {chunk.source_id for chunk in indexed}
    rule_source_ids = {chunk.source_id for chunk in indexed if chunk.corpus == "rules"}
    kwargs = {
        "rerank": rerank,
        "rrf_k": rrf_k,
        "rrf_weights": rrf_weights,
        "bm25_scope": bm25_scope,
        "rerank_pool": rerank_pool,
        "aggregate": aggregate,
        "rerank_fusion": rerank_fusion,
        "rerank_fusion_weights": rerank_fusion_weights,
        "bm25_protect": bm25_protect,
        "family_boost": family_boost,
    }
    engine.retrieve(queries[0]["text"], selected, top_k=8, **kwargs)
    rows: list[dict[str, Any]] = []
    for query, count in zip(queries, counts, strict=True):
        started = time.perf_counter()
        found = engine.retrieve(query["text"], selected, top_k=8, **kwargs)
        elapsed = time.perf_counter() - started
        flags = [_is_relevant_transferred(chunk, query) for chunk in found]
        if query.get("family") == "rules":
            sources = set(rule_source_ids)
        else:
            sources = _relevant_source_ids(query, literal=True)
        row: dict[str, Any] = {
            "id": query["id"],
            "family": query.get("family"),
            "hit5": any(flags[:5]),
            "ndcg": _ndcg_at_8(flags, count),
            "ndcg_doc": _doc_ndcg(found, sources, indexed_source_ids),
            "latency_s": elapsed,
        }
        if record_top5:
            row["top5"] = [chunk.chunk_id for chunk in found[:5]]
        rows.append(row)
    return rows


def render_locked_report(payload: dict[str, Any]) -> str:
    median = payload["median"]
    acceptance = payload["acceptance"]
    identical = payload["identical"]
    lines = [
        "# Контур B: детерминированный перемер",
        "",
        "Зафиксированный конфиг: bge-m3 топ-12, агрегация `none`, "
        "веса лексика/вектор 0.5/1.5, RRF k=20, BM25 full, e5-large, 256 токенов. "
        "Реранкер не заменяет порядок: RRF с BM25, веса 0.5/1.5, "
        "защита топ-1 выключена, family_boost 1. "
        "На dev hit@5 этой смеси 0.88, порог 0.9 не взят.",
        "Сессии ONNX в режиме эвалов: `intra_op_num_threads=1`, `inter_op_num_threads=1`, "
        "фиксированный сид, `use_deterministic_compute`. "
        "Реранкер эвалов — fp32 `onnx/model.onnx` той же bge-m3: "
        "квантованный граф при тех же опциях не бит-стабилен.",
        "Сплит прежний: нечётный `query_id` — dev, чётный — test.",
        "e5-large, bge-m3, RRF k=20, BM25 full и 256 токенов выбраны прогонами "
        "на всех 50 запросах, до того как подбор стал смотреть только dev. "
        "Текущий test — срез после этих гиперпараметров, не новый held-out. "
        "Веса, размер пула и aggregate после сплита считались только на dev.",
        "Текст правила 1 в индексе — новый. Метки `rule-1` не менялись.",
        "",
        f"Три прохода дали одинаковые hit@5 и nDCG@8: **{'да' if identical else 'нет'}**.",
        "В таблицу попала медиана. Сырые метрики каждого прохода — "
        "`precision_live_passes.json`.",
        "",
        "| Срез | hit@5 | nDCG@8 | nDCG@8 док | p95, с |",
        "|---|---:|---:|---:|---:|",
    ]
    order = ["dev", "test", "all", "camera", "characters", "world", "action", "assemble", "shots", "script", "rules"]
    labels = {
        "dev": "dev",
        "test": "test",
        "all": "все запросы",
    }
    for key in order:
        if key not in median:
            continue
        row = median[key]
        title = labels.get(key, key)
        lines.append(
            f"| {title} | {row['hit_at_5']:.4f} | {row['ndcg_at_8']:.4f} | "
            f"{row['ndcg_doc']:.4f} | {row['p95_s']:.3f} |"
        )
    lines.extend(["", "Сырые проходы (hit@5 / nDCG@8):", ""])
    for index, item in enumerate(payload["passes"]):
        bits = [
            f"{name} {item[name]['hit_at_5']:.4f}/{item[name]['ndcg_at_8']:.4f}"
            for name in ("dev", "test", "all")
            if name in item
        ]
        lines.append(f"- Проход {index + 1}: " + "; ".join(bits))
    if acceptance["experimental"]:
        lines.extend(
            [
                "",
                f"Порог nDCG@8 {NDCG_AT_8_MIN} на test не взят "
                f"({acceptance['test_ndcg_at_8']:.4f}) и не снижался. "
                "Конфиг C экспериментальный: **R5 не достигнут**.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                f"Порог nDCG@8 {NDCG_AT_8_MIN} на test взят "
                f"({acceptance['test_ndcg_at_8']:.4f}).",
            ]
        )
    hit = median["test"]["hit_at_5"]
    if hit < HIT_AT_5_MIN:
        lines.append(f"hit@5 test {hit:.4f} ниже порога {HIT_AT_5_MIN}.")
    else:
        lines.append(f"hit@5 test {hit:.4f}, порог {HIT_AT_5_MIN}.")
    p95 = median["test"]["p95_s"]
    if p95 > P95_MAX_S:
        lines.append(
            f"p95 эвала на test {p95:.3f} с выше лимита {P95_MAX_S:.1f} с "
            "(один поток, fp32)."
        )
    else:
        lines.append(f"p95 эвала на test {p95:.3f} с, лимит {P95_MAX_S:.1f} с.")
    prod = payload.get("prod_p95")
    if not isinstance(prod, dict) or "median_s" not in prod:
        lines.append("p95 прод-пути в артефакте нет.")
    else:
        median_s = float(prod["median_s"])
        taken = "взят" if median_s <= P95_MAX_S else "не взят"
        lines.append(
            "p95 прод-пути из артефакта "
            f"(commit {prod.get('commit') or '—'}, холодный _query_vectors): "
            f"{median_s:.3f} с. Лимит {P95_MAX_S:.1f} с {taken}."
        )
    quality = payload.get("prod_quality")
    if isinstance(quality, dict) and "test" in quality:
        lines.append(
            "Качество того же прод-пути, медиана трёх прогонов: "
            f"test hit@5 {quality['test']['hit_at_5']:.4f}, "
            f"nDCG@8 {quality['test']['ndcg_at_8']:.4f}."
        )
    lines.extend(
        [
            "",
            "## Разбор слабых семейств (только dev)",
            "",
            "Метки и нарезка не менялись. `rule-1` и 12 неоднозначных строк не трогались. "
            "Нужные секции уже отдельные части и помечены.",
            "",
            "assemble. Оба сборщика режутся ровно по `##` (8 частей), склейки чужих секций нет. "
            "live-25 и live-27 поднимают сборщик. live-29 (нечётный, dev) промахивается: "
            "в запросе «промпты» и «стыкуют», в тексте сборщика «собираешь» и «тип_стыка». "
            "BM25 всё же ставит `sd_asm` s0 и каталог на 4–5 места, реранкер отдаёт верх "
            "промптам кадров и скелету. Метка стоит на нужных чанках.",
            "",
            "camera. В `camera.md` заголовков больше восьми, части упакованы, но лестница "
            "крупностей остаётся в s1 вместе со словарём крупности. live-01 собирает камеру. "
            "live-03: BM25 №1 — camera s1, реранкер ставит выше части shots, где общий, "
            "средний и крупный план — значения полей. Каталог не перечисляет крупность и "
            "панораму, но тело секции BM25 уже находит. live-05: вверху короткий chrono-camera, "
            "дальше shots, frames и action.",
            "",
            "shots. `scenes_to_frames_ru.md` — 7 частей без склейки, покрытие и поля кадра "
            "отдельные секции. live-31: вверху сборка и action, shots s0/s3 на 6–7 местах. "
            "live-33: frames не входит в топ. Запрос «промпты картинок», документ "
            "«промт_картинки»; FTS5 unicode61 не стеммит, это разные токены. "
            "Синоним под один запрос не добавлялся. live-35: BM25 уже ранжирует shots, "
            "реранкер поднимает сборку и action.",
            "",
            "Окно 256 токенов оставляет заголовок `##` внутри окна (общий префикс роли "
            "около 100 токенов), хвост секции обрезается. Отклонённые варианты ниже — "
            "только dev (нечётные id), квантованный прогон.",
            "Заголовок секции первым, окно 256: hit@5 0.80, nDCG@8 0.5991 "
            "(база того прогона 0.96 / 0.6958). Окно 384: hit@5 0.92, nDCG@8 0.6542.",
            "Смесь с BM25 на dev: mix 0 — hit@5 0.92, nDCG@8 0.6583; "
            "mix 0.5 — 0.92 / 0.6155; mix 1.0 — 0.80 / 0.6375; mix 2.0 — 0.80 / 0.6234. "
            "В профиль не вошло.",
            "",
        ]
    )
    return "\n".join(lines)


def _remeasure_config() -> dict[str, Any]:
    from app.orchestrator.rag.profile import (
        CONTOUR_B_AGGREGATE,
        CONTOUR_B_BM25_PROTECT,
        CONTOUR_B_BM25_SCOPE,
        CONTOUR_B_FAMILY_BOOST,
        CONTOUR_B_FUSION_BM25,
        CONTOUR_B_FUSION_RERANK,
        CONTOUR_B_RERANK_FUSION,
        CONTOUR_B_RERANK_MAX_LENGTH,
        CONTOUR_B_RERANK_POOL,
        CONTOUR_B_RRF_K,
        CONTOUR_B_RRF_LEXICAL,
        CONTOUR_B_RRF_VECTOR,
    )

    return {
        "embedder": os.environ.get("ORCH_RAG_REMEASURE_EMBEDDER", "e5-large"),
        "reranker": "bge-m3",
        "max_length": CONTOUR_B_RERANK_MAX_LENGTH,
        "rrf_k": CONTOUR_B_RRF_K,
        "lexical": CONTOUR_B_RRF_LEXICAL,
        "vector": CONTOUR_B_RRF_VECTOR,
        "bm25_scope": CONTOUR_B_BM25_SCOPE,
        "rerank_pool": CONTOUR_B_RERANK_POOL,
        "aggregate": CONTOUR_B_AGGREGATE,
        "rerank_fusion": CONTOUR_B_RERANK_FUSION,
        "fusion_bm25": CONTOUR_B_FUSION_BM25,
        "fusion_rerank": CONTOUR_B_FUSION_RERANK,
        "bm25_protect": CONTOUR_B_BM25_PROTECT,
        "family_boost": CONTOUR_B_FAMILY_BOOST,
        "eval_intra_op": 1,
        "eval_inter_op": 1,
        "eval_seed": 0,
    }


def execute_one_pass(work_dir: Path, *, embedder_kind: str | None = None) -> dict[str, Any]:
    """Один проход в своём каталоге: новый ``orch_rag.db`` и свой reindex.

    Кэш запроса и корпуса предыдущего прохода сюда не переносится: это другой процесс.
    """
    from evals.orchestrator.label_expand import EXPANDED_PATH
    from evals.orchestrator.query_split import SPLIT_PATH

    config = _remeasure_config()
    kind = embedder_kind or str(config["embedder"])
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "orch_rag.db"
    if db_path.exists():
        db_path.unlink()
    expanded = json.loads(EXPANDED_PATH.read_text(encoding="utf-8"))["queries"]
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    eval_mode = kind != "hashing"
    embedder = build_embedder(kind, eval_mode=eval_mode)
    reranker = (
        build_reranker("bge-m3", max_length=int(config["max_length"]), eval_mode=True)
        if eval_mode
        else None
    )
    engine = _engine(db_path, embedder, reranker)
    try:
        print("reindex start", work_dir, flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        print("index chunks", len(_index_catalog(engine)), "pid", os.getpid(), flush=True)
        rows = measure_chunk_rows(
            engine,
            expanded,
            rerank=eval_mode,
            rrf_k=int(config["rrf_k"]),
            rrf_weights=(float(config["lexical"]), float(config["vector"])),
            bm25_scope=str(config["bm25_scope"]),
            rerank_pool=int(config["rerank_pool"]),
            aggregate=str(config["aggregate"]),
            rerank_fusion=str(config["rerank_fusion"]),
            rerank_fusion_weights=(float(config["fusion_bm25"]), float(config["fusion_rerank"])),
            bm25_protect=float(config["bm25_protect"]),
            family_boost=float(config["family_boost"]),
        )
        slices = group_slices(rows, list(split["dev"]), list(split["test"]))
    finally:
        engine.close()
    return {
        "slices": slices,
        "embedder_id": embedder.embedder_id,
        "reranker_id": None if reranker is None else reranker.reranker_id,
        "intra_op": getattr(embedder.inner, "intra_op_threads", None),
        "inter_op": 1 if eval_mode else None,
        "db_path": str(db_path.resolve()),
        "pid": os.getpid(),
        "fresh_db": True,
    }


def run_remeasure(out: Path | None = None, artifact: Path | None = None) -> dict[str, Any]:
    """Три прохода зафиксированного конфига C, каждый в своём процессе. Сетки нет."""
    report_path = out or REPORT_PATH
    artifact_path = artifact or PASSES_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-det-"))
    config = _remeasure_config()
    passes: list[dict[str, dict[str, float]]] = []
    pass_meta: list[dict[str, Any]] = []
    try:
        for index in range(PASS_COUNT):
            work = temp_dir / f"pass-{index}"
            out_path = work / "pass.json"
            work.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "evals.orchestrator.precision_live",
                    "--remeasure-pass",
                    "--pass-dir",
                    str(work),
                    "--pass-out",
                    str(out_path),
                ],
                check=True,
            )
            one = json.loads(out_path.read_text(encoding="utf-8"))
            passes.append(one["slices"])
            pass_meta.append(
                {
                    "embedder_id": one["embedder_id"],
                    "reranker_id": one["reranker_id"],
                    "intra_op": one["intra_op"],
                    "inter_op": one["inter_op"],
                    "db_path": one["db_path"],
                    "pid": one["pid"],
                }
            )
            print("PASS", index, "pid", one["pid"], one["embedder_id"], flush=True)
        packed = aggregate_pass_slices(passes)
        acceptance = contour_c_acceptance(packed["median"]["test"]["ndcg_at_8"])
        payload = {**packed, "pass_meta": pass_meta, "config": config, "acceptance": acceptance}
        write_pass_artifact(artifact_path, payload)
        text = render_locked_report(payload)
        report_path.write_text(text, encoding="utf-8")
        print(text, flush=True)
        print(json.dumps({"identical": packed["identical"], "acceptance": acceptance}, ensure_ascii=False), flush=True)
        return payload
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def measure_prod_path(artifact: Path | None = None, report: Path | None = None) -> dict[str, Any]:
    """p95 и качество ``get_engine(profile='contour-b')``.

    Три прогона, перед каждым сбрасывается ``_query_vectors``. Прогрев в p95 не входит.
    Индекс — тот, что собирает прод: ноды из проектной базы и git, не пустой MemoryNodeStore.
    """
    import platform

    from app.orchestrator.rag import get_engine, reset_engine
    from evals.orchestrator.label_expand import EXPANDED_PATH
    from evals.orchestrator.query_split import SPLIT_PATH

    expanded = json.loads(EXPANDED_PATH.read_text(encoding="utf-8"))["queries"]
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    import app.orchestrator.rag as rag_mod

    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-prod-"))
    rag_mod.default_db_path = lambda: temp_dir / "orch_rag.db"
    reset_engine()
    engine = get_engine(profile="contour-b")
    prod_corpora = ["rules", "history", "nodes", "prompts"]
    defaults = {key: value for key, value in engine.retrieve_defaults.items() if value is not None}
    samples: list[float] = []
    qualities: list[dict[str, dict[str, float]]] = []
    try:
        print("prod reindex", engine.db_path, flush=True)
        engine.reindex(prod_corpora, timeout=900)
        print("prod chunks", len(engine.store.fetch_chunks(prod_corpora)), flush=True)
        for index in range(PASS_COUNT):
            engine._clear_retrieval_cache()
            rows = measure_chunk_rows(
                engine,
                expanded,
                rerank=defaults.get("rerank", True) is not False,
                rrf_k=int(defaults["rrf_k"]),
                rrf_weights=tuple(defaults["rrf_weights"]),
                bm25_scope=str(defaults["bm25_scope"]),
                rerank_pool=int(defaults["rerank_pool"]),
                aggregate=str(defaults["aggregate"]),
                rerank_fusion=str(defaults["rerank_fusion"]),
                rerank_fusion_weights=tuple(defaults["rerank_fusion_weights"]),
                bm25_protect=float(defaults["bm25_protect"]),
                family_boost=float(defaults["family_boost"]),
                corpora=prod_corpora,
            )
            slices = group_slices(rows, list(split["dev"]), list(split["test"]))
            samples.append(slices["all"]["p95_s"])
            qualities.append(slices)
            print("PROD_PASS", index, round(samples[-1], 4), flush=True)
    finally:
        reset_engine()
        shutil.rmtree(temp_dir, ignore_errors=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    recorded = record_prod_p95(
        samples,
        config=_remeasure_config(),
        commit=commit,
        hardware={"cpu_count": os.cpu_count(), "platform": platform.platform()},
    )
    packed = aggregate_pass_slices(qualities)
    payload: dict[str, Any] = {}
    if artifact is not None and artifact.is_file():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["prod_p95"] = recorded
    payload["prod_quality"] = packed["median"]
    if artifact is not None:
        write_pass_artifact(artifact, payload)
    if report is not None and "median" in payload and "acceptance" in payload:
        report.write_text(render_locked_report(payload), encoding="utf-8")
    print(json.dumps({"prod_p95": recorded, "prod_quality": packed["median"]}, ensure_ascii=False), flush=True)
    return payload


def family_slices(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Срез all и по семействам. Dev/test сюда не подмешиваются."""
    slices: dict[str, dict[str, float]] = {"all": summarize_rows(rows)}
    families: list[str] = []
    for row in rows:
        family = str(row["family"])
        if family not in families:
            families.append(family)
    for family in families:
        slices[family] = summarize_rows([row for row in rows if row["family"] == family])
    return slices


def prod_p95_each_gate(samples: list[float]) -> dict[str, Any]:
    """Порог 1.2 с на каждом холодном прогоне, не на медиане."""
    if len(samples) != PASS_COUNT:
        raise ValueError(f"нужны {PASS_COUNT} прогона p95, получено {len(samples)}")
    ordered = [float(item) for item in samples]
    return {
        "samples_s": ordered,
        "limit_s": P95_MAX_S,
        "each_within_limit": all(item <= P95_MAX_S for item in ordered),
        "above": [item for item in ordered if item > P95_MAX_S],
    }


def execute_holdout_pass(work_dir: Path) -> dict[str, Any]:
    """Один проход holdout: свой ``orch_rag.db`` и свой reindex. Dev-плюс не подмешивается."""
    from evals.orchestrator.holdout import load_holdout_queries

    config = _remeasure_config()
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "orch_rag.db"
    if db_path.exists():
        db_path.unlink()
    queries = load_holdout_queries()
    embedder = build_embedder("e5-large", eval_mode=True)
    reranker = build_reranker("bge-m3", max_length=int(config["max_length"]), eval_mode=True)
    engine = _engine(db_path, embedder, reranker)
    try:
        print("holdout reindex", work_dir, flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        print("holdout chunks", len(_index_catalog(engine)), "pid", os.getpid(), flush=True)
        rows = measure_chunk_rows(
            engine,
            queries,
            rerank=True,
            rrf_k=int(config["rrf_k"]),
            rrf_weights=(float(config["lexical"]), float(config["vector"])),
            bm25_scope=str(config["bm25_scope"]),
            rerank_pool=int(config["rerank_pool"]),
            aggregate=str(config["aggregate"]),
            rerank_fusion=str(config["rerank_fusion"]),
            rerank_fusion_weights=(float(config["fusion_bm25"]), float(config["fusion_rerank"])),
            bm25_protect=float(config["bm25_protect"]),
            family_boost=float(config["family_boost"]),
        )
        slices = family_slices(rows)
    finally:
        engine.close()
    return {
        "slices": slices,
        "embedder_id": embedder.embedder_id,
        "reranker_id": reranker.reranker_id,
        "intra_op": getattr(embedder.inner, "intra_op_threads", None),
        "inter_op": 1,
        "db_path": str(db_path.resolve()),
        "pid": os.getpid(),
        "fresh_db": True,
        "query_ids": [query["id"] for query in queries],
    }


def run_holdout_measure(out: Path | None = None, artifact: Path | None = None) -> dict[str, Any]:
    """Три процесса fp32 на holdout. Повторный прогон отсюда не запускается."""
    report_path = out or REPORT_PATH
    artifact_path = artifact or PASSES_PATH
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-holdout-"))
    passes: list[dict[str, dict[str, float]]] = []
    pass_meta: list[dict[str, Any]] = []
    try:
        for index in range(PASS_COUNT):
            work = temp_dir / f"pass-{index}"
            out_path = work / "pass.json"
            work.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "evals.orchestrator.precision_live",
                    "--holdout-pass",
                    "--pass-dir",
                    str(work),
                    "--pass-out",
                    str(out_path),
                ],
                check=True,
            )
            one = json.loads(out_path.read_text(encoding="utf-8"))
            passes.append(one["slices"])
            pass_meta.append(
                {
                    "embedder_id": one["embedder_id"],
                    "reranker_id": one["reranker_id"],
                    "intra_op": one["intra_op"],
                    "inter_op": one["inter_op"],
                    "db_path": one["db_path"],
                    "pid": one["pid"],
                    "query_ids": one["query_ids"],
                }
            )
            print("HOLDOUT_PASS", index, "pid", one["pid"], one["slices"]["all"], flush=True)
        packed = aggregate_pass_slices(passes)
        acceptance = contour_c_acceptance(packed["median"]["all"]["ndcg_at_8"])
        hit_ok = packed["median"]["all"]["hit_at_5"] >= HIT_AT_5_MIN
        payload = {
            **packed,
            "pass_meta": pass_meta,
            "config": _remeasure_config(),
            "acceptance": acceptance,
            "hit_at_5_met": hit_ok,
            "set": "holdout",
        }
        write_pass_artifact(artifact_path, payload)
        report_path.write_text(_render_holdout_report(payload), encoding="utf-8")
        print(json.dumps({"identical": packed["identical"], "acceptance": acceptance, "hit_at_5_met": hit_ok}, ensure_ascii=False), flush=True)
        return payload
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _render_holdout_report(payload: dict[str, Any]) -> str:
    median = payload["median"]
    lines = [
        "# Holdout контура B",
        "",
        "Один замер: три процесса, fp32, один поток, сид 0, у каждого свой reindex.",
        "Dev-плюс в этот прогон не входил. Пороги не сдвигались.",
        "",
        "| срез | hit@5 | nDCG@8 | nDCG док | p95, с |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in median.items():
        lines.append(
            f"| {name} | {row['hit_at_5']:.4f} | {row['ndcg_at_8']:.4f} | "
            f"{row['ndcg_doc']:.4f} | {row['p95_s']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"nDCG@8 ≥ {NDCG_AT_8_MIN}: {payload['acceptance']['mark'] or 'взят'}.",
            f"hit@5 ≥ {HIT_AT_5_MIN}: {'взят' if payload['hit_at_5_met'] else 'не взят'}.",
            "",
        ]
    )
    return "\n".join(lines)


def measure_holdout_prod_p95(artifact: Path | None = None) -> dict[str, Any]:
    """Три холодных p95 ``get_engine(profile='contour-b')`` на holdout.

    Число выше 1.2 с остаётся в артефакте. Повторного прогона нет.
    """
    import platform

    from app.orchestrator.rag import get_engine, reset_engine
    from evals.orchestrator.holdout import load_holdout_queries

    queries = load_holdout_queries()
    import app.orchestrator.rag as rag_mod

    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-prod-holdout-"))
    rag_mod.default_db_path = lambda: temp_dir / "orch_rag.db"
    reset_engine()
    engine = get_engine(profile="contour-b")
    prod_corpora = ["rules", "history", "nodes", "prompts"]
    defaults = {key: value for key, value in engine.retrieve_defaults.items() if value is not None}
    samples: list[float] = []
    try:
        print("prod holdout reindex", engine.db_path, flush=True)
        engine.reindex(prod_corpora, timeout=900)
        for index in range(PASS_COUNT):
            engine._clear_retrieval_cache()
            rows = measure_chunk_rows(
                engine,
                queries,
                rerank=defaults.get("rerank", True) is not False,
                rrf_k=int(defaults["rrf_k"]),
                rrf_weights=tuple(defaults["rrf_weights"]),
                bm25_scope=str(defaults["bm25_scope"]),
                rerank_pool=int(defaults["rerank_pool"]),
                aggregate=str(defaults["aggregate"]),
                rerank_fusion=str(defaults["rerank_fusion"]),
                rerank_fusion_weights=tuple(defaults["rerank_fusion_weights"]),
                bm25_protect=float(defaults["bm25_protect"]),
                family_boost=float(defaults["family_boost"]),
                corpora=prod_corpora,
            )
            samples.append(summarize_rows(rows)["p95_s"])
            print("PROD_HOLDOUT_PASS", index, round(samples[-1], 4), flush=True)
    finally:
        reset_engine()
        shutil.rmtree(temp_dir, ignore_errors=True)
    gate = prod_p95_each_gate(samples)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    recorded = record_prod_p95(
        samples,
        config=_remeasure_config(),
        commit=commit,
        hardware={"cpu_count": os.cpu_count(), "platform": platform.platform()},
    )
    recorded["each_within_limit"] = gate["each_within_limit"]
    recorded["above"] = gate["above"]
    payload: dict[str, Any] = {}
    if artifact is not None and artifact.is_file():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["prod_p95"] = recorded
    payload["prod_p95_gate"] = gate
    if artifact is not None:
        write_pass_artifact(artifact, payload)
    print(json.dumps({"prod_p95": recorded, "gate": gate}, ensure_ascii=False), flush=True)
    return payload


HOLDOUT3_MEASURE_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_holdout3_measure.json"
HOLDOUT3_CONFIG_COMMIT = "1627ff1edd9fa4ca87f807dd757d7bc37e2372aa"
HOLDOUT3_VERDICT = "предварительный, на фрагментных запросах"


def _assert_holdout3_sha_before_load() -> str:
    """Сначала байты и пин. JSON открывается только после совпадения."""
    from evals.orchestrator.frozen_datasets import FROZEN_SHA256, FrozenDatasetError, file_sha256
    from evals.orchestrator.holdout3 import HOLDOUT3_PATH

    pin = FROZEN_SHA256["precision_live_holdout3.json"]["sha256"]
    digest = file_sha256(HOLDOUT3_PATH)
    if digest != pin:
        raise FrozenDatasetError(f"holdout-3 sha256 {digest} != {pin}")
    return digest


def measure_holdout3_once(out: Path | None = None) -> dict[str, Any]:
    """Один fp32-проход holdout-3 и три холодных p95. Повторный запуск запрещён."""
    import platform

    from app.orchestrator.rag import get_engine, reset_engine
    from evals.orchestrator.holdout3 import load_holdout3_queries

    destination = out or HOLDOUT3_MEASURE_PATH
    if destination.exists():
        raise RuntimeError(f"{destination.name} уже записан, повторный замер запрещён")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != HOLDOUT3_CONFIG_COMMIT:
        raise RuntimeError(f"конфиг заморожен на {HOLDOUT3_CONFIG_COMMIT}, HEAD {head}")
    config = _remeasure_config()
    if (
        config["aggregate"] != "expand_tail"
        or int(config["rrf_k"]) != 20
        or float(config["fusion_bm25"]) != 0.25
        or float(config["fusion_rerank"]) != 1.5
    ):
        raise RuntimeError(f"профиль не совпал с замороженным дефолтом: {config}")
    query_sha = _assert_holdout3_sha_before_load()
    queries = load_holdout3_queries()
    if len(queries) != 24 or any(not str(query["id"]).startswith("h3-") for query in queries):
        raise RuntimeError("holdout-3: ожидались 24 id h3-*")

    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-holdout3-"))
    db_path = temp_dir / "orch_rag.db"
    embedder = build_embedder("e5-large", eval_mode=True)
    reranker = build_reranker("bge-m3", max_length=int(config["max_length"]), eval_mode=True)
    engine = _engine(db_path, embedder, reranker)
    try:
        print("holdout3 reindex", os.getpid(), flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        print("holdout3 chunks", len(_index_catalog(engine)), flush=True)
        rows = measure_chunk_rows(
            engine,
            queries,
            rerank=True,
            rrf_k=int(config["rrf_k"]),
            rrf_weights=(float(config["lexical"]), float(config["vector"])),
            bm25_scope=str(config["bm25_scope"]),
            rerank_pool=int(config["rerank_pool"]),
            aggregate=str(config["aggregate"]),
            rerank_fusion=str(config["rerank_fusion"]),
            rerank_fusion_weights=(float(config["fusion_bm25"]), float(config["fusion_rerank"])),
            bm25_protect=float(config["bm25_protect"]),
            family_boost=float(config["family_boost"]),
        )
        slices = family_slices(rows)
    finally:
        engine.close()
    print("holdout3 slices", json.dumps(slices, ensure_ascii=False), flush=True)

    import app.orchestrator.rag as rag_mod

    prod_dir = Path(tempfile.mkdtemp(prefix="orch-rag-holdout3-prod-"))
    rag_mod.default_db_path = lambda: prod_dir / "orch_rag.db"
    reset_engine()
    prod = get_engine(profile="contour-b")
    defaults = {key: value for key, value in prod.retrieve_defaults.items() if value is not None}
    if (
        defaults.get("aggregate") != "expand_tail"
        or tuple(defaults.get("rerank_fusion_weights") or ()) != (0.25, 1.5)
        or int(defaults.get("rrf_k") or 0) != 20
    ):
        reset_engine()
        raise RuntimeError(f"прод-путь не замороженный дефолт: {defaults}")
    prod_corpora = ["rules", "history", "nodes", "prompts"]
    samples: list[float] = []
    try:
        print("holdout3 prod reindex", flush=True)
        prod.reindex(prod_corpora, timeout=900)
        for index in range(PASS_COUNT):
            prod._clear_retrieval_cache()
            prod_rows = measure_chunk_rows(
                prod,
                queries,
                rerank=defaults.get("rerank", True) is not False,
                rrf_k=int(defaults["rrf_k"]),
                rrf_weights=tuple(defaults["rrf_weights"]),
                bm25_scope=str(defaults["bm25_scope"]),
                rerank_pool=int(defaults["rerank_pool"]),
                aggregate=str(defaults["aggregate"]),
                rerank_fusion=str(defaults["rerank_fusion"]),
                rerank_fusion_weights=tuple(defaults["rerank_fusion_weights"]),
                bm25_protect=float(defaults["bm25_protect"]),
                family_boost=float(defaults["family_boost"]),
                corpora=prod_corpora,
            )
            samples.append(float(summarize_rows(prod_rows)["p95_s"]))
            print("HOLDOUT3_PROD_PASS", index, samples[-1], flush=True)
    finally:
        reset_engine()
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(prod_dir, ignore_errors=True)

    gate = prod_p95_each_gate(samples)
    overall = slices["all"]
    payload = {
        "set": "holdout-3",
        "passes": 1,
        "verdict_label": HOLDOUT3_VERDICT,
        "config_commit": HOLDOUT3_CONFIG_COMMIT,
        "query_sha256": query_sha,
        "config": config,
        "slices": slices,
        "hit_at_5": overall["hit_at_5"],
        "ndcg_at_8": overall["ndcg_at_8"],
        "thresholds": {"ndcg_at_8": NDCG_AT_8_MIN, "hit_at_5": HIT_AT_5_MIN, "p95_s": P95_MAX_S},
        "hit_at_5_met": overall["hit_at_5"] >= HIT_AT_5_MIN,
        "ndcg_at_8_met": overall["ndcg_at_8"] >= NDCG_AT_8_MIN,
        "prod_p95_s": samples,
        "prod_p95_gate": gate,
        "prod_p95_met": gate["each_within_limit"],
        "hardware": {"cpu_count": os.cpu_count(), "platform": platform.platform()},
    }
    write_pass_artifact(destination, payload)
    print(json.dumps({"hit_at_5": payload["hit_at_5"], "ndcg_at_8": payload["ndcg_at_8"], "prod_p95_s": samples}, ensure_ascii=False), flush=True)
    return payload


HOLDOUT4_MEASURE_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_holdout4_measure.json"
HOLDOUT4_DATASET_COMMIT = "e344be2e68c3652bdc871b3d5094fb95a0c22fbe"
HOLDOUT4_CONFIG_COMMIT = "1627ff1edd9fa4ca87f807dd757d7bc37e2372aa"


def _assert_holdout4_sha_before_load() -> str:
    """Сначала байты и пин. JSON открывается только после совпадения."""
    from evals.orchestrator.frozen_datasets import FROZEN_SHA256, FrozenDatasetError, file_sha256
    from evals.orchestrator.holdout4 import HOLDOUT4_PATH

    pin = FROZEN_SHA256["precision_live_holdout4.json"]["sha256"]
    digest = file_sha256(HOLDOUT4_PATH)
    if digest != pin:
        raise FrozenDatasetError(f"holdout-4 sha256 {digest} != {pin}")
    return digest


def _assert_frozen_contour_b() -> dict[str, Any]:
    """Профиль на диске — тот же, что в 1627ff1e: expand_tail, 0.25/1.5, k=20."""
    profile = ROOT / "app" / "orchestrator" / "rag" / "profile.py"
    committed = subprocess.check_output(
        ["git", "show", f"{HOLDOUT4_CONFIG_COMMIT}:app/orchestrator/rag/profile.py"],
        cwd=ROOT,
    )
    if profile.read_bytes() != committed:
        raise RuntimeError("profile.py разошёлся с замороженным 1627ff1e")
    config = _remeasure_config()
    if (
        config["aggregate"] != "expand_tail"
        or int(config["rrf_k"]) != 20
        or float(config["fusion_bm25"]) != 0.25
        or float(config["fusion_rerank"]) != 1.5
    ):
        raise RuntimeError(f"профиль не совпал с замороженным дефолтом: {config}")
    return config


def measure_holdout4_once(out: Path | None = None) -> dict[str, Any]:
    """Один fp32-проход holdout-4 и три холодных p95. Повторный запуск запрещён."""
    import platform

    from app.orchestrator.rag import get_engine, reset_engine
    from evals.orchestrator.holdout4 import load_holdout4_queries

    destination = out or HOLDOUT4_MEASURE_PATH
    if destination.exists():
        raise RuntimeError(f"{destination.name} уже записан, повторный замер запрещён")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != HOLDOUT4_DATASET_COMMIT:
        raise RuntimeError(f"набор заморожен на {HOLDOUT4_DATASET_COMMIT}, HEAD {head}")
    config = _assert_frozen_contour_b()
    query_sha = _assert_holdout4_sha_before_load()
    queries = load_holdout4_queries()
    if len(queries) < 32 or any(not str(query["id"]).startswith("h4-") for query in queries):
        raise RuntimeError("holdout-4: ожидались id h4-* и n ≥ 32")

    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-holdout4-"))
    db_path = temp_dir / "orch_rag.db"
    embedder = build_embedder("e5-large", eval_mode=True)
    reranker = build_reranker("bge-m3", max_length=int(config["max_length"]), eval_mode=True)
    engine = _engine(db_path, embedder, reranker)
    try:
        print("holdout4 reindex", os.getpid(), flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        print("holdout4 chunks", len(_index_catalog(engine)), flush=True)
        rows = measure_chunk_rows(
            engine,
            queries,
            rerank=True,
            rrf_k=int(config["rrf_k"]),
            rrf_weights=(float(config["lexical"]), float(config["vector"])),
            bm25_scope=str(config["bm25_scope"]),
            rerank_pool=int(config["rerank_pool"]),
            aggregate=str(config["aggregate"]),
            rerank_fusion=str(config["rerank_fusion"]),
            rerank_fusion_weights=(float(config["fusion_bm25"]), float(config["fusion_rerank"])),
            bm25_protect=float(config["bm25_protect"]),
            family_boost=float(config["family_boost"]),
            record_top5=True,
        )
        slices = family_slices(rows)
    finally:
        engine.close()
    print("holdout4 slices", json.dumps(slices, ensure_ascii=False), flush=True)
    by_id = {row["id"]: row for row in rows}
    per_query = []
    for query in queries:
        row = by_id[query["id"]]
        gold = [
            str(item["chunk_id"])
            for item in query.get("relevant") or []
            if int(item.get("grade") or 0) > 0 and item.get("chunk_id")
        ]
        per_query.append(
            {
                "id": query["id"],
                "family": query.get("family"),
                "gold": gold,
                "top5": list(row["top5"]),
            }
        )

    import app.orchestrator.rag as rag_mod

    prod_dir = Path(tempfile.mkdtemp(prefix="orch-rag-holdout4-prod-"))
    rag_mod.default_db_path = lambda: prod_dir / "orch_rag.db"
    reset_engine()
    prod = get_engine(profile="contour-b")
    defaults = {key: value for key, value in prod.retrieve_defaults.items() if value is not None}
    if (
        defaults.get("aggregate") != "expand_tail"
        or tuple(defaults.get("rerank_fusion_weights") or ()) != (0.25, 1.5)
        or int(defaults.get("rrf_k") or 0) != 20
    ):
        reset_engine()
        raise RuntimeError(f"прод-путь не замороженный дефолт: {defaults}")
    prod_corpora = ["rules", "history", "nodes", "prompts"]
    samples: list[float] = []
    try:
        print("holdout4 prod reindex", flush=True)
        prod.reindex(prod_corpora, timeout=900)
        for index in range(PASS_COUNT):
            prod._clear_retrieval_cache()
            prod_rows = measure_chunk_rows(
                prod,
                queries,
                rerank=defaults.get("rerank", True) is not False,
                rrf_k=int(defaults["rrf_k"]),
                rrf_weights=tuple(defaults["rrf_weights"]),
                bm25_scope=str(defaults["bm25_scope"]),
                rerank_pool=int(defaults["rerank_pool"]),
                aggregate=str(defaults["aggregate"]),
                rerank_fusion=str(defaults["rerank_fusion"]),
                rerank_fusion_weights=tuple(defaults["rerank_fusion_weights"]),
                bm25_protect=float(defaults["bm25_protect"]),
                family_boost=float(defaults["family_boost"]),
                corpora=prod_corpora,
            )
            samples.append(float(summarize_rows(prod_rows)["p95_s"]))
            print("HOLDOUT4_PROD_PASS", index, samples[-1], flush=True)
    finally:
        reset_engine()
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(prod_dir, ignore_errors=True)

    gate = prod_p95_each_gate(samples)
    overall = slices["all"]
    payload = {
        "set": "holdout-4",
        "passes": 1,
        "dataset_commit": HOLDOUT4_DATASET_COMMIT,
        "config_commit": HOLDOUT4_CONFIG_COMMIT,
        "query_sha256": query_sha,
        "config": config,
        "slices": slices,
        "queries": per_query,
        "hit_at_5": overall["hit_at_5"],
        "ndcg_at_8": overall["ndcg_at_8"],
        "thresholds": {"ndcg_at_8": NDCG_AT_8_MIN, "hit_at_5": HIT_AT_5_MIN, "p95_s": P95_MAX_S},
        "hit_at_5_met": overall["hit_at_5"] >= HIT_AT_5_MIN,
        "ndcg_at_8_met": overall["ndcg_at_8"] >= NDCG_AT_8_MIN,
        "prod_p95_s": samples,
        "prod_p95_gate": gate,
        "prod_p95_met": gate["each_within_limit"],
        "hardware": {"cpu_count": os.cpu_count(), "platform": platform.platform()},
    }
    write_pass_artifact(destination, payload)
    print(
        json.dumps(
            {"hit_at_5": payload["hit_at_5"], "ndcg_at_8": payload["ndcg_at_8"], "prod_p95_s": samples},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    parser.add_argument(
        "--write-queries",
        action="store_true",
        help="перезаписать precision_live_queries.json; без флага файл только сверяется",
    )
    parser.add_argument("--pair", action="store_true", help="только дефолт bge-m3 и гибрид без реранкера")
    parser.add_argument("--abc", action="store_true", help="варианты A и C на 8 частях, B из прошлого прогона")
    parser.add_argument("--tune", action="store_true", help="подбор на dev, затем один замер test")
    parser.add_argument(
        "--remeasure",
        action="store_true",
        help="три детерминированных прохода зафиксированного конфига C",
    )
    parser.add_argument("--prod-p95", action="store_true", help="p95 и качество прод-пути get_engine")
    parser.add_argument("--artifact", type=Path, default=PASSES_PATH)
    parser.add_argument("--remeasure-pass", action="store_true", help="один проход в отдельном процессе")
    parser.add_argument("--holdout-pass", action="store_true", help="один проход holdout в отдельном процессе")
    parser.add_argument("--holdout-measure", action="store_true", help="три процесса fp32 на замороженном holdout")
    parser.add_argument("--holdout-prod-p95", action="store_true", help="три холодных p95 прод-пути на holdout")
    parser.add_argument("--holdout3-measure", action="store_true", help="один проход holdout-3 на замороженном конфиге")
    parser.add_argument("--holdout4-measure", action="store_true", help="один проход holdout-4 на замороженном конфиге")
    parser.add_argument("--pass-dir", type=Path)
    parser.add_argument("--pass-out", type=Path)
    args = parser.parse_args()
    if args.holdout_pass:
        if args.pass_dir is None or args.pass_out is None:
            raise SystemExit("--holdout-pass требует --pass-dir и --pass-out")
        payload = execute_holdout_pass(args.pass_dir)
        args.pass_out.parent.mkdir(parents=True, exist_ok=True)
        args.pass_out.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    if args.holdout_measure:
        run_holdout_measure(args.out, args.artifact)
        return
    if args.holdout_prod_p95:
        measure_holdout_prod_p95(args.artifact)
        return
    if args.holdout3_measure:
        measure_holdout3_once()
        return
    if args.holdout4_measure:
        measure_holdout4_once()
        return
    if args.remeasure_pass:
        if args.pass_dir is None or args.pass_out is None:
            raise SystemExit("--remeasure-pass требует --pass-dir и --pass-out")
        payload = execute_one_pass(args.pass_dir)
        args.pass_out.parent.mkdir(parents=True, exist_ok=True)
        args.pass_out.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    if args.prod_p95:
        measure_prod_path(args.artifact, args.out)
        return
    if args.remeasure:
        run_remeasure(args.out, args.artifact)
        return
    if args.tune:
        run_tune()
        return
    if args.abc:
        run_abc(args.out)
        return
    if args.pair:
        run_profile_pair(args.out)
        return
    from evals.orchestrator.frozen_datasets import assert_frozen

    if args.write_queries:
        queries = build_queries()
        QUERY_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUERY_PATH.write_text(json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        assert_frozen(QUERY_PATH)
        queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
    prefixes_ok = _prefix_ok()
    print("e5 prefixes", prefixes_ok, flush=True)
    # Не data/orch_rag.db рантайма: временный каталог вне data/. Имя файла то же,
    # потому что писатель принимает только orch_rag.db.
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-live-"))
    try:
        _main_grid(temp_dir, queries, prefixes_ok, args.out)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _main_grid(temp_dir: Path, queries: list[dict[str, Any]], prefixes_ok: bool, out: Path) -> None:
    from evals.orchestrator.holdout import load_dev_queries, read_for_tuning

    read_for_tuning(QUERY_PATH)
    queries = load_dev_queries()
    require_dev_only([str(query["id"]) for query in queries])
    selection_ids = [str(query["id"]) for query in queries]
    runs: list[tuple[str, str, dict[str, float]]] = []
    saved: dict[str, list[dict[str, Any]]] = {}
    catalog: list[dict[str, str]] = []
    for embedder_kind in ("e5-small", "e5-large"):
        embedder = build_embedder(embedder_kind)
        engine = _engine(temp_dir / "orch_rag.db", embedder, None)
        try:
            engine.reindex(["rules", "nodes", "prompts"])
            if not catalog:
                catalog = _index_catalog(engine)
                print("index chunks", len(catalog), flush=True)
            for key, label, spec in _hybrid_grid(embedder_kind):
                metrics, details = measure(queries, engine, **{k: spec[k] for k in spec})
                runs.append((key, label, metrics))
                saved[key] = details
                print(label, {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()}, flush=True)
        finally:
            engine.close()
        best_key, best_label, best_metrics = max(
            (row for row in runs if row[0].startswith(embedder_kind + "+k")),
            key=lambda row: (row[2]["ndcg_at_8"], row[2]["hit_at_5"]),
        )
        print("best hybrid", embedder_kind, best_label, best_metrics["ndcg_at_8"], flush=True)
        base_spec = next(spec for key, _label, spec in _hybrid_grid(embedder_kind) if key == best_key)
        loaded: dict[tuple[str, int], Reranker] = {}
        try:
            for key, label, spec in _rerank_grid(embedder_kind, base_spec):
                slot = (spec["model"], spec["max_length"])
                if slot not in loaded:
                    loaded[slot] = build_reranker(spec["model"], max_length=spec["max_length"])
                rerank_engine = _engine(temp_dir / "orch_rag.db", embedder, loaded[slot])
                try:
                    rerank_engine.reindex(["rules", "nodes", "prompts"])
                    metrics, details = measure(
                        queries,
                        rerank_engine,
                        rerank=True,
                        rrf_k=spec["rrf_k"],
                        rrf_weights=spec["rrf_weights"],
                        bm25_scope=spec["bm25_scope"],
                        rerank_pool=spec["rerank_pool"],
                    )
                finally:
                    rerank_engine.close()
                runs.append((key, label, metrics))
                saved[key] = details
                print(label, {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()}, flush=True)
        finally:
            loaded.clear()
            del embedder
            gc.collect()
    chosen_key, why = select_on_dev(runs, selection_ids)
    misses = _miss_lines(queries, saved)
    text = render_report(runs, chosen_key=chosen_key, why=why, misses=misses)
    if not prefixes_ok:
        text += "\nПрефиксы e5 не сошлись с `query: ` / `passage: `.\n"
    else:
        text += "\nПрефиксы e5 проверены без весов: запрос начинается с `query: `, документ с `passage: `.\n"
    out.write_text(text, encoding="utf-8")
    detail_path = out.with_name("precision_live_rankings.json")
    detail_path.write_text(
        json.dumps({"chosen": chosen_key, "index": catalog, "runs": saved}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(text)


if __name__ == "__main__":
    main()
