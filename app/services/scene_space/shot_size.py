
from __future__ import annotations

import re

SIZE_LADDER: tuple[str, ...] = (
    "EWS",
    "WS",
    "FS",
    "MWS",
    "MS",
    "MCU",
    "CU",
    "ECU",
)
SIZE_INDEX: dict[str, int] = {code: i for i, code in enumerate(SIZE_LADDER)}

_JUNCTION_OK_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"CU", "ECU"}),
        frozenset({"EWS", "WS"}),
        frozenset({"CU", "MWS"}),
        frozenset({"MS", "WS"}),
        frozenset({"MCU", "FS"}),
    }
)

_LEGACY_SIZE: dict[str, str] = {
    "VLS": "EWS",
    "ELS": "EWS",
    "XLS": "EWS",
    "XWS": "EWS",
    "LS": "WS",
    "WIDE": "WS",
    "FULL": "FS",
    "FULLSHOT": "FS",
    "KNEE": "MWS",
    "MLS": "MWS",
    "WAIST": "MS",
    "MEDIUM": "MS",
    "CHEST": "MCU",
    "BCU": "ECU",
    "INSERT": "ECU",
    "INS": "ECU",
    "DETAIL": "ECU",
    "MACRO": "ECU",
}

_CODE_TOKEN_RE = re.compile(
    r"\b(EWS|XWS|XLS|ELS|VLS|MWS|MCU|ECU|BCU|MLS|WS|FS|MS|CU|LS|"
    r"INSERT|INS|WIDE|FULLSHOT|FULL|KNEE|WAIST|MEDIUM|CHEST|DETAIL|MACRO)\b",
    re.IGNORECASE,
)
_LADDER_SPLIT_RE = re.compile(r"\s*(?:→|->|↔|/)\s*")
_SIDE_WORD_RE = re.compile(
    r"\b(left|right|левый|правый|слева|справа)\b",
    re.IGNORECASE,
)

_RU_PHRASES: tuple[tuple[str, str], ...] = (
    ("голова и плечи", "MCU"),
    ("головы и плеч", "MCU"),
    ("по плеч", "MCU"),
    ("средне-крупн", "MCU"),
    ("среднекрупн", "MCU"),
    ("средний крупный", "MCU"),
    ("средне-крупный", "MCU"),
    ("первый средний", "MS"),
    ("по пояс", "MS"),
    ("поясн", "MS"),
    ("второй средний", "MWS"),
    ("по колени", "MWS"),
    ("по колен", "MWS"),
    ("колен", "MWS"),
    ("полный рост", "FS"),
    ("во весь рост", "FS"),
    ("во весь", "FS"),
    ("сверхдальн", "EWS"),
    ("очень общий", "EWS"),
    ("панорам", "EWS"),
    ("сверхкрупн", "ECU"),
    ("очень крупн", "ECU"),
    ("врезка предмета", "ECU"),
    ("врезка", "ECU"),
    ("макро", "ECU"),
    ("глаз", "ECU"),
    ("деталь", "ECU"),
    ("крупный план лица", "CU"),
    ("план лица", "CU"),
    ("средне-общий", "MWS"),
    ("среднеобщий", "MWS"),
    ("дальн", "EWS"),
    ("общий", "WS"),
    ("крупн", "CU"),
    ("средн", "MS"),
)


def size_from_ru(text: str) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        raise ValueError("unknown shot size: empty")
    chunk = _LADDER_SPLIT_RE.split(raw)[0].strip()
    chunk = _SIDE_WORD_RE.sub(" ", chunk)
    chunk = " ".join(chunk.split())
    if not chunk:
        raise ValueError(f"unknown shot size: {text!r}")

    token_match = _CODE_TOKEN_RE.search(chunk)
    if token_match:
        token = token_match.group(1).upper()
        if token in SIZE_INDEX:
            return token
        mapped = _LEGACY_SIZE.get(token)
        if mapped is not None:
            return mapped

    compact = chunk.replace("ё", "е").casefold()
    for phrase, code in _RU_PHRASES:
        if phrase in compact:
            return code

    folded = chunk.replace("-", "").replace(" ", "").upper()
    if folded in SIZE_INDEX:
        return folded
    mapped = _LEGACY_SIZE.get(folded)
    if mapped is not None:
        return mapped

    raise ValueError(f"unknown shot size: {text!r}")


def size_index(code: str) -> int:
    return SIZE_INDEX[size_from_ru(code)]


def junction_verdict(prev: str, cur: str) -> str:
    a = size_from_ru(prev)
    b = size_from_ru(cur)
    if a == b:
        return "error_same"
    pair = frozenset({a, b})
    if pair in _JUNCTION_OK_PAIRS:
        return "ok"
    delta = abs(SIZE_INDEX[a] - SIZE_INDEX[b])
    if delta == 1:
        return "warn_adj"
    if delta == 2:
        return "ok"
    return "warn_smash"


def size_at(index: int) -> str:
    return SIZE_LADDER[max(0, min(len(SIZE_LADDER) - 1, int(index)))]


def size_step(code: str, delta: int) -> str:
    i = size_index(code)
    j = max(0, min(len(SIZE_LADDER) - 1, i + int(delta)))
    if j == i:
        j = max(0, min(len(SIZE_LADDER) - 1, i - int(delta)))
    return SIZE_LADDER[j]
