"""Клиент оператора GPT через API (без браузера/CDP).

Пока нет боевого chat+files провайдера — stub пишет фактический результат
на диск (включая analysis.json по vp.check.v1), чтобы связи/UI/resolve
можно было проверять end-to-end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.check_analysis import (
    SCHEMA_ID,
    CheckAnalysis,
    append_response_footer,
    parse_check_analysis,
    write_analysis_json,
)


@dataclass
class OperatorApiResult:
    reply_text: str
    output_paths: list[Path]
    gate_status: str | None = None
    analysis: CheckAnalysis | None = None


def _stub_analysis(*, role: str, prompt: str, accompanying: str) -> CheckAnalysis:
    """Детерминированный stub-вердикт в формате vp.check.v1."""
    probe = f"{prompt}\n{accompanying}\n".lower()
    fail_markers = ("fail", "не ок", "неок", "reject", "стоп", "ошибк")
    is_fail = any(m in probe for m in fail_markers)
    verdict = "fail" if is_fail else "pass"
    return parse_check_analysis(
        json.dumps(
            {
                "schema": SCHEMA_ID,
                "verdict": verdict,
                "summary": f"stub {role}: {verdict}",
                "checks": [
                    {
                        "id": "stub",
                        "ok": not is_fail,
                        "note": "gpt-operator/api stub (нет боевого провайдера)",
                    }
                ],
                "forward": {"mode": "inherit", "paths": []},
                "fix": {
                    "target": "source" if is_fail else "none",
                    "instructions": "см. промт (stub fail)" if is_fail else "",
                    "rewrite_file": None,
                },
            },
            ensure_ascii=False,
        )
    )


async def run_operator_api(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
) -> OperatorApiResult:
    """Вызов API-оператора. Сейчас — детерминированный stub без сети."""
    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)

    names = ", ".join(p.name for p in input_paths) or "(нет файлов)"
    prompt_for_model = (
        append_response_footer(prompt)
        if role in ("review", "gate", "compare")
        else (prompt or "")
    )

    body = (
        f"[gpt-operator/api stub]\n"
        f"role={role}\n"
        f"outputMode={output_mode}\n"
        f"files={names}\n"
        f"prompt_chars={len((prompt_for_model or '').strip())}\n"
        f"text_chars={len((accompanying or '').strip())}\n\n"
        f"Инструкция (сопровод):\n{(accompanying or '').strip() or '—'}\n\n"
        f"Мастер-промт (начало):\n{(prompt_for_model or '').strip()[:800] or '—'}\n"
    )

    analysis: CheckAnalysis | None = None
    gate_status: str | None = None
    output_paths: list[Path] = []

    if role in ("review", "gate", "compare"):
        analysis = _stub_analysis(
            role=role, prompt=prompt or "", accompanying=accompanying or ""
        )
        gate_status = analysis.verdict
        analysis_path = write_analysis_json(out_dir, analysis)
        output_paths.append(analysis_path)
        # Ответ = канонический JSON (плюс stub-заголовок для отладки).
        body = (
            f"[gpt-operator/api stub]\n"
            f"{json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2)}\n"
        )
    elif role == "extract":
        body += "\nextract: {\"ok\": true, \"items\": []}\n"

    if output_mode == "project_file" and input_paths:
        sidecar = out_dir / "operator_sidecar_manifest.txt"
        sidecar.write_text(body, encoding="utf-8")
        output_paths.append(sidecar)
    elif output_mode == "sidecar":
        sidecar = out_dir / "operator_transform.txt"
        sidecar.write_text(body, encoding="utf-8")
        output_paths.append(sidecar)
    else:
        reply_path = out_dir / "gpt_reply.txt"
        reply_path.write_text(body, encoding="utf-8")
        output_paths.append(reply_path)

    # Если в будущем сюда придёт реальный ответ — разбираем JSON из body.
    if analysis is None and role in ("review", "gate", "compare"):
        analysis = parse_check_analysis(body)
        gate_status = analysis.verdict
        output_paths.insert(0, write_analysis_json(out_dir, analysis))

    return OperatorApiResult(
        reply_text=body,
        output_paths=output_paths,
        gate_status=gate_status,
        analysis=analysis,
    )
