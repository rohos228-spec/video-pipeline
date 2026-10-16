"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { RiskBadge } from "@/components/action-layer/plan-card";
import {
  approvePlan, canApprove, getPlan, isTerminal, previewFromError, runPlan, PLAN_STATUS_RU, STEP_STATUS_RU, stopReasonRu,
  type PlanPreview, type PlanView, type StepView,
} from "@/lib/action-layer";
import {
  changeErrorRu, describeChangeError, describePreviewProblems, describeTarget, previewApply, previewRollback,
  shortDigest, type Proposal,
} from "@/lib/change-control";
import { cn } from "@/lib/utils";

export type FlowMode = "apply" | "rollback";

interface Props {
  mode: FlowMode;
  proposal: Proposal;
  currentVersion?: number;
  changeLines: string[];
  onClose: () => void;
  onFinished: () => void;
}

type Phase =
  | { name: "setup" }
  | { name: "previewing" }
  | { name: "ready"; planId: string; preview: PlanPreview }
  | { name: "invalid"; message: string; preview: PlanPreview | null }
  | { name: "running"; planId: string }
  | { name: "failed"; message: string };

const TITLE: Record<FlowMode, string> = { apply: "Применение изменения", rollback: "Откат изменения" };

export function ChangeFlow({ mode, proposal, currentVersion, changeLines, onClose, onFinished }: Props) {
  const [phase, setPhase] = useState<Phase>(mode === "rollback" ? { name: "setup" } : { name: "previewing" });
  const [reason, setReason] = useState("");
  const startedRef = useRef(false);

  async function startPreview() {
    setPhase({ name: "previewing" });
    try {
      const r = mode === "apply"
        ? await previewApply(proposal)
        : await previewRollback(proposal.target, currentVersion ?? 0, reason);
      if (!r.plan_id || !r.preview) {
        setPhase({ name: "invalid", message: "Сервер не вернул план для этого изменения.", preview: null });
      } else if (!r.preview.ok) {
        setPhase({ name: "invalid", message: describePreviewProblems(r.preview) || "План не прошёл проверку.", preview: r.preview });
      } else {
        setPhase({ name: "ready", planId: r.plan_id, preview: r.preview });
      }
    } catch (e) {
      setPhase({ name: "invalid", message: describeChangeError(e), preview: previewFromError(e) });
    }
  }

  useEffect(() => {
    if (mode === "apply" && !startedRef.current) {
      startedRef.current = true;
      void startPreview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const title = TITLE[mode];
  return (
    <section aria-label={title} data-testid={`change-flow-${mode}`} className="space-y-2 rounded border border-white/10 p-2">
      <div className="flex items-center justify-between gap-2">
        <h5 className="font-semibold text-white/85">{title}{phase.name === "running" ? "" : phase.name === "failed" ? " · не выполнено" : " · ничего ещё не изменено"}</h5>
        {phase.name === "ready" && <RiskBadge risk={phase.preview.risk_max} />}
      </div>
      <p className="text-white/55">
        Цель: {describeTarget(proposal.target)} · предложение {proposal.id.slice(0, 8)} · контрольный хэш {shortDigest(proposal.digest)}
      </p>

      {phase.name === "setup" && (
        <div className="space-y-1.5">
          <p className="text-white/70">
            Откат запишет новую версию с прежним значением (история не удаляется). Сначала сервер проверит, что откат возможен;
            выполнится он только после вашего подтверждения.
          </p>
          <label className="block">
            <span className="text-white/60">Причина отката (необязательно)</span>
            <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} aria-label="Причина отката"
              className="mt-0.5 block w-full max-w-md rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30"
              placeholder="например: результат стал хуже" />
          </label>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void startPreview()}>Проверить откат</Button>
            <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onClose}>Закрыть</Button>
          </div>
        </div>
      )}

      {phase.name === "previewing" && <p className="text-white/50">Проверяю, можно ли выполнить это сейчас…</p>}

      {phase.name === "invalid" && (
        <div className="space-y-1.5">
          <p role="alert" data-testid="change-flow-invalid" className="rounded border border-red-400/30 bg-red-500/10 p-1.5 text-red-200">
            Сейчас это сделать нельзя. {phase.message}
          </p>
          <p className="text-white/50">Ничего не изменено. Подтверждение не запрашивалось.</p>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onClose}>Закрыть</Button>
        </div>
      )}

      {phase.name === "ready" && (
        <ReadyBlock mode={mode} proposal={proposal} planId={phase.planId} preview={phase.preview} changeLines={changeLines}
          onBack={onClose} onStarted={(planId) => setPhase({ name: "running", planId })}
          onError={(message) => setPhase({ name: "failed", message })} />
      )}

      {phase.name === "running" && <RunBlock mode={mode} planId={phase.planId} onFinished={onFinished} onClose={onClose} />}

      {phase.name === "failed" && (
        <div className="space-y-1.5">
          <p role="alert" className="text-red-300">{phase.message}</p>
          <p className="text-white/50">Если подтверждение уже выдано, оно одноразовое и живёт ограниченное время; можно открыть план заново.</p>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onClose}>Закрыть</Button>
        </div>
      )}
    </section>
  );
}

