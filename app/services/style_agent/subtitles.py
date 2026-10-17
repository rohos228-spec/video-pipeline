"""OCR вшитых субтитров (локально, бесплатно: EasyOCR ru+en, GPU если есть).

Кадры каждые ``step_s`` → OCR → авто-поиск полосы субтитров (горизонтальная
полоса, где текст чаще всего МЕНЯЕТСЯ) → склейка одинаковых строк подряд в
сегменты → ``subtitles.json`` + ``subtitles.srt`` + визуальный стиль.
"""

from __future__ import annotations

import difflib
import json
import statistics
import tempfile
from pathlib import Path
from typing import Any, Callable

from loguru import logger

DEFAULT_STEP_S = 0.4
MIN_CONF = 0.35
BAND_BINS = 20
SAME_RATIO = 0.82

# reader(img_path) -> [(box[[x,y]x4], text, conf)]
OcrFn = Callable[[Path], list[tuple[list[list[float]], str, float]]]

_readers: dict[str, Any] = {}


def _get_reader(langs: tuple[str, ...]) -> Any:
    key = "+".join(langs)
    if key not in _readers:
        import easyocr  # type: ignore

        try:
            import torch

            gpu = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            gpu = False
        _readers[key] = easyocr.Reader(list(langs), gpu=gpu, verbose=False)
    return _readers[key]


def _read(img: Path, langs: tuple[str, ...]) -> list[tuple[list[list[float]], str, float]]:
    return [
        ([[float(x), float(y)] for x, y in b], str(t), float(c)) for b, t, c in _get_reader(langs).readtext(str(img))
    ]


def _latin_ratio(texts: list[str]) -> float:
    lat = sum(1 for t in texts for ch in t if "a" <= ch.lower() <= "z")
    cyr = sum(1 for t in texts for ch in t if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    return lat / (lat + cyr) if (lat + cyr) else 0.5


def make_auto_ocr(sample: list[Path]) -> OcrFn:
    """ru+en на пробных кадрах → если текст латиницей, дальше только en (иначе ru), без смеси алфавитов."""
    texts: list[str] = []
    for fp in sample:
        try:
            texts += [t for _, t, c in _read(fp, ("ru", "en")) if c >= MIN_CONF]
        except Exception:  # noqa: BLE001
            continue
    r = _latin_ratio(texts)
    langs: tuple[str, ...] = ("en",) if r >= 0.7 else (("ru",) if r <= 0.3 else ("ru", "en"))
    logger.info("subtitles: OCR язык {} (латиница {:.0%})", "+".join(langs), r)

    def _fn(img: Path) -> list[tuple[list[list[float]], str, float]]:
        return _read(img, langs)

    _fn.langs = langs  # type: ignore[attr-defined]
    return _fn


def ocr_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("easyocr") is not None
    except Exception:  # noqa: BLE001
        return False


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("|", "i").split())


def _fmt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def pick_band(frames: list[list[dict[str, Any]]], height: int) -> tuple[float, float] | None:
    """Полоса по Y (доли высоты), где больше всего РАЗНЫХ текстов."""
    texts: dict[int, set[str]] = {}
    for dets in frames:
        for d in dets:
            b = min(BAND_BINS - 1, int(d["yc"] / max(1, height) * BAND_BINS))
            texts.setdefault(b, set()).add(_norm(d["text"]))
    if not texts:
        return None
    best = max(texts, key=lambda k: len(texts[k]))
    if len(texts[best]) < 2:
        return None
    lo, hi = best, best
    while lo - 1 in texts and len(texts[lo - 1]) >= len(texts[best]) * 0.4:
        lo -= 1
    while hi + 1 in texts and len(texts[hi + 1]) >= len(texts[best]) * 0.4:
        hi += 1
    return lo / BAND_BINS, (hi + 1) / BAND_BINS


def segments_from_lines(lines: list[tuple[float, str]], step_s: float) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    for t, text in lines:
        if not text:
            if segs and segs[-1].get("_open"):
                segs[-1]["_open"] = False
            continue
        if segs and segs[-1].get("_open") and difflib.SequenceMatcher(None, _norm(segs[-1]["text"]), _norm(text)).ratio() >= SAME_RATIO:
            segs[-1]["end"] = round(t + step_s, 3)
            if len(text) > len(segs[-1]["text"]):
                segs[-1]["text"] = text
            continue
        if segs:
            segs[-1]["_open"] = False
        segs.append({"start": round(t, 3), "end": round(t + step_s, 3), "text": text, "_open": True})
    for s in segs:
        s.pop("_open", None)
    return segs


