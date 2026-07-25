"""Клиент оператора GPT через API (без браузера/CDP).

Пока нет боевого chat+files провайдера — stub пишет фактический результат
на диск, чтобы связи/UI/resolve можно было проверять end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class OperatorApiResult:
    reply_text: str
    output_paths: list[Path]
    gate_status: str | None = None


def _infer_gate(reply: str) -> str:
    low = (reply or "").lower()
    fail_markers = ("fail", "не ок", "неок", "reject", "стоп", "ошибк")
    if any(m in low for m in fail_markers):
        return "fail"
    return "pass"


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
    body = (
        f"[gpt-operator/api stub]\n"
        f"role={role}\n"
        f"outputMode={output_mode}\n"
        f"files={names}\n"
        f"prompt_chars={len((prompt or '').strip())}\n"
        f"text_chars={len((accompanying or '').strip())}\n\n"
        f"Инструкция (сопровод):\n{(accompanying or '').strip() or '—'}\n\n"
        f"Мастер-промт (начало):\n{(prompt or '').strip()[:800] or '—'}\n"
    )

    gate_status: str | None = None
    if role in ("review", "gate", "compare"):
        gate_status = "pass"
        body += "\nВердикт: OK (stub pass)\n"
    elif role == "extract":
        body += "\nextract: {\"ok\": true, \"items\": []}\n"

    output_paths: list[Path] = []
    if output_mode == "project_file" and input_paths:
        # не трогаем live project.xlsx в stub — пишем sidecar-копию манифеста
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

    if role == "gate":
        gate_status = _infer_gate(body)

    return OperatorApiResult(
        reply_text=body,
        output_paths=output_paths,
        gate_status=gate_status,
    )
