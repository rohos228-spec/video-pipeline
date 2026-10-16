
from __future__ import annotations

import json
import subprocess
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_FORMATS = ("mp3", "wav", "ogg", "flac", "mov", "mp4", "m4a", "aac", "matroska", "webm")
MIN_FILE_BYTES = 1000
SILENT_PEAK_DB = -80.0
SILENT_RMS_DB = -60.0

FAILURE_TEXTS_RU = {
    "no_voice_source": "Нет озвучки: готового файла нет, а генерация голоса выключена.",
    "missing_text": "Нет закадрового текста: озвучивать нечего.",
    "no_frames": "В проекте нет кадров для озвучки.",
    "tts_failed": "Сервис озвучки не вернул голос.",
    "asr_failed": "Не удалось распознать речь для разметки по кадрам.",
    "budget_blocked": "Платный вызов озвучки не разрешён ограничением трат.",
    "verification_failed": "Файл озвучки не прошёл проверку.",
    "unknown": "Озвучка завершилась ошибкой.",
}


@dataclass
class AudioVerdict:
    path: str
    ok: bool = False
    codes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    format: str | None = None
    codec: str | None = None
    duration: float | None = None
    size_bytes: int | None = None
    peak_db: float | None = None
    rms_db: float | None = None
    silent: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AudioStepError(RuntimeError):

    def __init__(self, failure_class: str, message: str, *, verdict: AudioVerdict | None = None):
        super().__init__(message)
        self.failure_class = failure_class
        self.operator_text = FAILURE_TEXTS_RU.get(failure_class, FAILURE_TEXTS_RU["unknown"])
        self.verdict = verdict


def _ffprobe(path: Path) -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        return data if isinstance(data, dict) and data.get("format") else None
    except Exception:
        return None


def _levels_ffmpeg(path: Path) -> tuple[float | None, float | None]:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "astats", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120)
    except Exception:
        return None, None
    peak = rms = None
    for line in out.stderr.splitlines():
        try:
            if "Peak level dB" in line:
                v = float(line.rsplit(":", 1)[-1].strip())
                peak = v if peak is None else max(peak, v)
            elif "RMS level dB" in line:
                v = float(line.rsplit(":", 1)[-1].strip())
                rms = v if rms is None else max(rms, v)
        except ValueError:
            continue
    return peak, rms


def _levels_wav(path: Path) -> tuple[float | None, float | None]:
    import array
    import math
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return None, None
            raw = w.readframes(w.getnframes())
    except Exception:
        return None, None
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    if not a:
        return None, None
    peak = max(abs(x) for x in a)
    rms = math.sqrt(sum(x * x for x in a) / len(a))
    db = lambda v: -120.0 if v <= 0 else 20 * math.log10(v / 32768.0)
    return db(peak), db(rms)


def verify_audio(path: Path | str, *, expect_duration: float | None = None, duration_tol: float = 0.5,
                 allowed_formats: tuple[str, ...] = ALLOWED_FORMATS,
                 min_duration: float = 0.05) -> AudioVerdict:
    p = Path(path)
    v = AudioVerdict(path=str(p))

    def bad(code: str, text: str) -> None:
        v.codes.append(code)
        v.problems.append(text)

    if not p.is_file():
        bad("missing_file", f"файл не создан: {p.name}")
        return v
    v.size_bytes = p.stat().st_size
    if v.size_bytes < MIN_FILE_BYTES:
        bad("empty_file", f"файл пуст или слишком мал ({v.size_bytes} Б): {p.name}")
        return v
    info = _ffprobe(p)
    if info is None:
        bad("unreadable", f"ffprobe не читает файл: {p.name}")
        return v
    fmt = info.get("format") or {}
    v.format = str(fmt.get("format_name") or "")
    streams = [s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"]
    if not streams:
        bad("no_audio_stream", f"в файле нет звуковой дорожки: {p.name}")
        return v
    v.codec = str(streams[0].get("codec_name") or "")
    if not any(name in v.format for name in allowed_formats):
        bad("bad_format", f"недопустимый формат «{v.format}»: {p.name}")
    try:
        v.duration = float(fmt.get("duration") or streams[0].get("duration"))
    except (TypeError, ValueError):
        v.duration = None
    if v.duration is None or v.duration < min_duration:
        bad("zero_duration", f"нулевая или нечитаемая длительность: {p.name}")
        return v
    if expect_duration is not None and abs(v.duration - expect_duration) > duration_tol:
        bad("duration_mismatch",
            f"длительность {v.duration:.2f} с вне допуска (ожидалась {expect_duration:.2f} с): {p.name}")
    peak, rms = _levels_wav(p) if p.suffix.lower() == ".wav" else (None, None)
    if peak is None and rms is None:
        peak, rms = _levels_ffmpeg(p)
    v.peak_db, v.rms_db = peak, rms
    if peak is None and rms is None:
        bad("level_unknown", f"не удалось измерить уровень сигнала: {p.name}")
    else:
        v.silent = bool((peak is not None and peak <= SILENT_PEAK_DB)
                        or (rms is not None and rms <= SILENT_RMS_DB and (peak is None or peak <= -40.0)))
        if v.silent:
            bad("silent", f"тишина: нулевая амплитуда (пик {peak} dB, RMS {rms} dB): {p.name}")
    v.ok = not v.codes
    return v


def classify_audio_failure(exc: BaseException) -> tuple[str, str]:
    cls = getattr(exc, "failure_class", None)
    if isinstance(cls, str) and cls in FAILURE_TEXTS_RU:
        return cls, FAILURE_TEXTS_RU[cls]
    name = type(exc).__name__
    msg = str(exc).lower()
    if name in {"PaidCallBlocked", "ResumeOnlyBlocked"}:
        kind = "budget_blocked"
    elif "нет кадров" in msg:
        kind = "no_frames"
    elif "нет закадрового текста" in msg or "нет voiceover" in msg:
        kind = "missing_text"
    elif "нет озвучки" in msg or "11labs отключ" in msg:
        kind = "no_voice_source"
    elif any(w in msg for w in ("whisper", "asr", "распозна")):
        kind = "asr_failed"
    elif any(w in msg for w in ("elevenlabs", "11labs", "tts", "synthesize", "голос")) or "httpx" in type(exc).__module__:
        kind = "tts_failed"
    else:
        kind = "unknown"
    return kind, FAILURE_TEXTS_RU[kind]
