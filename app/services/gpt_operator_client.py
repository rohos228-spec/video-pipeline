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
    # Ответ — JSON apply-ops (DB SoT): TSV-writeback в xlsx пропущен,
    # применяет вызывающий (enrich_xlsx) через db_apply.
    apply_ops: dict | None = None


def _needs_check_writeback_retry(reply_text: str) -> bool:
    """True если check+fix ответ без пригодного TSV writeback."""
    from app.services.check_analysis import CHECK_WRITEBACK_MARKER
    from app.services.xlsx_text_writeback import extract_sheet_blocks

    text = reply_text or ""
    if CHECK_WRITEBACK_MARKER not in text:
        return True
    _report, wb = split_check_reply_and_writeback(text)
    return not extract_sheet_blocks(wb or text)


def _apply_ops_has_payload(data: dict | None) -> bool:
    """Есть что писать в DB (не пустой отказ модели)."""
    if not isinstance(data, dict):
        return False
    return bool(
        data.get("ops")
        or data.get("actions")
        or data.get("characters")
        or data.get("scenes")
    )


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
    check_streams: int | None = None,
    db_sot_check: bool = False,
) -> OperatorApiResult:
    """Вызов API-оператора GPT.

    Если задан ключ API (`settings.gpt_api_enabled`) — реальный вызов
    OpenAI-совместимого шлюза; иначе — детерминированный stub без сети
    (dev/tests).
    """
    from app.services.check_streams import clamp_check_streams, default_check_streams
    from app.services.gpt_api import gpt_api_enabled, is_image_path

    streams = (
        clamp_check_streams(check_streams)
        if check_streams is not None
        else default_check_streams()
    )
    if check_mode and streams == 0:
        raise RuntimeError(
            "check_streams=0: GPT-проверка отключена. "
            "Поставь meta.check_streams 1..10."
        )

    # Vision limit 8: checkMode с кучей PNG → батчи, один итоговый отчёт.
    # checkFix/TSV для PNG-check отключаем: правки только через db_patch.
    vision_png_n = sum(1 for p in input_paths if is_image_path(p))
    vision_check_fix = False if (check_mode and vision_png_n > 0) else check_fix
    if gpt_api_enabled() and check_mode and vision_png_n > 8:
        return await _run_check_vision_batched(
            project_dir=project_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=prompt,
            accompanying=accompanying,
            input_paths=input_paths,
            check_fix=vision_check_fix,
            source_prompt_keys=source_prompt_keys,
            check_streams=streams,
        )

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
            check_fix=vision_check_fix if check_mode else check_fix,
            source_prompt_keys=source_prompt_keys,
            db_sot_check=db_sot_check,
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
    db_sot_check: bool = False,
) -> OperatorApiResult:
    """Реальный вызов GPT через OpenAI-совместимый API (без браузера/CDP)."""
    from app.services.gpt_api import chat, collect_result_urls, download_content

    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)

    is_check = _is_check(role=role, check_mode=check_mode)
    mode = "fix" if check_fix else "report_only"
    raw_prompt = prompt or ""
    if check_mode:
        # assemble_check_master_prompt уже кладёт TXT-footer — не дублируем.
        if "# ОТЧЁТ ПРОВЕРКИ" in raw_prompt:
            prompt_for_model = raw_prompt
        else:
            prompt_for_model = append_txt_report_footer(raw_prompt)
    elif is_check:
        prompt_for_model = append_response_footer(raw_prompt)
    else:
        prompt_for_model = raw_prompt

    from app.services.llm_contract import build_api_accompany

    accomp = accompanying or ""
    effective_output = "text" if check_mode else output_mode
    # TSV writeback — только legacy check+fix. DB SoT — никогда.
    if check_mode and check_fix and not db_sot_check:
        accomp = build_api_accompany(accomp, expect_xlsx_writeback=True)

    xlsx_contract = (
        "apply_ops"
        if (effective_output == "project_file" and not is_check) or db_sot_check
        else "tsv"
    )
    chat_paths = list(input_paths)
    if db_sot_check:
        chat_paths = [
            p
            for p in chat_paths
            if p.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}
        ] or chat_paths
    result = await chat(
        prompt=prompt_for_model,
        accompanying=accomp,
        input_paths=chat_paths,
        temperature=0.0 if is_check else None,
        xlsx_write_contract=xlsx_contract,
    )
    reply_text = result.text

    # DB SoT: project_file / check DB → apply-ops JSON.
    apply_ops: dict | None = None
    if (effective_output == "project_file" and not is_check) or (
        check_mode and db_sot_check
    ):
        from app.services.db_apply import extract_apply_ops_json

        apply_ops = extract_apply_ops_json(reply_text or "")
        if apply_ops is not None and not _apply_ops_has_payload(apply_ops):
            if not (check_mode and not check_fix):
                logger.warning(
                    "gpt_operator/api: node={} — пустой apply-ops "
                    "(error/report={})",
                    node_key,
                    str(apply_ops.get("error") or apply_ops.get("report") or "")[
                        :160
                    ],
                )
            apply_ops = None
        elif apply_ops is not None:
            logger.info(
                "gpt_operator/api: node={} — apply-ops JSON "
                "(ops={}, characters={}, scenes={}), запись через DB",
                node_key,
                len(apply_ops.get("ops") or []),
                len(apply_ops.get("characters") or []),
                len(apply_ops.get("scenes") or []),
            )

    # check+fix legacy Excel: retry на XLSX_WRITEBACK. DB SoT — skip.
    if (
        check_mode
        and check_fix
        and not db_sot_check
        and _needs_check_writeback_retry(result.text)
    ):
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
            input_paths=chat_paths,
            temperature=0.0,
            xlsx_write_contract="tsv",
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
        elif check_fix and not db_sot_check:
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
        elif check_fix and db_sot_check and apply_ops is not None:
            analysis.fix.target = "db"
            analysis.fix.instructions = (
                analysis.fix.instructions
                or "apply-ops JSON → DB (scene_registry / frames)"
            )
        gate_status = analysis.verdict
        output_paths.insert(0, write_analysis_json(out_dir, analysis))
        report_path = write_check_report_txt(
            out_dir, analysis, mode=mode, source_prompts=source_keys
        )
        output_paths.insert(1, report_path)
        reply_text = render_check_report_txt(
            analysis, mode=mode, source_prompts=source_keys
        )

    # project_file (не check): только apply-ops. Без TSV-fallback — иначе
    # модель пишет Excel-диалект, а enrich_xlsx его отвергает.
    if effective_output == "project_file" and not is_check and apply_ops is None:
        from dataclasses import replace

        from app.services.db_apply import extract_apply_ops_json

        logger.warning(
            "gpt_operator/api: project_file без apply-ops node={} — JSON retry",
            node_key,
        )
        retry_prompt = (
            f"{prompt_for_model}\n\n"
            "# КОНТРАКТ ЗАПИСИ В БАЗУ (повтор, обязателен)\n"
            "Предыдущий ответ ОТКЛОНЁН. Нужен JSON apply-ops с данными.\n"
            "Источник: db_frames.json + закадр во вложении/accompanying. "
            "Пайплайн запишет JSON в DB сам.\n"
            "Верни ТОЛЬКО один JSON-объект с непустыми полями:\n"
            '{"characters":[...],"scenes":[...],"ops":[...],"report":"..."}\n'
            "или минимум:\n"
            '{"ops":[{"frame_uuid":"<uuid>","fields":{"место":"…"}}]}\n'
            "Без markdown, без TSV, без поля error, без просьб о вложениях."
        )
        # Retry без xlsx — иначе модель снова упирается в «нет файла».
        retry_paths = [
            p
            for p in input_paths
            if p.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}
        ]
        retry = await chat(
            prompt=retry_prompt,
            accompanying=accomp,
            input_paths=retry_paths or list(input_paths),
            temperature=0.0,
            xlsx_write_contract="apply_ops",
        )
        reply_text = retry.text or ""
        try:
            result = replace(result, text=reply_text)
        except TypeError:
            # тесты / моки могут отдавать SimpleNamespace, не dataclass
            from types import SimpleNamespace

            result = SimpleNamespace(text=reply_text)
        apply_ops = extract_apply_ops_json(reply_text or "")
        if apply_ops is not None and not _apply_ops_has_payload(apply_ops):
            apply_ops = None
        if apply_ops is None:
            logger.warning(
                "gpt_operator/api: project_file всё ещё без apply-ops node={}",
                node_key,
            )
            raise RuntimeError(
                "project_file: модель не вернула apply-ops JSON "
                '{"ops":[…]} / {"characters":[…]} / {"scenes":[…]}. '
                "TSV `# Лист:` и отказ «нет xlsx» больше не принимаются."
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
        apply_ops=apply_ops,
    )


