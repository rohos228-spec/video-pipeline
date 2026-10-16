"""Детерминированное извлечение признаков из ролика (без LLM).

ffprobe → формат; ffmpeg scene-detect → границы планов; кадры → листы
(overview 3×2, motion 3×N) и палитра; аудио → голос / музыкальная
подложка, громкость (ebur128), паузы (silencedetect); ASR через
``app.services.asr.transcribe_words`` → темп речи.

Все внешние процессы идут через ``run_cmd`` (тесты подменяют/или
используют реальный ffmpeg на синтетическом ролике).
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.services.image_strip import compose_grid_strip

SCENE_THRESHOLD = 0.30
MIN_SHOT_LEN_S = 0.4
MAX_SHOTS = 60
OVERVIEW_COLS, OVERVIEW_ROWS = 3, 2
MOTION_COLS, MOTION_ROWS = 3, 4  # 4 плана × (start/mid/end)
FRAME_HEIGHT = 360
DEFAULT_MAX_SHEETS = 4
PALETTE_COLORS = 6

_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")
_SIL_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END_RE = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")
_LUFS_RE = re.compile(r"I:\s*(-?[0-9.]+)\s*LUFS")
_LRA_RE = re.compile(r"LRA:\s*(-?[0-9.]+)\s*LU")


class ExtractError(RuntimeError):
    pass


def run_cmd(cmd: list[str], *, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    """Обёртка subprocess (точка подмены в тестах)."""
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


# ── ffprobe ────────────────────────────────────────────────────────────────


def probe(video: Path) -> dict[str, Any]:
    proc = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video),
        ],
        timeout=60,
    )
    if proc.returncode != 0:
        raise ExtractError(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ExtractError(f"ffprobe: не JSON ({exc})") from exc
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = _to_float(fmt.get("duration")) or _to_float(vstream.get("duration")) or 0.0
    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    fps = _parse_rate(vstream.get("avg_frame_rate")) or _parse_rate(vstream.get("r_frame_rate")) or 0.0
    return {
        "duration_s": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "aspect": aspect_label(width, height),
        "resolution": resolution_label(width, height),
        "has_audio": astream is not None,
        "audio_channels": int((astream or {}).get("channels") or 0),
        "video_codec": vstream.get("codec_name"),
    }


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_rate(raw: Any) -> float | None:
    s = str(raw or "").strip()
    if not s or s == "0/0":
        return None
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            den = float(b)
            return float(a) / den if den else None
        except ValueError:
            return None
    return _to_float(s)


def aspect_label(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    r = w / h
    candidates = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "4:5": 0.8, "4:3": 4 / 3, "3:4": 0.75, "2:3": 2 / 3}
    name = min(candidates, key=lambda k: abs(candidates[k] - r))
    return name if abs(candidates[name] - r) < 0.06 else f"{w}:{h}"


def resolution_label(w: int, h: int) -> str:
    short = min(w, h)
    if short >= 2000:
        return "4k"
    if short >= 1000:
        return "1080p"
    if short >= 700:
        return "720p"
    return f"{w}x{h}" if w and h else "unknown"


# ── планы (shot detection) ─────────────────────────────────────────────────


def detect_shots(video: Path, duration_s: float, *, threshold: float = SCENE_THRESHOLD) -> list[dict[str, float]]:
    """Границы планов по ffmpeg select=gt(scene,thr) → список {start,end,len}."""
    proc = run_cmd(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=900,
    )
    cuts = sorted({round(float(m), 3) for m in _PTS_RE.findall(proc.stderr or "")})
    bounds = [0.0] + [c for c in cuts if MIN_SHOT_LEN_S < c < max(duration_s - MIN_SHOT_LEN_S, 0.0)] + [duration_s]
    shots: list[dict[str, float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < MIN_SHOT_LEN_S and shots:
            shots[-1]["end"] = b
            shots[-1]["len"] = round(b - shots[-1]["start"], 3)
            continue
        shots.append({"start": round(a, 3), "end": round(b, 3), "len": round(b - a, 3)})
    if not shots and duration_s > 0:
        shots = [{"start": 0.0, "end": round(duration_s, 3), "len": round(duration_s, 3)}]
    return shots[:MAX_SHOTS]


def shot_metrics(shots: list[dict[str, float]], duration_s: float) -> dict[str, Any]:
    lens = [float(s["len"]) for s in shots if float(s.get("len") or 0) > 0]
    if not lens:
        return {"shots": 0, "shot_len_s": None, "shot_len_p10_s": None, "shot_len_p90_s": None, "cuts_per_min": None}
    return {
        "shots": len(lens),
        "shot_len_s": round(statistics.median(lens), 3),
        "shot_len_p10_s": round(_percentile(lens, 10), 3),
        "shot_len_p90_s": round(_percentile(lens, 90), 3),
        "cuts_per_min": round(max(0, len(lens) - 1) / duration_s * 60.0, 2) if duration_s > 0 else None,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    k = (len(vals) - 1) * pct / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


# ── кадры и листы ──────────────────────────────────────────────────────────


def extract_frame(video: Path, t: float, out: Path, *, height: int = FRAME_HEIGHT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run_cmd(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, t):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale=-2:{height}",
            str(out),
        ],
        timeout=120,
    )
    if proc.returncode != 0 or not out.is_file():
        raise ExtractError(f"ffmpeg frame @{t:.2f}s: {proc.stderr.strip()[:200]}")
    return out


def _pick_evenly(items: list[Any], n: int) -> list[Any]:
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[min(len(items) - 1, int(i * step + step / 2))] for i in range(n)]


@dataclass
class Sheets:
    overview: list[Path] = field(default_factory=list)
    motion: list[Path] = field(default_factory=list)
    keyframes: list[Path] = field(default_factory=list)

    def all(self) -> list[Path]:
        return [*self.overview, *self.motion]


def build_sheets(
    video: Path,
    shots: list[dict[str, float]],
    duration_s: float,
    out_dir: Path,
    *,
    max_sheets: int = DEFAULT_MAX_SHEETS,
) -> Sheets:
    """Overview-листы (центры планов, 3×2) и motion-листы (start/mid/end × 4 плана)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    key_dir = out_dir / "keyframes"
    sheets = Sheets()
    if duration_s <= 0:
        return sheets

    max_sheets = max(1, int(max_sheets))
    n_overview = max(1, math.ceil(max_sheets / 2))
    n_motion = max(1, max_sheets - n_overview)

    cells = OVERVIEW_COLS * OVERVIEW_ROWS
    centers = [(s["start"] + s["end"]) / 2 for s in shots] or [duration_s / 2]
    want = min(len(centers), n_overview * cells)
    want = max(cells, want) if len(centers) >= cells else cells
    times = _pick_evenly(centers, want) if len(centers) >= want else _spread_times(duration_s, want)
    frames: list[Path] = []
    for i, t in enumerate(times):
        fp = key_dir / f"ov_{i:03d}.jpg"
        try:
            frames.append(extract_frame(video, min(t, max(duration_s - 0.05, 0)), fp))
        except ExtractError as exc:
            logger.warning("style_extract: кадр пропущен: {}", exc)
    sheets.keyframes.extend(frames)
    for i in range(0, len(frames) - cells + 1, cells):
        if len(sheets.overview) >= n_overview:
            break
        out = out_dir / f"overview_{len(sheets.overview) + 1:02d}.jpg"
        try:
            sheets.overview.append(compose_grid_strip(frames[i : i + cells], out, cols=OVERVIEW_COLS, rows=OVERVIEW_ROWS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: overview-лист не собран: {}", exc)

    motion_shots = [s for s in shots if s["len"] >= 0.8] or shots
    picked = _pick_evenly(motion_shots, n_motion * MOTION_ROWS)
    mframes: list[Path] = []
    for i, s in enumerate(picked):
        a, b = float(s["start"]), float(s["end"])
        for j, t in enumerate((a + 0.05, (a + b) / 2, max(a, b - 0.15))):
            fp = key_dir / f"mo_{i:03d}_{j}.jpg"
            try:
                mframes.append(extract_frame(video, min(t, max(duration_s - 0.05, 0)), fp))
            except ExtractError as exc:
                logger.warning("style_extract: motion-кадр пропущен: {}", exc)
    per_sheet = MOTION_COLS * MOTION_ROWS
    for i in range(0, len(mframes) - per_sheet + 1, per_sheet):
        if len(sheets.motion) >= n_motion:
            break
        out = out_dir / f"motion_{len(sheets.motion) + 1:02d}.jpg"
        try:
            sheets.motion.append(compose_grid_strip(mframes[i : i + per_sheet], out, cols=MOTION_COLS, rows=MOTION_ROWS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: motion-лист не собран: {}", exc)
    if not sheets.motion and len(mframes) >= 3:
        # Короткий ролик: один ряд start/mid/end.
        rows = max(1, len(mframes) // MOTION_COLS)
        rows = min(rows, MOTION_ROWS)
        out = out_dir / "motion_01.jpg"
        try:
            sheets.motion.append(compose_grid_strip(mframes[: rows * MOTION_COLS], out, cols=MOTION_COLS, rows=rows))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: motion-лист (короткий) не собран: {}", exc)
    return sheets


def _spread_times(duration_s: float, n: int) -> list[float]:
    if n <= 0:
        return []
    step = duration_s / (n + 1)
    return [step * (i + 1) for i in range(n)]


# ── палитра / статичность ─────────────────────────────────────────────────


def palette(frames: list[Path], *, n_colors: int = PALETTE_COLORS) -> dict[str, Any]:
    """Доминирующие цвета (quantize по всем кадрам) + насыщенность/контраст."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return {"palette_hex": [], "saturation": None, "contrast": None}
    if not frames:
        return {"palette_hex": [], "saturation": None, "contrast": None}
    tiles = []
    for fp in frames[:24]:
        try:
            im = Image.open(fp).convert("RGB")
            im.thumbnail((160, 160))
            tiles.append(im)
        except Exception:  # noqa: BLE001
            continue
    if not tiles:
        return {"palette_hex": [], "saturation": None, "contrast": None}
    w = max(t.width for t in tiles)
    canvas = Image.new("RGB", (w, sum(t.height for t in tiles)))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y))
        y += t.height
    q = canvas.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    hexes: list[str] = []
    for _cnt, idx in counts[:n_colors]:
        r, g, b = pal[idx * 3 : idx * 3 + 3]
        hexes.append(f"#{r:02X}{g:02X}{b:02X}")
    px = list(canvas.resize((64, 64)).getdata())
    sats = []
    lums = []
    for r, g, b in px:
        _h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        sats.append(s)
        lums.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
    sat = statistics.mean(sats) if sats else 0.0
    contrast = statistics.pstdev(lums) / 255 if lums else 0.0
    return {
        "palette_hex": hexes,
        "saturation_num": round(sat, 3),
        "contrast_num": round(contrast, 3),
        "saturation": "low" if sat < 0.25 else ("high" if sat > 0.55 else "medium"),
        "contrast": "low" if contrast < 0.15 else ("high" if contrast > 0.3 else "medium"),
    }


def static_ratio(keyframe_pairs: list[tuple[Path, Path]]) -> float | None:
    """Доля планов, где start≈end (камера и объект почти не движутся)."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:  # noqa: BLE001
        return None
    if not keyframe_pairs:
        return None
    static = 0
    total = 0
    for a, b in keyframe_pairs:
        try:
            ia = Image.open(a).convert("L").resize((48, 27))
            ib = Image.open(b).convert("L").resize((48, 27))
        except Exception:  # noqa: BLE001
            continue
        diff = ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0] / 255
        total += 1
        if diff < 0.04:
            static += 1
    return round(static / total, 3) if total else None


# ── аудио ──────────────────────────────────────────────────────────────────


def extract_audio(video: Path, out: Path) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run_cmd(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)],
        timeout=600,
    )
    if proc.returncode != 0 or not out.is_file():
        logger.warning("style_extract: аудио не извлечено: {}", proc.stderr.strip()[:200])
        return None
    return out


def separate_voice_music(audio: Path, out_dir: Path) -> dict[str, Path | None]:
    """Голос / музыка: demucs, если установлен; иначе band-pass + подложка из пауз."""
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = out_dir / "voice.wav"
    music = out_dir / "music_bed.wav"
    if shutil.which("demucs"):
        proc = run_cmd(
            ["demucs", "--two-stems=vocals", "-o", str(out_dir / "demucs"), str(audio)],
            timeout=1800,
        )
        if proc.returncode == 0:
            stem_dir = next((p for p in (out_dir / "demucs").rglob("vocals.wav")), None)
            if stem_dir is not None:
                shutil.copyfile(stem_dir, voice)
                other = stem_dir.with_name("no_vocals.wav")
                if other.is_file():
                    shutil.copyfile(other, music)
                return {"voice": voice, "music": music if music.is_file() else None, "method": "demucs"}  # type: ignore[dict-item]
    proc = run_cmd(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio), "-af", "highpass=f=120,lowpass=f=3800", str(voice)],
        timeout=600,
    )
    v = voice if proc.returncode == 0 and voice.is_file() else None
    proc = run_cmd(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio), "-af", "lowpass=f=160,highpass=f=40", str(music)],
        timeout=600,
    )
    m = music if proc.returncode == 0 and music.is_file() else None
    return {"voice": v, "music": m, "method": "bandpass"}  # type: ignore[dict-item]


