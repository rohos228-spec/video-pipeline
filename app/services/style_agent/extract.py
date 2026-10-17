"""Детерминированное извлечение признаков из ролика (без LLM).

ffprobe → формат; scene-score ffmpeg по **каждому** кадру → адаптивный
детектор склеек (z-score к локальному фону + подсказки от смены субтитров
+ «плавные» переходы); таймлайн кадров 120/мин (хук — в 2 раза плотнее,
плюс кадры вокруг каждой склейки); OCR субтитров → транскрипт №1 (ASR —
запасной); листы для vision-агентов с вшитыми таймкодами; аудио → голос /
подложка, громкость (ebur128), паузы (silencedetect).

Все внешние процессы идут через ``run_cmd`` (точка подмены в тестах).
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

from app.services.style_agent import ocr as ocr_mod

# ── параметры ──────────────────────────────────────────────────────────────
SCENE_THRESHOLD = 0.30  # старый порог (оставлен для совместимости/справки)
CUT_FLOOR = 0.045  # минимальный scene-score «жёсткой» склейки
CUT_Z_MIN = 5.0  # z-score к локальному фону (±1.5 с)
CUT_WEAK_FLOOR = 0.02  # слабый кандидат — принимается только рядом со сменой субтитра
CUT_WEAK_Z = 3.0
UNCERTAIN_SCORE = 0.12  # ниже — проверяем гистограммой «до/после»; похожи → uncertain (вспышка/рывок)
UNCERTAIN_HIST = 0.10
CUT_CUE_WINDOW_S = 0.4
CUT_LOCAL_WINDOW_S = 1.5
CUT_NMS_S = 0.3  # два кандидата ближе — один
GRADUAL_MIN_SPAN_S = 0.25
MIN_SHOT_LEN_S = 0.4
MAX_SHOTS = 120

DEFAULT_FRAMES_PER_MIN = 120
HOOK_SECONDS = 5.0
HOOK_DENSITY = 2
CUT_PAD_S = 0.2
TIMELINE_WIDTH = 720  # кадры таймлайна (нужны OCR) — не шире 720

SHEET_COLS, SHEET_ROWS = 3, 4  # шот-лист: 4 плана × (start/mid/end)
OVERVIEW_COLS, OVERVIEW_ROWS = 3, 2
CUT_PAIRS_PER_SHEET = 8  # лист пар «до/после» склейки
FRAME_HEIGHT = 360
DEFAULT_MAX_SHEETS = 6
PALETTE_COLORS = 6

_PTS_RE = re.compile(r"pts_time:\s*([0-9.]+)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9.]+)")
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


def _ff(*args: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]


# ── ffprobe ────────────────────────────────────────────────────────────────


def probe(video: Path) -> dict[str, Any]:
    proc = run_cmd(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)],
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


# ── склейки (cut detection) ────────────────────────────────────────────────


def scene_scores(video: Path, out_file: Path | None = None) -> list[tuple[float, float]]:
    """scene-score ffmpeg для каждого кадра → [(t, score)]."""
    proc = run_cmd(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-vf",
            "select='gte(scene,0)',metadata=print",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=900,
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    return parse_scene_scores(text, out_file)


def parse_scene_scores(text: str, out_file: Path | None = None) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    cur: float | None = None
    for line in text.splitlines():
        m = _PTS_RE.search(line)
        if m:
            cur = float(m.group(1))
            continue
        m = _SCORE_RE.search(line)
        if m and cur is not None:
            out.append((round(cur, 4), float(m.group(1))))
            cur = None
    if out_file is not None:
        try:
            out_file.write_text(json.dumps([[t, round(s, 5)] for t, s in out]), encoding="utf-8")
        except OSError:
            pass
    return out


def detect_cuts(
    scores: list[tuple[float, float]],
    duration_s: float,
    *,
    floor: float = CUT_FLOOR,
    z_min: float = CUT_Z_MIN,
    cue_times: list[float] | None = None,
    weak_floor: float = CUT_WEAK_FLOOR,
    weak_z: float = CUT_WEAK_Z,
    cue_window_s: float = CUT_CUE_WINDOW_S,
) -> list[dict[str, Any]]:
    """Адаптивный детектор склеек по per-frame scene-score.

    Жёсткая склейка: score ≥ floor и z ≥ z_min к локальному фону (медиана/MAD
    в окне ±1.5 с) и локальный максимум (±0.3 с). Слабый кандидат (score ≥
    weak_floor, z ≥ weak_z) принимается, если рядом (±cue_window) сменился
    субтитр — в 3D-анимации с похожей палитрой cut часто даёт score 0.03–0.07.
    Серия высоких значений ≥ 0.25 с → «плавный» переход (dissolve/whip/morph),
    отмечается одной склейкой с ``kind='gradual'`` и ``span``.
    """
    if len(scores) < 3 or duration_s <= 0:
        return []
    ts = [t for t, _ in scores]
    ss = [s for _, s in scores]
    n = len(ss)
    fps = (n - 1) / max(ts[-1] - ts[0], 1e-3)
    win = max(3, int(CUT_LOCAL_WINDOW_S * fps))
    nms = max(1, int(CUT_NMS_S * fps))
    cues = sorted(cue_times or [])

    def _near_cue(t: float) -> bool:
        return any(abs(t - c) <= cue_window_s for c in cues)

    cands: list[dict[str, Any]] = []
    for i in range(1, n):
        s = ss[i]
        if s < weak_floor:
            continue
        lo, hi = max(0, i - win), min(n, i + win + 1)
        nb = ss[lo:i] + ss[i + 1 : hi]
        if not nb:
            continue
        base = statistics.median(nb)
        mad = statistics.median([abs(x - base) for x in nb]) + 0.002
        z = (s - base) / mad
        a, b = max(0, i - nms), min(n, i + nms + 1)
        if s < max(ss[a:b]):
            continue
        t = ts[i]
        if t < MIN_SHOT_LEN_S or t > duration_s - MIN_SHOT_LEN_S:
            continue
        hard = s >= floor and z >= z_min
        weak = (not hard) and s >= weak_floor and z >= weak_z and _near_cue(t)
        if not (hard or weak):
            continue
        # плавный переход: сколько соседних кадров держатся выше floor/2
        j = i
        while j - 1 > 0 and ss[j - 1] >= floor / 2:
            j -= 1
        k = i
        while k + 1 < n and ss[k + 1] >= floor / 2:
            k += 1
        span = ts[k] - ts[j]
        kind = "gradual" if span >= GRADUAL_MIN_SPAN_S else ("cue" if weak else "hard")
        cands.append(
            {
                "t": round(t, 3),
                "score": round(s, 4),
                "z": round(z, 1),
                "kind": kind,
                "span": round(span, 3) if kind == "gradual" else 0.0,
                "subtitle_cue": _near_cue(t),
            }
        )
    # NMS по времени: ближе MIN_SHOT_LEN_S — оставить сильнейший
    cands.sort(key=lambda c: c["t"])
    out: list[dict[str, Any]] = []
    for c in cands:
        if out and c["t"] - out[-1]["t"] < MIN_SHOT_LEN_S:
            if c["score"] > out[-1]["score"]:
                out[-1] = c
            continue
        out.append(c)
    return out[: MAX_SHOTS - 1]


def shots_from_cuts(cuts: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    """Склейки → планы [{i, start, end, len, transition_in, cut_score, cut_kind}]."""
    bounds: list[tuple[float, dict[str, Any] | None]] = [(0.0, None)] + [(float(c["t"]), c) for c in cuts]
    shots: list[dict[str, Any]] = []
    for idx, ((a, cut), (b, _nxt)) in enumerate(zip(bounds, bounds[1:] + [(float(duration_s), None)])):
        if b <= a:
            continue
        shots.append(
            {
                "i": idx + 1,
                "start": round(a, 3),
                "end": round(b, 3),
                "len": round(b - a, 3),
                "transition_in": (cut or {}).get("kind") or "start",
                "cut_score": (cut or {}).get("score"),
                "subtitle_cue": bool((cut or {}).get("subtitle_cue")) if cut else False,
                "uncertain": bool((cut or {}).get("uncertain")) if cut else False,
            }
        )
    if not shots and duration_s > 0:
        shots = [{"i": 1, "start": 0.0, "end": round(duration_s, 3), "len": round(duration_s, 3), "transition_in": "start", "cut_score": None, "subtitle_cue": False}]
    return shots


def detect_shots(video: Path, duration_s: float, *, cue_times: list[float] | None = None) -> list[dict[str, Any]]:
    """Совместимость: scene_scores + detect_cuts + shots_from_cuts."""
    scores = scene_scores(video)
    return shots_from_cuts(detect_cuts(scores, duration_s, cue_times=cue_times), duration_s)


def shot_metrics(shots: list[dict[str, Any]], duration_s: float) -> dict[str, Any]:
    lens = [float(s["len"]) for s in shots if float(s.get("len") or 0) > 0]
    if not lens:
        return {"shots": 0, "shot_len_s": None, "shot_len_p10_s": None, "shot_len_p90_s": None, "cuts_per_min": None}
    kinds = [str(s.get("transition_in") or "") for s in shots[1:]]
    total_t = max(1, len(kinds))
    return {
        "shots": len(lens),
        "shot_len_s": round(statistics.median(lens), 3),
        "shot_len_p10_s": round(_percentile(lens, 10), 3),
        "shot_len_p90_s": round(_percentile(lens, 90), 3),
        "shot_len_min_s": round(min(lens), 3),
        "shot_len_max_s": round(max(lens), 3),
        "cuts_per_min": round(max(0, len(lens) - 1) / duration_s * 60.0, 2) if duration_s > 0 else None,
        "transitions_measured": {
            "cut": round(sum(1 for k in kinds if k in ("hard", "cue")) / total_t, 3),
            "gradual": round(sum(1 for k in kinds if k == "gradual") / total_t, 3),
        },
        "hook_shots_first_5s": sum(1 for s in shots if float(s["start"]) < HOOK_SECONDS),
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


# ── кадры: таймлайн и листы ────────────────────────────────────────────────


def extract_frame(video: Path, t: float, out: Path, *, height: int = FRAME_HEIGHT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run_cmd(
        _ff("-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale=-2:{height}", str(out)),
        timeout=120,
    )
    if proc.returncode != 0 or not out.is_file():
        raise ExtractError(f"ffmpeg frame @{t:.2f}s: {proc.stderr.strip()[:200]}")
    return out


def _extract_series(video: Path, out_dir: Path, prefix: str, rate: float, *, t_max: float | None = None) -> list[tuple[float, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(video)]
    if t_max is not None:
        args += ["-t", f"{t_max:.3f}"]
    args += ["-vf", f"fps={rate:.4f},scale='min({TIMELINE_WIDTH},iw)':-2", "-q:v", "4", str(out_dir / f"{prefix}_%05d.jpg")]
    proc = run_cmd(_ff(*args), timeout=900)
    if proc.returncode != 0:
        raise ExtractError(f"ffmpeg timeline ({prefix}): {proc.stderr.strip()[:200]}")
    out: list[tuple[float, Path]] = []
    for fp in sorted(out_dir.glob(f"{prefix}_*.jpg")):
        idx = int(fp.stem.rsplit("_", 1)[1]) - 1
        out.append((round(idx / rate, 3), fp))
    return out


def sample_timeline(
    video: Path,
    duration_s: float,
    out_dir: Path,
    *,
    frames_per_min: int = DEFAULT_FRAMES_PER_MIN,
    cuts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Кадры таймлайна: базовая сетка 120/мин, хук (первые 5 с) ×2, ±0.2 с вокруг склеек.

    Возвращает {"base": [(t, path)], "hook": [...], "cuts": [{"t", "before", "after"}], "rate"}.
    """
    rate = max(0.5, float(frames_per_min) / 60.0)
    base = _extract_series(video, out_dir, "tl", rate)
    hook: list[tuple[float, Path]] = []
    if duration_s > 1.0:
        dense = _extract_series(video, out_dir, "hk", rate * HOOK_DENSITY, t_max=min(HOOK_SECONDS, duration_s))
        # чётные индексы плотной сетки совпадают с базовой — оставляем промежуточные
        hook = [(t, p) for i, (t, p) in enumerate(dense) if i % HOOK_DENSITY != 0]
    around: list[dict[str, Any]] = []
    for c in cuts or []:
        t = float(c["t"])
        pair: dict[str, Any] = {"t": t}
        for tag, tt in (("before", t - CUT_PAD_S), ("after", t + CUT_PAD_S)):
            fp = out_dir / f"cut_{int(round(t * 1000)):07d}_{tag}.jpg"
            try:
                pair[tag] = str(extract_frame(video, min(max(tt, 0.0), max(duration_s - 0.05, 0.0)), fp))
            except ExtractError as exc:
                logger.debug("style_extract: кадр у склейки пропущен: {}", exc)
        around.append(pair)
    return {"base": base, "hook": hook, "cuts": around, "rate": rate, "count": len(base) + len(hook) + 2 * len(around)}


