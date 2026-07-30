"""Клиент оператора GPT через API (без браузера/CDP).

Пока нет боевого chat+files провайдера — stub пишет фактический результат
на диск (включая analysis.json / check_report.txt), чтобы связи/UI/resolve
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
    append_txt_report_footer,
    parse_check_analysis,
    render_check_report_txt,
    split_check_reply_and_writeback,
    write_analysis_json,
    write_check_report_txt,
)


@dataclass
class OperatorApiResult:
    reply_text: str
    output_paths: list[Path]
    gate_status: str | None = None
    analysis: CheckAnalysis | None = None


def _needs_check_writeback_retry(reply_text: str) -> bool:
    """True если check+fix ответ без пригодного TSV writeback."""
    from app.services.check_analysis import CHECK_WRITEBACK_MARKER
    from app.services.xlsx_text_writeback import extract_sheet_blocks

    text = reply_text or ""
    if CHECK_WRITEBACK_MARKER not in text:
        return True
    _report, wb = split_check_reply_and_writeback(text)
    return not extract_sheet_blocks(wb or text)


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


def _is_check(*, role: str, check_mode: bool) -> bool:
    return bool(check_mode) or role in ("review", "gate", "compare")


async def run_operator_api(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
    check_mode: bool = False,
    check_fix: bool = True,
    source_prompt_keys: list[str] | None = None,
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
            check_mode=check_mode,
            check_fix=check_fix,
            source_prompt_keys=source_prompt_keys,
        )

    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)

    names = ", ".join(p.name for p in input_paths) or "(нет файлов)"
    is_check = _is_check(role=role, check_mode=check_mode)
    mode = "fix" if check_fix else "report_only"
    if check_mode:
        prompt_for_model = append_txt_report_footer(prompt or "")
    elif is_check:
        prompt_for_model = append_response_footer(prompt)
    else:
        prompt_for_model = prompt or ""

    body = (
        f"[gpt-operator/api stub]\n"
        f"role={role}\n"
        f"checkMode={check_mode}\n"
        f"checkFix={check_fix}\n"
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
    source_keys = list(source_prompt_keys or [])

    if is_check:
        analysis = _stub_analysis(
            role=role, prompt=prompt or "", accompanying=accompanying or ""
        )
        if mode == "report_only":
            analysis.fix.rewrite_file = None
            analysis.forward.mode = "inherit"
            analysis.forward.paths = []
        gate_status = analysis.verdict
        analysis_path = write_analysis_json(out_dir, analysis)
        report_path = write_check_report_txt(
            out_dir, analysis, mode=mode, source_prompts=source_keys
        )
        output_paths.extend([analysis_path, report_path])
        body = render_check_report_txt(
            analysis, mode=mode, source_prompts=source_keys
        )
    elif role == "extract":
        body += '\nextract: {"ok": true, "items": []}\n'

    if output_mode == "project_file" and input_paths and not check_mode:
        sidecar = out_dir / "operator_sidecar_manifest.txt"
        sidecar.write_text(body, encoding="utf-8")
        output_paths.append(sidecar)
    elif output_mode == "sidecar" and not check_mode:
        sidecar = out_dir / "operator_transform.txt"
        sidecar.write_text(body, encoding="utf-8")
        output_paths.append(sidecar)
    else:
        reply_path = out_dir / "gpt_reply.txt"
        reply_path.write_text(body, encoding="utf-8")
        output_paths.append(reply_path)

    if analysis is None and is_check:
        analysis = parse_check_analysis(body)
        gate_status = analysis.verdict
        output_paths.insert(0, write_analysis_json(out_dir, analysis))
        output_paths.insert(
            1,
            write_check_report_txt(
                out_dir, analysis, mode=mode, source_prompts=source_keys
            ),
        )

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
    check_mode: bool = False,
    check_fix: bool = True,
    source_prompt_keys: list[str] | None = None,
) -> OperatorApiResult:
    """Реальный вызов GPT через OpenAI-совместимый API (без браузера/CDP)."""
    from app.services.gpt_api import chat, collect_result_urls, download_content
    from app.services.xlsx_text_writeback import WRITEBACK_HINT, writeback_project_xlsx

    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)

    is_check = _is_check(role=role, check_mode=check_mode)
    mode = "fix" if check_fix else "report_only"
    if check_mode:
        prompt_for_model = append_txt_report_footer(prompt or "")
    elif is_check:
        prompt_for_model = append_response_footer(prompt)
    else:
        prompt_for_model = prompt or ""

    accomp = accompanying or ""
    effective_output = "text" if check_mode else output_mode
    # check+fix: модель возвращает TSV writeback — нужен тот же hint, что у project_file.
    if check_mode and check_fix and WRITEBACK_HINT not in accomp:
        accomp = f"{accomp}\n\n{WRITEBACK_HINT}".strip() if accomp else WRITEBACK_HINT
    if effective_output == "project_file" and WRITEBACK_HINT not in accomp:
        accomp = f"{accomp}\n\n{WRITEBACK_HINT}".strip() if accomp else WRITEBACK_HINT

    result = await chat(
        prompt=prompt_for_model,
        accompanying=accomp,
        input_paths=list(input_paths),
        temperature=0.0 if is_check else None,
    )
    reply_text = result.text

    # check+fix: если модель отказалась из‑за «нет project.xlsx» / нет
    # XLSX_WRITEBACK — один retry с жёстким API-контрактом (см. D5).
    if check_mode and check_fix and _needs_check_writeback_retry(result.text):
        logger.warning(
            "gpt_operator/api: check+fix без XLSX_WRITEBACK node={} — retry",
            node_key,
        )
        retry_prompt = (
            f"{prompt_for_model}\n\n"
            "# КОНТРАКТ API (повтор, обязателен)\n"
            "Предыдущий ответ без --- XLSX_WRITEBACK --- отклонён. "
            "Бинарный project.xlsx недоступен — во вложении TSV-экспорт. "
            "Отказ «нет файла» запрещён. Сейчас: отчёт + блок "
            "--- XLSX_WRITEBACK --- с `# Лист:` TSV; forward file: fixed."
        )
        result = await chat(
            prompt=retry_prompt,
            accompanying=accomp,
            input_paths=list(input_paths),
            temperature=0.0,
        )
        reply_text = result.text

    analysis: CheckAnalysis | None = None
    gate_status: str | None = None
    output_paths: list[Path] = []
    source_keys = list(source_prompt_keys or [])

    downloaded: list[Path] = []
    for i, url in enumerate(collect_result_urls(result.text)):
        suffix = Path(url.split("?")[0]).suffix or ".bin"
        dest = out_dir / f"content_{i + 1}{suffix}"
        try:
            got = await download_content(url, dest)
            downloaded.append(got)
            output_paths.append(got)
        except Exception as e:  # noqa: BLE001
            logger.warning("gpt_operator/api: не скачал {}: {}", url, e)

    if is_check:
        report_part, wb_part = split_check_reply_and_writeback(result.text)
        analysis = parse_check_analysis(report_part or result.text)
        if mode == "report_only":
            analysis.fix.rewrite_file = None
            analysis.forward.mode = "inherit"
            analysis.forward.paths = []
        elif check_fix:
            fixed_rel = _apply_check_fix_writeback(
                project_dir=project_dir,
                node_key=node_key,
                out_dir=out_dir,
                input_paths=input_paths,
                reply_text=result.text,
                writeback_part=wb_part,
                downloaded=downloaded,
            )
            if fixed_rel:
                analysis.fix.rewrite_file = fixed_rel
                analysis.fix.target = "xlsx"
                analysis.forward.mode = "explicit"
                analysis.forward.paths = [fixed_rel]
        gate_status = analysis.verdict
        output_paths.insert(0, write_analysis_json(out_dir, analysis))
        report_path = write_check_report_txt(
            out_dir, analysis, mode=mode, source_prompts=source_keys
        )
        output_paths.insert(1, report_path)
        reply_text = render_check_report_txt(
            analysis, mode=mode, source_prompts=source_keys
        )

    if effective_output == "project_file":
        project_xlsx = project_dir / "project.xlsx"
        updated = writeback_project_xlsx(
            project_xlsx=project_xlsx,
            reply_text=result.text,
            downloaded_paths=downloaded,
        )
        if updated is not None:
            output_paths.insert(0, updated)
        else:
            logger.warning(
                "gpt_operator/api: project_file без writeback (нет xlsx/TSV) node={}",
                node_key,
            )

    if effective_output == "sidecar":
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


def _apply_check_fix_writeback(
    *,
    project_dir: Path,
    node_key: str,
    out_dir: Path,
    input_paths: list[Path],
    reply_text: str,
    writeback_part: str,
    downloaded: list[Path],
) -> str | None:
    """Наложить TSV/скачанный xlsx на копию входа → excel_gpt_uploads/.../project_fixed.xlsx."""
    import shutil

    from app.services.xlsx_text_writeback import extract_sheet_blocks, writeback_project_xlsx

    src: Path | None = None
    for p in input_paths:
        if p.suffix.lower() in {".xlsx", ".xlsm", ".xls"} and p.is_file():
            src = p
            break
    if src is None:
        candidate = project_dir / "project.xlsx"
        if candidate.is_file():
            src = candidate
    if src is None:
        logger.warning(
            "gpt_operator/api: check fix — нет исходного xlsx для writeback node={}",
            node_key,
        )
        return None

    wb_text = (writeback_part or "").strip() or (reply_text or "")
    if not extract_sheet_blocks(wb_text) and not any(
        p.suffix.lower() in {".xlsx", ".xlsm", ".xls"} for p in downloaded
    ):
        return None

    fixed = out_dir / "project_fixed.xlsx"
    try:
        shutil.copy2(src, fixed)
    except OSError as e:
        logger.warning("gpt_operator/api: check fix copy failed: {}", e)
        return None

    updated = writeback_project_xlsx(
        project_xlsx=fixed,
        reply_text=wb_text,
        downloaded_paths=downloaded,
    )
    if updated is None or not fixed.is_file() or fixed.stat().st_size < 64:
        logger.warning(
            "gpt_operator/api: check fix writeback не применился node={}",
            node_key,
        )
        return None

    rel = f"excel_gpt_uploads/{node_key}/project_fixed.xlsx"
    logger.info(
        "gpt_operator/api: check fix → {} ({} байт)",
        rel,
        fixed.stat().st_size,
    )
    return rel
