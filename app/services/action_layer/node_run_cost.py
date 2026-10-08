"""Верхняя оценка прогона текстовой ноды группы script_frames_qc (Vibecode).

Не калибровалась: фактический расход Vibecode ключа никто не сверял с балансом, поэтому
вся оценка названа верхней и построена на допущениях, перечисленных ниже.

Откуда берутся числа (по коду, не по замерам):
- Пачки: ``apply_ops_batches.split_vo_units`` по 8 ячеек закадра (тяжёлая ячейка идёт соло),
  те же функции вызываются на реальных данных проекта.
- Запросов на пачку: перебор худшего исхода ``_run_adaptive`` (1 -> 2 -> 4, повтор
  недостающих кадров, повтор при недоборе на листе); каждый вызов пачки - до двух запросов
  (повтор JSON внутри ``run_operator_api``).
- Токены входа: размер файла промта ноды + сопроводительный текст проекта + подсказки/футер
  (допуск) + самый большой db_frames_batch*.json; символов на токен взято 2.0 (кириллица, JSON).
- Токены выхода: Vibecode-запрос уходит без max_tokens, поэтому потолок ВЫХОДА допущен:
  запас на рассуждение + допуск на ячейку по виду ноды.
Допущения оценки, которые код не гарантирует: неуспешные попытки транспорта (таймаут, сеть)
не тарифицируются; обрыв-продолжение добавляет только хвост 2 x 2000 токенов входа.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

UNCALIBRATED = "верхняя оценка, не откалибровано"
CHARS_PER_TOKEN = 2.0
PROMPT_MARGIN = 1.2            # промт могут подменить правкой текста; запас на файл
HINT_CHARS = 1500              # _APPLY_OPS_HINT и блок «DB SoT» в сопроводительном тексте
FOOTER_CHARS = 8000            # _batch_footer пачки
SYSTEM_CHARS = 6000            # системная часть build_messages
MAPPING_LINE_CHARS = 60        # «кадр N = uuid» на каждый кадр с uuid
NEIGHBOR_HINT_CHARS = 300      # подсказки соседей по площадке на ячейку
CONTINUE_INPUT_TOKENS = 2 * 2000
REASONING_ALLOWANCE_TOKENS = 8000
UNITS_PER_BATCH = 8            # SCRIPT_FRAMES_QC_UNITS_PER_BATCH

# вид ноды: (суффикс, имя промта, допуск символов выхода на ячейку, нужен ли план проекта)
NODE_KINDS: dict[str, tuple[str, int, bool]] = {
    "_fw_script": ("scene_skeleton_agent", 1500, True),
    "_fw_action": ("action", 2000, False),
    "_fw_shots": ("scenes_to_frames_ru", 4700, False),
    "_fw_qc": ("shots_qc_ru", 2000, False),
}


def node_kind(node_key: str) -> str | None:
    key = str(node_key or "")
    for suffix in NODE_KINDS:
        if key.endswith(suffix):
            return suffix
    return None


def is_report_node(node_key: str) -> bool:
    key = str(node_key or "")
    return key.endswith("_fw_report") or "_fw_report_" in key


def expected_prompt(node_key: str) -> str | None:
    kind = node_kind(node_key)
    return NODE_KINDS[kind][0] if kind else None


@lru_cache(maxsize=None)
def _chunk_calls(m: int, level: int, under: int) -> int:
    """Худшее число вызовов _one_chunk при _run_adaptive(пачка из m кадров, level, under)."""
    from app.services.adaptive_llm_batches import next_split_level, split_in_half

    nxt = next_split_level(level)

    def halves(size: int) -> list[int]:
        return [len(p) for p in split_in_half(list(range(size)))]

    best = 1                                              # ответ полный
    if nxt is None or m <= 1:                             # исключение
        exc = 1 + (_chunk_calls(m, level, under - 1) if under > 0 else 0)
    else:
        exc = 1 + sum(_chunk_calls(h, nxt, 2) for h in halves(m))
    best = max(best, exc)
    if m > 1:                                             # недобор: один кадр
        best = max(best, 1 + _chunk_calls(1, nxt or level, 0))
    if m <= 1:                                            # недобор единственного кадра
        return max(best, 1)
    if under > 0:                                         # недобор нескольких: повтор тех же
        best = max(best, 1 + _chunk_calls(m, level, 0))
    elif nxt is not None:                                 # дальше делится пополам
        best = max(best, 1 + sum(_chunk_calls(h, nxt, 2) for h in halves(m)))
    return best


def worst_requests_for_pack(m: int) -> int:
    """Запросов к модели на пачку из m кадров в худшем исходе (до 2 запросов на вызов)."""
    return 2 * _chunk_calls(max(int(m), 1), 1, 2)


@dataclass(frozen=True)
class RunBasis:
    node_kind: str
    packs: int
    units: int
    rows: int
    min_requests: int
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    parts: dict = field(default_factory=dict)

    def declare(self) -> dict:
        return {"min_llm_requests": self.min_requests, "max_llm_requests": self.max_requests,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens}


def _frame_objects(frames: list[dict]) -> list[Any]:
    out = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        out.append(SimpleNamespace(
            uuid=fr.get("uuid"), number=fr.get("number"),
            voiceover_text=fr.get("voiceover_text"), meaning=fr.get("meaning"),
            image_prompt=fr.get("image_prompt"), animation_prompt=fr.get("animation_prompt"),
            attrs=fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}))
    return out


def compute_run_basis(*, node_key: str, frames: list[dict], entities: list[dict],
                      prompt_chars: int, user_text_chars: int,
                      project_text_chars: int) -> RunBasis | None:
    """Базис оценки по реальным данным проекта; None, если запускать нечего."""
    from app.services.apply_ops_batches import select_frames_for_batches, split_vo_units
    from app.services.db_frames_context import (
        build_excel_gpt_db_context, collapse_script_writer_frames)
    from app.services.vo_shot_expand import is_shot_child

    suffix = node_kind(node_key)
    if suffix is None:
        raise ValueError("нода не из группы script_frames_qc")
    _name, out_chars_per_cell, needs_plan = NODE_KINDS[suffix]

    objs = _frame_objects(frames)
    collapsed = collapse_script_writer_frames(objs)
    parent_ids = {str(r.get("uuid") or "").strip() for r in collapsed
                  if str(r.get("uuid") or "").strip()}
    gpt_frames = [fr for fr in objs if str(fr.uuid or "") in parent_ids]
    if suffix == "_fw_qc":
        gpt_frames = [fr for fr in gpt_frames if not is_shot_child(fr)]
    # Без strip_prompts и без снятия выхода ноды: данных не меньше, чем при реальном прогоне.
    ctx = build_excel_gpt_db_context(project_id=0, slug="x", frames=gpt_frames,
                                     characters=list(entities or []), full_vo=True)
    overlay = {str(r.get("uuid") or "").strip(): r for r in collapsed}
    for row in ctx["frames"]:
        src = overlay.get(str(row.get("uuid") or "").strip())
        if src and src.get("voiceover_text"):
            row["voiceover_text"] = src["voiceover_text"]

    pending = select_frames_for_batches(ctx["frames"], dense=True, force_full=True)
    if not pending:
        return None
    packs = split_vo_units(pending, UNITS_PER_BATCH)
    head = {k: v for k, v in ctx.items() if k != "frames"}

    def pack_json_chars(chunk: list[dict]) -> int:
        batch = {**head, "frames": chunk, "batch": {"index": 99, "split_level": 1,
                                                   "frames": len(chunk)}}
        return len(json.dumps(batch, ensure_ascii=False, indent=2))

    max_json = max(pack_json_chars(p) for p in packs)
    max_rows = max(len(p) for p in packs)
    units = len({str(r.get("uuid") or "") for r in pending})
    uuid_frames = sum(1 for fr in objs if fr.uuid)

    fixed_chars = (int(prompt_chars * PROMPT_MARGIN) + int(user_text_chars) + HINT_CHARS
                   + MAPPING_LINE_CHARS * uuid_frames + NEIGHBOR_HINT_CHARS * units
                   + FOOTER_CHARS + SYSTEM_CHARS + (int(project_text_chars) if needs_plan else 0))
    in_tokens = math.ceil((fixed_chars + max_json) / CHARS_PER_TOKEN) + CONTINUE_INPUT_TOKENS
    out_tokens = REASONING_ALLOWANCE_TOKENS + math.ceil(out_chars_per_cell * max_rows
                                                        / CHARS_PER_TOKEN)
    requests = sum(worst_requests_for_pack(len(p)) for p in packs)
    return RunBasis(
        node_kind=suffix, packs=len(packs), units=units, rows=len(pending),
        min_requests=len(packs), max_requests=requests,
        max_input_tokens=in_tokens, max_output_tokens=out_tokens,
        parts={"prompt_chars": prompt_chars, "user_text_chars": user_text_chars,
               "project_text_chars": project_text_chars if needs_plan else 0,
               "max_pack_json_chars": max_json, "max_pack_rows": max_rows,
               "worst_requests_per_full_pack": worst_requests_for_pack(max_rows)})


# --- цена ---------------------------------------------------------------------

def text_model_id() -> str:
    """Модель, как её выбирает llm_override для нод группы (только Vibecode)."""
    from app.services.text_llm_catalog import (
        catalog_api_model, catalog_item, resolve_active_model_id)
    from app.settings import settings

    active = resolve_active_model_id(settings)
    item = catalog_item(active) or {}
    if str(item.get("provider") or "").strip().lower() != "vibecode":
        active = "gpt-5.6-sol-vibecode"
    return catalog_api_model(active, default="gpt-5.6-sol")


def price_request_credits(in_tokens: int, out_tokens: int) -> tuple[float, str] | None:
    """Кредитов за один запрос по прайсу Vibecode x наценка каталога; None = цены нет."""
    from app.services.api_tracker_hook import vibecode_cost
    from app.services.kie_catalog import CREDIT_USD
    from app.services.vibecode_catalog import PRICE_MARKUP

    model = text_model_id()
    usd = vibecode_cost(model, int(in_tokens), int(out_tokens))
    if usd is None:
        return None
    credits = usd * PRICE_MARKUP / CREDIT_USD
    return credits, f"{model}, прайс Vibecode x{PRICE_MARKUP:g} (наценка каталога, не откалибрована)"


# --- шаги конвейера проекта (D08): оценка запуска img/video/audio/music/... ----------------------
#
# Откуда числа: количество генераций берётся из доски монтажа (кадры и второй шот), цена за генерацию -
# из ``kie_catalog`` по тем же значениям, что уходят в запрос (``builtin_montage_more._kie_unit``).
# Что оценка НЕ знает: Outsee и прочие модели без тарифа в репозитории, озвучка (по символам),
# музыка, LLM-шаги промтов. Для них код оценки неизвестен, и запуск возможен только с потолком
# ``ceiling_credits``, который назвал оператор (потолок входит в хэш плана и резервируется в бюджете).
# Шаг ``assemble`` локальный (ffmpeg и локальный ASR), платных вызовов провайдеров нет
# (``node_capabilities.MEDIA_RECOVERY_DECISIONS['assemble']['cost_class']``).

MEDIA_UNCALIBRATED = "оценка по каталогу тарифов, расход шага не сверялся с балансом провайдера"

# код шага -> (вид оплаты, название). Публикации нет: она остаётся действием человека.
MEDIA_STEPS: dict[str, tuple[str, str]] = {
    "img_pr": ("llm", "Промты картинок"),
    "img": ("image", "Картинки"),
    "anim_pr": ("llm", "Промты анимации"),
    "video": ("video", "Видео"),
    "audio": ("audio", "Озвучка"),
    "music": ("music", "Музыка"),
    "assemble": ("local", "Финальная сборка"),
}
# Сколько платных попыток на одну картинку/клип закладывает оценка (как у регенерации на доске).
IMAGE_STEP_ATTEMPTS = 3
VIDEO_STEP_ATTEMPTS = 4


def media_units(board_frames: list[dict]) -> dict[str, int]:
    """Верхняя граница числа генераций шага: по картинке/клипу на шот (второй шот = ещё одна)."""
    frames = [f for f in board_frames or [] if isinstance(f, dict)]
    shots = sum(2 if f.get("has_shot2") else 1 for f in frames)
    return {"frames": len(frames), "images": shots, "videos": shots}


def media_step_kind(step_code: str) -> str | None:
    meta = MEDIA_STEPS.get(str(step_code or ""))
    return meta[0] if meta else None


def media_code_estimate(step_code: str, *, units: int | None, image_generator: str | None = None,
                        image_resolution: str | None = None, video_generator: str | None = None,
                        video_resolution: str | None = None):
    """Оценка по коду: CostEstimate (известная) либо None с причиной в ``basis`` неизвестной оценки."""
    from app.services.action_layer.builtin_montage_more import (
        _VIDEO_CLIP_SECONDS, _kie_unit)
    from app.services.action_layer.spec import CostEstimate

    kind = media_step_kind(step_code)
    if kind is None:
        return CostEstimate.unknown(f"шаг {step_code!r} не входит в каталог запуска")
    if kind == "local":
        return CostEstimate(0.0, 0.0, "сборка идёт локально (ffmpeg, локальный ASR), "
                                      "платных вызовов провайдеров нет")
    if kind in ("image", "video"):
        if not units or units < 1:
            return CostEstimate.unknown("не заявлено число генераций (count): возьмите его у "
                                        "step.get_run_cost_basis")
        if kind == "image":
            unit, why = _kie_unit("image", image_generator, image_resolution)
            attempts = IMAGE_STEP_ATTEMPTS
        else:
            unit, why = _kie_unit("video", video_generator, video_resolution)
            attempts = VIDEO_STEP_ATTEMPTS
        if unit is None:
            return CostEstimate.unknown(why)
        note = (f"{MEDIA_UNCALIBRATED}: до {units} генераций x {attempts} платные попытки, {why}"
                + (f"; клип допущен {_VIDEO_CLIP_SECONDS} с (шаг длительность не передаёт)"
                   if kind == "video" else ""))
        return CostEstimate(0.0, round(units * attempts * unit, 2), note)
    why = {"llm": "число и размер LLM-вызовов шага из кода не определены",
           "audio": "озвучка тарифицируется по символам, объём и провайдер из кода не определены",
           "music": "трек Suno: провайдер (Kie или браузерный Outsee) выбирается на ходу"}[kind]
    return CostEstimate.unknown(why)


def apply_ceiling(code_estimate, ceiling: float | None):
    """Итоговая оценка с потолком оператора.

    Известная оценка по коду остаётся как есть; потолок ниже её верхней границы - неизвестная
    оценка (запуск не подтвердить). Нет оценки по коду - верхняя граница равна потолку оператора.
    """
    from app.services.action_layer.spec import CostEstimate

    if code_estimate.known:
        if ceiling is not None and ceiling < code_estimate.high:
            return CostEstimate.unknown(
                f"потолок {ceiling:g} кр ниже расчёта {code_estimate.high:g} кр: запуск не подтвердить")
        return code_estimate
    if ceiling is None:
        return code_estimate
    return CostEstimate(
        0.0, round(float(ceiling), 2),
        f"потолок оператора {ceiling:g} кр (цену по коду определить нельзя: {code_estimate.basis}); "
        "потолок резервируется в бюджете слоя, воркер его сам не знает - жёсткий предел расхода "
        "дают лимиты spend_guard и аварийный стоп")