function ReadyBlock({ mode, proposal, planId, preview, changeLines, onBack, onStarted, onError }: {
  mode: FlowMode; proposal: Proposal; planId: string; preview: PlanPreview; changeLines: string[];
  onBack: () => void; onStarted: (planId: string) => void; onError: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const verb = mode === "apply" ? "применение" : "откат";

  async function go() {
    setBusy(true);
    setError("");
    try {
      await approvePlan(planId);
      await runPlan(planId);
      onStarted(planId);
    } catch (e) {
      const msg = describeChangeError(e);
      setError(msg);
      if (/план изменился|не прошёл проверку/i.test(msg)) onError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {preview.destructive_effects.length > 0 && (
        <div className="rounded border border-red-400/30 bg-red-500/10 p-1.5" data-testid="change-flow-effects">
          <p className="font-semibold text-red-200">Что произойдёт:</p>
          <ul className="list-disc pl-4 text-red-100/90">
            {preview.destructive_effects.map((d) => <li key={`${d.index}-${d.action_id}`}>{d.text}</li>)}
          </ul>
        </div>
      )}
      {changeLines.length > 0 && (
        <ul className="list-disc pl-4 text-white/75">{changeLines.map((l, i) => <li key={i}>{l}</li>)}</ul>
      )}
      <p className="text-white/70">
        Проверки пройдены, но {verb} ещё не выполнено: нужно ваше подтверждение. Само по себе ничего не применяется.
        {mode === "apply" ? " Откат будет доступен после применения." : ""}
      </p>
      {!preview.needs_approval && (
        <p role="alert" className="text-amber-200">Сервер не запросил подтверждение для этого плана, хотя действие разрушающее. Выполнение отключено: сообщите разработчику.</p>
      )}
      {preview.budget && !preview.budget.fits && <p role="alert" className="text-red-300">План не помещается в потолок расходов.</p>}

      {confirming ? (
        <div role="alertdialog" aria-label="Подтверждение изменения" data-testid="change-confirm-dialog"
          className="space-y-1 rounded border border-amber-400/40 bg-amber-500/10 p-2 text-amber-50">
          {mode === "apply" ? (
            <>
              <p>
                Вы подтверждаете применение предложения {proposal.id.slice(0, 8)} к {proposal.node_key ? `ноде ${proposal.node_key}` : "проекту"}.
                Расход кредитов: 0.
              </p>
              {changeLines.length > 0 && <p>Будет изменено: {changeLines.join("; ")}.</p>}
              <p>
                Откатить можно: кнопка «Откатить…» в этой карточке запишет новую версию с прежним значением. Откат тоже потребует вашего подтверждения.
                Он не вернёт данные, файлы и уже потраченные запросы.
              </p>
            </>
          ) : (
            <>
              <p>Вы подтверждаете откат: {describeTarget(proposal.target)} вернётся к значению, предшествовавшему применению. Расход кредитов: 0.</p>
              <p>История версий сохранится: откат добавит новую строку. Данные, файлы и уже потраченные запросы не возвращаются.</p>
            </>
          )}
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" className="h-7 text-xs" disabled={busy || !canApprove(preview)} onClick={() => void go()}>
              {busy ? "Выполняю…" : mode === "apply" ? "Да, подтверждаю и применяю" : "Да, подтверждаю и откатываю"}
            </Button>
            <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busy} onClick={() => setConfirming(false)}>Назад</Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" className="h-7 border-amber-400/50 text-xs text-amber-100"
            disabled={busy || !canApprove(preview)} onClick={() => setConfirming(true)}>
            {mode === "apply" ? "Подтвердить и применить…" : "Подтвердить и откатить…"}
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onBack}>Отменить</Button>
        </div>
      )}
      {error && <p role="alert" className="text-red-300">{error}</p>}
    </div>
  );
}

