"""Добавить случай в оценку планировщика (tests/fixtures/action_planner_eval.json).

Нужен, чтобы «плохая фраза» от оператора попадала в набор оценки одной командой и сразу проверялась:
идентификатор уникален, действия и параметры существуют в реестре, плейсхолдеры известны сиду, исход
согласован с шагами. Ничего не вызывает по сети и не запускает модель. Файл пишется атомарно и в том же
формате, что и сейчас (отступ 1, русские буквы как есть), поэтому в git видна только новая запись.

Примеры (из корня репозитория, python = .venv/Scripts/python.exe):

  # фраза, на которой планировщик ошибся, и то, что должно получиться
  add_eval_case.py --id swap_shots_k9 --text "поменяй шоты в девятом кадре" --workflow swap \\
      --step montage.swap_shots '{"frame_number": 9, "kind": "both"}'

  # нужно уточнение (слова, которые должны быть в вопросе, необязательны)
  add_eval_case.py --id vague_fix --text "поправь там" --outcome clarify --workflow ambiguous

  # такую просьбу выполнять нельзя
  add_eval_case.py --id no_push --text "запушь в гитхаб" --outcome refuse --workflow out_of_catalog

  # загрузка с файлом
  add_eval_case.py --id att_voice2 --text "загрузи озвучку" --workflow "upload attached" \\
      --attachment audio:voice.mp3:2400000 --step montage.upload_voice '{"source_path": "attachments/"}'

  # случай целиком JSON-ом (из файла или '-' для stdin)
  add_eval_case.py --json case.json

Код выхода: 0 добавлено (или --dry-run прошёл проверку), 1 ошибки проверки (файл не тронут), 2 неверные аргументы.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "action_planner_eval.json"
OUTCOMES = ("plan", "clarify", "refuse")
CONTEXT_KEYS = ("selected_scene", "selected_frame", "status", "frame_count", "attachments")
ATTACHMENT_KINDS = ("image", "video", "audio", "excel", "json", "text")
CASE_KEYS = ("id", "text", "workflow", "context", "outcome", "steps", "also_offered",
             "clarify_contains", "explanation_contains")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
PLACEHOLDER_RE = re.compile(r"^\$[a-z_]+(\.[A-Za-z0-9_]+)+$")
PREFIX_ONLY_KEYS = {"source_path"}   # в фикстуре пишется префиксом "attachments/": путь подставляет сервер


def _load_seed():
    spec = importlib.util.spec_from_file_location("eval_seed_for_add_case", ROOT / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def known_variables() -> dict:
    """Плейсхолдеры, которые раскрывает сид (`$frame.22`, `$proposal.3.id`, `$target.shots_prompt`, ...)."""
    return _load_seed().expected_vars(2)


def default_registry():
    sys.path.insert(0, str(ROOT))
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    ensure_builtin_actions()
    return registry


def load_fixture(path: Path = FIXTURE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_fixture(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def _resolve(value, variables: dict, problems: list[str], where: str):
    """Подставляет известные плейсхолдеры целой строкой; неизвестный `$x.y` - ошибка."""
    if isinstance(value, str) and value.startswith("$"):
        if value[1:] in variables:
            return variables[value[1:]]
        if PLACEHOLDER_RE.match(value):
            problems.append(f"{where}: неизвестный плейсхолдер {value!r} "
                            f"(известные: $frame.N, $uuid.N, $proposal.N.id/.digest, $target.*, $version.*)")
        return value
    if isinstance(value, dict):
        return {k: _resolve(v, variables, problems, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, variables, problems, f"{where}[{i}]") for i, v in enumerate(value)]
    return value


def _check_step(i: int, step, registry, variables: dict, problems: list[str]) -> None:
    where = f"шаг {i + 1}"
    if not isinstance(step, dict) or set(step) - {"action_id", "params"} or "action_id" not in step:
        problems.append(f"{where}: шаг - объект {{\"action_id\": ..., \"params\": {{...}}}} без других полей")
        return
    aid, params = step["action_id"], step.get("params", {})
    spec = registry.find(aid) if isinstance(aid, str) else None
    if spec is None:
        problems.append(f"{where}: действия {aid!r} нет в реестре (список: GET /api/action-layer/catalog)")
        return
    if not isinstance(params, dict):
        problems.append(f"{where}: params должен быть объектом")
        return
    props = spec.params_schema.get("properties", {})
    for key, value in params.items():
        if key not in props:
            problems.append(f"{where}: у {aid} нет параметра {key!r} (есть: {', '.join(sorted(props))})")
            continue
        if key in PREFIX_ONLY_KEYS:
            if not (isinstance(value, str) and value.startswith("attachments/")):
                problems.append(f"{where}: {key} в фикстуре пишется префиксом \"attachments/\"")
            continue
        resolved = _resolve(value, variables, problems, f"{where}.{key}")
        from app.services.action_layer import schema as schema_mod
        for err in schema_mod.validate(props[key], resolved, key):
            if err.endswith(": required"):   # ожидание в фикстуре - подмножество параметров: пропуск полей не ошибка
                continue
            problems.append(f"{where}: {aid}: {err}")


def _check_context(ctx, problems: list[str]) -> None:
    if not isinstance(ctx, dict):
        problems.append("context должен быть объектом")
        return
    for key in ctx:
        if key not in CONTEXT_KEYS:
            problems.append(f"context.{key}: допустимы только {', '.join(CONTEXT_KEYS)}")
    for key in ("selected_scene", "selected_frame", "frame_count"):
        if key in ctx and not (isinstance(ctx[key], int) and not isinstance(ctx[key], bool) and ctx[key] >= 1):
            problems.append(f"context.{key}: целое число не меньше 1")
    if "status" in ctx and not (isinstance(ctx["status"], str) and ctx["status"].strip()):
        problems.append("context.status: непустая строка")
    for i, a in enumerate(ctx.get("attachments") or []):
        if not (isinstance(a, dict) and a.get("kind") in ATTACHMENT_KINDS and isinstance(a.get("name"), str)
                and a["name"].strip() and (a.get("size") is None or isinstance(a["size"], int) and a["size"] >= 0)):
            problems.append(f"context.attachments[{i}]: нужен объект {{kind: {'|'.join(ATTACHMENT_KINDS)}, "
                            f"name, size}}")
    if "attachments" in ctx and not isinstance(ctx["attachments"], list):
        problems.append("context.attachments: список")


def validate_case(case, existing: list[dict], registry, variables: dict | None = None,
                  *, check_duplicates: bool = True) -> list[str]:
    """Список ошибок (пустой = случай годится). Не читает и не пишет файлы."""
    variables = known_variables() if variables is None else variables
    problems: list[str] = []
    if not isinstance(case, dict):
        return ["случай должен быть JSON-объектом"]
    extra = sorted(set(case) - set(CASE_KEYS))
    if extra:
        problems.append(f"лишние поля: {', '.join(extra)} (допустимы: {', '.join(CASE_KEYS)})")
    cid = case.get("id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        problems.append("id: латиница в нижнем регистре, цифры и _, до 64 символов (например swap_shots_k9)")
    elif check_duplicates and cid in {c.get("id") for c in existing}:
        problems.append(f"id {cid!r} уже есть в наборе")
    text = case.get("text")
    if not isinstance(text, str) or not text.strip():
        problems.append("text: непустая фраза оператора")
    else:
        if len(text) > 2000:
            problems.append("text: не длиннее 2000 символов")
        if check_duplicates:
            same = next((c["id"] for c in existing
                         if str(c.get("text", "")).strip().lower() == text.strip().lower()), None)
            if same:
                problems.append(f"такая фраза уже есть в случае {same!r}")
    if not isinstance(case.get("workflow"), str) or not case["workflow"].strip():
        problems.append("workflow: короткая метка области (swap, upload attached, media recovery, ...)")
    outcome = case.get("outcome")
    if outcome not in OUTCOMES:
        problems.append(f"outcome: одно из {', '.join(OUTCOMES)}")
    steps = case.get("steps", [])
    if not isinstance(steps, list):
        problems.append("steps: список")
        steps = []
    if outcome == "plan" and not steps:
        problems.append("outcome=plan требует хотя бы один шаг (--step ACTION_ID '{...}')")
    if outcome in ("clarify", "refuse") and steps:
        problems.append(f"outcome={outcome}: шагов быть не должно")
    for i, step in enumerate(steps):
        _check_step(i, step, registry, variables, problems)
    also = case.get("also_offered", [])
    if not isinstance(also, list):
        problems.append("also_offered: список id действий")
    else:
        for aid in also:
            if not isinstance(aid, str) or registry.find(aid) is None:
                problems.append(f"also_offered: действия {aid!r} нет в реестре")
    _check_context(case.get("context", {}), problems)
    for key, allowed in (("clarify_contains", ("clarify",)), ("explanation_contains", ("plan",))):
        if key in case:
            val = case[key]
            if not (isinstance(val, list) and val and all(isinstance(x, str) and x.strip() for x in val)):
                problems.append(f"{key}: непустой список слов")
            if outcome not in allowed:
                problems.append(f"{key} применимо только при outcome={allowed[0]}")
    if outcome == "plan":
        ctx_att = (case.get("context") or {}).get("attachments") if isinstance(case.get("context"), dict) else None
        if any(isinstance(s, dict) and "source_path" in (s.get("params") or {}) for s in steps) and not ctx_att:
            problems.append("шаг с source_path требует context.attachments (файл, который прикрепил оператор)")
    return problems


def prefilter_warnings(case: dict, registry) -> list[str]:
    """Предупреждения: предфильтр каталога не показывает ожидаемые действия (офлайн-тест recall тогда упадёт)."""
    if case.get("outcome") != "plan":
        return []
    from app.services.action_layer import planner_catalog
    ctx = case.get("context") or {}
    facts = {"project_id": 1, "selected_scene": ctx.get("selected_scene"), "selected_frame": ctx.get("selected_frame"),
             "status": ctx.get("status", "idle"), "frame_count": ctx.get("frame_count", 10),
             "max_frame_number": 10, "nodes": []}
    sel = planner_catalog.select_catalog(case["text"], facts, registry, 24)
    wanted = {s["action_id"] for s in case["steps"]} | set(case.get("also_offered", []))
    missing = sorted(wanted - sel.offered)
    if not missing:
        return []
    return [f"предфильтр каталога не показывает модели: {', '.join(missing)}. Случай добавится, но "
            f"tests/test_action_planner_eval_offline.py упадёт на нём: это настоящая ошибка отбора действий "
            f"(planner_catalog.py), её нужно чинить, а не скрывать"]


def canonical(case: dict) -> dict:
    out = {k: case[k] for k in CASE_KEYS if k in case} | {k: v for k, v in case.items() if k not in CASE_KEYS}
    out.setdefault("workflow", "")
    out.setdefault("context", {})
    out.setdefault("steps", [])
    out.setdefault("also_offered", [])
    return {**{k: out[k] for k in CASE_KEYS if k in out}, **{k: v for k, v in out.items() if k not in CASE_KEYS}}


def add_case(case: dict, path: Path = FIXTURE, *, registry=None, variables: dict | None = None,
             dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Проверяет и добавляет случай. Возвращает (ошибки, предупреждения); при ошибках файл не меняется."""
    registry = registry or default_registry()
    data = load_fixture(path)
    case = canonical(case) if isinstance(case, dict) else case
    problems = validate_case(case, data["cases"], registry, variables)
    if problems:
        return problems, []
    warnings = prefilter_warnings(case, registry)
    if dry_run:
        return [], warnings
    data["cases"].append(case)
    text = dump_fixture(data)
    fd, tmp = tempfile.mkstemp(prefix="eval_case_", suffix=".json", dir=str(Path(path).parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return [], warnings


def _parse_attachment(spec: str) -> dict:
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError("--attachment вида вид:имя[:размер], например audio:voice.mp3:2400000")
    out = {"kind": parts[0], "name": parts[1]}
    if len(parts) == 3:
        try:
            out["size"] = int(parts[2])
        except ValueError as exc:
            raise argparse.ArgumentTypeError("размер вложения - целое число байт") from exc
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Добавить случай в tests/fixtures/action_planner_eval.json",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--fixture", type=Path, default=FIXTURE, help="файл набора (по умолчанию боевой)")
    p.add_argument("--json", dest="json_src", help="случай целиком: путь к JSON-файлу или '-' (stdin)")
    p.add_argument("--id")
    p.add_argument("--text", help="фраза оператора как есть")
    p.add_argument("--outcome", choices=OUTCOMES, default="plan")
    p.add_argument("--workflow", help="метка области; по умолчанию берётся из первого действия (montage.swap_shots -> montage)")
    p.add_argument("--step", nargs=2, action="append", metavar=("ACTION_ID", "PARAMS_JSON"), default=[],
                   help="ожидаемый шаг; повторять по порядку; параметры - подмножество, project_id не нужен")
    p.add_argument("--also-offered", default="", help="id через запятую: действия, которые тоже должны попасть в каталог модели")
    p.add_argument("--clarify-contains", action="append", default=[], help="слово, которое должно быть в вопросе (clarify)")
    p.add_argument("--explanation-contains", action="append", default=[], help="слово в пояснении плана (например 'по умолчанию')")
    p.add_argument("--selected-frame", type=int)
    p.add_argument("--selected-scene", type=int)
    p.add_argument("--status")
    p.add_argument("--frame-count", type=int)
    p.add_argument("--attachment", action="append", type=_parse_attachment, default=[], help="вид:имя[:размер]")
    p.add_argument("--dry-run", action="store_true", help="только проверить, файл не менять")
    p.add_argument("--strict", action="store_true", help="предупреждения предфильтра считать ошибкой")
    return p


def case_from_args(a: argparse.Namespace) -> dict:
    if a.json_src:
        raw = sys.stdin.read() if a.json_src == "-" else Path(a.json_src).read_text(encoding="utf-8")
        return json.loads(raw)
    steps = []
    for aid, raw in a.step:
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"параметры шага {aid}: не JSON ({exc}). В PowerShell оберните JSON в одинарные кавычки.")
        steps.append({"action_id": aid, "params": params})
    ctx: dict = {}
    for key in ("selected_scene", "selected_frame", "status", "frame_count"):
        if getattr(a, key) is not None:
            ctx[key] = getattr(a, key)
    if a.attachment:
        ctx["attachments"] = a.attachment
    workflow = a.workflow or (steps[0]["action_id"].split(".")[0] if steps else "")
    case = {"id": a.id, "text": a.text, "workflow": workflow, "context": ctx, "outcome": a.outcome,
            "steps": steps, "also_offered": [x.strip() for x in a.also_offered.split(",") if x.strip()]}
    if a.clarify_contains:
        case["clarify_contains"] = a.clarify_contains
    if a.explanation_contains:
        case["explanation_contains"] = a.explanation_contains
    return case


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        case = case_from_args(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Не удалось прочитать случай: {exc}", file=sys.stderr)
        return 2
    if not args.json_src and not (args.id and args.text):
        print("Нужны --id и --text (или --json).", file=sys.stderr)
        return 2
    problems, warnings = add_case(case, args.fixture, dry_run=args.dry_run)
    if problems or (args.strict and warnings):
        for line in problems + (warnings if args.strict else []):
            print(f"ОШИБКА: {line}", file=sys.stderr)
        print("Файл не изменён.", file=sys.stderr)
        return 1
    for line in warnings:
        print(f"ВНИМАНИЕ: {line}", file=sys.stderr)
    total = len(load_fixture(args.fixture)["cases"])
    verb = "Проверено (dry-run), не записано" if args.dry_run else "Добавлено"
    print(f"{verb}: {case.get('id')}. Случаев в наборе: {total}.")
    if not args.dry_run:
        print("Дальше: .venv/Scripts/python.exe -m pytest tests/test_action_planner_eval_offline.py "
              "tests/test_action_planner_oracle.py -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
