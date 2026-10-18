"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { openStudioNode, type ActionResultView, type FieldDiffRow, type TextDiffLine } from "@/lib/orchestratorActionCards";

const CARD = "mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2";

const TITLES: Record<ActionResultView["kind"], string> = {
  create: "Новая нода",
  node: "Правка ноды",
  prompt: "Правка промта",
  params: "Параметры",
  report: "Отчёт",
  mark: "Пометка",
  error: "Ошибка",
  waiting: "Ждёт подтверждения",
  neutral: "Действие",
  invalid: "Некорректный ответ действия",
};

function NodeLink({ result }: { result: ActionResultView }) {
  if (!result.nodeRef) return null;
  const open = () => openStudioNode(result.nodeRef!);
  return (
    <div className="mt-2 flex items-center gap-2">
      <button type="button" className="text-[11px] text-primary underline-offset-2 hover:underline" data-testid="orch-node-ref" onClick={open}>
        {result.nodeRef.node_id}
      </button>
      <Button type="button" size="sm" variant="outline" className="h-7 text-[11px]" data-testid="orch-open-node" onClick={open}>
        Открыть
      </Button>
    </div>
  );
}

function Unified({ lines }: { lines: TextDiffLine[] }) {
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

function Fields({ rows, hideBefore }: { rows: FieldDiffRow[]; hideBefore: boolean }) {
  return (
    <div className="mt-2 space-y-1" data-testid="orch-field-diff">
      {rows.map((row) => (
        <div key={row.key} className="text-[11px] text-white/80" data-testid="orch-field-row" data-field={row.key} data-op={row.op}>
          <span className="text-white/45">{row.key}</span>
          {hideBefore || row.before == null ? null : <span data-testid="orch-field-before">{` было ${row.before}`}</span>}
          {row.op === "removed" ? (
            <span data-testid="orch-field-removed"> удалён</span>
          ) : (
            <span data-testid="orch-field-after">{row.after == null ? " стало null" : ` стало ${row.after}`}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function RiskLine({ result }: { result: ActionResultView }) {
  return (
    <div className="mt-1 text-[10px] uppercase tracking-wide text-white/40">
      {result.action}
      {result.metadata.paid ? " · платно" : ""}
      {result.metadata.destructive ? " · деструктивно" : ""}
      {result.metadata.risk ? ` · риск ${result.metadata.risk}` : ""}
    </div>
  );
}

function Extras({ result }: { result: ActionResultView }) {
  return (
    <>
      {result.conflict ? (
        <p className="mt-2 text-[11px] text-destructive" data-testid="orch-version-conflict">
          Конфликт версий · 409. Данные не изменены, версия устарела.
          {result.version != null ? ` · версия ${result.version}` : ""}
          {result.baseVersion != null ? ` · база ${result.baseVersion}` : ""}
        </p>
      ) : result.version != null ? (
        <p className="mt-2 text-[11px] text-white/50" data-testid="orch-action-version">
          версия {result.version}
          {result.baseVersion != null ? ` · база ${result.baseVersion}` : ""}
        </p>
      ) : null}
      {result.archiveRef ? (
        <p className="mt-1 text-[11px] text-white/60" data-testid="orch-archive">
          {`архив ${result.archiveRef}`}
        </p>
      ) : null}
      {result.snapshotId ? (
        <p className="mt-1 text-[11px] text-white/60" data-testid="orch-snapshot">
          {result.snapshotUrl ? (
            <a href={result.snapshotUrl} className="underline-offset-2 hover:underline">
              снимок {result.snapshotId}
            </a>
          ) : (
            `снимок ${result.snapshotId}`
          )}
        </p>
      ) : null}
      {result.warnings.length ? (
        <ul className="mt-2 space-y-1 text-[11px] text-warning" data-testid="orch-action-warnings">
          {result.warnings.map((item, index) => (
            <li key={`${index}:${item}`}>{item}</li>
          ))}
        </ul>
      ) : null}
      {result.partial || result.skipped.length ? (
        <div className="mt-2" data-testid="orch-action-partial">
          {result.partial ? <p className="text-[11px] text-white/55">Частично · пропущено</p> : null}
          {result.skipped.length ? (
            <ul className="mt-1 space-y-1 text-[11px] text-white/55" data-testid="orch-action-skipped">
              {result.skipped.map((item, index) => (
                <li key={`${index}:${item}`}>{`пропущено ${item}`}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {result.error && !result.conflict ? (
        <p className="mt-2 text-[11px] text-destructive" data-testid="orch-action-error">
          {result.error}
        </p>
      ) : null}
    </>
  );
}

export function ActionResultCard({
  result,
  onReportEdit,
  onRestore,
  onConflictRetry,
}: {
  result: ActionResultView;
  onReportEdit: (reportId: number, instruction: string) => Promise<boolean>;
  onRestore: (restoreAction: string, snapshotId: string | null, archiveRef: string | null) => Promise<boolean>;
  onConflictRetry: (actionName: string, baseVersion: number | null) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [restored, setRestored] = useState(false);
  const [retried, setRetried] = useState(false);

  const sendEdit = async () => {
    if (!result.reportId || busy || sent || !instruction.trim()) return;
    setBusy(true);
    try {
      const ok = await onReportEdit(result.reportId, instruction);
      if (ok) setSent(true);
    } finally {
      setBusy(false);
    }
  };

  const sendRestore = async () => {
    if (!result.restoreAction || busy || restored) return;
    setBusy(true);
    try {
      const ok = await onRestore(result.restoreAction, result.snapshotId, result.archiveRef);
      if (ok) setRestored(true);
    } finally {
      setBusy(false);
    }
  };

  const sendRetry = async () => {
    if (!result.conflict || busy || retried) return;
    setBusy(true);
    try {
      const ok = await onConflictRetry(result.action, result.baseVersion);
      if (ok) setRetried(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={CARD}
      data-testid={result.kind === "waiting" ? "orch-action-wait" : "orch-action-card"}
      data-action={result.action}
      data-kind={result.kind}
      data-conflict={result.conflict ? "true" : "false"}
    >
      <div className="text-xs font-semibold text-white">{TITLES[result.kind]}</div>
      {result.kind === "invalid" ? null : <RiskLine result={result} />}
      <Extras result={result} />
      {result.kind === "waiting" ? (
        <p className="mt-2 text-[11px] text-white/70" data-testid="orch-approval-estimate">
          {result.estimate == null ? "стоимость неизвестна" : `стоимость ${result.estimate}`}
        </p>
      ) : result.kind === "mark" ? (
        <p className="mt-2 text-[11px] text-white/80" data-testid="orch-soft-delete">
          Помечено, файл на месте
        </p>
      ) : result.kind === "report" && result.html != null ? (
        <iframe
          title="Превью отчёта"
          data-testid="orch-action-report"
          sandbox=""
          srcDoc={result.html}
          className="mt-2 h-40 w-full rounded-md border border-white/[0.08] bg-white"
        />
      ) : result.fields ? (
        <Fields rows={result.fields} hideBefore={result.hideBefore} />
      ) : result.unified ? (
        <Unified lines={result.unified} />
      ) : null}
      <NodeLink result={result} />
      {result.kind === "report" && result.reportId ? (
        <div className="mt-2">
          {editing ? (
            <div className="space-y-2">
              <Textarea
                data-testid="orch-report-instruction"
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="Что поправить в отчёте"
                className="min-h-[64px] text-xs"
                rows={2}
              />
              <Button
                type="button"
                size="sm"
                className="h-7 text-[11px]"
                data-testid="orch-report-send"
                disabled={busy || sent || !instruction.trim()}
                onClick={() => void sendEdit()}
              >
                {sent ? "Отправлено" : "Отправить правку"}
              </Button>
            </div>
          ) : (
            <Button type="button" size="sm" variant="outline" className="h-7 text-[11px]" data-testid="orch-report-edit" onClick={() => setEditing(true)}>
              Править
            </Button>
          )}
        </div>
      ) : null}
      {result.conflict ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="mt-2 h-7 text-[11px]"
          data-testid="orch-conflict-retry"
          disabled={busy || retried}
          onClick={() => void sendRetry()}
        >
          {retried ? "Отправлено" : "Обновить и повторить"}
        </Button>
      ) : null}
      {result.restoreAction ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="mt-2 h-7 text-[11px]"
          data-testid="orch-restore"
          disabled={busy || restored}
          onClick={() => void sendRestore()}
        >
          {restored ? "Отправлено" : "Восстановить"}
        </Button>
      ) : null}
    </div>
  );
}
