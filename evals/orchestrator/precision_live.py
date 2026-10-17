"""Контур B: precision@8 на живых корпусах и модели multilingual-e5-small.

Не для CI. Запуск качает веса в стандартный кэш Hugging Face
(``~/.cache/huggingface`` или ``HF_HOME`` / ``FASTEMBED_CACHE_PATH``), не в ``data/``.

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
import json
import tempfile
from pathlib import Path
from typing import Any

from app.orchestrator.rag.embedder import build_embedder
from app.orchestrator.rag.engine import RagEngine

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


def precision_at_8(queries: list[dict[str, Any]], engine: RagEngine, **kwargs: Any) -> dict[str, float]:
    indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
    for chunk in indexed:
        chunk.metadata.pop("_embedding", None)
    scores: list[float] = []
    hits = 0
    recalls: list[float] = []
    ceilings: list[float] = []
    for query in queries:
        found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
        matched = sum(1 for chunk in found if _is_relevant(chunk, query))
        labeled = sum(1 for chunk in indexed if _is_relevant(chunk, query))
        scores.append(matched / 8)
        recalls.append(matched / labeled if labeled else 0.0)
        ceilings.append(min(8, labeled) / 8)
        if matched:
            hits += 1
    return {
        "precision_at_8": sum(scores) / len(scores),
        "recall_at_8": sum(recalls) / len(recalls),
        "hit_at_8": hits / len(queries),
        "ceiling": sum(ceilings) / len(ceilings),
    }


def render_report(rows: list[tuple[str, dict[str, float]]], *, note: str) -> str:
    lines = [
        "# Контур B: precision@8 на живых корпусах",
        "",
        "Модель: `intfloat/multilingual-e5-small` (официальный ONNX, mean pooling).",
        "В каталоге FastEmbed 0.3–0.9 есть только `multilingual-e5-large`, не small из плана §5,",
        "поэтому контур B грузит small напрямую. CI этот прогон не запускает и веса не качает.",
        "Кэш весов — стандартный каталог Hugging Face, не `data/`.",
        "",
        "Корпуса: правила `docs/fable/user_rules.md`; ноды `NODE_GROUPS`",
        "(каталога `templates/node_groups` на этом main нет); промпты этих нод",
        "из `templates/excel_gpt_agents` и `prompts/scene_design`.",
        "",
        "50 запросов — перефразы без имён нод, без `R-*` и без дословных заголовков правил.",
        "42 запроса: релевантен любой чанк двух нод семейства (карточка и секции промпта).",
        "8 запросов: одно конкретное правило. Потолок — min(8, число таких чанков) / 8.",
        "",
        "Определение как в плане: macro `|релевантные ∩ top-8| / 8`. Порог релиза 0.8.",
        "",
        note,
        "",
        "| Прогон | precision@8 | recall@8 | hit@8 |",
        "|---|---:|---:|---:|",
    ]
    for name, metrics in rows:
        lines.append(
            f"| {name} | {metrics['precision_at_8']:.4f} | {metrics['recall_at_8']:.4f} | {metrics['hit_at_8']:.4f} |"
        )
    lines.append("")
    lines.append(f"Потолок разметки: **{rows[0][1]['ceiling']:.4f}**.")
    lines.append("")
    lines.append(
        "Нарезка промпта — до 8 секций `##`, в карточку ноды добавлено начало промпта, "
        "в каждый кусок промпта — человеческое название ноды. Перебор k и весов RRF "
        "порог 0.8 не взял: у соседних агентов scene_design косинус e5-small почти одинаковый, "
        "в top-8 стабильно попадают чужие секции. hit@8 при этом высокий: хотя бы один свой чанк "
        "обычно есть. Дефолт гибрида оставлен k=60, веса 1/1."
    )
    lines.append("")
    return "\n".join(lines)


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
    engine = RagEngine(
        db_path=temp_dir / "orch_rag.db",
        embedder=build_embedder("e5-small"),
        rules_path=ROOT / "docs" / "fable" / "user_rules.md",
        repo_root=ROOT,
        scan_git=True,
        project_db=None,
    )
    try:
        engine.reindex(["rules", "nodes", "prompts"])
        runs: list[tuple[str, dict[str, float]]] = []
        runs.append(("гибрид RRF k=60, веса 1/1", precision_at_8(queries, engine)))
        runs.append(("только BM25", precision_at_8(queries, engine, channel="bm25")))
        runs.append(("только вектор", precision_at_8(queries, engine, channel="vector")))
        best_name = "гибрид RRF k=60, веса 1/1"
        best = runs[0][1]
        note = "Веса RRF по умолчанию не менялись: равные 1 и 1, k=60."
        if best["precision_at_8"] < 0.8:
            for lexical, vector, k in ((0.5, 1.5, 60), (0.25, 1.75, 60), (1.0, 2.0, 40), (0.5, 1.5, 20)):
                metrics = precision_at_8(
                    queries,
                    engine,
                    rrf_k=k,
                    rrf_weights=(lexical, vector),
                )
                label = f"гибрид k={k}, веса лексика/вектор {lexical}/{vector}"
                runs.append((label, metrics))
                if metrics["precision_at_8"] > best["precision_at_8"]:
                    best = metrics
                    best_name = label
            note = (
                "Равные веса ниже 0.8, перебран k и веса лексика/вектор. "
                f"Лучший прогон в этой таблице: {best_name} = {best['precision_at_8']:.4f}. "
                "Дефолт ретривера в коде оставлен k=60 и веса 1/1: закрытый CI-набор на них "
                "уже посчитан, а смена дефолта без повторной ручной разметки Фейбла не утверждалась."
            )
        text = render_report(runs, note=note)
        args.out.write_text(text, encoding="utf-8")
        print(text)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
