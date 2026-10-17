"""OCR вшитых субтитров → строки с таймкодами.

Субтитры — источник транскрипта №1 (точнее ASR на музыке и шуме). Бэкенды:
``rapidocr_onnxruntime`` (pip, без системных зависимостей) → ``pytesseract``
→ нет. Функция распознавания инжектируется (``OcrFn``), тесты подменяют.

Алгоритм: кадры таймлайна (2 fps) → crop нижней полосы → OCR → боксы,
центр которых лежит в устойчивой «полосе субтитров» (медиана y ± 7 %) →
нормализация текста → склейка подряд идущих одинаковых (fuzzy ≥ 0.85)
кадров в строку ``{start, end, text, conf}``.
"""

from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

# (text, confidence, center_y_rel 0..1 относительно полного кадра, center_x_rel)
OcrBox = tuple[str, float, float, float]
OcrFn = Callable[[Path], list[OcrBox]]

SUB_BAND_TOP = 0.55
SUB_BAND_BOTTOM = 0.97
MIN_CONF = 0.6
SAME_TEXT_RATIO = 0.85
MAX_LINE_GAP_S = 0.75

_WS_RE = re.compile(r"\s+")
_JUNK_RE = re.compile(r"^[\W_]+$")

_backend_cache: dict[str, Any] = {}


def _rapidocr() -> OcrFn | None:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    engine = _backend_cache.get("rapidocr")
    if engine is None:
        engine = RapidOCR()
        _backend_cache["rapidocr"] = engine

    def _run(frame: Path) -> list[OcrBox]:
        import numpy as np
        from PIL import Image

        im = Image.open(frame).convert("RGB")
        w, h = im.size
        top = int(h * SUB_BAND_TOP)
        crop = im.crop((0, top, w, int(h * SUB_BAND_BOTTOM)))
        res, _elapsed = engine(np.array(crop))
        out: list[OcrBox] = []
        for box, text, conf in res or []:
            try:
                ys = [float(p[1]) for p in box]
                xs = [float(p[0]) for p in box]
                cy = (top + sum(ys) / len(ys)) / h
                cx = (sum(xs) / len(xs)) / w
                out.append((str(text), float(conf), cy, cx))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        return out

    return _run


def _tesseract() -> OcrFn | None:
    try:
        import pytesseract  # type: ignore
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None

    def _run(frame: Path) -> list[OcrBox]:
        im = Image.open(frame).convert("RGB")
        w, h = im.size
        top = int(h * SUB_BAND_TOP)
        crop = im.crop((0, top, w, int(h * SUB_BAND_BOTTOM)))
        data = pytesseract.image_to_data(crop, output_type=pytesseract.Output.DICT)
        lines: dict[tuple[int, int, int], list[tuple[str, float, float, float]]] = {}
        for i, txt in enumerate(data.get("text") or []):
            if not str(txt).strip():
                continue
            try:
                conf = float(data["conf"][i]) / 100.0
            except (TypeError, ValueError, KeyError, IndexError):
                conf = 0.0
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            cy = (top + float(data["top"][i]) + float(data["height"][i]) / 2) / h
            cx = (float(data["left"][i]) + float(data["width"][i]) / 2) / w
            lines.setdefault(key, []).append((str(txt), conf, cy, cx))
        out: list[OcrBox] = []
        for words in lines.values():
            text = " ".join(w[0] for w in words)
            conf = statistics.mean(w[1] for w in words)
            cy = statistics.mean(w[2] for w in words)
            cx = statistics.mean(w[3] for w in words)
            out.append((text, conf, cy, cx))
        return out

    return _run


def _easyocr() -> OcrFn | None:
    """EasyOCR (GPU, если есть CUDA) - fallback, когда rapidocr не установлен."""
    try:
        import easyocr  # type: ignore
        import numpy as np
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    if "easyocr" not in _backend_cache:
        try:
            import torch  # type: ignore

            gpu = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            gpu = False
        try:
            _backend_cache["easyocr"] = easyocr.Reader(["en"], gpu=gpu, verbose=False)
        except Exception:  # noqa: BLE001
            return None
    reader = _backend_cache["easyocr"]

    def _run(frame: Path) -> list[OcrBox]:
        im = Image.open(frame).convert("RGB")
        w, h = im.size
        top = int(h * SUB_BAND_TOP)
        crop = np.asarray(im.crop((0, top, w, int(h * SUB_BAND_BOTTOM))))
        out: list[OcrBox] = []
        for box, text, conf in reader.readtext(crop, paragraph=False):
            if not str(text).strip():
                continue
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
            out.append((str(text), float(conf), (top + sum(ys) / len(ys)) / h, (sum(xs) / len(xs)) / w))
        return out

    return _run


def default_ocr() -> OcrFn | None:
    """Первый доступный бэкенд или None (тогда транскрипт — из ASR)."""
    for name, factory in (("rapidocr", _rapidocr), ("easyocr", _easyocr), ("tesseract", _tesseract)):
        fn = factory()
        if fn is not None:
            _backend_cache["name"] = name
            return fn
    return None