def frame_at(timeline: dict[str, Any], t: float) -> Path | None:
    """Ближайший кадр таймлайна к моменту t."""
    best: tuple[float, Path] | None = None
    for tt, fp in list(timeline.get("base") or []) + list(timeline.get("hook") or []):
        d = abs(tt - t)
        if best is None or d < best[0]:
            best = (d, fp)
    return best[1] if best else None


def _tc(t: float) -> str:
    m = int(t // 60)
    s = t - m * 60
    return f"{m:02d}:{s:05.2f}"


def _font(size: int):  # noqa: ANN202
    from PIL import ImageFont

    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def compose_labeled_grid(cells: list[tuple[Path, str]], out: Path, *, cols: int, cell_height: int = FRAME_HEIGHT) -> Path:
    """Сетка кадров с подписью (таймкод/роль) на чёрной полосе сверху каждого кадра."""
    from PIL import Image, ImageDraw

    if not cells:
        raise ExtractError("пустой лист")
    ims = []
    for fp, label in cells:
        im = Image.open(fp).convert("RGB")
        im.thumbnail((10_000, cell_height))
        ims.append((im, label))
    cw = max(im.width for im, _ in ims)
    bar = 22
    rows = (len(ims) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * (cw + 4), rows * (cell_height + bar + 4)), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    font = _font(15)
    for i, (im, label) in enumerate(ims):
        x = (i % cols) * (cw + 4)
        y = (i // cols) * (cell_height + bar + 4)
        draw.rectangle((x, y, x + cw, y + bar), fill=(0, 0, 0))
        draw.text((x + 4, y + 3), label, fill=(255, 230, 0), font=font)
        canvas.paste(im, (x + (cw - im.width) // 2, y + bar))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=86)
    return out


@dataclass
class Sheets:
    overview: list[Path] = field(default_factory=list)
    shots: list[Path] = field(default_factory=list)
    cuts: list[Path] = field(default_factory=list)
    keyframes: list[Path] = field(default_factory=list)
    # совместимость с v1
    motion: list[Path] = field(default_factory=list)

    def all(self) -> list[Path]:
        return [*self.overview, *self.shots, *self.cuts]


def build_sheets(
    video: Path,
    shots: list[dict[str, Any]],
    duration_s: float,
    out_dir: Path,
    *,
    max_sheets: int = DEFAULT_MAX_SHEETS,
    timeline: dict[str, Any] | None = None,
) -> Sheets:
    """Листы для vision-агентов (все — с таймкодами на кадрах).

    - ``shot_NN.jpg`` — по 4 плана на лист: start / mid / end каждого плана;
    - ``overview_NN.jpg`` — 6 кадров, равномерно по ролику (рисовка/палитра);
    - ``cuts_NN.jpg`` — пары «до/после» для подтверждения склеек (если есть timeline).
    ``max_sheets`` ограничивает число шот-листов (по 4 плана = ~24 плана на 6 листов;
    при большем числе планов берутся равномерно).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    key_dir = out_dir / "keyframes"
    key_dir.mkdir(parents=True, exist_ok=True)
    sheets = Sheets()
    if duration_s <= 0:
        return sheets
    max_sheets = max(1, int(max_sheets))

    # overview
    ov_times = _spread_times(duration_s, OVERVIEW_COLS * OVERVIEW_ROWS)
    ov_cells: list[tuple[Path, str]] = []
    for i, t in enumerate(ov_times):
        fp = key_dir / f"ov_{i:03d}.jpg"
        try:
            ov_cells.append((extract_frame(video, t, fp), _tc(t)))
            sheets.keyframes.append(fp)
        except ExtractError as exc:
            logger.warning("style_extract: кадр пропущен: {}", exc)
    if ov_cells:
        try:
            sheets.overview.append(compose_labeled_grid(ov_cells, out_dir / "overview_01.jpg", cols=OVERVIEW_COLS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: overview-лист не собран: {}", exc)

    # shot sheets
    per_sheet = SHEET_ROWS
    picked = shots if len(shots) <= max_sheets * per_sheet else _pick_evenly(shots, max_sheets * per_sheet)
    cells: list[tuple[Path, str]] = []
    for s in picked:
        a, b = float(s["start"]), float(s["end"])
        for tag, t in (("start", a + 0.05), ("mid", (a + b) / 2), ("end", max(a, b - 0.15))):
            fp = key_dir / f"sh_{int(s['i']):03d}_{tag}.jpg"
            try:
                extract_frame(video, min(t, max(duration_s - 0.05, 0.0)), fp)
            except ExtractError as exc:
                logger.warning("style_extract: кадр плана пропущен: {}", exc)
                continue
            sheets.keyframes.append(fp)
            cells.append((fp, f"#{int(s['i'])} {tag} {_tc(t)}"))
    for n, i in enumerate(range(0, len(cells), per_sheet * SHEET_COLS), start=1):
        chunk = cells[i : i + per_sheet * SHEET_COLS]
        try:
            sheets.shots.append(compose_labeled_grid(chunk, out_dir / f"shot_{n:02d}.jpg", cols=SHEET_COLS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: шот-лист не собран: {}", exc)
    sheets.motion = list(sheets.shots)

    # cut pairs
    pairs = [c for c in (timeline or {}).get("cuts") or [] if c.get("before") and c.get("after")]
    for n, i in enumerate(range(0, len(pairs), CUT_PAIRS_PER_SHEET), start=1):
        chunk = pairs[i : i + CUT_PAIRS_PER_SHEET]
        cells_c: list[tuple[Path, str]] = []
        for c in chunk:
            cells_c.append((Path(c["before"]), f"cut {_tc(float(c['t']))} −0.2s"))
            cells_c.append((Path(c["after"]), f"cut {_tc(float(c['t']))} +0.2s"))
        try:
            sheets.cuts.append(compose_labeled_grid(cells_c, out_dir / f"cuts_{n:02d}.jpg", cols=4, cell_height=240))
        except Exception as exc:  # noqa: BLE001
            logger.warning("style_extract: лист склеек не собран: {}", exc)
    return sheets


def _pick_evenly(items: list[Any], n: int) -> list[Any]:
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[min(len(items) - 1, int(i * step + step / 2))] for i in range(n)]


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
    for fp in frames[:40]:
        try:
            im = Image.open(fp).convert("RGB")
            # субтитры внизу — не считать в палитру
            im = im.crop((0, 0, im.width, int(im.height * 0.7)))
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


def frame_diff(a: Path, b: Path) -> float | None:
    try:
        from PIL import Image, ImageChops, ImageStat

        ia = Image.open(a).convert("L").resize((48, 27))
        ib = Image.open(b).convert("L").resize((48, 27))
    except Exception:  # noqa: BLE001
        return None
    return ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0] / 255


def hist_distance(a: Path, b: Path) -> float | None:
    """1 − пересечение RGB-гистограмм (4×4×4) двух кадров без полосы субтитров, 0..1."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None

    def _hist(fp: Path) -> list[float]:
        im = Image.open(fp).convert("RGB")
        im = im.crop((0, 0, im.width, int(im.height * 0.7))).resize((64, 64))
        h = [0] * 64
        for r, g, bb in im.getdata():
            h[(r // 64) * 16 + (g // 64) * 4 + (bb // 64)] += 1
        n = float(sum(h)) or 1.0
        return [x / n for x in h]

    try:
        ha, hb = _hist(a), _hist(b)
    except Exception:  # noqa: BLE001
        return None
    return round(1.0 - sum(min(x, y) for x, y in zip(ha, hb)), 4)


def annotate_cuts(cuts: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> None:
    """В каждую склейку — ``hist_delta`` и ``uncertain`` (слабый score + похожие кадры до/после)."""
    by_t = {round(float(p["t"]), 3): p for p in pairs}
    for c in cuts:
        p = by_t.get(round(float(c["t"]), 3))
        if not p or not p.get("before") or not p.get("after"):
            continue
        d = hist_distance(Path(p["before"]), Path(p["after"]))
        if d is None:
            continue
        c["hist_delta"] = d
        c["uncertain"] = bool(float(c["score"]) < UNCERTAIN_SCORE and d < UNCERTAIN_HIST)


def static_ratio(keyframe_pairs: list[tuple[Path, Path]]) -> float | None:
    """Доля планов, где start≈end (камера и объект почти не движутся)."""
    vals = [d for d in (frame_diff(a, b) for a, b in keyframe_pairs) if d is not None]
    if not vals:
        return None
    return round(sum(1 for d in vals if d < 0.04) / len(vals), 3)


def shot_motion_energy(key_dir: Path, shots: list[dict[str, Any]]) -> None:
    """В каждый план — ``motion_energy`` (start→end diff 0..1) и ``static`` (bool)."""
    for s in shots:
        a = key_dir / f"sh_{int(s['i']):03d}_start.jpg"
        b = key_dir / f"sh_{int(s['i']):03d}_end.jpg"
        if a.is_file() and b.is_file():
            d = frame_diff(a, b)
            if d is not None:
                s["motion_energy"] = round(d, 3)
                s["static"] = d < 0.04


# ── аудио ──────────────────────────────────────────────────────────────────


def extract_audio(video: Path, out: Path) -> Path | None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = run_cmd(_ff("-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)), timeout=600)
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
        proc = run_cmd(["demucs", "--two-stems=vocals", "-o", str(out_dir / "demucs"), str(audio)], timeout=1800)
        if proc.returncode == 0:
            stem_dir = next((p for p in (out_dir / "demucs").rglob("vocals.wav")), None)
            if stem_dir is not None:
                shutil.copyfile(stem_dir, voice)
                other = stem_dir.with_name("no_vocals.wav")
                if other.is_file():
                    shutil.copyfile(other, music)
                return {"voice": voice, "music": music if music.is_file() else None, "method": "demucs"}  # type: ignore[dict-item]
    proc = run_cmd(_ff("-i", str(audio), "-af", "highpass=f=120,lowpass=f=3800", str(voice)), timeout=600)
    v = voice if proc.returncode == 0 and voice.is_file() else None
    proc = run_cmd(_ff("-i", str(audio), "-af", "lowpass=f=160,highpass=f=40", str(music)), timeout=600)
    m = music if proc.returncode == 0 and music.is_file() else None
    return {"voice": v, "music": m, "method": "bandpass"}  # type: ignore[dict-item]


def loudness(audio: Path) -> dict[str, float | None]:
    proc = run_cmd(["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af", "ebur128=framelog=quiet", "-f", "null", "-"], timeout=600)
    text = proc.stderr or ""
    m = _LUFS_RE.findall(text)
    lra = _LRA_RE.findall(text)
    lufs = float(m[-1]) if m else None
    return {"lufs": None if lufs is None or lufs < -60 else round(lufs, 2), "lra": round(float(lra[-1]), 2) if lra else None}


def silences(audio: Path, *, noise_db: float = -35.0, min_len_s: float = 0.25) -> list[dict[str, float]]:
    proc = run_cmd(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af", f"silencedetect=noise={noise_db}dB:d={min_len_s}", "-f", "null", "-"],
        timeout=600,
    )
    out: list[dict[str, float]] = []
    for m in _SIL_END_RE.finditer(proc.stderr or ""):
        end = float(m.group(1))
        dur = float(m.group(2))
        out.append({"start": round(end - dur, 3), "end": round(end, 3), "len": round(dur, 3)})
    return out


def speech_metrics(words: list[Any], duration_s: float, sil: list[dict[str, float]]) -> dict[str, Any]:
    """Темп речи и паузы по словам (``WordTS``: word/start/end)."""
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
        "transcript": text[:8000],
        "silences": len(sil),
    }


# ── сборка профиля ролика ──────────────────────────────────────────────────

AsrFn = Callable[[Path, str], list[Any]]


def _default_asr(audio: Path, language: str) -> list[Any]:
    from app.services.asr import transcribe_words

    return transcribe_words(audio, language=language)


def _dump_words(words: list[Any], out: Path) -> None:
    try:
        from app.services.whisper import dump_words_json

        dump_words_json(words, out)
    except Exception:  # noqa: BLE001
        out.write_text(
            json.dumps(
                [{"word": getattr(w, "word", ""), "start": getattr(w, "start", 0), "end": getattr(w, "end", 0)} for w in words],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def analyze_video(
    video: Path,
    out_dir: Path,
    *,
    max_sheets: int = DEFAULT_MAX_SHEETS,
    language: str = "ru",
    asr_fn: AsrFn | None = None,
    skip_asr: bool = False,
    ocr_fn: ocr_mod.OcrFn | None = None,
    skip_ocr: bool = False,
    frames_per_min: int = DEFAULT_FRAMES_PER_MIN,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Полный детерминированный разбор одного ролика → ``metrics.json`` + листы.

    Порядок: probe → scene-scores → предварительные склейки → таймлайн кадров
    (120/мин + хук + ±0.2 с у склеек) → OCR субтитров → уточнение склеек по
    сменам субтитров → листы → палитра/энергия движения → аудио (стемы,
    громкость, паузы) → транскрипт (субтитры, иначе ASR) → метрики речи.
    """

    def _p(stage: str) -> None:
        if progress:
            progress(stage)

    out_dir.mkdir(parents=True, exist_ok=True)
    _p("probe")
    fmt = probe(video)
    duration = float(fmt["duration_s"] or 0.0)
    (out_dir / "probe.json").write_text(json.dumps(fmt, ensure_ascii=False, indent=2), encoding="utf-8")

    _p("cuts")
    scores = scene_scores(video, out_dir / "scene_scores.json")
    cuts = detect_cuts(scores, duration)

    _p("frames")
    tl_dir = out_dir / "timeline"
    timeline = sample_timeline(video, duration, tl_dir, frames_per_min=frames_per_min, cuts=cuts)

    # OCR субтитров
    subs: list[dict[str, Any]] = []
    sub_info: dict[str, Any] = {"backend": "", "lines": 0, "band": None}
    if not skip_ocr:
        fn = ocr_fn or ocr_mod.default_ocr()
        if fn is None:
            sub_info["error"] = "OCR-бэкенд не найден (pip install rapidocr-onnxruntime)"
        else:
            _p("ocr")
            frames_sorted = sorted(list(timeline["base"]) + list(timeline["hook"]), key=lambda x: x[0])
            raw = ocr_mod.read_frames(frames_sorted, fn)
            (out_dir / "ocr_raw.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            band = ocr_mod.subtitle_band(raw)
            lines = ocr_mod.build_lines(raw, band=band, frame_step_s=1.0 / float(timeline["rate"]))
            subs = [ln.as_dict() for ln in lines]
            sub_info = {"backend": "injected" if ocr_fn else ocr_mod.backend_name(), "lines": len(subs), "band": band}
    (out_dir / "subtitles.json").write_text(json.dumps(subs, ensure_ascii=False, indent=1), encoding="utf-8")

    # уточнение склеек по сменам субтитров (слабые кандидаты)
    if subs:
        cue_times = [float(s["start"]) for s in subs]
        cuts2 = detect_cuts(scores, duration, cue_times=cue_times)
        new = [c for c in cuts2 if all(abs(c["t"] - o["t"]) > MIN_SHOT_LEN_S for o in cuts)]
        if new:
            for c in new:
                pair: dict[str, Any] = {"t": float(c["t"])}
                for tag, tt in (("before", c["t"] - CUT_PAD_S), ("after", c["t"] + CUT_PAD_S)):
                    fp = tl_dir / f"cut_{int(round(c['t'] * 1000)):07d}_{tag}.jpg"
                    try:
                        pair[tag] = str(extract_frame(video, min(max(tt, 0.0), max(duration - 0.05, 0.0)), fp))
                    except ExtractError:
                        pass
                timeline["cuts"].append(pair)
            timeline["cuts"].sort(key=lambda c: c["t"])
        cuts = cuts2
    annotate_cuts(cuts, timeline["cuts"])
    shots = shots_from_cuts(cuts, duration)
    (out_dir / "cuts.json").write_text(json.dumps(cuts, ensure_ascii=False, indent=1), encoding="utf-8")

    _p("sheets")
    sheets = build_sheets(video, shots, duration, out_dir / "sheets", max_sheets=max_sheets, timeline=timeline)
    shot_motion_energy(out_dir / "sheets" / "keyframes", shots)
    (out_dir / "shots.json").write_text(json.dumps(shots, ensure_ascii=False, indent=1), encoding="utf-8")
    motion = shot_metrics(shots, duration)
    energies = [float(s["motion_energy"]) for s in shots if s.get("motion_energy") is not None]
    motion["static_ratio"] = round(sum(1 for e in energies if e < 0.04) / len(energies), 3) if energies else None
    motion["motion_energy_median"] = round(statistics.median(energies), 3) if energies else None
    pal = palette([p for _t, p in timeline["base"]][:: max(1, len(timeline["base"]) // 24 or 1)])

    # аудио
    audio_info: dict[str, Any] = {"has_audio": bool(fmt.get("has_audio"))}
    speech: dict[str, Any] = {}
    music: dict[str, Any] = {"present": False}
    words: list[Any] = []
    transcript_source = "none"
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
                json.dumps({"full": full_loud, "voice": voice_loud, "music": music_loud}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (out_dir / "silence.json").write_text(json.dumps(sil, ensure_ascii=False), encoding="utf-8")
            audio_info["loudness_lufs"] = full_loud.get("lufs")
            audio_info["lra"] = full_loud.get("lra")
            music_lufs = music_loud.get("lufs")
            voice_lufs = voice_loud.get("lufs")
            music = {
                "present": music_lufs is not None and music_lufs > -50,
                "loudness_lufs": music_lufs,
                "level_vs_voice_db": round(music_lufs - voice_lufs, 2) if music_lufs is not None and voice_lufs is not None else None,
            }
            if len(subs) >= 3:
                transcript_source = "subtitles"
                words = ocr_mod.lines_to_words([ocr_mod.SubtitleLine(**{k: s[k] for k in ("start", "end", "text", "conf")}) for s in subs])
            elif not skip_asr:
                _p("asr")
                try:
                    words = (asr_fn or _default_asr)(stems.get("voice") or audio, language)
                    transcript_source = "asr" if words else "none"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("style_extract: ASR не удался ({}): {}", video.name, exc)
                    audio_info["asr_error"] = str(exc)[:300]
            _dump_words(words, out_dir / "words.json")
            speech = speech_metrics(words, duration, sil)
    elif len(subs) >= 3:
        transcript_source = "subtitles"
        words = ocr_mod.lines_to_words([ocr_mod.SubtitleLine(**{k: s[k] for k in ("start", "end", "text", "conf")}) for s in subs])
        speech = speech_metrics(words, duration, [])
    speech["transcript_source"] = transcript_source

    # связка закадра и планов (детерминированная часть)
    from app.services.style_agent import linkage as linkage_mod

    link = linkage_mod.link_subtitles_to_shots(subs, shots)
    script = linkage_mod.script_metrics(subs, duration)
    (out_dir / "linkage.json").write_text(json.dumps(link, ensure_ascii=False, indent=1), encoding="utf-8")

    profile = {
        "video": video.name,
        "format": fmt,
        "shots": shots,
        "cuts": cuts,
        "motion": motion,
        "palette": pal,
        "audio": audio_info,
        "speech": speech,
        "music": music,
        "subtitles": subs,
        "subtitles_info": sub_info,
        "linkage": link,
        "script": script,
        "words_n": len(words),
        "timeline": {
            "frames": int(timeline.get("count") or 0),
            "rate_fps": timeline.get("rate"),
            "frames_per_min": frames_per_min,
            "hook_density": HOOK_DENSITY,
            "cut_pad_s": CUT_PAD_S,
        },
        "sheets": {
            "overview": [str(p) for p in sheets.overview],
            "shots": [str(p) for p in sheets.shots],
            "cuts": [str(p) for p in sheets.cuts],
            "motion": [str(p) for p in sheets.shots],
            "keyframes": len(sheets.keyframes),
        },
    }
    (out_dir / "metrics.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile
