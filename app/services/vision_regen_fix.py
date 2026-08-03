"""Авто-правки промта перед vision-regen (нет c0X / двойники).

Если агент не дал ## db_patch, система сама дописывает жёсткий VISION_FIX
в промт_картинки по critical findings + Базе кадра.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.check_analysis import (
    extract_critical_frame_regen_targets,
    extract_vision_issues,
    normalize_frame_regen_token,
)

_CID_RE = re.compile(r"\bc(\d{1,3})\b", re.IGNORECASE)
_MISSING_RE = re.compile(
    r"(?i)(?:нет|отсутств\w*|missing|без|не\s+вид\w*|"
    r"должен\s+(?:быть|присутств\w*)|нужен|обязателен)\s+"
    r"(?:заявленн\w+\s+|обязательн\w+\s+)?(c\d{1,3})"
)
_CLONE_RE = re.compile(
    r"(?i)двойник|клон|лишн\w+\s+(?:фигур|персонаж|мужск|женск)|"
    r"не\s+предусмотр\w+|не\s+указан\w+\s+в\s+баз|"
    r"похож\w+\s+(?:мужск|фигур)|два\s+(?:одинаков|похож)"
)
_VISION_FIX_RE = re.compile(
    r"\n?\[VISION_FIX\][\s\S]*?\[/VISION_FIX\]\s*",
    re.IGNORECASE,
)


def _norm_cid(raw: str) -> str:
    m = re.match(r"^c?(\d{1,3})$", (raw or "").strip().lower())
    if not m:
        return ""
    return f"c{int(m.group(1)):02d}"


def _frame_nums_from_issue(body: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(
        r"\bframe[_-]?(\d{1,4})(?:[_-]?(?:s2|shot2))?\b",
        body or "",
        re.IGNORECASE,
    ):
        out.add(int(m.group(1)))
    tok = normalize_frame_regen_token(body or "")
    if tok:
        out.add(int(tok["number"]))
    return out


def _persons_list(fr: Frame) -> list[str]:
    attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
    raw = attrs.get("персонажи") or attrs.get("characters") or attrs.get("persons")
    if isinstance(raw, list):
        return [_norm_cid(str(x)) for x in raw if _norm_cid(str(x))]
    s = str(raw or "").strip()
    if not s or s in ("—", "-", "–"):
        return []
    return [_norm_cid(m.group(0)) for m in _CID_RE.finditer(s) if _norm_cid(m.group(0))]


def _char_blurb(char_rows: list[dict[str, str]], cid: str) -> str:
    for c in char_rows:
        if (c.get("id") or "").lower() == cid:
            bits = [cid]
            if c.get("name"):
                bits.append(str(c["name"]))
            if c.get("look"):
                bits.append(f"look:{c['look'][:120]}")
            if c.get("clothes"):
                bits.append(f"clothes:{c['clothes'][:120]}")
            return " — ".join(bits)
    return cid


def _fix_block(
    *,
    must: list[str],
    forbid_clones: bool,
    char_rows: list[dict[str, str]],
    reason: str,
) -> str:
    lines = ["[VISION_FIX]", f"Reason: {reason[:200]}"]
    if must:
        lines.append(
            "MUST show exactly these character ids, clearly recognizable: "
            + "; ".join(_char_blurb(char_rows, c) for c in must)
        )
        lines.append(
            "Do NOT omit any listed character. Each listed id appears once "
            "unless the base prompt explicitly requires more."
        )
    if forbid_clones:
        lines.append(
            "FORBIDDEN: extra duplicate humans / twins / lookalikes of the "
            "same hero not listed in MUST. No unexplained second copy of c01/c02."
        )
        if not must:
            lines.append(
                "If base cast is empty/—, show zero hero doubles: only props/"
                "environment unless prompt names a specific non-hero figure."
            )
    lines.append("[/VISION_FIX]")
    return "\n".join(lines)


def merge_prompt_with_fix(base: str, fix: str) -> str:
    """Заменить старый VISION_FIX или дописать новый."""
    text = _VISION_FIX_RE.sub("", base or "").rstrip()
    fix = (fix or "").strip()
    if not fix:
        return text
    if not text:
        return fix
    return f"{text}\n\n{fix}"


def plan_vision_prompt_fixes(
    reply: str,
    *,
    frames_by_num: dict[int, Frame],
    char_rows: list[dict[str, str]],
    frame_targets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """ops для apply_ops: дописать VISION_FIX в промт critical-кадров."""
    targets = frame_targets or extract_critical_frame_regen_targets(reply)
    if not targets:
        return []

    critical = [
        i for i in extract_vision_issues(reply) if i.get("severity") == "critical"
    ]
    # index: frame_num → {missing cids, clone flag, reason}
    per: dict[int, dict[str, Any]] = {}
    for issue in critical:
        body = str(issue.get("text") or "")
        nums = _frame_nums_from_issue(body)
        if not nums:
            continue
        missing = [_norm_cid(m.group(1)) for m in _MISSING_RE.finditer(body)]
        missing = [c for c in missing if c]
        clone = bool(_CLONE_RE.search(body))
        for n in nums:
            slot = per.setdefault(n, {"must": set(), "clone": False, "reasons": []})
            slot["must"].update(missing)
            if clone:
                slot["clone"] = True
            slot["reasons"].append(body[:160])

    ops: list[dict[str, Any]] = []
    seen_uuid: set[str] = set()
    for t in targets:
        if int(t.get("shot") or 1) != 1:
            continue
        num = int(t["number"])
        fr = frames_by_num.get(num)
        if fr is None or not (fr.uuid or "").strip():
            continue
        uuid = str(fr.uuid).strip()
        if uuid in seen_uuid:
            continue
        info = per.get(num) or {}
        must = set(info.get("must") or [])
        # База кадра: обязательный каст (персонажи=c02 → MUST show c02)
        must.update(_persons_list(fr))
        clone = bool(info.get("clone")) or num in per
        # Любой regen-кадр: запрет двойников + каст из Базы/findings
        if not must and not clone:
            clone = True
        reason = "; ".join(info.get("reasons") or []) or f"vision regen frame {num}"
        fix = _fix_block(
            must=sorted(must),
            forbid_clones=True,
            char_rows=char_rows,
            reason=reason,
        )
        new_prompt = merge_prompt_with_fix(fr.image_prompt or "", fix)
        if new_prompt.strip() == (fr.image_prompt or "").strip():
            continue
        seen_uuid.add(uuid)
        ops.append(
            {
                "target": "frame",
                "frame_uuid": uuid,
                "fields": {"промт_картинки": new_prompt},
            }
        )
    return ops


def merge_db_patches(
    primary: dict[str, Any] | None,
    auto_ops: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Модельный db_patch важнее; auto только для uuid без ops."""
    if not auto_ops and not primary:
        return None
    out: dict[str, Any] = {}
    if primary and isinstance(primary.get("characters"), list):
        out["characters"] = list(primary["characters"])
    have: set[str] = set()
    ops: list[dict[str, Any]] = []
    for op in (primary or {}).get("ops") or []:
        if not isinstance(op, dict):
            continue
        ops.append(op)
        u = str(op.get("frame_uuid") or "").strip()
        if u:
            have.add(u)
    for op in auto_ops:
        u = str(op.get("frame_uuid") or "").strip()
        if u and u in have:
            continue
        ops.append(op)
    if ops:
        out["ops"] = ops
    return out or None


async def build_auto_vision_db_patch(
    session: AsyncSession,
    project: Project,
    reply: str,
    frame_targets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from app.services.vision_check_db import load_character_rows

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    by_num = {fr.number: fr for fr in frames}
    chars = await load_character_rows(session, project)
    return plan_vision_prompt_fixes(
        reply,
        frames_by_num=by_num,
        char_rows=chars,
        frame_targets=frame_targets,
    )