def backend_name() -> str:
    return str(_backend_cache.get("name") or "")


def normalize_text(text: str) -> str:
    t = _WS_RE.sub(" ", str(text or "")).strip()
    t = t.replace("|", "I").replace("’", "'").replace("`", "'")
    return t


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SAME_TEXT_RATIO


@dataclass
class SubtitleLine:
    start: float
    end: float
    text: str
    conf: float

    def as_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text, "conf": round(self.conf, 3)}


def read_frames(frames: list[tuple[float, Path]], ocr: OcrFn, *, progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any]]:
    """OCR каждого кадра → [{t, boxes:[(text,conf,cy,cx)]}] (сырой слой, сохраняется в ocr_raw.json)."""
    raw: list[dict[str, Any]] = []
    for i, (t, fp) in enumerate(frames):
        try:
            boxes = ocr(fp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ocr: кадр {} пропущен: {}", fp.name, exc)
            boxes = []
        raw.append({"t": round(float(t), 3), "boxes": [list(b) for b in boxes]})
        if progress and (i % 10 == 0 or i == len(frames) - 1):
            progress(i + 1, len(frames))
    return raw


def subtitle_band(raw: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Полоса y, где текст встречается чаще всего (субтитры), ±7 %."""
    ys = [
        float(b[2])
        for fr in raw
        for b in fr.get("boxes") or []
        if float(b[1]) >= MIN_CONF and not _JUNK_RE.match(str(b[0]))
    ]
    if len(ys) < 3:
        return None
    # Мода по корзинам 2 %: субтитры дают самую плотную корзину.
    bins: dict[int, int] = {}
    for y in ys:
        bins[int(y * 50)] = bins.get(int(y * 50), 0) + 1
    top_bin = max(bins, key=lambda k: bins[k])
    center = (top_bin + 0.5) / 50
    return (max(0.0, center - 0.07), min(1.0, center + 0.07))


def _looks_like_logo(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters or len(text.split()) > 2:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.8


def build_lines(raw: list[dict[str, Any]], *, band: tuple[float, float] | None = None, frame_step_s: float = 0.5) -> list[SubtitleLine]:
    """Сырые боксы → строки субтитров с таймкодами."""
    band = band or subtitle_band(raw)
    if band is None:
        return []
    per_frame: list[tuple[float, str, float]] = []
    for fr in raw:
        picked = []
        for b in fr.get("boxes") or []:
            text, conf, cy = normalize_text(str(b[0])), float(b[1]), float(b[2])
            if conf < MIN_CONF or not text or _JUNK_RE.match(text):
                continue
            if band[0] <= cy <= band[1]:
                picked.append((cy, text, conf))
        picked.sort()
        if len(picked) > 1:
            # Логотип/надпись на одежде, попавшая в полосу (КАПС, ≤ 2 слов), — не субтитр, если рядом есть обычный текст
            normal = [p for p in picked if not _looks_like_logo(p[1])]
            if normal:
                picked = normal
        if picked:
            per_frame.append((float(fr["t"]), " ".join(p[1] for p in picked), statistics.mean(p[2] for p in picked)))
        else:
            per_frame.append((float(fr["t"]), "", 0.0))
    lines: list[SubtitleLine] = []
    cur: SubtitleLine | None = None
    for t, text, conf in per_frame:
        if not text:
            if cur is not None and t - cur.end > MAX_LINE_GAP_S:
                lines.append(cur)
                cur = None
            continue
        if cur is not None and _same(cur.text, text) and t - cur.end <= MAX_LINE_GAP_S:
            cur.end = t + frame_step_s
            if conf > cur.conf:
                cur.text, cur.conf = text, conf
            continue
        if cur is not None:
            lines.append(cur)
        cur = SubtitleLine(start=t, end=t + frame_step_s, text=text, conf=conf)
    if cur is not None:
        lines.append(cur)
    # Схлопнуть соседей, у которых конец/начало перекрываются из-за шага кадров.
    for a, b in zip(lines, lines[1:]):
        if a.end > b.start:
            a.end = b.start
    return [ln for ln in lines if ln.text]


def lines_to_words(lines: list[SubtitleLine]) -> list[Any]:
    """Строки → псевдо-слова (как ``WordTS``: word/start/end) для speech_metrics."""

    class _W:
        __slots__ = ("word", "start", "end")

        def __init__(self, word: str, start: float, end: float) -> None:
            self.word, self.start, self.end = word, start, end

    out: list[Any] = []
    for ln in lines:
        toks = [t for t in ln.text.split(" ") if t]
        if not toks:
            continue
        step = (ln.end - ln.start) / len(toks)
        for i, tok in enumerate(toks):
            out.append(_W(tok, ln.start + i * step, ln.start + (i + 1) * step))
    return out
