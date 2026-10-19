"use client";
import {useCallback, useEffect, useRef, useState} from "react";
import {Button} from "@/components/ui/button";
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from "@/components/ui/dialog";
import {observations} from "@/lib/observations";
import {mediaRecovery} from "@/lib/media-recovery";
import {describeError, previewFromError, previewPlan, type PlanPreviewResponse} from "@/lib/action-layer";
import {
  MAX_REASON_CHARS, MEDIA_NODES, MIN_REASON_CHARS, RESUME_NODES, RELEASE_ACTION, RESUME_ACTION, SECOND_CHARGE_WARNING,
  cleanReason, codeText, formatAge, formatBlockedUntil, hasTaskId, ledgerCounts, ledgerText, modeClass, modeExplain,
  modeLabel, providerLabel, reasonOk, releaseParams, resumeBlockedReason, resumeParams, taskIds,
  type RecoveryAssessment, type UnknownOutcome, type UnknownOutcomes,
} from "@/lib/media-recovery-view";
import {
  COVERAGE_LEVEL_CLASS, coverageLevelLabel, coverageSummaryLine, forbiddenText, type NodeCoverage, type NodeCoverageRow,
} from "@/lib/node-coverage-view";
import {PlanCard} from "../action-layer/plan-card";
import {RunProgress} from "../action-layer/run-progress";
import {cn} from "@/lib/utils";

type NodeState = {data?: RecoveryAssessment; error?: string};

