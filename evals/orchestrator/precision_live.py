"""Контур B: hit@5 и nDCG@8 на живых корпусах, четыре локальных варианта.

Не для CI. Запуск качает веса в стандартный кэш Hugging Face
(``~/.cache/huggingface`` или ``HF_HOME`` / ``FASTEMBED_CACHE_PATH``), не в ``data/``.
Сеть — только Hugging Face за весами. Провайдеры вроде 52pay не вызываются.

Приёмка: hit@5 ≥ 0.9 и nDCG@8 ≥ 0.7. precision@8, recall@8 и hit@8 — для сведения.

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
import tempfile
import time
from pathlib import Path
from typing import Any

from app.orchestrator.rag.embedder import build_embedder
from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.profile import CONTOUR_B_EMBEDDER, CONTOUR_B_RERANKER
from app.orchestrator.rag.rerank import Reranker, build_reranker

HIT_AT_5_MIN = 0.9
NDCG_AT_8_MIN = 0.7
LARGE_RERANK_P95_MAX_S = 1.5

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
) -> dict[str, float]:
    """Метрики и задержка retrieve. Холодная загрузка весов в замер не входит."""
    indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    labeled_by_query = [sum(1 for chunk in indexed if _is_relevant(chunk, query)) for query in queries]
    engine.retrieve(
        queries[0]["text"],
        ["rules", "nodes", "prompts"],
        top_k=8,
        rerank=rerank,
    )
    precisions: list[float] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    ceilings: list[float] = []
    hit5 = 0
    hit8 = 0
    latencies: list[float] = []
    for query, labeled in zip(queries, labeled_by_query, strict=True):
        started = time.perf_counter()
        found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, rerank=rerank)
        latencies.append(time.perf_counter() - started)
        flags = [_is_relevant(chunk, query) for chunk in found]
        matched = sum(1 for flag in flags if flag)
        precisions.append(matched / 8)
        recalls.append(matched / labeled if labeled else 0.0)
        ndcgs.append(_ndcg_at_8(flags, labeled))
        ceilings.append(min(8, labeled) / 8)
        if any(flags[:5]):
            hit5 += 1
        if matched:
            hit8 += 1
    count = len(queries)
    return {
        "hit_at_5": hit5 / count,
        "ndcg_at_8": sum(ndcgs) / count,
        "precision_at_8": sum(precisions) / count,
        "recall_at_8": sum(recalls) / count,
        "hit_at_8": hit8 / count,
        "p50_s": _percentile(latencies, 50),
        "p95_s": _percentile(latencies, 95),
        "ceiling": sum(ceilings) / count,
    }


def _passes(metrics: dict[str, float]) -> bool:
    return metrics["hit_at_5"] >= HIT_AT_5_MIN and metrics["ndcg_at_8"] >= NDCG_AT_8_MIN


def choose_default(runs: list[tuple[str, str, dict[str, float]]]) -> tuple[str, str]:
    """Правило куратора. Возвращает (ключ варианта, объяснение)."""
    by_key = {key: metrics for key, _label, metrics in runs}
    large = by_key["e5-large+rerank"]
    if large["p95_s"] > LARGE_RERANK_P95_MAX_S:
        return (
            "e5-small+rerank",
            f"p95 e5-large + реранкер = {large['p95_s']:.3f} с > {LARGE_RERANK_P95_MAX_S:.1f} с, "
            "поэтому дефолт — e5-small + реранкер.",
        )
    ranked = sorted(
        runs,
        key=lambda item: (
            _passes(item[2]),
            item[2]["hit_at_5"],
            item[2]["ndcg_at_8"],
            -item[2]["p95_s"],
        ),
        reverse=True,
    )
    key, label, metrics = ranked[0]
    return (
        key,
        f"p95 e5-large + реранкер = {large['p95_s']:.3f} с ≤ {LARGE_RERANK_P95_MAX_S:.1f} с, "
        f"лучший по hit@5 и nDCG@8 — {label} "
        f"(hit@5={metrics['hit_at_5']:.4f}, nDCG@8={metrics['ndcg_at_8']:.4f}).",
    )


def render_report(rows: list[tuple[str, str, dict[str, float]]], *, chosen_key: str, why: str) -> str:
    lines = [
        "# Контур B: hit@5 и nDCG@8 на живых корпусах",
        "",
        "Порог precision@8 ≥ 0.8 снят. Приёмка на этих 50 перефразах: "
        f"**hit@5 ≥ {HIT_AT_5_MIN}** и **nDCG@8 ≥ {NDCG_AT_8_MIN}**. "
        "precision@8, recall@8 и hit@8 посчитаны для сведения.",
        "",
        "Модели локальные, CPU. e5 — официальный ONNX intfloat (mean pooling). "
        "Реранкер — квантованный ONNX `BAAI/bge-reranker-base` (экспорт Xenova, "
        "тот же cross-encoder, что в каталоге FastEmbed), поверх топ-30 гибрида RRF k=60, веса 1/1. "
        "CI этот прогон не запускает: hashing, без реранкера, веса не качаются. "
        "Кэш весов — стандартный каталог Hugging Face, не `data/`. "
        "Живых вызовов провайдера нет.",
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
    chosen_label = next(label for key, label, _metrics in rows if key == chosen_key)
    chosen = next(metrics for key, _label, metrics in rows if key == chosen_key)
    lines.extend(
        [
            "",
            f"Потолок precision@8 по разметке: **{rows[0][2]['ceiling']:.4f}**.",
            "",
            f"Дефолт контура B: **{chosen_label}** (ключ `{chosen_key}`). {why} "
            f"В `app/orchestrator/rag/profile.py` для явного включения записано "
            f"embedder `{CONTOUR_B_EMBEDDER}`, реранкер `{CONTOUR_B_RERANKER}`.",
            "",
            "Критерии выбранного дефолта: "
            f"hit@5 = {chosen['hit_at_5']:.4f} (порог {HIT_AT_5_MIN}), "
            f"nDCG@8 = {chosen['ndcg_at_8']:.4f} (порог {NDCG_AT_8_MIN}). "
            + ("Оба порога взяты." if _passes(chosen) else "Пороги не взяты."),
            "",
            "Гибрид по умолчанию в коде ретривера — RRF k=60, веса 1/1. "
            "Реранкер опционален: `RagEngine(reranker=None)` и `retrieve(..., rerank=False)`. "
            "`get_engine()` реранкер не включает и остаётся на hashing.",
            "",
        ]
    )
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    queries = build_queries()
    QUERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUERY_PATH.write_text(json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Не data/orch_rag.db рантайма: временный каталог вне data/. Имя файла то же,
    # потому что писатель принимает только orch_rag.db.
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-live-"))
    reranker = build_reranker("bge")
    runs: list[tuple[str, str, dict[str, float]]] = []
    blocks = (
        (
            "e5-small",
            (
                (False, "e5-small+hybrid", "e5-small + гибрид"),
                (True, "e5-small+rerank", "e5-small + гибрид + реранкер"),
            ),
        ),
        (
            "e5-large",
            (
                (False, "e5-large+hybrid", "e5-large + гибрид"),
                (True, "e5-large+rerank", "e5-large + гибрид + реранкер"),
            ),
        ),
    )
    for embedder_kind, modes in blocks:
        embedder = build_embedder(embedder_kind)
        engine = _engine(temp_dir / "orch_rag.db", embedder, reranker)
        try:
            engine.reindex(["rules", "nodes", "prompts"])
            for use_rerank, key, label in modes:
                metrics = measure(queries, engine, rerank=use_rerank)
                runs.append((key, label, metrics))
                print(label, metrics, flush=True)
        finally:
            engine.close()
            del engine, embedder
            gc.collect()
    chosen_key, why = choose_default(runs)
    text = render_report(runs, chosen_key=chosen_key, why=why)
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
