"""Явный профиль контура B. Сам по себе веса не грузит.

``get_engine()`` без профиля и CI остаются на hashing без реранкера.
Включение:

- ``ORCH_RAG_PROFILE=contour-b`` или ``get_engine(profile="contour-b")``
  строит embedder и реранкер и кладёт k, веса, scope и пул в ``retrieve``.
- ``ORCH_RAG_RERANK=0`` или ``retrieve(..., rerank=False)`` выключает реранкер.
- После смены переменных нужен ``reset_engine()``: синглтон уже собранный движок не перечитывает.

Ручная сборка без синглтона::

    RagEngine(
        embedder=build_embedder(CONTOUR_B_EMBEDDER),
        reranker=(
            build_reranker(CONTOUR_B_RERANKER, max_length=CONTOUR_B_RERANK_MAX_LENGTH)
            if CONTOUR_B_RERANKER != "none"
            else None
        ),
    )
    engine.retrieve(
        query,
        rrf_k=CONTOUR_B_RRF_K,
        rrf_weights=(CONTOUR_B_RRF_LEXICAL, CONTOUR_B_RRF_VECTOR),
        bm25_scope=CONTOUR_B_BM25_SCOPE,
        rerank_pool=CONTOUR_B_RERANK_POOL,
        aggregate=CONTOUR_B_AGGREGATE,
        rerank=False,  # гибрид без реранкера
    )

Дефолт — вариант C (8 частей, метки s0–s7) с bge-m3.
R5 по nDCG@8 на test взят. hit@5 0.9 на test не взят.
Подбор на нечётных query_id (dev), до него сплит зафиксирован.
Порог nDCG@8 0.65 не снижался: это приёмка R5.

На dev (нечётные id) реранкер не заменяет список: он сливается с BM25 через RRF.
Лучшая такая смесь — веса BM25/реранкер 0.5/1.5, защита топ-1 BM25 выключена,
family_boost 1. Более лёгкие веса BM25 и равные 1/1 дали меньший nDCG.
hit@5 этой смеси на dev 0.88, порог 0.9 не взят; замена списка реранкером
на dev выше, но в профиль не входит.
Dev-плюс (все 50, fp32, 1 поток, сид 0), до holdout:
``prompt_pool`` hit@5 0.8400, nDCG@8 0.7051, assemble 0.5000, world 0.6667.
``none`` hit@5 0.9400, nDCG@8 0.6119, assemble 0.6667, world 0.8333.
В профиль входит ``none``: на dev-плюс это единственный из двух, где hit@5 ≥ 0.9
и assemble/world выше. nDCG@8 этого варианта на dev-плюс 0.6119, порог 0.65
не снижался. Holdout в выборе не участвовал.
Dev-70 (live-01..50 и hold-01..20) через choose_default выбрал ``expand_tail``
и слияние BM25/реранкер 0.25/1.5 при RRF k=20: hit@5 0.9286, nDCG@8 0.7007.
Top-5 как у ``none``, места 6–8 — соседние части тех же промптов.
Порог nDCG@8 0.65 не снижался. Флаг эксперимента снят. Holdout-3 в выборе не участвовал.
Dev-v2 (те же 70 плюс d2-01..30) оставил expand_tail, слияние 0.25/1.5 и k=20
и включил защиту топ-1 BM25 с порогом 2: hit@5 0.9000, nDCG@8 0.6711.
Срез dev-70 у этого варианта 0.9286 / 0.7007, как у прежнего дефолта.
Лексика/вектор остаются 0.5/1.5. BM25 full, реранк топ-12, family_boost 1, 256 токенов.
Три холодных p95 прод-пути на dev-70, кэш сброшен, реранкер top-12:
0.904 / 1.053 / 0.956 с, каждый ≤ 1.2. Это не p95 fp32-эвала.
Детерминированный перемер — три прохода, ``intra_op=1``, ``inter_op=1``, сид 0.
Реранкер эвалов — fp32-граф той же bge-m3: квантованный граф не бит-стабилен.
Три прохода fp32 совпали. test: hit@5 0.8000, nDCG@8 0.6768.
Порог 0.65 на test взят. hit@5 0.9 на test не взят.
p95 эвала выше 1.2 с из‑за одного потока и fp32. p95 прод-пути — в отчёте.
``get_engine(profile="contour-b")`` грузит квантованный файл.
p95 этого пути в код не зашивается: его пишет замер в артефакт.
``ORCH_RAG_RERANK=0`` или ``retrieve(..., rerank=False)`` оставляют гибрид.
Цифры — в ``evals/orchestrator/datasets/precision_live_report.md``.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B. get_engine() читает их только при profile=contour-b.
CONTOUR_B_EMBEDDER = "e5-large"
CONTOUR_B_RERANKER = "bge-m3"
CONTOUR_B_RRF_K = 20
CONTOUR_B_RRF_LEXICAL = 0.5
CONTOUR_B_RRF_VECTOR = 1.5
CONTOUR_B_BM25_SCOPE = "full"
CONTOUR_B_RERANK_POOL = 12
CONTOUR_B_AGGREGATE = "expand_tail"
CONTOUR_B_RERANK_MAX_LENGTH = 256
# Слияние реранкера с BM25. replace в профиль не входит.
CONTOUR_B_RERANK_FUSION = "rrf"
CONTOUR_B_FUSION_BM25 = 0.25
CONTOUR_B_FUSION_RERANK = 1.5
CONTOUR_B_BM25_PROTECT = 2.0
CONTOUR_B_FAMILY_BOOST = 1.0
# Dev-v2: expand_tail + слияние 0.25/1.5, k=20, защита топ-1 BM25 = 2.
# hit@5 0.9000, nDCG@8 0.6711. На dev-70 0.9286 / 0.7007. Порог 0.65 не снижался.
CONTOUR_B_EXPERIMENTAL = False
CONTOUR_B_R5_NOTE = ""