async def _run_check_vision_batched(
    *,
    project_dir: Path,
    node_key: str,
    role: str,
    output_mode: str,
    prompt: str,
    accompanying: str,
    input_paths: list[Path],
    check_fix: bool = True,
    source_prompt_keys: list[str] | None = None,
    check_streams: int = 2,
) -> OperatorApiResult:
    """Несколько vision-батчей по 8 PNG → склеенный отчёт + общий verdict."""
    import asyncio

    from app.services.check_analysis import (
        extract_critical_frame_regen_targets,
        extract_critical_hero_regen_ids,
        extract_db_patch,
        extract_prose_reject_frame_targets,
        has_critical_vision_issues,
        resolve_vision_check_gate,
    )
    from app.services.check_streams import acquire_check_slot, clamp_check_streams
    from app.services.gpt_api import is_image_path

    images = [p for p in input_paths if is_image_path(p)]
    others = [p for p in input_paths if not is_image_path(p)]
    batch_size = 8
    batches = [images[i : i + batch_size] for i in range(0, len(images), batch_size)]
    streams = max(1, clamp_check_streams(check_streams))
    logger.info(
        "gpt_operator/api: vision batch check node={} images={} batches={} "
        "check_streams={}",
        node_key,
        len(images),
        len(batches),
        streams,
    )

    async def _one_batch(bi: int, batch: list[Path]) -> tuple[int, OperatorApiResult]:
        batch_prompt = (
            f"{prompt}\n\n"
            f"# BATCH {bi}/{len(batches)}\n"
            f"Проверь ТОЛЬКО эти файлы: {', '.join(p.name for p in batch)}.\n"
            "Обязательно: mode=report_only; ## scores; ## issues с [critical]/[warning];\n"
            "regen/frames только для critical этого батча; ## db_patch из uuid ## База.\n"
            "ЗАПРЕЩЕНО: XLSX_WRITEBACK, жалобы на отсутствие TSV/xlsx, mode=fix.\n"
            "В findings для брака пиши [critical], не [error]."
        )
        async with acquire_check_slot(streams):
            res = await _run_operator_api_real(
                project_dir=project_dir,
                node_key=node_key,
                role=role,
                output_mode=output_mode,
                prompt=batch_prompt,
                accompanying=accompanying,
                input_paths=list(others) + list(batch),
                check_mode=True,
                check_fix=check_fix,
                source_prompt_keys=source_prompt_keys,
            )
        return bi, res

    if streams <= 1:
        ordered: list[tuple[int, OperatorApiResult]] = []
        for bi, batch in enumerate(batches, start=1):
            ordered.append(await _one_batch(bi, batch))
    else:
        gathered = await asyncio.gather(
            *[_one_batch(bi, batch) for bi, batch in enumerate(batches, start=1)]
        )
        ordered = sorted(gathered, key=lambda x: x[0])

    parts: list[str] = []
    any_fail = False
    all_hero: list[str] = []
    all_frames: list[dict] = []
    merged_patch: dict = {"characters": [], "ops": []}
    seen_hero: set[str] = set()
    seen_frame: set[tuple[int, int]] = set()
    last: OperatorApiResult | None = None

    for bi, res in ordered:
        last = res
        text = res.reply_text or ""
        parts.append(f"===== BATCH {bi}/{len(batches)} =====\n{text}")
        gate = resolve_vision_check_gate(text)
        prose_rej = extract_prose_reject_frame_targets(text)
        has_crit = has_critical_vision_issues(text)
        batch_fail = (
            gate == "fail"
            or (res.gate_status or "").lower() == "fail"
            or (res.analysis and res.analysis.verdict == "fail")
            or has_crit
            or bool(prose_rej)
        )
        if batch_fail:
            any_fail = True
        # Pass-батч: не тащить кадры из «перегенерация не требуется».
        batch_pass = (
            not has_crit
            and not prose_rej
            and (res.gate_status or "").lower() != "fail"
            and not (res.analysis and res.analysis.verdict == "fail")
            and gate != "fail"
        )
        if not batch_pass:
            for hid in extract_critical_hero_regen_ids(text):
                if hid not in seen_hero:
                    seen_hero.add(hid)
                    all_hero.append(hid)
            for ft in extract_critical_frame_regen_targets(text):
                key = (int(ft["number"]), int(ft.get("shot") or 1))
                if key not in seen_frame:
                    seen_frame.add(key)
                    all_frames.append({"number": key[0], "shot": key[1]})
        patch = extract_db_patch(text)
        if patch:
            for c in patch.get("characters") or []:
                if isinstance(c, dict):
                    merged_patch["characters"].append(c)
            for op in patch.get("ops") or []:
                if isinstance(op, dict):
                    merged_patch["ops"].append(op)

    verdict = "fail" if any_fail else "pass"
    regen_lines: list[str] = []
    if all_hero:
        regen_lines.append("## regen_heroes")
        regen_lines.append("regen: " + ", ".join(all_hero))
    if all_frames:
        regen_lines.append("## regen_frames")
        toks = [
            f"{t['number']}s2" if t["shot"] == 2 else str(t["number"])
            for t in all_frames
        ]
        regen_lines.append("frames: " + ", ".join(toks))
    if merged_patch["characters"] or merged_patch["ops"]:
        regen_lines.append("## db_patch")
        regen_lines.append("```json")
        regen_lines.append(json.dumps(merged_patch, ensure_ascii=False, indent=2))
        regen_lines.append("```")

    merged = (
        "# ОТЧЁТ ПРОВЕРКИ\n"
        f"verdict: {verdict}\n"
        "mode: report_only\n"
        "source_prompts: vision_batched\n\n"
        "## summary\n"
        f"Склеенный vision-check: {len(batches)} батч(ей), "
        f"{len(images)} изображений. Итог: {verdict}.\n\n"
        + ("\n".join(regen_lines) + "\n\n" if regen_lines else "")
        + "## analysis\n"
        + "\n\n".join(parts)
    )

    out_dir = project_dir / "excel_gpt_uploads" / node_key
    out_dir.mkdir(parents=True, exist_ok=True)
    analysis = parse_check_analysis(merged)
    # Force merged verdict (parse may miss)
    if analysis.verdict != verdict:
        analysis.verdict = verdict  # type: ignore[assignment]
    mode = "fix" if check_fix else "report_only"
    write_analysis_json(out_dir, analysis)
    write_check_report_txt(
        out_dir, analysis, mode=mode, source_prompts=list(source_prompt_keys or [])
    )
    # Preserve raw merged text with regen/db_patch for vision_check_loop parsers
    reply_path = out_dir / "gpt_reply.txt"
    reply_path.write_text(merged, encoding="utf-8")
    report_path = out_dir / "check_report.txt"
    report_path.write_text(merged, encoding="utf-8")

    return OperatorApiResult(
        reply_text=merged,
        output_paths=[reply_path, report_path],
        gate_status=verdict,
        analysis=analysis,
        apply_ops=last.apply_ops if last else None,
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
