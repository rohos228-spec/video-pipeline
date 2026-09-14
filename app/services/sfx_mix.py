"""Микс звуков сопровождения (SFX) в финальный ролик.

``collect_sfx_inputs`` — сгенерированные sfx-файлы проекта (чекпоинт
``ai_jobs.sfx_files`` от шага sfx_gen) → входы ffmpeg.
``build_mux_audio_args`` — аргументы и filter_complex для микса
VO + BGM + SFX: каждый sfx через ``adelay`` (позиция по метке) и
``volume`` (gain; duck-события тише), всё в ``amix``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class SfxInput:
    path: Path
    t_start: float  # секунды от начала ролика
    gain: float
    kind: str
    frame_number: int | None = None


def collect_sfx_inputs(
    project: Any,
    video_frame_starts: dict[int, float] | None = None,
) -> list[SfxInput]:
    """SFX-входы проекта для микса (только файлы, реально есть на диске).

    Если передан video_frame_starts, метки t_start динамически перепривязываются
    к фактическому началу кадра на видео-таймлайне (с сохранением внутрикадрового
    смещения offset_in_frame), чтобы исключить рассинхронизацию (lag) из-за
    выпадения пауз речи или подрезки видео.
    """
    import json
    from app.services.sfx_gen import load_sfx_files

    planned_starts: dict[int, float] = {}
    if video_frame_starts:
        data_dir = getattr(project, "data_dir", None)
        if data_dir is not None:
            data_path = Path(data_dir)
            audio_path = data_path / "audio"
            if audio_path.is_dir():
                words_files = sorted(
                    audio_path.glob("words_*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if words_files:
                    try:
                        wdata = json.loads(words_files[0].read_text(encoding="utf-8"))
                        for it in wdata.get("frames", []):
                            fn = int(it.get("frame_number", 0))
                            st = float(it.get("start_ts", 0.0))
                            planned_starts[fn] = st
                    except Exception as e:
                        logger.debug("collect_sfx_inputs: ошибка чтения words_*.json: {}", e)

            if not planned_starts:
                plan_path = data_path / "sfx_plan.json"
                if plan_path.is_file():
                    try:
                        pdata = json.loads(plan_path.read_text(encoding="utf-8"))
                        if isinstance(pdata.get("frame_starts"), dict):
                            planned_starts = {int(k): float(v) for k, v in pdata["frame_starts"].items()}
                    except Exception:
                        pass

    out: list[SfxInput] = []
    for rec in load_sfx_files(project):
        try:
            fn = int(rec.get("frame_number") or 0)
            gain = float(rec.get("gain") or 0.5)
            if rec.get("duck"):
                gain *= 0.35  # фоновые звуки под речью — тише

            orig_t = max(0.0, float(rec.get("t_start") or 0.0))
            final_t = orig_t

            if video_frame_starts and fn in video_frame_starts:
                offset = rec.get("offset_in_frame")
                if offset is None:
                    p_start = planned_starts.get(fn)
                    if p_start is not None:
                        offset = max(0.0, orig_t - p_start)
                    else:
                        offset = 0.0
                else:
                    offset = max(0.0, float(offset))

                actual_v_start = video_frame_starts[fn]
                final_t = round(actual_v_start + offset, 3)
                if abs(final_t - orig_t) > 0.05:
                    logger.info(
                        "[#{}] sfx_mix: кадр #{:02d} ({}) синхра t_start: {:.2f}с -> {:.2f}с "
                        "(видео-склейка {:.2f}с + смещение {:.2f}с, устранено запаздывание {:.2f}с)",
                        getattr(project, "id", "?"),
                        fn,
                        rec.get("kind"),
                        orig_t,
                        final_t,
                        actual_v_start,
                        offset,
                        orig_t - final_t,
                    )

            out.append(
                SfxInput(
                    path=Path(str(rec["path"])),
                    t_start=final_t,
                    gain=min(max(gain, 0.02), 1.0),
                    kind=str(rec.get("kind") or "sfx"),
                    frame_number=fn,
                )
            )
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda s: s.t_start)
    return out


def build_mux_audio_args(
    *,
    bgm_path: Path | None,
    bgm_gain: float,
    output_duration: float | None,
    tail: float,
    sfx: list[SfxInput],
    voice_gain: float = 1.0,
) -> tuple[list[str], str | None]:
    """(extra ffmpeg args, filter_complex | None) для микса аудио.

    Вход 0 = видео (concat), вход 1 = озвучка. BGM — stream_loop.
    SFX — обычные входы далее. None → простое маппирование [1:a]
    (ни BGM, ни SFX).
    """
    args: list[str] = []
    chains: list[str] = []
    mix_in: list[str] = []
    next_idx = 2

    dur = f"{output_duration:.3f}" if output_duration is not None else None
    gain_filter = f",volume={voice_gain:.4f}" if abs(voice_gain - 1.0) > 1e-4 else ""
    if dur:
        chains.append(f"[1:a]apad=whole_dur={dur}{gain_filter}[vo]")
    else:
        chains.append(f"[1:a]anull{gain_filter}[vo]")
    mix_in.append("[vo]")

    if bgm_path is not None:
        args.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        trim = f"atrim=0:{dur}," if dur else ""
        chains.append(
            f"[{next_idx}:a]volume={bgm_gain:.4f},{trim}asetpts=PTS-STARTPTS[bgm]"
        )
        mix_in.append("[bgm]")
        next_idx += 1

    for k, s in enumerate(sfx):
        if not s.path.is_file():
            logger.warning("sfx_mix: нет файла {} — пропуск", s.path)
            continue
        args.extend(["-i", str(s.path)])
        ms = max(0, int(round(s.t_start * 1000)))
        chains.append(
            f"[{next_idx}:a]adelay={ms}|{ms},volume={s.gain:.4f}[sf{k}]"
        )
        mix_in.append(f"[sf{k}]")
        next_idx += 1

    if not mix_in or (len(mix_in) == 1 and bgm_path is None and not sfx):
        return [], None

    duration_mode = "longest" if dur else "first"
    filter_complex = (
        ";".join(chains)
        + ";"
        + "".join(mix_in)
        + f"amix=inputs={len(mix_in)}:duration={duration_mode}:"
        "dropout_transition=2:normalize=0[aout]"
    )
    return args, filter_complex
