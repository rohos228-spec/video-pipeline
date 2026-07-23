"""Таймкоды кадров на листе «план» (строка 15): ASR → «0:03.28-0:05.76»."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.services.mapper import FrameTiming, enforce_monotonic_timings, map_frames
from app.services.media_probe import probe_duration
from app.services.whisper import WordTS, load_words_json
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, read_plan_voiceover_cells

R15_VOICE_START_TOLERANCE_S = 1.0

# 0:03.28-0:05.76  (минуты : секунды.сотые)
_TIMECODE_RE = re.compile(r"^(\d+):(\d{2})\.(\d{2})$")
_TIMECODE_LEGACY_RE = re.compile(r"^(\d+):(\d{2})$")
_DASHES = str.maketrans("–—−", "---")


def format_timecode(seconds: float) -> str:
    """Секунды → M:SS.ss без округления до целых."""
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    sec = total - minutes * 60
    return f"{minutes}:{sec:05.2f}"


def format_timecode_range(start: float, end: float) -> str:
    end = max(float(end), float(start) + 0.01)
    return f"{format_timecode(start)}-{format_timecode(end)}"


def normalize_timestamp_label(text: str) -> str:
    """Excel/ручной ввод: unicode-тире, пробелы вокруг «-»."""
    raw = (text or "").strip().translate(_DASHES)
    if not raw:
        return ""
    raw = re.sub(r"\s*-\s*", "-", raw)
    return raw


def _parse_one_timecode(part: str) -> float | None:
    part = normalize_timestamp_label(part)
    m = _TIMECODE_RE.match(part)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 100.0
    m = _TIMECODE_LEGACY_RE.match(part)
    if m:
        return float(int(m.group(1)) * 60 + int(m.group(2)))
    return None


def parse_timecode_range(text: str) -> tuple[float, float] | None:
    raw = normalize_timestamp_label(text)
    if "-" not in raw:
        return None
    start_s, end_s = raw.split("-", 1)
    start = _parse_one_timecode(start_s)
    end = _parse_one_timecode(end_s)
    if start is None or end is None or end <= start:
        return None
    return start, end


def find_words_json(audio_dir: Path) -> Path | None:
    if not audio_dir.is_dir():
        return None
    hits = sorted(audio_dir.glob("words*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


async def compute_frame_timestamp_ranges(
    project,
    frame_numbers: list[int],
    *,
    voice_full_path: Path,
    words_path: Path | None = None,
) -> list[tuple[int, str, float, float]]:
    """ASR + R49 → (frame_number, «M:SS.ss-M:SS.ss», start, end)."""
    audio_dir = project.data_dir / "audio"
    words_file = words_path or find_words_json(audio_dir)
    if words_file is None or not words_file.is_file():
        raise FileNotFoundError(f"нет words*.json в {audio_dir}")

    words = load_words_json(words_file)
    if not words:
        raise RuntimeError(f"пустой {words_file.name}")

    cells = read_plan_voiceover_cells(project, frame_numbers)
    if not any(t.strip() for _, t in cells):
        raise RuntimeError("нет текста в строке 49 листа «план»")

    master = await probe_duration(voice_full_path)
    timings = map_frames(cells, words, audio_duration=master)

    by_num = {t.frame_number: t for t in timings}
    out: list[tuple[int, str, float, float]] = []
    missing: list[int] = []
    for num in frame_numbers:
        t = by_num.get(num)
        if t is None or t.duration <= 0:
            missing.append(num)
            continue
        label = format_timecode_range(t.start_ts, t.end_ts)
        out.append((num, label, t.start_ts, t.end_ts))

    if missing:
        sample = ", ".join(str(n) for n in missing[:8])
        extra = f" (+{len(missing) - 8})" if len(missing) > 8 else ""
        raise RuntimeError(
            f"таймкоды не для всех кадров: нет/битые {len(missing)} "
            f"(напр. {sample}{extra})"
        )
    return out


def clips_from_timestamp_cells(
    cells: list[tuple[int, str]],
    timestamps: list[tuple[int, str]],
    voice_full_path: Path,
    *,
    master: float,
) -> list | None:
    """FrameAudioClip из строки 15 — start/end как в Excel, без подгонки."""
    from app.services.frame_audio import FrameAudioClip

    text_by = dict(cells)
    clips: list = []
    for num, label in timestamps:
        if not (label or "").strip():
            return None
        parsed = parse_timecode_range(label)
        if parsed is None:
            return None
        start, end = parsed
        if end <= start:
            return None
        clips.append(
            FrameAudioClip(
                frame_number=num,
                path=voice_full_path,
                text=text_by.get(num, ""),
                start_ts=round(start, 3),
                end_ts=round(end, 3),
                duration=round(end - start, 3),
            )
        )
    return clips or None


def ts_cells_from_frame_timings(timings: list) -> list[tuple[int, str]]:
    return [
        (t.frame_number, format_timecode_range(t.start_ts, t.end_ts))
        for t in timings
    ]


def clips_from_frame_timings(
    timings: list,
    cells: list[tuple[int, str]],
    voice_full_path: Path,
) -> list:
    """FrameAudioClip из map_frames / ASR — без чтения Excel."""
    from app.services.frame_audio import FrameAudioClip

    text_by = dict(cells)
    return [
        FrameAudioClip(
            frame_number=t.frame_number,
            path=voice_full_path,
            text=text_by.get(t.frame_number, ""),
            start_ts=round(float(t.start_ts), 3),
            end_ts=round(float(t.end_ts), 3),
            duration=round(float(t.duration), 3),
        )
        for t in timings
    ]


def count_parsed_timestamp_cells(
    timestamps: list[tuple[int, str]],
) -> tuple[int, int, list[int]]:
    """(всего непустых, распарсено, номера битых кадров)."""
    filled = [(n, lbl) for n, lbl in timestamps if (lbl or "").strip()]
    bad = [n for n, lbl in filled if parse_timecode_range(lbl) is None]
    return len(filled), len(filled) - len(bad), bad


async def load_assembly_timeline_from_r15(
    project,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    voice_full_path: Path,
) -> tuple[list | None, float | None]:
    """Таймлайн строго из R15. None если строка пустая; иначе clips или RuntimeError."""
    master = await probe_duration(voice_full_path)
    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    filled_n, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
    if filled_n == 0:
        logger.warning(
            "[#{}] R{} пуста в {} — сборка без Excel-таймкодов",
            project.id,
            ts_row,
            project.data_dir / "project.xlsx",
        )
        return None, None

    if bad:
        sample = ", ".join(str(n) for n in bad[:8])
        raise RuntimeError(
            f"лист «план» R{ts_row}: не читаются метки кадров {sample}"
            f"{f' (+{len(bad) - 8})' if len(bad) > 8 else ''} — формат 0:03.28-0:05.76"
        )

    clips = clips_from_timestamp_cells(cells, ts_cells, voice_full_path, master=master)
    if clips is None or len(clips) != len(frame_numbers):
        missing = [
            n for n, lbl in ts_cells if not (lbl or "").strip() or parse_timecode_range(lbl) is None
        ]
        sample = ", ".join(str(n) for n in missing[:8])
        raise RuntimeError(
            f"R{ts_row}: метки только для {len(clips or [])}/{len(frame_numbers)} кадров"
            f"{f' (пропуски: {sample})' if sample else ''}"
        )

    logger.info(
        "[#{}] сборка строго по R{} xlsx ({} кадров, frame1={} … end={:.2f}s)",
        project.id,
        ts_row,
        len(clips),
        ts_cells[0][1] if ts_cells else "?",
        clips[-1].end_ts,
    )
    return clips, master


def write_montage_timeline_audit(
    project,
    *,
    ts_cells: list[tuple[int, str]],
    clips: list,
    xlsx_path: Path,
    ts_row: int,
    source: str = "excel_only",
) -> Path:
    """Сохранить на диск точные метки, которые ушли в ffmpeg — для сверки с Excel."""
    out_dir = project.data_dir / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "montage_timeline_used.json"
    st = xlsx_path.stat() if xlsx_path.is_file() else None
    payload = {
        "source": source,
        "sheet": "план",
        "row": ts_row,
        "xlsx_path": str(xlsx_path),
        "xlsx_exists": xlsx_path.is_file(),
        "xlsx_mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        if st
        else None,
        "xlsx_size": st.st_size if st else None,
        "frames": [
            {
                "frame_number": c.frame_number,
                "excel_label": dict(ts_cells).get(c.frame_number, ""),
                "start_ts": c.start_ts,
                "end_ts": c.end_ts,
                "duration": c.duration,
            }
            for c in clips
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[#{}] montage audit → {}", project.id, out_path)
    return out_path


async def require_assembly_timeline_from_excel(
    project,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    voice_full_path: Path,
    *,
    ts_cells: list[tuple[int, str]] | None = None,
    ts_row: int | None = None,
) -> tuple[list, float, list[tuple[int, str]], int]:
    """Единственный источник таймингов монтажа: строка 15 project.xlsx (всегда с диска)."""
    xlsx_path = project.data_dir / "project.xlsx"
    if not xlsx_path.is_file():
        raise RuntimeError(
            f"[#{project.id}] нет файла {xlsx_path} — положите project.xlsx в папку проекта"
        )

    # Каждый запуск — заново с диска; кэш preflight не подставляем.
    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    st = xlsx_path.stat()
    logger.info(
        "[#{}] Excel R{} fresh read {} (mtime={}, {} bytes)",
        project.id,
        ts_row,
        xlsx_path,
        datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        st.st_size,
    )

    master = await probe_duration(voice_full_path)
    clips = clips_from_timestamp_cells(cells, ts_cells, voice_full_path, master=master)
    if clips is not None and len(clips) == len(frame_numbers):
        logger.info(
            "[#{}] сборка строго по R{} snapshot ({} кадров, frame1={} … end={:.2f}s)",
            project.id,
            ts_row,
            len(clips),
            ts_cells[0][1] if ts_cells else "?",
            clips[-1].end_ts,
        )
    else:
        clips = None
    if clips is None:
        _filled, parsed_n, _bad = count_parsed_timestamp_cells(ts_cells)
        raise RuntimeError(
            f"[#{project.id}] Excel R{ts_row} пуста или не читается "
            f"({parsed_n}/{len(frame_numbers)} меток, файл: {xlsx_path}). "
            "Сохраните Excel, закройте его в Office, проверьте: "
            f"python scripts/check_r15.py {project.id}"
        )

    _assert_clips_match_excel_labels(clips, ts_cells, project.id)

    audit_source = "excel_only"
    if os.environ.get("MONTAGE_SYNC_R15_FROM_ASR", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        ts_cells, clips, sync_mode = await auto_sync_r15_from_voice(
            project,
            frame_numbers=frame_numbers,
            cells=cells,
            clips=clips,
            ts_cells=ts_cells,
            ts_row=ts_row,
            voice_full_path=voice_full_path,
            master=master,
        )
        audit_source = sync_mode

    write_montage_timeline_audit(
        project,
        ts_cells=ts_cells,
        clips=clips,
        xlsx_path=xlsx_path,
        ts_row=ts_row,
        source=audit_source,
    )
    return clips, float(master), ts_cells, ts_row


def _assert_clips_match_excel_labels(
    clips: list,
    ts_cells: list[tuple[int, str]],
    project_id: int,
) -> None:
    """Сборка только если parsed R15 == то, что уйдёт в ffmpeg."""
    labels = dict(ts_cells)
    bad: list[str] = []
    for clip in clips:
        label = labels.get(clip.frame_number, "")
        parsed = parse_timecode_range(label)
        if parsed is None:
            bad.append(f"кадр {clip.frame_number}: битая метка {label!r}")
            continue
        start, end = parsed
        if abs(start - clip.start_ts) > 0.05 or abs(end - clip.end_ts) > 0.05:
            bad.append(
                f"кадр {clip.frame_number}: Excel {label!r} != "
                f"монтаж {clip.start_ts:.2f}-{clip.end_ts:.2f}s"
            )
    if bad:
        sample = "\n".join(bad[:8])
        extra = f"\n... ещё {len(bad) - 8}" if len(bad) > 8 else ""
        raise RuntimeError(
            f"[#{project_id}] R15 не совпадает с таймлайном монтажа:\n{sample}{extra}"
        )


async def ensure_r15_from_asr(
    project,
    *,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    words: list[WordTS],
    voice_full_path: Path,
    master: float | None = None,
) -> tuple[list[tuple[int, str]], int]:
    """Если строка 15 пуста — заполнить таймкодами из ASR + текста R49."""
    from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, write_plan_timestamps

    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    _filled, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
    need = len(frame_numbers)
    from app.services.montage.r15 import r15_cells_monotonic

    if parsed_n >= need and not bad and r15_cells_monotonic(ts_cells):
        return ts_cells, ts_row
    if parsed_n >= need and not bad:
        logger.warning(
            "[#{}] R{}: метки заполнены, но не по порядку — пересчёт из ASR",
            project.id,
            ts_row,
        )

    if not words:
        raise RuntimeError(
            f"[#{project.id}] R{ts_row} пуста ({parsed_n}/{need}) — "
            "нужен ASR (words.json) для автозаполнения строки 15"
        )

    if master is None:
        master = await probe_duration(voice_full_path)

    timings = map_frames(cells, words)
    if not timings or len(timings) != len(frame_numbers):
        timings = map_frames(cells, words, audio_duration=master)
    if not timings:
        raise RuntimeError(
            f"[#{project.id}] ASR не сопоставил текст кадров — строку 15 не заполнить"
        )
    timings = enforce_monotonic_timings(timings, master=master)

    ranges = [
        (t.frame_number, format_timecode_range(t.start_ts, t.end_ts)) for t in timings
    ]
    written = write_plan_timestamps(project, ranges)
    if written <= 0:
        raise RuntimeError(
            f"[#{project.id}] не удалось записать R{ts_row} в project.xlsx — закрой Excel"
        )

    logger.info(
        "[#{}] R{} автозаполнена из ASR: {} кадров → {}",
        project.id,
        ts_row,
        written,
        project.data_dir / "project.xlsx",
    )
    return read_plan_timestamps_cells(project, frame_numbers)


async def auto_sync_r15_from_voice(
    project,
    *,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    clips: list,
    ts_cells: list[tuple[int, str]],
    ts_row: int,
    voice_full_path: Path,
    master: float,
) -> tuple[list[tuple[int, str]], list, str]:
    """Сверка R15 с ASR. Excel занят → монтаж по ASR в памяти, без падения."""
    audio_dir = project.data_dir / "audio"
    words_path = find_words_json(audio_dir)
    if words_path is None or not words_path.is_file():
        logger.warning(
            "[#{}] нет words.json — R15 не сверяем с озвучкой",
            project.id,
        )
        return ts_cells, clips, "excel_only"

    words = load_words_json(words_path)
    if not words:
        return ts_cells, clips, "excel_only"

    lines = r15_voice_diff_lines(
        clips=clips,
        ts_cells=ts_cells,
        cells=cells,
        words=words,
        master=master,
    )
    if not lines:
        return ts_cells, clips, "excel_only"

    timings = map_frames(cells, words, audio_duration=master)
    from app.storage.plan_sheet_v8 import write_plan_timestamps

    ranges = [
        (t.frame_number, format_timecode_range(t.start_ts, t.end_ts))
        for t in timings
    ]
    written = write_plan_timestamps(project, ranges)

    if written > 0:
        ts_cells, _ts_row = read_plan_timestamps_cells(project, frame_numbers)
        synced_clips = clips_from_timestamp_cells(
            cells, ts_cells, voice_full_path, master=master
        )
        if synced_clips is None or len(synced_clips) != len(frame_numbers):
            logger.warning(
                "[#{}] auto-sync записал R15, но перечитать не удалось — ASR в памяти",
                project.id,
            )
        else:
            logger.warning(
                "[#{}] R15 auto-sync в Excel: {} кадров (пример: {})",
                project.id,
                len(lines),
                lines[0].strip(),
            )
            return ts_cells, synced_clips, "excel_autosync"

    memory_ts = ts_cells_from_frame_timings(timings)
    memory_clips = clips_from_frame_timings(timings, cells, voice_full_path)
    final_dir = project.data_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    fallback_path = final_dir / "r15_asr_timeline_used.json"
    fallback_path.write_text(
        json.dumps(
            {
                "source": "asr_memory",
                "reason": "project.xlsx locked or write failed",
                "xlsx_path": str(project.data_dir / "project.xlsx"),
                "diff_sample": lines[:12],
                "frames": [
                    {
                        "frame_number": c.frame_number,
                        "start_ts": c.start_ts,
                        "end_ts": c.end_ts,
                        "duration": c.duration,
                    }
                    for c in memory_clips
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.warning(
        "[#{}] project.xlsx занят — монтаж по ASR в памяти ({} кадров). "
        "Закрой Excel. Метки: {} … audit → {}",
        project.id,
        len(lines),
        lines[0].strip(),
        fallback_path,
    )
    return memory_ts, memory_clips, "asr_memory"


def r15_voice_diff_lines(
    *,
    clips: list,
    ts_cells: list[tuple[int, str]],
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
    threshold: float = R15_VOICE_START_TOLERANCE_S,
) -> list[str]:
    """Сравнить start в Excel R15 с началом речи кадра в ASR."""
    if not words:
        return []
    _ = master
    timings = map_frames(cells, words)
    by_asr = {t.frame_number: t for t in timings}
    labels = dict(ts_cells)
    lines: list[str] = []
    for clip in clips:
        asr = by_asr.get(clip.frame_number)
        if asr is None:
            continue
        delta = abs(float(clip.start_ts) - float(asr.start_ts))
        if delta <= threshold:
            continue
        lines.append(
            f"  кадр {clip.frame_number}: Excel {clip.start_ts:.2f}s "
            f"({labels.get(clip.frame_number, '')!r}), "
            f"озвучка ~{asr.start_ts:.2f}s (Δ{delta:.1f}s)"
        )
    return lines


async def assert_r15_matches_voice(
    project,
    *,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    clips: list,
    ts_cells: list[tuple[int, str]],
    voice_full_path: Path,
    master: float,
) -> None:
    """Сборка падает, если R15 на диске не совпадает с реальной озвучкой."""
    audio_dir = project.data_dir / "audio"
    words_path = find_words_json(audio_dir)
    if words_path is None or not words_path.is_file():
        logger.warning(
            "[#{}] нет words.json — сверка R15 с озвучкой пропущена "
            "(запусти ASR или remontage_prep + assemble)",
            project.id,
        )
        return

    words = load_words_json(words_path)
    if not words:
        return

    lines = r15_voice_diff_lines(
        clips=clips,
        ts_cells=ts_cells,
        cells=cells,
        words=words,
        master=master,
    )
    if not lines:
        return

    sample = "\n".join(lines[:10])
    extra = f"\n  ... ещё {len(lines) - 10}" if len(lines) > 10 else ""
    xlsx_path = project.data_dir / "project.xlsx"
    raise RuntimeError(
        f"[#{project.id}] R15 в Excel не совпадает с озвучкой ({words_path.name}):\n"
        f"{sample}{extra}\n"
        f"Файл: {xlsx_path}\n"
        "Поправь строку 15, СОХРАНИ xlsx и закрой Excel.\n"
        f"Диагностика: python scripts/r15_vs_voice.py {project.id}"
    )


async def try_timeline_from_xlsx_row15(
    project,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    voice_full_path: Path,
) -> tuple[list | None, float]:
    """Legacy alias."""
    clips, master = await load_assembly_timeline_from_r15(
        project, frame_numbers, cells, voice_full_path
    )
    if clips is None:
        return None, await probe_duration(voice_full_path)
    return clips, float(master)


def write_asr_timestamps_to_r15(project, clips: list) -> int:
    """После ASR: записать реальные метки в строку 15 листа «план»."""
    from app.storage.plan_sheet_v8 import write_plan_timestamps

    from app.services.mapper import FrameTiming, enforce_monotonic_timings

    timings = [
        FrameTiming(c.frame_number, c.start_ts, c.end_ts, c.duration)
        for c in clips
        if c.duration > 0
    ]
    if not timings:
        return 0
    master = max(c.end_ts for c in clips if c.duration > 0)
    timings = enforce_monotonic_timings(timings, master=master)
    ranges = [
        (t.frame_number, format_timecode_range(t.start_ts, t.end_ts)) for t in timings
    ]
    written = write_plan_timestamps(project, ranges)
    if written:
        logger.info(
            "[#{}] plan R15: записано {} таймкодов ASR → {}",
            project.id,
            written,
            project.data_dir / "project.xlsx",
        )
    else:
        logger.warning(
            "[#{}] plan R15: не удалось записать (закрой Excel?)",
            project.id,
        )
    return written