def loudness(audio: Path) -> dict[str, float | None]:
    proc = run_cmd(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        timeout=600,
    )
    text = proc.stderr or ""
    m = _LUFS_RE.findall(text)
    lra = _LRA_RE.findall(text)
    lufs = float(m[-1]) if m else None
    return {
        "lufs": None if lufs is None or lufs < -60 else round(lufs, 2),
        "lra": round(float(lra[-1]), 2) if lra else None,
    }


def silences(audio: Path, *, noise_db: float = -35.0, min_len_s: float = 0.25) -> list[dict[str, float]]:
    proc = run_cmd(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(audio),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_len_s}",
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )
    out: list[dict[str, float]] = []
    for m in _SIL_END_RE.finditer(proc.stderr or ""):
        end = float(m.group(1))
        dur = float(m.group(2))
        out.append({"start": round(end - dur, 3), "end": round(end, 3), "len": round(dur, 3)})
    return out


def speech_metrics(words: list[Any], duration_s: float, sil: list[dict[str, float]]) -> dict[str, Any]:
    """Темп речи и паузы по словам ASR (``WordTS``: word/start/end)."""
    toks = [w for w in words if str(getattr(w, "word", "")).strip()]
    if not toks:
        return {
            "words": 0,
            "pace_wpm": None,
            "chars_per_sec": None,
            "pause_median_s": None,
            "pause_p90_s": None,
            "speech_ratio": None,
            "first_3s_text": "",
            "transcript": "",
        }
    first = float(getattr(toks[0], "start", 0.0))
    last = float(getattr(toks[-1], "end", duration_s))
    span = max(last - first, 0.5)
    text = " ".join(str(w.word).strip() for w in toks)
    gaps = []
    for a, b in zip(toks, toks[1:]):
        g = float(getattr(b, "start", 0.0)) - float(getattr(a, "end", 0.0))
        if g >= 0.25:
            gaps.append(g)
    speaking = span - sum(gaps)
    first_3s = " ".join(str(w.word).strip() for w in toks if float(getattr(w, "start", 0.0)) < first + 3.0)
    return {
        "words": len(toks),
        "pace_wpm": round(len(toks) / span * 60.0, 1),
        "chars_per_sec": round(len(text.replace(" ", "")) / max(speaking, 0.5), 2),
        "pause_median_s": round(statistics.median(gaps), 3) if gaps else 0.0,
        "pause_p90_s": round(_percentile(gaps, 90), 3) if gaps else 0.0,
        "speech_ratio": round(min(1.0, speaking / duration_s), 3) if duration_s > 0 else None,
        "first_3s_text": first_3s[:300],
        "transcript": text[:6000],
        "silences": len(sil),
    }