def _style(dets: list[dict[str, Any]], width: int, height: int, frames_dir: Path) -> dict[str, Any]:
    if not dets:
        return {}
    hs = [d["h"] for d in dets]
    ycs = [d["yc"] for d in dets]
    words = [len(d["text"].split()) for d in dets]
    out: dict[str, Any] = {
        "font_height_frac": round(statistics.median(hs) / max(1, height), 4),
        "font_height_px": round(statistics.median(hs), 1),
        "y_center_frac": round(statistics.median(ycs) / max(1, height), 3),
        "position": "bottom" if statistics.median(ycs) / height > 0.66 else ("top" if statistics.median(ycs) / height < 0.33 else "center"),
        "words_per_line": round(statistics.median(words), 1),
        "max_words_per_line": max(words),
        "uppercase_ratio": round(sum(1 for d in dets if d["text"].isupper()) / len(dets), 2),
    }
    try:
        from PIL import Image

        fills, outs = [], []
        for d in dets[:40]:
            im = Image.open(frames_dir / d["frame"]).convert("RGB")
            x0, y0, x1, y1 = d["bbox"]
            px = sorted(im.crop((int(x0), int(y0), int(x1), int(y1))).getdata(), key=lambda p: sum(p))
            if len(px) < 20:
                continue
            k = max(1, len(px) // 10)
            fills.append(tuple(sum(c) / k for c in zip(*px[-k:])))
            outs.append(tuple(sum(c) / k for c in zip(*px[:k])))
        if fills:
            f = tuple(int(statistics.median(c)) for c in zip(*fills))
            o = tuple(int(statistics.median(c)) for c in zip(*outs))
            out["fill_hex"] = "#%02X%02X%02X" % f
            out["outline_hex"] = "#%02X%02X%02X" % o
            out["outline"] = sum(o) < 150
    except Exception as exc:  # noqa: BLE001
        logger.debug("subtitles: цвет не определён: {}", exc)
    return out


def extract_subtitles(
    video: Path,
    out_dir: Path,
    duration_s: float,
    width: int,
    height: int,
    *,
    step_s: float = DEFAULT_STEP_S,
    ocr_fn: OcrFn | None = None,
    run_cmd: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """→ {segments, text, style, band, frames_ocr}; пишет subtitles.json / .srt."""
    from app.services.style_agent import extract as ex

    run = run_cmd or ex.run_cmd
    ocr = ocr_fn
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="subs_") as td:
        fdir = Path(td)
        proc = run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vf", f"fps=1/{step_s}", "-q:v", "3", str(fdir / "s_%05d.jpg")],
            timeout=900,
        )
        files = sorted(fdir.glob("s_*.jpg"))
        if not files:
            return {"segments": [], "text": "", "style": {}, "error": (getattr(proc, "stderr", "") or "")[:200]}
        if ocr is None:
            step = max(1, len(files) // 8)
            ocr = make_auto_ocr(files[::step][:8])
        per_frame: list[list[dict[str, Any]]] = []
        for fp in files:
            dets = []
            try:
                for box, text, conf in ocr(fp):
                    if conf < MIN_CONF or not text.strip():
                        continue
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    dets.append(
                        {"text": text.strip(), "conf": float(conf), "yc": float(min(ys) + max(ys)) / 2, "h": float(max(ys) - min(ys)), "x": float(min(xs)), "bbox": (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))), "frame": fp.name}
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("subtitles: OCR кадра {} не удался: {}", fp.name, exc)
            per_frame.append(dets)
        band = pick_band(per_frame, height)
        lines: list[tuple[float, str]] = []
        band_dets: list[dict[str, Any]] = []
        for i, dets in enumerate(per_frame):
            t = i * step_s
            if band is None:
                lines.append((t, ""))
                continue
            inb = [d for d in dets if band[0] <= d["yc"] / height <= band[1]]
            band_dets.extend(inb)
            inb.sort(key=lambda d: (round(d["yc"] / max(1.0, d["h"])), d["x"]))
            lines.append((t, " ".join(d["text"] for d in inb)))
        segs = segments_from_lines(lines, step_s)
        style = _style(band_dets, width, height, fdir)
    result = {
        "segments": segs,
        "text": " ".join(s["text"] for s in segs),
        "style": style,
        "band": list(band) if band else None,
        "frames_ocr": len(files),
        "step_s": step_s,
        "engine": "easyocr " + "+".join(getattr(ocr, "langs", ("custom",))),
    }
    (out_dir / "subtitles.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    srt = "\n".join(f"{i}\n{_fmt_ts(s['start'])} --> {_fmt_ts(s['end'])}\n{s['text']}\n" for i, s in enumerate(segs, 1))
    (out_dir / "subtitles.srt").write_text(srt, encoding="utf-8")
    return result
