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
import shutil
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
P95_MAX_S = 1.5

ROOT = Path(__file__).resolve().parents[2]
QUERY_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_queries.json"
REPORT_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_report.md"

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
    if query.get("family") == "rules":
        return chunk.corpus == "rules" and any(
            item["part_key"] == chunk.metadata.get("part_key") for item in query["relevant"]
        )
    family_ids = {item["source_id"] for item in query["relevant"] if item.get("source_id")}
    return chunk.source_id in family_ids


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
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Метрики, задержка и top-8 по запросам. Холодная загрузка весов не входит."""
    indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    labeled_by_query = [sum(1 for chunk in indexed if _is_relevant(chunk, query)) for query in queries]
    kwargs = {
        "rerank": rerank,
        "rrf_k": rrf_k,
        "bm25_scope": bm25_scope,
        "rerank_pool": rerank_pool,
    }
    if rrf_weights is not None:
        kwargs["rrf_weights"] = rrf_weights
    engine.retrieve(queries[0]["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
    precisions: list[float] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    ceilings: list[float] = []
    hit5 = 0
    hit8 = 0
    latencies: list[float] = []
    details: list[dict[str, Any]] = []
    for query, labeled in zip(queries, labeled_by_query, strict=True):
        started = time.perf_counter()
        found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
        latencies.append(time.perf_counter() - started)
        flags = [_is_relevant(chunk, query) for chunk in found]
        matched = sum(1 for flag in flags if flag)
        precisions.append(matched / 8)
        recalls.append(matched / labeled if labeled else 0.0)
        ndcgs.append(_ndcg_at_8(flags, labeled))
        ceilings.append(min(8, labeled) / 8)
        hit_at_5 = any(flags[:5])
        if hit_at_5:
            hit5 += 1
        if matched:
            hit8 += 1
        details.append(
            {
                "id": query["id"],
                "hit5": hit_at_5,
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
    metrics = {
        "hit_at_5": hit5 / count,
        "ndcg_at_8": sum(ndcgs) / count,
        "precision_at_8": sum(precisions) / count,
        "recall_at_8": sum(recalls) / count,
        "hit_at_8": hit8 / count,
        "p50_s": _percentile(latencies, 50),
        "p95_s": _percentile(latencies, 95),
        "ceiling": sum(ceilings) / count,
    }
    return metrics, details


def _passes(metrics: dict[str, float]) -> bool:
    return metrics["hit_at_5"] >= HIT_AT_5_MIN and metrics["ndcg_at_8"] >= NDCG_AT_8_MIN


def _rank_key(row: tuple[str, str, dict[str, float]]) -> tuple[float, float, float]:
    return (row[2]["ndcg_at_8"], row[2]["hit_at_5"], -row[2]["p95_s"])


def _is_rerank_key(key: str) -> bool:
    return "+jina+" in key or "+bge-m3+" in key


def choose_default(runs: list[tuple[str, str, dict[str, float]]]) -> tuple[str, str]:
    """Лучший по nDCG@8 среди p95 ≤ 1.5 с.

    Если реранкер не поднимает nDCG выше гибрида e5-large, дефолт — этот гибрид
    без реранкера, даже когда гибрид e5-small набрал больше.
    Если ни один вариант не уложился в p95, ключ пустой и текст говорит «нет дефолта».
    """
    eligible = [row for row in runs if row[2]["p95_s"] <= P95_MAX_S]
    if not eligible:
        return "", f"Нет дефолта: ни один вариант не уложился в p95 ≤ {P95_MAX_S:.1f} с."
    pool = eligible
    best = max(pool, key=_rank_key)
    large = [row for row in pool if row[0].startswith("e5-large+k")]
    best_large = max(large, key=_rank_key) if large else None
    rerank_helps = (
        best_large is not None
        and _is_rerank_key(best[0])
        and best[2]["ndcg_at_8"] > best_large[2]["ndcg_at_8"]
    )
    if rerank_helps or best_large is None:
        chosen = best
        why = (
            f"Среди вариантов с p95 ≤ {P95_MAX_S:.1f} с лучший nDCG@8 у «{chosen[1]}»: "
            f"{chosen[2]['ndcg_at_8']:.4f}, hit@5={chosen[2]['hit_at_5']:.4f}, "
            f"p95={chosen[2]['p95_s']:.3f} с."
        )
    else:
        chosen = best_large
        why = (
            f"Реранкер не поднимает nDCG@8 выше гибрида e5-large. Дефолт — «{chosen[1]}»: "
            f"nDCG@8={chosen[2]['ndcg_at_8']:.4f}, hit@5={chosen[2]['hit_at_5']:.4f}, "
            f"p95={chosen[2]['p95_s']:.3f} с."
        )
        if best[0] != chosen[0]:
            why += (
                f" Глобальный максимум nDCG среди p95 ≤ {P95_MAX_S:.1f} с — «{best[1]}» "
                f"({best[2]['ndcg_at_8']:.4f}); он не выбран, потому что реранкер не помогает."
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
        "Порог precision@8 ≥ 0.8 снят. Приёмка на этих 50 перефразах: "
        f"**hit@5 ≥ {HIT_AT_5_MIN}** и **nDCG@8 ≥ {NDCG_AT_8_MIN}**. "
        "precision@8, recall@8 и hit@8 посчитаны для сведения.",
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
        "| Вариант | hit@5 | nDCG@8 | precision@8 | recall@8 | hit@8 | p50, с | p95, с | приёмка |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for _key, label, metrics in rows:
        mark = "да" if _passes(metrics) else "нет"
        lines.append(
            f"| {label} | {metrics['hit_at_5']:.4f} | {metrics['ndcg_at_8']:.4f} | "
            f"{metrics['precision_at_8']:.4f} | {metrics['recall_at_8']:.4f} | "
            f"{metrics['hit_at_8']:.4f} | {metrics['p50_s']:.3f} | {metrics['p95_s']:.3f} | {mark} |"
        )
    matched = next(((key, label, metrics) for key, label, metrics in rows if key == chosen_key), None)
    ceiling = rows[0][2]["ceiling"] if rows else 0.0
    if matched is None:
        default_lines = [
            "",
            f"Потолок precision@8 по разметке: **{ceiling:.4f}**.",
            "",
            f"Дефолт: **нет дефолта**. {why}",
            "",
        ]
    else:
        key, chosen_label, chosen = matched
        default_lines = [
            "",
            f"Потолок precision@8 по разметке: **{ceiling:.4f}**.",
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
    queries = json.loads(QUERY_PATH.read_text(encoding="utf-8"))
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
        chosen_key, why = choose_default(runs)
        misses = _miss_lines(queries, saved)
        text = render_report(runs, chosen_key=chosen_key, why=why, misses=misses)
        text += (
            "\nЧанкование промптов: `split_prompt(max_parts=3)` по PLAN §5.2. "
            "Таблица из 32 вариантов снята при `max_parts=8` и этот индекс не описывает. "
            "Разметка и live-29 не менялись.\n"
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    parser.add_argument("--pair", action="store_true", help="только дефолт bge-m3 и гибрид без реранкера")
    args = parser.parse_args()
    if args.pair:
        run_profile_pair(args.out)
        return
    queries = build_queries()
    QUERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUERY_PATH.write_text(json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    chosen_key, why = choose_default(runs)
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
