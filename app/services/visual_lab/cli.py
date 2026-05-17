"""CLI entrypoint for the visual lab.

    python -m app.services.visual_lab.cli init     --slug my-cats --name "..."
    python -m app.services.visual_lab.cli refs     --slug my-cats [--seed]
    python -m app.services.visual_lab.cli run      --slug my-cats
    python -m app.services.visual_lab.cli auto     --slug my-cats --iters 5
    python -m app.services.visual_lab.cli think    --slug my-cats
    python -m app.services.visual_lab.cli build    --slug my-cats
    python -m app.services.visual_lab.cli report   --slug my-cats
    python -m app.services.visual_lab.cli excel    --slug my-cats

The browser-backed commands (`run`, `auto`, `think`, `build`) require
the Chrome CDP endpoint at localhost:29229 to be reachable and logged
into ChatGPT + outsee.io.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from loguru import logger  # noqa: F401

from app.services.visual_lab.build import build_phase as _build
from app.services.visual_lab.excel_export import rebuild_excel
from app.services.visual_lab.references import (
    add_reference,
    copy_seed_references,
)
from app.services.visual_lab.report import render_report
from app.services.visual_lab.storage import LabStorage
from app.services.visual_lab.think import think_phase as _think


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="visual_lab")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create skeleton on disk.")
    p_init.add_argument("--slug", required=True)
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--base-prompt", default="", help="initial visual prompt")

    p_refs = sub.add_parser("refs", help="Manage reference images.")
    p_refs.add_argument("--slug", required=True)
    p_refs.add_argument("--seed", action="store_true", help="copy bundled seed refs")
    p_refs.add_argument("--add", nargs=2, metavar=("IMAGE", "PROMPT"))

    p_run = sub.add_parser("run", help="Run ONE iteration.")
    p_run.add_argument("--slug", required=True)
    p_run.add_argument("--build", action="store_true",
                       help="rebuild master_prompt before generating")

    p_auto = sub.add_parser("auto", help="Run N iterations end-to-end.")
    p_auto.add_argument("--slug", required=True)
    p_auto.add_argument("--iters", type=int, default=5)

    for name, helptext in (
        ("think", "Run think phase only."),
        ("build", "Run build phase only."),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--slug", required=True)

    p_rep = sub.add_parser("report", help="Render report.md")
    p_rep.add_argument("--slug", required=True)

    p_xls = sub.add_parser("excel", help="Rebuild scores.xlsx")
    p_xls.add_argument("--slug", required=True)

    return p


def _cmd_init(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    project = storage.ensure_skeleton(args.name)
    if args.base_prompt:
        project.base_visual_prompt = args.base_prompt
        project.master_prompt = args.base_prompt
        storage.save_project(project)
    print(f"OK init: {storage.root}")
    return 0


def _cmd_refs(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    if not storage.project_path.exists():
        print(f"ERR: project {args.slug!r} not initialized. Run `init` first.")
        return 2
    if args.seed:
        out = copy_seed_references(storage)
        print(f"OK seed refs copied: {len(out)} files")
    if args.add:
        img, prompt = args.add
        ref = add_reference(
            storage, image_path=Path(img), prompt=prompt
        )
        print(f"OK reference added: {ref.file}")
    return 0


@contextlib.asynccontextmanager
async def _make_runner(storage: LabStorage) -> AsyncIterator[object]:
    from app.bots.browser import browser_session
    from app.bots.chatgpt import ChatGPTBot
    from app.bots.outsee import OutseeBot
    from app.services.visual_lab.runner import VisualLabRunner

    project = storage.load_project()
    if project is None:
        raise RuntimeError(f"project {storage.slug!r} not initialized")

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        gpt = ChatGPTBot(bs)

        async def gen_image(prompt: str, out_path: Path, prefix: str) -> None:
            res = await outsee.generate_image(
                prompt=prompt,
                out_path=out_path,
                aspect_ratio=project.aspect_ratio,
                model_slug=project.model_slug,
                relax=project.relax,
                prompt_id_prefix=prefix,
            )
            if not res.success or not out_path.exists():
                raise RuntimeError(
                    f"outsee returned success={res.success} but no file at {out_path}"
                )

        async def ask_with_files(prompt: str, attachments: list[Path]) -> str:
            await gpt.new_conversation()
            return await gpt.ask_with_files(prompt, attachments, timeout=900)

        runner = VisualLabRunner(
            storage,
            generate_image=gen_image,
            chatgpt_ask_with_files=ask_with_files,
        )
        yield runner


async def _cmd_run(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    async with _make_runner(storage) as runner:
        iter_doc = await runner.run_one(run_build_before=args.build)
    print(
        json.dumps(
            {
                "iter": iter_doc.iter,
                "phase": iter_doc.phase,
                "verdict": iter_doc.verdict,
                "weighted_score": round(iter_doc.weighted_score, 2),
            },
            ensure_ascii=False,
        )
    )
    render_report(storage)
    return 0


async def _cmd_auto(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    async with _make_runner(storage) as runner:
        done = await runner.run_auto(iterations=args.iters)
    for it in done:
        print(
            json.dumps(
                {
                    "iter": it.iter,
                    "phase": it.phase,
                    "verdict": it.verdict,
                    "weighted_score": round(it.weighted_score, 2),
                },
                ensure_ascii=False,
            )
        )
    render_report(storage)
    return 0


async def _cmd_think(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    async with _make_runner(storage) as runner:
        res = await _think(
            storage,
            chatgpt_ask_with_files=runner.chatgpt_ask_with_files,
        )
    print(json.dumps({"new_hypotheses": len(res.new_hypotheses)}, ensure_ascii=False))
    render_report(storage)
    return 0


async def _cmd_build(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    async with _make_runner(storage) as runner:
        res = await _build(
            storage,
            chatgpt_ask_with_files=runner.chatgpt_ask_with_files,
        )
    print(
        json.dumps(
            {"master_prompt_len": len(res.master_prompt)},
            ensure_ascii=False,
        )
    )
    render_report(storage)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    text = render_report(storage)
    print(text)
    return 0


def _cmd_excel(args: argparse.Namespace) -> int:
    storage = LabStorage(args.slug)
    rebuild_excel(storage)
    print(f"OK excel rebuilt: {storage.excel_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    sync = {
        "init": _cmd_init,
        "refs": _cmd_refs,
        "report": _cmd_report,
        "excel": _cmd_excel,
    }
    async_cmds = {
        "run": _cmd_run,
        "auto": _cmd_auto,
        "think": _cmd_think,
        "build": _cmd_build,
    }
    if args.cmd in sync:
        return sync[args.cmd](args)
    if args.cmd in async_cmds:
        return asyncio.run(async_cmds[args.cmd](args))
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
