"""Live accuracy eval of the action planner on a sample of tests/fixtures/action_planner_eval.json.

Sends a small sample of the Russian eval requests through the real planner (strict validation,
catalog pre-filter, offered-only actions) and reports accuracy of action ids / params / clarification
and the tokens used. The planner only builds plan previews: nothing is created, approved or executed.
PAID (capped by --max-requests and --max-usd, also under --concurrency). Refuses to run without --live.
The project is a realistic seed (scripts/eval_seed.py: 30 frames, files, queue, nodes, change proposals,
master prompt variants) unless --no-seed-realistic is given. --shard i/n runs every n-th case (1-based i),
--concurrency N sends N requests at a time; the planner hourly call limit is raised for this process only.
Only VIBECODE_API_KEY / VIBECODE_BASE_URL / GPT_RELAY_TOKEN / GPT_PROXY_URL / GPT_BASE_URL are read from
--env-file; nothing secret is printed or written.
Cases with context.attachments ([{kind, name, size}]) get fake attachments (no files, no paths shown to the model):
the server fills source_path of the upload step from the attachment id, as in the real command line.
The scoring helpers (load_cases, sample_cases, score_case) are offline and unit-tested.
--set main|heldout|heldout2|heldout3|heldout4|both picks the fixture: main = tests/fixtures/action_planner_eval.json, heldout =
tests/fixtures/action_planner_heldout.json (first held-out sample, already looked at: now a regression set),
heldout2 = tests/fixtures/action_planner_heldout2.json (second, fresh held-out sample; never tune rules on it
and do not read its cases next to the errors), heldout3 = tests/fixtures/action_planner_heldout3.json (third fresh sample,
written before the rule fixes of the heldout2 review; not part of ``both``), heldout4 = tests/fixtures/action_planner_heldout4.json
(fourth fresh sample, written before the rule fixes of the heldout3 review; not part of ``both``). Never tune rules on any held-out set.
The report always has the metrics per set (``by_set``), so the held-out number is separate; transport errors
are counted apart (``transport_errors``) and also give ``*_no_transport`` figures that leave them out.
Cases with ``requires_action`` are skipped while that action is not in the registry (reported as ``skipped``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_ENV = ("VIBECODE_API_KEY", "VIBECODE_BASE_URL", "GPT_RELAY_TOKEN", "GPT_PROXY_URL", "GPT_BASE_URL")
FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_eval.json"
HELDOUT_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_heldout.json"
HELDOUT2_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_heldout2.json"
HELDOUT3_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_heldout3.json"
HELDOUT4_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_heldout4.json"
SETS = ("main", "heldout", "heldout2", "heldout3", "heldout4", "both")


def load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in _ENV:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_cases(path: Path = FIXTURE) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def load_heldout(path: Path = HELDOUT_FIXTURE) -> list[dict]:
    return load_cases(path)


def tag_set(cases: list[dict], name: str) -> list[dict]:
    """Копии случаев с пометкой набора (``set``): по ней отчёт считает метрики раздельно."""
    return [{**c, "set": name} for c in cases]


def check_disjoint(main: list[dict], heldout: list[dict]) -> list[str]:
    """Нарушения неприкосновенности отложенной выборки: общие id или совпадающий (без регистра/пробелов) текст."""
    def norm(t: str) -> str:
        return " ".join(str(t).lower().replace("ё", "е").split())

    bad = [f"id {i}" for i in {c["id"] for c in main} & {c["id"] for c in heldout}]
    texts = {norm(c["text"]) for c in main}
    bad += [f"text {c['id']}" for c in heldout if norm(c["text"]) in texts]
    return bad


def load_selected(which: str) -> list[dict]:
    """Случаи выбранного набора (main, heldout, heldout2, both) с пометкой ``set``;
    both = main, heldout, heldout2 подряд (если файл второго набора есть)."""
    if which not in SETS:
        raise ValueError(f"--set must be one of {SETS}, got {which!r}")
    main, held = tag_set(load_cases(), "main"), tag_set(load_heldout(), "heldout")
    bad = check_disjoint(main, held)
    if bad:
        raise ValueError(f"held-out set overlaps the main set: {bad[:5]}")
    held2: list[dict] = []
    if which in ("heldout2", "both") and HELDOUT2_FIXTURE.exists():
        held2 = tag_set(load_cases(HELDOUT2_FIXTURE), "heldout2")
        bad = check_disjoint(main + held, held2)
        if bad:
            raise ValueError(f"heldout2 overlaps other sets: {bad[:5]}")
    elif which == "heldout2":
        raise ValueError(f"{HELDOUT2_FIXTURE.name} not found")
    held3: list[dict] = []
    if which == "heldout3":
        if not HELDOUT3_FIXTURE.exists():
            raise ValueError(f"{HELDOUT3_FIXTURE.name} not found")
        held3 = tag_set(load_cases(HELDOUT3_FIXTURE), "heldout3")
        if HELDOUT2_FIXTURE.exists():
            held2 = tag_set(load_cases(HELDOUT2_FIXTURE), "heldout2")
        bad = check_disjoint(main + held + held2, held3)
        if bad:
            raise ValueError(f"heldout3 overlaps other sets: {bad[:5]}")
    held4: list[dict] = []
    if which == "heldout4":
        if not HELDOUT4_FIXTURE.exists():
            raise ValueError(f"{HELDOUT4_FIXTURE.name} not found")
        held4 = tag_set(load_cases(HELDOUT4_FIXTURE), "heldout4")
        others = main + held
        for extra in (HELDOUT2_FIXTURE, HELDOUT3_FIXTURE):
            if extra.exists():
                others += tag_set(load_cases(extra), extra.stem)
        bad = check_disjoint(others, held4)
        if bad:
            raise ValueError(f"heldout4 overlaps other sets: {bad[:5]}")
    return {"main": main, "heldout": held, "heldout2": held2, "heldout3": held3, "heldout4": held4,
            "both": main + held + held2}[which]


def runnable(cases: list[dict], registry) -> tuple[list[dict], list[str]]:
    """(случаи, которые можно гонять; id пропущенных): ``requires_action`` ещё не в реестре - пропуск."""
    keep, skipped = [], []
    for c in cases:
        need = c.get("requires_action")
        if need and registry.find(need) is None:
            skipped.append(c["id"])
        else:
            keep.append(c)
    return keep, skipped


def sample_cases(cases: list[dict], n: int) -> list[dict]:
    """Deterministic spread over the fixture, round-robin by workflow so every area is hit first."""
    n = max(0, min(n, len(cases)))
    by_wf: dict[str, list[dict]] = {}
    for c in cases:
        by_wf.setdefault(c.get("workflow", ""), []).append(c)
    out: list[dict] = []
    queues = [list(v) for _, v in sorted(by_wf.items())]
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def _subset(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k])
                                                for k, v in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(expected) <= len(actual)
                and all(_subset(e, a) for e, a in zip(expected, actual)))
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() in actual.strip().lower()
    return expected == actual


def parse_shard(text: str) -> tuple[int, int]:
    """'i/n' (1-based i) -> (i, n); raises ValueError on anything else."""
    try:
        i, n = (int(x) for x in str(text).split("/", 1))
    except ValueError as exc:
        raise ValueError(f"--shard must look like 2/4, got {text!r}") from exc
    if n < 1 or not 1 <= i <= n:
        raise ValueError(f"--shard i/n needs 1 <= i <= n, got {text!r}")
    return i, n


def shard_cases(cases: list[dict], i: int, n: int) -> list[dict]:
    """Every n-th case starting at the i-th (1-based): shards are disjoint and together cover all cases."""
    return [c for k, c in enumerate(cases) if k % n == i - 1]


class SpendBudget:
    """Money cap that stays correct under concurrency: a request starts only while
    spent + (requests in flight + 1) * estimate stays within the cap. The estimate is the most expensive
    finished request so far, never below ``floor_usd``, so the cap is not overshot unless a request costs
    more than anything seen before. Once the cap is hit nothing new starts (the cut is deterministic)."""

    def __init__(self, max_usd: float, floor_usd: float = 0.02) -> None:
        self.max_usd, self.floor_usd = max_usd, floor_usd
        self.spent, self.worst, self.inflight = 0.0, 0.0, 0
        self.stopped = False

    def _estimate(self) -> float:
        return max(self.floor_usd, self.worst)

    def try_start(self) -> bool:
        """Call without awaiting between the check and the increment (single event loop thread)."""
        if self.stopped:
            return False
        if self.spent + (self.inflight + 1) * self._estimate() > self.max_usd + 1e-12:
            self.stopped = True
            return False
        self.inflight += 1
        return True

    def finish(self, cost: float | None) -> None:
        self.inflight = max(0, self.inflight - 1)
        self.spent += float(cost or 0.0)
        self.worst = max(self.worst, float(cost or 0.0))


async def run_cases(cases: list[dict], run_one, *, concurrency: int, budget: SpendBudget,
                    on_row=None) -> list[dict]:
    """Run ``run_one(case) -> row`` with at most ``concurrency`` in flight; rows come back in case order.
    Cases not started because the budget is exhausted are simply absent."""
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    slots: list[dict | None] = [None] * len(cases)

    async def worker(idx: int, case: dict) -> None:
        async with sem:
            if not budget.try_start():
                return
            try:
                row = await run_one(case)
            except BaseException:
                budget.finish(0.0)
                raise
            budget.finish(row.get("est_cost_usd"))
            slots[idx] = row
            if on_row:
                on_row(row)

    await asyncio.gather(*(worker(i, c) for i, c in enumerate(cases)))
    return [r for r in slots if r is not None]


def resolve_vars(node, variables: dict):
    """Replace whole-string placeholders like "$frame.22" (see eval_seed.expected_vars) in expectations."""
    if isinstance(node, str) and node.startswith("$") and node[1:] in variables:
        return variables[node[1:]]
    if isinstance(node, dict):
        return {k: resolve_vars(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_vars(v, variables) for v in node]
    return node


def select_cases(all_cases: list[dict], only: str, max_requests: int) -> list[dict]:
    """--only ID,ID: exactly those cases (fixture order, no sampling); otherwise the round-robin sample."""
    if not only.strip():
        return sample_cases(all_cases, max_requests)
    want = [x.strip() for x in only.split(",") if x.strip()]
    known = {c["id"] for c in all_cases}
    unknown = [x for x in want if x not in known]
    if unknown:
        raise ValueError(f"--only: unknown case ids {unknown}")
    return [c for c in all_cases if c["id"] in set(want)]


def score_case(case: dict, steps: list[dict], clarification: str | None, error_code: str | None,
               explanation: str | None = None, variables: dict | None = None,
               options: list[str] | None = None) -> dict:
    """Compare a planner result with the expectation. Params are compared as a subset (project_id ignored)."""
    case = {**case, "steps": resolve_vars(case.get("steps", []), variables or {})}
    kind = case["outcome"]
    got = [s.get("action_id") for s in steps]
    row = {"id": case["id"], "outcome": kind, "got_actions": got, "clarified": bool(clarification),
           "error": error_code, "set": case.get("set", "main"),
           "transport_error": error_code == "transport_error"}
    if kind == "plan":
        want = [s["action_id"] for s in case["steps"]]
        row["ids_ok"] = got == want
        row["set_ok"] = set(got) == set(want)
        row["params_ok"] = row["ids_ok"] and all(
            _subset({k: v for k, v in w["params"].items() if k != "project_id"}, g.get("params") or {})
            for w, g in zip(case["steps"], steps))
        row["ok"] = row["params_ok"]
        need = [x.lower() for x in case.get("explanation_contains", [])]
        if need:  # e.g. the human must read which defaults were applied before approving
            row["explained_ok"] = all(x in (explanation or "").lower() for x in need)
            row["ok"] = bool(row["ok"] and row["explained_ok"])
    elif kind == "clarify":
        need = [x.lower() for x in case.get("clarify_contains", [])]
        # the question, its option buttons and the explanation together must carry the required words
        text = " ".join([clarification or "", *(options or []), explanation or ""]).lower()
        row["ok"] = bool(clarification) and not steps and all(x in text for x in need)
    else:  # refuse: no steps are acceptable (clarification or a server rejection)
        row["ok"] = not steps
    return row


def summarize(rows: list[dict]) -> dict:
    plans = [r for r in rows if r["outcome"] == "plan"]
    return {
        "requests": len(rows),
        "accuracy": round(sum(1 for r in rows if r.get("ok")) / max(1, len(rows)), 3),
        "plan_ids_exact": round(sum(1 for r in plans if r.get("ids_ok")) / max(1, len(plans)), 3),
        "plan_params_ok": round(sum(1 for r in plans if r.get("params_ok")) / max(1, len(plans)), 3),
        "clarify_ok": [r["ok"] for r in rows if r["outcome"] == "clarify"],
        "refuse_ok": [r["ok"] for r in rows if r["outcome"] == "refuse"],
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
        "avg_prompt_tokens": round(sum(r.get("prompt_tokens", 0) for r in rows) / max(1, len(rows))),
        "est_cost_usd": round(sum(r.get("est_cost_usd") or 0.0 for r in rows), 5),
        "failed_ids": [r["id"] for r in rows if not r.get("ok")],
        "transport_errors": sum(1 for r in rows if r.get("transport_error")),
        "transport_error_kinds": _kinds(rows),
        "accuracy_no_transport": _ratio([r for r in rows if not r.get("transport_error")],
                                        lambda r: r.get("ok")),
        "plan_ids_exact_no_transport": _ratio([r for r in plans if not r.get("transport_error")],
                                              lambda r: r.get("ids_ok")),
    }


def _kinds(rows: list[dict]) -> dict:
    """{тип исключения: сколько} по строкам с transport_error (текст в ``error_detail`` строки)."""
    out: dict[str, int] = {}
    for r in rows:
        if r.get("transport_error"):
            kind = str(r.get("error_detail") or "unknown").split(":", 1)[0].split(" ", 1)[0]
            out[kind] = out.get(kind, 0) + 1
    return out


def _ratio(rows: list[dict], pred) -> float:
    return round(sum(1 for r in rows if pred(r)) / max(1, len(rows)), 3)


def summarize_by_set(rows: list[dict]) -> dict:
    """{набор: summarize(строки набора)}: отложенная выборка считается отдельно от основной."""
    names = sorted({r.get("set", "main") for r in rows})
    return {n: summarize([r for r in rows if r.get("set", "main") == n]) for n in names}


def _load_seed_module():
    import importlib.util
    path = Path(__file__).resolve().parent / "eval_seed.py"
    spec = importlib.util.spec_from_file_location("eval_seed", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--seed-data", default="", help="a data dir copy to start from (default: empty data dir)")
    parser.add_argument("--project-id", type=int, default=2)
    parser.add_argument("--seed-realistic", action=argparse.BooleanOptionalAction, default=True,
                        help="fill the project with frames, files, queue, nodes, proposals, prompt variants")
    parser.add_argument("--set", dest="case_set", choices=SETS, default="main",
                        help="main = основной набор, heldout..heldout4 = отложенные выборки, both = main+heldout+heldout2 "
                             "(метрики раздельно)")
    parser.add_argument("--max-requests", type=int, default=10)
    parser.add_argument("--only", default="", help="comma-separated case ids to run (cheap re-run of failures)")
    parser.add_argument("--max-usd", type=float, default=0.10)
    parser.add_argument("--est-usd", type=float, default=0.02,
                        help="assumed cost of one request before real costs are known (cap safety margin)")
    parser.add_argument("--shard", default="1/1", help="i/n: run every n-th case starting at the i-th (1-based)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.live:
        print("needs --live")
        return 2
    try:
        shard_i, shard_n = parse_shard(args.shard)
    except ValueError as exc:
        print(str(exc))
        return 2
    try:
        select_cases(load_selected(args.case_set), args.only, args.max_requests)  # unknown ids fail early
    except ValueError as exc:
        print(str(exc))
        return 2
    os.environ.update(load_env(args.env_file))
    work = Path(tempfile.mkdtemp(prefix="eval-planner-"))
    if args.seed_data:
        shutil.copytree(args.seed_data, work / "data")
    else:
        (work / "data").mkdir()
    os.environ.update(SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", TELEGRAM_ENABLED="false",
                      DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"))
    # This process only: the default 30 calls/hour made earlier runs stop with rate_limited.
    os.environ["ACTION_LAYER_PLANNER_CALLS_PER_HOUR"] = "10000"
    # Every planner call reserves 0.05 USD in the local spend ledger of this throw-away DB, so ~200 calls hit
    # the default 10 USD project cap. The real money limit of this run is --max-usd, not the app caps.
    os.environ["SPEND_PROJECT_CAP_USD"] = os.environ["SPEND_DAILY_CAP_USD"] = "1000"
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if not (os.environ.get("VIBECODE_API_KEY") or "").strip():
        print("VIBECODE_API_KEY missing in env file; nothing sent")
        return 3
    from app.main import _init_db
    from app.services.action_layer import planner
    from app.services.action_layer.spec import ensure_builtin_actions

    await _init_db()
    ensure_builtin_actions()
    if args.seed_realistic:
        info = await _load_seed_module().seed_realistic(args.project_id, prompts_dir=work / "prompts")
        print(json.dumps({"seeded_project": info.project_id, "frames": len(info.frame_ids),
                          "proposals": len(info.proposals)}))

    seed_mod = _load_seed_module()
    variables = seed_mod.expected_vars(args.project_id) if args.seed_realistic else {}

    async def run_one(case: dict) -> dict:
        ctx = case.get("context") or {}
        started = time.monotonic()
        steps, clar, code, meta, outcome, detail = [], None, None, {}, None, ""
        try:
            outcome = await planner.plan_from_text(
                case["text"], operator="local_web_operator", project_id=args.project_id,
                selected_scene=ctx.get("selected_scene"), selected_frame=ctx.get("selected_frame"),
                attachments=seed_mod.fake_attachments(args.project_id, ctx.get("attachments")))
            steps = [s if isinstance(s, dict) else {"action_id": getattr(s, "action_id", None),
                                                    "params": getattr(s, "params", None)}
                     for s in (outcome.reply.steps or [])]
            clar, meta = outcome.reply.needs_clarification, outcome.meta
        except Exception as exc:  # noqa: BLE001 - a planner refusal/rejection is a scored result
            code = getattr(exc, "code", type(exc).__name__)
            # type and text of the underlying exception (redacted by the planner) - for transport_error
            detail = getattr(exc, "detail", "") or planner.describe_exception(exc)
        expl = outcome.reply.explanation_ru if outcome is not None else None
        opts = list(outcome.reply.clarification_options) if outcome is not None else []
        row = score_case(case, steps, clar, code, expl, variables, opts)
        row["error_detail"] = detail
        row.update(text=case["text"], clarification=clar, explanation=expl,
                   want=[x["action_id"] for x in case.get("steps", [])], params=[x.get("params") for x in steps],
                   want_params=[x.get("params") for x in resolve_vars(case.get("steps", []), variables)],
                   options=list(outcome.reply.clarification_options) if outcome is not None else [])
        row.update(seconds=round(time.monotonic() - started, 1),
                   prompt_tokens=meta.get("prompt_tokens", 0), completion_tokens=meta.get("completion_tokens", 0),
                   est_cost_usd=meta.get("est_cost_usd"), catalog_offered=meta.get("catalog_offered"),
                   attempts=meta.get("attempts"), applied_defaults=meta.get("applied_defaults"))
        return row

    from app.services.action_layer.spec import registry as live_registry
    pool, skipped = runnable(load_selected(args.case_set), live_registry)
    if skipped:
        print(f"skipped (action not in registry): {skipped}")
    cases = shard_cases(select_cases(pool, args.only, args.max_requests), shard_i, shard_n)
    budget = SpendBudget(args.max_usd, floor_usd=args.est_usd)
    rows = await run_cases(cases, run_one, concurrency=args.concurrency, budget=budget,
                           on_row=lambda r: print(json.dumps(r, ensure_ascii=False, default=str), flush=True))
    if budget.stopped:
        print(f"stopped: estimated spend {budget.spent:.4f} USD reached --max-usd {args.max_usd} "
              f"({len(rows)} of {len(cases)} cases ran)")
    report = {"summary": summarize(rows), "by_set": summarize_by_set(rows), "skipped": skipped,
              "rows": rows, "shard": args.shard, "concurrency": args.concurrency}
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(json.dumps({"by_set": report["by_set"]}, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
