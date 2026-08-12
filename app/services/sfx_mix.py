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


def collect_sfx_inputs(project: Any) -> list[SfxInput]:
    """SFX-входы проекта для микса (только файлы, реально есть на диске)."""
    from app.services.sfx_gen import load_sfx_files

    out: list[SfxInput] = []
    for rec in load_sfx_files(project):
        try:
            gain = float(rec.get("gain") or 0.5)
            if rec.get("duck"):
                gain *= 0.35  # фоновые звуки под речью — тише
            out.append(
                SfxInput(
                    path=Path(str(rec["path"])),
                    t_start=max(0.0, float(rec.get("t_start") or 0.0)),
                    gain=min(max(gain, 0.02), 1.0),
                    kind=str(rec.get("kind") or "sfx"),
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
    if dur:
        chains.append(f"[1:a]apad=whole_dur={dur}[vo]")
    else:
        chains.append("[1:a]anull[vo]")
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
