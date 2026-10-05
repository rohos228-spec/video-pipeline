"use client";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { ChangeFlow, type FlowMode } from "./change-flow";
import { CodeDiff, ParamTable, TextDiff } from "./diff-view";
import { VersionsList } from "./versions-list";
import {
  canReject, describeChangeError, describeHuman, REJECT_REASON_MAX, REJECT_REASON_MIN, rejectProposal, describeProposalFailure, describeProposer, describeTarget, formatWhen, getProposal,
  kindLabel, parseParamDiff, parseUnifiedDiff, rollbackState, shortDigest, statusLabel, TIER_HINT_RU, TIER_LABEL_RU,
  type Proposal, type ProposalStatus, type Tier,
} from "@/lib/change-control";
import { cn } from "@/lib/utils";

const TIER_CLASS: Record<Tier, string> = {
  T0: "border-white/25 text-white/75",
  T1: "border-sky-400/40 text-sky-200",
  T2: "border-amber-400/50 text-amber-200",
  T3: "border-red-400/50 text-red-300",
};

export function TierBadge({ tier }: { tier: string }) {
  const known = tier in TIER_LABEL_RU;
  return (
    <span data-testid="tier-badge" title={known ? TIER_HINT_RU[tier as Tier] : undefined}
      className={cn("rounded border px-1.5 text-[10px]", known ? TIER_CLASS[tier as Tier] : "border-white/20 text-white/50")}>
      {tier} · {known ? TIER_LABEL_RU[tier as Tier] : "неизвестный уровень"}
    </span>
  );
}

const STATUS_CLASS: Partial<Record<ProposalStatus, string>> = {
  proposed: "border-white/25 text-white/80",
  approved: "border-sky-400/40 text-sky-200",
  applied: "border-emerald-400/40 text-emerald-200",
  rolled_back: "border-amber-400/40 text-amber-200",
  failed: "border-red-400/40 text-red-300",
  rejected: "border-white/20 text-white/50",
  developer_review: "border-red-400/30 text-red-200",
};

/** Строки «что изменится» для окна подтверждения. */
function changeLinesOf(p: Proposal): string[] {
  if (p.kind === "prompt_text") {
    const { added, removed } = parseUnifiedDiff(p.diff || "");
    const chars = typeof p.candidate.text === "string" ? (p.candidate.text as string).length : null;
    return [`текст промпта ноды ${p.node_key}: +${added} / −${removed} строк${chars != null ? `, новый текст ${chars} симв.` : ""}, новый вариант «${String(p.candidate.variant ?? "")}»`];
  }
  const rows = parseParamDiff(p.diff || "");
  return rows ? rows.map((r) => `${r.name}: ${r.before} → ${r.after}`) : (p.diff ? [p.diff] : []);
}

interface Props {
  proposal: Proposal;
  defaultOpen?: boolean;
  /** Обновить список после применения/отката. */
  onChanged: () => void;
  /** Пока открыт поток применения/отката, карточка закреплена в списке (не пропадает при смене статуса). */
  onPin?: (on: boolean) => void;
}

export function ProposalCard({ proposal, defaultOpen = false, onChanged, onPin }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const [detail, setDetail] = useState<Proposal | null>(null);
  const [error, setError] = useState("");
  const [flow, setFlow] = useState<FlowMode | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [rejectDone, setRejectDone] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const [versionsKey, setVersionsKey] = useState(0);

  const reload = useCallback(async () => {
    try {
      const d = await getProposal(proposal.id);
      setDetail(d);
      setError("");
      setVersionsKey((n) => n + 1);
    } catch (e) {
      setError(`Не удалось загрузить предложение: ${describeChangeError(e)}`);
    }
  }, [proposal.id]);

  useEffect(() => {
    if (open && !detail) void reload();
  }, [open, detail, reload]);

  // Список обновился (например, после применения): перечитываем подробности, если они уже открыты.
  useEffect(() => {
    if (detail && (detail.status !== proposal.status || detail.updated_at !== proposal.updated_at)) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal.status, proposal.updated_at]);

  const p = detail ?? proposal;
  const who = describeProposer(p.proposer);
  const t3 = p.tier === "T3";
  const t0 = p.tier === "T0";
  const rb = detail ? rollbackState(detail) : null;
  const stale = Boolean(p.stale) && (p.status === "proposed" || p.status === "approved");
  const rationaleShort = p.rationale.length > 140 ? `${p.rationale.slice(0, 140)}…` : p.rationale;

  return (
    <article data-testid="proposal-card" data-proposal-id={p.id} data-tier={p.tier} data-status={p.status}
      className={cn("space-y-1.5 rounded border p-2", p.status === "failed" ? "border-red-400/25" : t3 ? "border-red-400/20" : "border-white/10")}>
      <div className="flex flex-wrap items-center gap-1.5">
        <TierBadge tier={p.tier} />
        <strong className="min-w-0 break-words">{kindLabel(p.kind)}{p.node_key && <span className="font-normal text-white/60"> · нода {p.node_key}</span>}</strong>
        <span data-testid="status-chip" className={cn("rounded border px-1.5 text-[10px]", STATUS_CLASS[p.status] ?? "border-white/20 text-white/50")}>{statusLabel(p.status)}</span>
        {stale && (
          <span data-testid="stale-chip" className="rounded border border-amber-400/50 px-1.5 text-[10px] text-amber-200">
            устарело: версия цели изменилась
          </span>
        )}
        <span data-testid="proposer-chip" title={p.proposer}
          className={cn("rounded border px-1.5 text-[10px]", who.kind === "planner" ? "border-amber-400/40 text-amber-200" : "border-white/20 text-white/60")}>
          предложил: {who.label}
        </span>
        <span className="ml-auto text-white/40">{formatWhen(p.created_at)}</span>
      </div>
      <p className={cn("break-words text-white/75", !open && "line-clamp-2")}>{open ? p.rationale : rationaleShort}</p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs text-white/60" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Свернуть" : "Подробнее"}
        </Button>
        {!open && p.tier === "T3" && <span className="text-red-200/80">не применяется автоматически никогда</span>}
        {!open && stale && <span className="text-amber-200">применить нельзя: можно только отклонить</span>}
        {!open && p.status === "failed" && <span className="text-red-300">не применено, см. подробности</span>}
      </div>

      {open && (
        <div className="space-y-2 border-t border-white/10 pt-2">
          {error && <p role="alert" className="text-red-300">{error}</p>}
          {!detail && !error && <p className="text-white/50">Загружаю подробности…</p>}

          {detail && (
            <>
              {p.status !== "rejected" && <p className="text-white/50">{TIER_HINT_RU[p.tier as Tier] ?? "Неизвестный уровень предложения."}</p>}

              {t3 && (
                <div role="note" data-testid="t3-explainer" className="space-y-1 rounded border border-red-400/30 bg-red-500/10 p-1.5 text-red-100">
                  <p className="font-semibold">Запись для разработчика: автоматически не применяется никогда.</p>
                  <p>У этого уровня нет функции применения вообще. В проект ничего не пишется: ни файлов, ни настроек, ни версий. Кнопок применения здесь нет и не будет.</p>
                </div>
              )}

              {stale && (
                <div role="note" data-testid="stale-explainer" className="space-y-1 rounded border border-amber-400/40 bg-amber-500/10 p-1.5 text-amber-50">
                  <p className="font-semibold">Предложение устарело: после его создания версия цели изменилась.</p>
                  <p>Применить его нельзя (сервер откажет с кодом stale_base). Отклоните его и, если изменение всё ещё нужно, составьте новое предложение на актуальном значении.</p>
                </div>
              )}

              {p.status === "rejected" && (
                <div data-testid="reject-info" className="rounded border border-white/15 bg-white/[0.04] p-1.5">
                  <p className="font-semibold text-white/80">Отклонено оператором: {describeHuman(p.rejected_by)} · {formatWhen(p.rejected_at)}</p>
                  <p className="break-words text-white/70">Причина: {p.reject_reason || "не указана"}</p>
                  <p className="text-white/45">В проект ничего не записано.</p>
                </div>
              )}

              {t3 && (
                <div className="space-y-1">
                  <p><span className="text-white/50">Что предлагается:</span> {String(p.candidate.summary ?? "")}</p>
                  {typeof p.candidate.path === "string" && p.candidate.path && <p><span className="text-white/50">Файл:</span> <span className="font-mono">{p.candidate.path as string}</span></p>}
                  <CodeDiff diff={String(p.candidate.diff ?? p.diff ?? "")} />
                </div>
              )}

              {t0 && (
                <div className="space-y-1">
                  <p className="whitespace-pre-wrap break-words rounded border border-white/10 bg-black/30 p-1.5 text-white/85">{String(p.candidate.text ?? p.diff ?? "")}</p>
                  <p className="text-white/50">Это совет: применять нечего, исполнителя у него нет.</p>
                </div>
              )}

              {p.tier === "T2" && (
                <div className="space-y-1">
                  <TextDiff diff={p.diff} />
                  <p className="text-white/55">
                    Будет создан новый вариант промпта «{String(p.candidate.variant ?? "?")}»
                    {typeof p.candidate.text === "string" ? ` (${(p.candidate.text as string).length} симв.)` : ""} и выбран на этой ноде.
                    Общие файлы промптов не перезаписываются.
                  </p>
                </div>
              )}

              {p.tier === "T1" && <ParamTable diff={p.diff} />}

              {(p.tier === "T1" || p.tier === "T2") && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-white/70" data-testid="version-info">
                  <dt className="text-white/45">Цель</dt><dd>{describeTarget(p.target)} <span className="font-mono text-[10px] text-white/35">{p.target}</span></dd>
                  <dt className="text-white/45">Основано на версии</dt><dd>{p.base_version === 0 ? "0 (изменений ещё не было)" : p.base_version}</dd>
                  <dt className="text-white/45">Текущая версия цели</dt><dd>{p.current_version ?? "неизвестна"}</dd>
                  {p.applied_version != null && <><dt className="text-white/45">Применено как версия</dt><dd>{p.applied_version}{p.applied_at ? ` · ${formatWhen(p.applied_at)}` : ""}</dd></>}
                  {p.approved_by && <><dt className="text-white/45">Подтвердил</dt><dd>{describeHuman(p.approved_by)} · {formatWhen(p.approved_at)}</dd></>}
                  <dt className="text-white/45">Контрольный хэш</dt><dd className="font-mono text-[11px]" title={p.digest}>{shortDigest(p.digest, 16)}</dd>
                </dl>
              )}

              {(p.tier === "T1" || p.tier === "T2") && (
                <div className="space-y-0.5" data-testid="checks">
                  <p className="text-white/50">Проверки</p>
                  {p.status === "failed" && p.error
                    ? <p role="alert" data-testid="proposal-error" className="rounded border border-red-400/30 bg-red-500/10 p-1.5 text-red-200">{describeProposalFailure(p.error)}</p>
                    : <p className="text-white/70">
                        {p.status === "applied" || p.status === "rolled_back"
                          ? "Пройдены при создании и при применении; после записи значение прочитано обратно и сверено."
                          : "Пройдены при создании предложения. Перед применением сервер повторит их и проверит, что значение в проекте не менялось."}
                      </p>}
                  {p.shadow && p.shadow.length > 0 && (
                    <ul className="list-disc pl-4">
                      {p.shadow.map((s) => <li key={s.name} className={s.ok ? "text-emerald-200" : "text-red-300"}>Пробный прогон «{s.name}»: {s.ok ? "пройден" : "не пройден"}{s.note ? ` — ${s.note}` : ""}</li>)}
                    </ul>
                  )}
                  {(!p.shadow || p.shadow.length === 0) && <p className="text-white/40">Пробный прогон перед применением не настроен: платных вызовов контур не делает.</p>}
                </div>
              )}

              {/* Управление: применить и откатить только человеку и только через подтверждение */}
              {!flow && (p.tier === "T1" || p.tier === "T2") && (
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {p.can_apply && !stale && (
                      <Button size="sm" variant="outline" className="h-7 border-amber-400/50 text-xs text-amber-100" onClick={() => { setFlow("apply"); onPin?.(true); }}>
                        Применить…
                      </Button>
                    )}
                    {p.status === "applied" && rb?.ok && (
                      <Button size="sm" variant="outline" className="h-7 border-amber-400/50 text-xs text-amber-100" onClick={() => { setFlow("rollback"); onPin?.(true); }}>
                        Откатить…
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" className="h-7 text-xs" aria-expanded={showVersions} onClick={() => setShowVersions((v) => !v)}>
                      {showVersions ? "Скрыть историю версий" : "История версий"}
                    </Button>
                  </div>
                  {p.can_apply && !stale && <p className="text-white/50">Применение выполняется только после вашего подтверждения; автоматически ничего не применяется.</p>}
                  {p.status === "applied" && rb && !rb.ok && rb.reason && <p className="text-amber-200">{rb.reason}</p>}
                  {p.status === "applied" && rb === null && <p className="text-amber-200">Текущая версия цели неизвестна: откат отсюда не предлагается.</p>}
                  {!p.can_apply && p.status !== "applied" && p.status !== "rejected" && (
                    <p className="text-white/50">Это предложение уже нельзя применить: {statusLabel(p.status)}.</p>
                  )}
                </div>
              )}

              {canReject(p) && !flow && !rejectDone && (
                <RejectBlock proposal={p} prominent={stale} open={rejecting}
                  onOpen={() => { setRejecting(true); onPin?.(true); }}
                  onCancel={() => { setRejecting(false); onPin?.(false); }}
                  onRejected={() => { setRejectDone(true); void reload(); onChanged(); }} />
              )}
              {rejectDone && (
                <div className="space-y-1">
                  <p role="status" data-testid="reject-result" className="rounded border border-emerald-400/30 bg-emerald-500/10 p-1.5 text-emerald-100">Предложение отклонено. Оно перенесено в «Закрытые»; в проект ничего не записано.</p>
                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setRejectDone(false); setRejecting(false); onPin?.(false); }}>Закрыть</Button>
                </div>
              )}

              {flow && (
                <ChangeFlow mode={flow} proposal={p} currentVersion={p.current_version} changeLines={changeLinesOf(p)}
                  onClose={() => { setFlow(null); onPin?.(false); }}
                  onFinished={() => { void reload(); onChanged(); }} />
              )}

              {showVersions && p.target && (p.tier === "T1" || p.tier === "T2") && <VersionsList target={p.target} refreshKey={versionsKey} />}
            </>
          )}
        </div>
      )}
    </article>
  );
}