export function MediaRecoverySection({projectId}: {projectId: number | null}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [nodes, setNodes] = useState<Record<string, NodeState>>({});
  const [unknown, setUnknown] = useState<UnknownOutcomes | null>(null);
  const [unknownError, setUnknownError] = useState("");
  const [coverage, setCoverage] = useState<NodeCoverage | null>(null);
  const [coverageError, setCoverageError] = useState("");
  const [plan, setPlan] = useState<PlanPreviewResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [busyNode, setBusyNode] = useState("");
  const [planError, setPlanError] = useState("");
  const [release, setRelease] = useState<UnknownOutcome | null>(null);
  const [reason, setReason] = useState("");
  const [reasonBusy, setReasonBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const planRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (project: number, isCancelled: () => boolean) => {
    setLoading(true);
    const [cov, unk, ...assessed] = await Promise.allSettled([
      observations.nodeCoverage(project),
      mediaRecovery.unknownOutcomes(project),
      ...MEDIA_NODES.map((n) => mediaRecovery.assessment(project, n.node_type)),
    ]);
    if (isCancelled()) return;
    if (cov.status === "fulfilled") { setCoverage(cov.value); setCoverageError(""); }
    else { setCoverage(null); setCoverageError("Покрытие нод недоступно: сервер не ответил."); }
    if (unk.status === "fulfilled") { setUnknown(unk.value as UnknownOutcomes); setUnknownError(""); }
    else { setUnknown(null); setUnknownError("Список блокировок недоступен: сервер не ответил."); }
    const next: Record<string, NodeState> = {};
    MEDIA_NODES.forEach((n, i) => {
      const r = assessed[i];
      next[n.node_type] = r.status === "fulfilled"
        ? {data: r.value as RecoveryAssessment}
        : {error: "Состояние не прочитано: сервер не ответил."};
    });
    setNodes(next);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!open || projectId == null) return;
    let cancelled = false;
    setPlan(null); setRunId(null); setRunning(false); setPlanError("");
    void load(projectId, () => cancelled);
    return () => { cancelled = true; };
  }, [open, projectId, load]);

  const reload = () => { if (projectId != null) void load(projectId, () => false); };

  const showPlan = (r: PlanPreviewResponse) => {
    setPlan(r); setRunId(null); setPlanError("");
    setTimeout(() => planRef.current?.scrollIntoView?.({block: "nearest"}), 0);
  };

  async function planResume(nodeType: string, title: string) {
    if (projectId == null) return;
    setBusyNode(nodeType); setPlanError("");
    const label = `Докачать медиа: ${title}`;
    try {
      showPlan(await previewPlan([{action_id: RESUME_ACTION, params: resumeParams(projectId, nodeType)}], label));
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) showPlan({plan_id: null, preview: pv});
      else setPlanError(describeError(e));
    } finally {
      setBusyNode("");
    }
  }

  async function planRelease() {
    if (projectId == null || !release || !reasonOk(reason)) return;
    setReasonBusy(true); setDialogError("");
    const label = `Снять блокировку неизвестного исхода: ${release.key} (${providerLabel(release.provider)})`;
    try {
      const r = await previewPlan([{action_id: RELEASE_ACTION, params: releaseParams(projectId, release, reason)}], label);
      setRelease(null); setReason("");
      showPlan(r);
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) { setRelease(null); setReason(""); showPlan({plan_id: null, preview: pv}); }
      else setDialogError(describeError(e));
    } finally {
      setReasonBusy(false);
    }
  }

  if (projectId == null) return null;
  const coverageByNode = new Map<string, NodeCoverageRow>((coverage?.nodes ?? []).map((r) => [r.node_type, r]));
  const blocked = unknown?.blocked ?? [];

  return (
    <details data-testid="media-recovery-panel" className="space-y-2 rounded border border-white/10 p-2 text-xs"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer font-semibold text-white/80">Восстановление медиа</summary>
      <p className="mt-2 text-white/40">
        Что можно сделать с оплаченными клипами, предметами, музыкой и озвучкой после сбоя, не платя второй раз.
        Эта панель только читает. «Докачать» и «Снять блокировку…» не выполняются сразу: они составляют план,
        который вы проверите и подтвердите в карточке плана.
      </p>
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" variant="outline" className="h-7 text-xs" disabled={loading} onClick={reload}>
          {loading ? "Загрузка…" : "Обновить"}
        </Button>
        {coverage && !coverageError && (
          <span data-testid="media-recovery-coverage-summary" className="text-white/60">
            Покрытие нод: {coverageSummaryLine(coverage.summary)}
          </span>
        )}
      </div>
      {loading && Object.keys(nodes).length === 0 && <p data-testid="media-recovery-loading" className="text-white/50">Загрузка…</p>}
      {coverageError && <p role="alert" className="text-amber-200">{coverageError}</p>}
      {planError && <p role="alert" className="text-red-300">{planError}</p>}

      <div ref={planRef} className="space-y-2" data-testid="media-recovery-plan">
        {plan && !runId && (
          <PlanCard plan={plan} busyElsewhere={running}
            onStarted={(id) => { setRunId(id); setRunning(true); setPlan(null); }}
            onDismiss={() => setPlan(null)} />
        )}
        {runId && <RunProgress planId={runId} onFinished={() => { setRunning(false); reload(); }} />}
      </div>

      {Object.keys(nodes).length > 0 && (
        <ul className="space-y-1.5" aria-label="Медиа-ноды">
          {MEDIA_NODES.map((n) => {
            const st = nodes[n.node_type];
            if (!st) return null;
            const a = st.data;
            const cov = coverageByNode.get(n.node_type);
            const why = resumeBlockedReason(a);
            const ids = a ? taskIds(a) : [];
            return (
              <li key={n.node_type} data-testid="media-recovery-row" data-node={n.node_type}
                className="space-y-1 rounded border border-white/[0.08] p-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-white/85">{n.title}</span>
                  <code className="text-[10px] text-white/40">{n.node_type}</code>
                  {a && (
                    <span data-testid="media-recovery-verdict" data-mode={a.verdict.mode}
                      className={cn("whitespace-nowrap rounded border px-1.5 text-[10px]", modeClass(a.verdict.mode))}>
                      {modeLabel(a.verdict.mode)}
                    </span>
                  )}
                  {cov && (
                    <span className={cn("whitespace-nowrap rounded border px-1.5 text-[10px]", COVERAGE_LEVEL_CLASS[cov.level])}
                      title="Уровень из панели «Покрытие нод»">
                      {coverageLevelLabel(cov.level)}
                    </span>
                  )}
                  <span className="flex-1" />
                  {a && (
                    <Button type="button" size="sm" variant="outline" className="h-7 text-xs"
                      data-testid="media-resume-button"
                      disabled={why !== null || busyNode !== "" || running}
                      title={why ?? "Составить план: спросить задачу у провайдера и скачать готовое, без новой оплаты"}
                      onClick={() => void planResume(n.node_type, n.title)}>
                      {busyNode === n.node_type ? "Составляю…" : "Докачать"}
                    </Button>
                  )}
                </div>
                {st.error && <p role="alert" className="text-amber-200">{st.error}</p>}
                {a && (
                  <>
                    <p className="text-white/65">{modeExplain(a.verdict.mode, a.verdict.code)}</p>
                    <p className="text-white/45">
                      {codeText(a.verdict.code)}
                      {" · "}задача провайдера: {hasTaskId(a) ? <code className="text-white/60">{ids.slice(0, 2).join(", ")}{ids.length > 2 ? "…" : ""}</code> : "нет"}
                      {" · "}{ledgerCounts(a.keys)}
                      {" · "}{ledgerText(a.ledger)}
                    </p>
                    {why && RESUME_NODES.includes(n.node_type) && <p className="text-white/45" data-testid="media-resume-why">Докачать нельзя: {why}</p>}
                  </>
                )}
                {cov && (
                  <p className="text-white/40" data-testid="media-recovery-forbidden">Автовосстановление по реестру: {forbiddenText(cov)}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {open && (Object.keys(nodes).length > 0 || unknown || unknownError) && (
        <section data-testid="media-unknown-block" aria-label="Неизвестный исход" className="space-y-1.5 rounded border border-red-400/20 p-1.5">
          <h5 className="font-semibold text-white/85">Неизвестный исход отправки</h5>
          <p className="text-white/45">
            Ответ на платную отправку потерян: запрос мог быть принят и оплачен, поэтому повтор заблокирован на время окна.
            Блокировку снимает только человек после сверки кабинета провайдера.
          </p>
          {unknownError && <p role="alert" className="text-amber-200">{unknownError}</p>}
          {unknown && !unknown.window_enabled && (
            <p className="text-white/50">Журнал возобновления выключен (MEDIA_RESUME_WINDOW_S=0): блокировок нет.</p>
          )}
          {unknown && unknown.window_enabled && blocked.length === 0 && (
            <p data-testid="media-unknown-empty" className="text-white/50">Блокировок неизвестного исхода нет.</p>
          )}
          {blocked.length > 0 && (
            <ul className="space-y-1">
              {blocked.map((b) => (
                <li key={`${b.key}|${b.provider}`} data-testid="media-unknown-row"
                  className="flex flex-wrap items-center gap-2 rounded border border-white/[0.08] p-1.5">
                  <code className="text-white/85">{b.key}</code>
                  <span className="rounded border border-red-400/40 px-1.5 text-[10px] text-red-300">{providerLabel(b.provider)}</span>
                  <span className="text-white/55">возраст {formatAge(b.age_s)} · {formatBlockedUntil(b.blocked_until)}</span>
                  <span className="flex-1" />
                  <Button type="button" size="sm" variant="outline" className="h-7 border-red-400/40 text-xs text-red-200"
                    data-testid="media-release-button" disabled={running}
                    onClick={() => { setRelease(b); setReason(""); setDialogError(""); }}>
                    Снять блокировку…
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <Dialog open={release !== null} onOpenChange={(o) => { if (!o && !reasonBusy) setRelease(null); }}>
        <DialogContent className="z-[130] max-w-md text-xs" overlayClassName="z-[125]" data-testid="media-release-dialog">
          <DialogHeader>
            <DialogTitle className="text-sm">Снять блокировку неизвестного исхода</DialogTitle>
            <DialogDescription className="text-xs text-white/55">
              Ключ {release?.key}, провайдер {providerLabel(release?.provider)}, возраст {formatAge(release?.age_s)}.
            </DialogDescription>
          </DialogHeader>
          <div role="alert" className="space-y-1 rounded border border-red-400/40 bg-red-500/10 p-2 text-red-100" data-testid="media-release-warning">
            <p className="font-semibold">{SECOND_CHARGE_WARNING}</p>
            <p>
              Откройте кабинет {providerLabel(release?.provider)} и убедитесь, что задачи по этому ключу нет или она не оплачена.
              Блокировка снимается только у этого провайдера и только по этому ключу.
            </p>
          </div>
          <label className="block space-y-1">
            <span className="text-white/70">Причина (что проверено в кабинете), не короче {MIN_REASON_CHARS} символов</span>
            <textarea rows={3} value={reason} maxLength={MAX_REASON_CHARS} onChange={(e) => setReason(e.target.value)}
              data-testid="media-release-reason"
              className="w-full rounded border border-white/10 bg-black/40 p-1.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary" />
            <span className={cn("block", reasonOk(reason) ? "text-white/40" : "text-amber-200")}>
              {cleanReason(reason).length} / {MIN_REASON_CHARS} символов минимум
            </span>
          </label>
          <p className="text-white/45">Дальше будет показан план. Блокировка снимется только после вашего подтверждения в карточке плана.</p>
          {dialogError && <p role="alert" className="text-red-300">{dialogError}</p>}
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="destructive" className="h-7 text-xs" data-testid="media-release-plan"
              disabled={!reasonOk(reason) || reasonBusy} onClick={() => void planRelease()}>
              {reasonBusy ? "Составляю…" : "Показать план"}
            </Button>
            <Button type="button" size="sm" variant="ghost" className="h-7 text-xs" disabled={reasonBusy} onClick={() => setRelease(null)}>
              Отмена
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </details>
  );
}