function stepResultLines(mode: FlowMode, step: StepView): string[] {
  const r = step.result ?? {};
  const num = (k: string) => (typeof r[k] === "number" ? (r[k] as number) : null);
  if (mode === "apply") {
    const v = num("version");
    const prev = num("previous_version");
    return v != null ? [`Записана версия ${v}${prev != null ? ` (предыдущая: ${prev})` : ""}. Значение прочитано обратно и сверено.`] : [];
  }
  const v = num("version");
  const from = num("restored_from_version");
  const before = num("previous_current");
  return v != null
    ? [`Записана версия ${v}: восстановлено значение версии ${from ?? "?"}${before != null ? ` (до отката была версия ${before})` : ""}.`]
    : [];
}

function RunBlock({ mode, planId, onFinished, onClose }: { mode: FlowMode; planId: string; onFinished: () => void; onClose: () => void }) {
  const [view, setView] = useState<PlanView | null>(null);
  const [error, setError] = useState("");
  const notified = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let done = false;
      try {
        const next = await getPlan(planId);
        if (cancelled) return;
        setView(next);
        setError("");
        done = isTerminal(next.status) && !next.live;
        if (done && !notified.current) { notified.current = true; onFinished(); }
      } catch (e) {
        if (!cancelled) setError(describeChangeError(e));
      }
      if (!cancelled && !done) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId]);

  if (!view) return <p className="text-white/50">{error || "Загружаю состояние выполнения…"}</p>;
  const step = view.steps[0];
  const finished = isTerminal(view.status) && !view.live;
  const ok = view.status === "succeeded" && step?.status === "succeeded";
  return (
    <div className="space-y-1.5" aria-live="polite" data-testid="change-flow-run">
      <p className="text-white/75">
        План: {PLAN_STATUS_RU[view.status]}
        {step && <> · шаг: <span className={cn(step.status === "failed" || step.status === "unknown" ? "text-red-300" : step.status === "succeeded" ? "text-emerald-300" : "text-sky-200")}>{STEP_STATUS_RU[step.status]}</span></>}
      </p>
      {step?.status === "running" && (
        <div role="progressbar" aria-label="Выполнение" className="h-1.5 overflow-hidden rounded bg-white/10">
          <div className="h-full w-1/3 animate-pulse bg-sky-300/70" />
        </div>
      )}
      {ok && step && (
        <div role="status" data-testid="change-flow-result" className="rounded border border-emerald-400/30 bg-emerald-500/10 p-1.5 text-emerald-100">
          <p className="font-semibold">{mode === "apply" ? "Изменение применено." : "Откат выполнен."}</p>
          {stepResultLines(mode, step).map((l, i) => <p key={i}>{l}</p>)}
        </div>
      )}
      {step?.error && (
        <p role="alert" data-testid="change-flow-error" className="rounded border border-red-400/30 bg-red-500/10 p-1.5 text-red-200">
          {mode === "apply" ? "Не применено" : "Откат не выполнен"}: {changeErrorRu(step.error)}
        </p>
      )}
      {step?.status === "unknown" && <p className="text-red-300">Состояние шага неизвестно: проверьте значение в проекте вручную, автоповтора нет.</p>}
      {view.error && !step?.error && <p role="alert" className="text-red-300">Ошибка плана: {changeErrorRu(view.error)}</p>}
      {view.stop_reason && <p role="status" className="text-amber-200">План остановлен: {stopReasonRu(view.stop_reason)}</p>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {finished && <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onClose}>Закрыть</Button>}
    </div>
  );
}