# ── сборка профиля ролика ──────────────────────────────────────────────────

AsrFn = Callable[[Path, str], list[Any]]


def _default_asr(audio: Path, language: str) -> list[Any]:
    from app.services.asr import transcribe_words

    return transcribe_words(audio, language=language)


def analyze_video(
    video: Path,
    out_dir: Path,
    *,
    max_sheets: int = DEFAULT_MAX_SHEETS,
    language: str = "ru",
    asr_fn: AsrFn | None = None,
    skip_asr: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Полный детерминированный разбор одного ролика → ``metrics.json`` + листы."""

    def _p(stage: str) -> None:
        if progress:
            progress(stage)

    out_dir.mkdir(parents=True, exist_ok=True)
    _p("probe")
    fmt = probe(video)
    duration = float(fmt["duration_s"] or 0.0)
    (out_dir / "probe.json").write_text(json.dumps(fmt, ensure_ascii=False, indent=2), encoding="utf-8")

    _p("shots")
    shots = detect_shots(video, duration)
    (out_dir / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=2), encoding="utf-8")
    motion = shot_metrics(shots, duration)

    _p("frames")
    sheets = build_sheets(video, shots, duration, out_dir / "sheets", max_sheets=max_sheets)
    pal = palette(sheets.keyframes)
    pairs: list[tuple[Path, Path]] = []
    key_dir = out_dir / "sheets" / "keyframes"
    for fp in sorted(key_dir.glob("mo_*_0.jpg")):
        end_fp = fp.with_name(fp.name.replace("_0.jpg", "_2.jpg"))
        if end_fp.is_file():
            pairs.append((fp, end_fp))
    motion["static_ratio"] = static_ratio(pairs)

    audio_info: dict[str, Any] = {"has_audio": bool(fmt.get("has_audio"))}
    speech: dict[str, Any] = {}
    music: dict[str, Any] = {"present": False}
    words_n = 0
    if fmt.get("has_audio"):
        _p("audio")
        audio = extract_audio(video, out_dir / "audio.wav")
        if audio is not None:
            stems = separate_voice_music(audio, out_dir)
            audio_info["separation"] = stems.get("method")
            full_loud = loudness(audio)
            voice_loud = loudness(stems["voice"]) if stems.get("voice") else {"lufs": None, "lra": None}
            music_loud = loudness(stems["music"]) if stems.get("music") else {"lufs": None, "lra": None}
            sil = silences(audio)
            (out_dir / "loudness.json").write_text(
                json.dumps({"full": full_loud, "voice": voice_loud, "music": music_loud}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "silence.json").write_text(json.dumps(sil, ensure_ascii=False), encoding="utf-8")
            audio_info["loudness_lufs"] = full_loud.get("lufs")
            audio_info["lra"] = full_loud.get("lra")
            music_lufs = music_loud.get("lufs")
            voice_lufs = voice_loud.get("lufs")
            music = {
                "present": music_lufs is not None and music_lufs > -50,
                "loudness_lufs": music_lufs,
                "level_vs_voice_db": (
                    round(music_lufs - voice_lufs, 2) if music_lufs is not None and voice_lufs is not None else None
                ),
            }
            words: list[Any] = []
            if not skip_asr:
                _p("asr")
                try:
                    words = (asr_fn or _default_asr)(stems.get("voice") or audio, language)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("style_extract: ASR не удался ({}): {}", video.name, exc)
                    audio_info["asr_error"] = str(exc)[:300]
            words_n = len(words)
            try:
                from app.services.whisper import dump_words_json

                dump_words_json(words, out_dir / "words.json")
            except Exception:  # noqa: BLE001
                (out_dir / "words.json").write_text(
                    json.dumps(
                        [
                            {"word": getattr(w, "word", ""), "start": getattr(w, "start", 0), "end": getattr(w, "end", 0)}
                            for w in words
                        ],
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            speech = speech_metrics(words, duration, sil)

    profile = {
        "video": video.name,
        "format": fmt,
        "shots": shots,
        "motion": motion,
        "palette": pal,
        "audio": audio_info,
        "speech": speech,
        "music": music,
        "words_n": words_n,
        "sheets": {
            "overview": [str(p) for p in sheets.overview],
            "motion": [str(p) for p in sheets.motion],
            "keyframes": len(sheets.keyframes),
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile
