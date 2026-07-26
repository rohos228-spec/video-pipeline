"""Клиент оператора GPT через API (без браузера/CDP).

Пока нет боевого chat+files провайдера — stub пишет фактический результат
на диск (включая analysis.json по vp.check.v1), чтобы связи/UI/resolve
можно было проверять end-to-end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

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
    """Вызов API-оператора GPT.

    Если задан ключ API (`settings.gpt_api_enabled`) — реальный вызов
    OpenAI-совместимого шлюза; иначе — детерминированный stub без сети
    (dev/tests).
    """
    from app.services.gpt_api import gpt_api_enabled

    if gpt_api_enabled():
        return await _run_operator_api_real(
            project_dir=project_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=prompt,
            accompanying=accompanying,
            input_paths=input_paths,
        )

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


async def _run_operator_api_real(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
) -> OperatorApiResult:
    """Реальный вызов GPT через OpenAI-совместимый API (без браузера/CDP)."""
    from app.services.gpt_api import chat, collect_result_urls, download_content
    from app.services.xlsx_text_writeback import WRITEBACK_HINT, writeback_project_xlsx

    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)

    is_check = role in ("review", "gate", "compare")
    prompt_for_model = append_response_footer(prompt) if is_check else (prompt or "")

    accomp = accompanying or ""
    if output_mode == "project_file" and WRITEBACK_HINT not in accomp:
        accomp = f"{accomp}\n\n{WRITEBACK_HINT}".strip() if accomp else WRITEBACK_HINT

    result = await chat(
        prompt=prompt_for_model,
        accompanying=accomp,
        input_paths=list(input_paths),
        # Проверочные роли: строгий JSON, temperature=0 для стабильного вердикта.
        temperature=0.0 if is_check else None,
    )
    reply_text = result.text

    analysis: CheckAnalysis | None = None
    gate_status: str | None = None
    output_paths: list[Path] = []

    if is_check:
        analysis = parse_check_analysis(reply_text)
        gate_status = analysis.verdict
        output_paths.append(write_analysis_json(out_dir, analysis))

    # Скачивание/копирование контента: если модель вернула ссылки на файлы —
    # тянем их в папку ноды (картинки/видео/xlsx из ответа GPT).
    downloaded: list[Path] = []
    for i, url in enumerate(collect_result_urls(reply_text)):
        suffix = Path(url.split("?")[0]).suffix or ".bin"
        dest = out_dir / f"content_{i + 1}{suffix}"
        try:
            got = await download_content(url, dest)
            downloaded.append(got)
            output_paths.append(got)
        except Exception as e:  # noqa: BLE001
            logger.warning("gpt_operator/api: не скачал {}: {}", url, e)

    # Рабочие ноды (project_file): запись обратно в project.xlsx.
    if output_mode == "project_file":
        project_xlsx = project_dir / "project.xlsx"
        updated = writeback_project_xlsx(
            project_xlsx=project_xlsx,
            reply_text=reply_text,
            downloaded_paths=downloaded,
        )
        if updated is not None:
            output_paths.insert(0, updated)
        else:
            logger.warning(
                "gpt_operator/api: project_file без writeback (нет xlsx/TSV) node={}",
                node_key,
            )

    # Текст ответа на диск (для UI / forward.inherit).
    if output_mode == "sidecar":
        sidecar = out_dir / "operator_transform.txt"
        sidecar.write_text(reply_text, encoding="utf-8")
        output_paths.append(sidecar)
    else:
        reply_path = out_dir / "gpt_reply.txt"
        reply_path.write_text(reply_text, encoding="utf-8")
        output_paths.append(reply_path)

    return OperatorApiResult(
        reply_text=reply_text,
        output_paths=output_paths,
        gate_status=gate_status,
        analysis=analysis,
    )
