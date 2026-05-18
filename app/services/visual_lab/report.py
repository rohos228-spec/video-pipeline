"""Markdown report generator for the visual lab.

Writes ``report.md`` to the project root with everything the user needs
to read at a glance: weakest criteria, score history, top words, open
hypotheses, errors. Re-generated after every successful iteration.
"""

from __future__ import annotations

from app.services.visual_lab.criteria import CRITERIA, CRITERION_BY_ID
from app.services.visual_lab.storage import LabStorage
from app.services.visual_lab.think import all_weak_criteria


def render_report(storage: LabStorage) -> str:
    """Build the markdown report string (also writes it to disk)."""
    project = storage.load_project()
    if project is None:
        text = (
            f"# Visual lab report — `{storage.slug}`\n\n"
            f"_project.json не найден_\n"
        )
        storage.report_path.write_text(text, encoding="utf-8")
        return text

    iters = storage.load_all_iters()
    successful = [
        i for i in iters if i.phase == "ok" and i.weighted_score > 0
    ]
    kb = storage.load_knowledge()
    weak = all_weak_criteria(storage)

    lines: list[str] = [
        f"# Visual lab — `{project.name}` (slug `{project.slug}`)",
        "",
        f"- status: **{project.status}**",
        f"- current_iter: **{project.current_iter}**",
        f"- best_iter: **{project.best_iter}**, best_score: **{project.best_score:.2f}**",
        f"- target_avg_score: {project.stopping_rules.target_avg_score}",
        f"- aspect_ratio: {project.aspect_ratio}, model: `{project.model_slug}`, relax: {project.relax}",
        f"- total_iterations_attempted: {project.meta.total_iterations_attempted}",
        f"- total_iterations_succeeded: {project.meta.total_iterations_succeeded}",
        "",
    ]

    if project.meta.total_phase_errors_by_type:
        lines.append("## Errors by phase")
        for k, v in sorted(project.meta.total_phase_errors_by_type.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")

    if weak:
        lines.append("## Слабейшие критерии (по среднему за все итерации)")
        for cid in weak:
            c = CRITERION_BY_ID[cid]
            lines.append(f"- **{cid}** ({c.group}): {c.name_ru}")
        lines.append("")

    # Score history.
    if project.average_scores_history:
        lines.append("## История весовой оценки")
        lines.append("| iter | weighted_score | delta |")
        lines.append("|---|---|---|")
        prev = None
        for point in project.average_scores_history:
            delta = "" if prev is None else f"{point.weighted_score - prev:+.2f}"
            lines.append(
                f"| {point.iter} | {point.weighted_score:.2f} | {delta} |"
            )
            prev = point.weighted_score
        lines.append("")

    # Word effects.
    if kb.word_effects:
        lines.append("## Word effects (топ 20 по |avg_weighted_delta|)")
        lines.append("| word | tested | stability | avg_weighted_delta | top criteria |")
        lines.append("|---|---|---|---|---|")
        sorted_words = sorted(
            kb.word_effects.items(),
            key=lambda x: abs(x[1].avg_weighted_delta),
            reverse=True,
        )[:20]
        for word, eff in sorted_words:
            top = sorted(
                eff.avg_delta.items(), key=lambda x: abs(x[1]), reverse=True
            )[:3]
            top_str = ", ".join(f"{c}:{v:+.1f}" for c, v in top)
            lines.append(
                f"| `{word}` | {eff.tested} | {eff.stability} | "
                f"{eff.avg_weighted_delta:+.2f} | {top_str} |"
            )
        lines.append("")

    if kb.visual_rules.get("do") or kb.visual_rules.get("dont"):
        lines.append("## Visual rules")
        for r in kb.visual_rules.get("do", []):
            lines.append(f"- ✅ {r.rule} _({r.supporting_evidence})_")
        for r in kb.visual_rules.get("dont", []):
            lines.append(f"- ❌ {r.rule} _({r.supporting_evidence})_")
        lines.append("")

    if kb.hypotheses:
        open_h = [h for h in kb.hypotheses if h.status == "PROPOSED"]
        if open_h:
            lines.append("## Открытые гипотезы")
            for h in sorted(open_h, key=lambda x: -x.priority)[:10]:
                lines.append(
                    f"- **#{h.id} [{h.type}] prio={h.priority}**: {h.text}"
                )
            lines.append("")

    # Recent iterations.
    if successful:
        lines.append("## Последние 5 успешных итераций")
        for it in successful[-5:]:
            scores = it.analysis.scores if it.analysis else {}
            top_pros = ", ".join((it.analysis.visual_pros if it.analysis else [])[:3])
            top_cons = ", ".join((it.analysis.visual_cons if it.analysis else [])[:3])
            lines.append(
                f"### iter {it.iter} — score {it.weighted_score:.2f} ({it.verdict})"
            )
            lines.append(f"- prompt_len: {len(it.prompt.text)}")
            if top_pros:
                lines.append(f"- ✅ pros: {top_pros}")
            if top_cons:
                lines.append(f"- ❌ cons: {top_cons}")
            lines.append(
                "- scores: "
                + ", ".join(f"{c.id}:{scores.get(c.id, '—')}" for c in CRITERIA)
            )
            lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    storage.report_path.write_text(text, encoding="utf-8")
    return text


__all__ = ["render_report"]
