"use client";

import { Button } from "@/components/ui/button";
import { fieldDiff, lineDiff, openStudioNode, type ActionResultView } from "@/lib/orchestratorActionCards";

const CARD = "mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2";

const TITLES: Record<ActionResultView["kind"], string> = {
  create: "Новая нода",
  node: "Правка ноды",
  prompt: "Правка промта",
  params: "Параметры",
  report: "Отчёт",
  neutral: "Действие",
};

function NodeLink({ result }: { result: ActionResultView }) {
  if (!result.nodeRef) return null;
  const open = () => openStudioNode(result.nodeRef!);
  return (
    <div className="mt-2 flex items-center gap-2">
      <button type="button" className="text-[11px] text-primary underline-offset-2 hover:underline" data-testid="orch-node-ref" onClick={open}>
        {result.nodeRef.node_key}
      </button>
      <Button type="button" size="sm" variant="outline" className="h-7 text-[11px]" data-testid="orch-open-node" onClick={open}>
        Открыть
      </Button>
    </div>
  );
}

function TextDiff({ before, after, hideBefore }: { before: string | null; after: string | null; hideBefore: boolean }) {
  if (hideBefore) {
    if (!after) return null;
    return (
      <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-white/80" data-testid="orch-diff-after">
        {after}
      </pre>
    );
  }
  const lines = lineDiff(before ?? "", after ?? "");
  return (
    <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-white/80" data-testid="orch-diff">
      {lines.map((line, index) => (
        <span
          key={`${line.kind}-${index}`}
          data-testid="orch-diff-line"
          data-kind={line.kind}
          className={line.kind === "add" ? "text-primary" : line.kind === "remove" ? "text-white/40" : "text-white/80"}
        >
          {line.kind === "add" ? "+ " : line.kind === "remove" ? "− " : "  "}
          {line.text}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function Fields({
  before,
  after,
  hideBefore,
}: {
  before: Record<string, string> | null;
  after: Record<string, string> | null;
  hideBefore: boolean;
}) {
  const rows = hideBefore
    ? Object.keys(after ?? {})
        .sort()
        .map((key) => ({ key, before: null as string | null, after: after?.[key] ?? null }))
    : fieldDiff(before, after);
  return (
    <div className="mt-2 space-y-1" data-testid="orch-field-diff">
      {rows.map((row) => (
        <div key={row.key} className="text-[11px] text-white/80" data-testid="orch-field-row" data-field={row.key}>
          <span className="text-white/45">{row.key}</span>
          {hideBefore ? null : <span data-testid="orch-field-before">{row.before == null ? "" : ` было ${row.before}`}</span>}
          <span data-testid="orch-field-after">{row.after == null ? "" : ` стало ${row.after}`}</span>
        </div>
      ))}
    </div>
  );
}

export function ActionResultCard({ result }: { result: ActionResultView }) {
  const hideBefore = result.kind === "create";
  const fields = result.beforeFields || result.afterFields;

  return (
    <div className={CARD} data-testid="orch-action-card" data-action={result.action} data-kind={result.kind}>
      <div className="text-xs font-semibold text-white">{TITLES[result.kind]}</div>
      <div className="mt-1 text-[10px] uppercase tracking-wide text-white/40">
        {result.action}
        {result.metadata.paid ? " · платно" : ""}
        {result.metadata.destructive ? " · деструктивно" : ""}
        {result.metadata.risk ? ` · риск ${result.metadata.risk}` : ""}
      </div>
      {fields ? <Fields before={result.beforeFields} after={result.afterFields} hideBefore={hideBefore} /> : <TextDiff before={result.beforeText} after={result.afterText} hideBefore={hideBefore} />}
      <NodeLink result={result} />
    </div>
  );
}