/** Отклонение: только по клику оператора, причина обязательна (3..500 символов). Применять/писать в проект не умеет. */
function RejectBlock({ proposal, prominent, open, onOpen, onCancel, onRejected }: {
  proposal: Proposal; prominent: boolean; open: boolean; onOpen: () => void; onCancel: () => void; onRejected: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const len = reason.trim().length;
  const valid = len >= REJECT_REASON_MIN && reason.length <= REJECT_REASON_MAX;

  async function go() {
    setBusy(true);
    setError("");
    try {
      await rejectProposal(proposal.id, reason);
      onRejected();
    } catch (e) {
      setError(describeChangeError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button size="sm" variant="outline" data-testid="reject-open"
        className={cn("h-7 text-xs", prominent ? "border-amber-400/60 text-amber-100" : "border-white/20 text-white/70")} onClick={onOpen}>
        Отклонить…
      </Button>
    );
  }
  return (
    <div role="group" aria-label="Отклонение предложения" data-testid="reject-panel" className="space-y-1.5 rounded border border-white/15 p-2">
      <p className="text-white/70">Отклонённое предложение нельзя применить. В проект ничего не записывается. Укажите причину: она сохранится в журнале.</p>
      <label className="block">
        <span className="text-white/60">Причина (от {REJECT_REASON_MIN} символов)</span>
        <input aria-label="Причина отклонения" value={reason} maxLength={REJECT_REASON_MAX} onChange={(e) => { setReason(e.target.value); setError(""); }}
          className="mt-0.5 block w-full max-w-md rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30"
          placeholder="например: устарело, составлено заново" />
      </label>
      {error && <p role="alert" className="text-red-300">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" variant="destructive" className="h-7 text-xs" disabled={busy || !valid} onClick={() => void go()}>
          {busy ? "Отклоняю…" : "Отклонить предложение"}
        </Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onCancel}>Отмена</Button>
      </div>
    </div>
  );
}
