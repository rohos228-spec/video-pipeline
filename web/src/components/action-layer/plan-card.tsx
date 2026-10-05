"use client";
import {useState} from "react";
import {Button} from "@/components/ui/button";
import {
  approvePlan, canApprove, canRunWithoutApproval, describeError, describeParams, formatStepCost, formatTotalCost,
  runPlan, RISK_LABEL_RU, type PlanPreviewResponse, type RiskTier,
} from "@/lib/action-layer";
import {cn} from "@/lib/utils";

const RISK_CLASS: Record<RiskTier, string> = {
  read: "border-white/20 text-white/70",
  local_safe: "border-sky-400/40 text-sky-200",
  paid: "border-amber-400/50 text-amber-200",
  destructive: "border-red-400/50 text-red-300",
};

export function RiskBadge({risk}: {risk: RiskTier | undefined}) {
  if (!risk) return <span className="rounded border border-white/20 px-1.5 text-[10px] text-white/50">неизвестно</span>;
  return <span className={cn("rounded border px-1.5 text-[10px]", RISK_CLASS[risk])}>{RISK_LABEL_RU[risk]}</span>;
}

interface Props {
  plan: PlanPreviewResponse;
  busyElsewhere?: boolean;
  onStarted: (planId: string) => void;
  onDismiss: () => void;
}

export function PlanCard({plan, busyElsewhere, onStarted, onDismiss}: Props) {
  const {preview} = plan;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasId = Boolean(plan.plan_id);

  async function go(withApproval: boolean) {
    setBusy(true); setError("");
    try {
      if (withApproval) await approvePlan(plan.plan_id); // только из клика оператора
      await runPlan(plan.plan_id);
      onStarted(plan.plan_id);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  const spends = preview.steps.some((s) => s.spends_money);
  const total = formatTotalCost(preview);
  const unknownCost = preview.cost.unknown_steps > 0;

  return (
    <section aria-label="Предпросмотр плана" data-testid="action-plan-card" className="space-y-2 rounded border border-white/10 p-2">
      <div className="flex items-center justify-between gap-2">
        <h5 className="font-semibold text-white/85">План до выполнения · ничего ещё не запущено</h5>
        <RiskBadge risk={preview.risk_max} />
      </div>
      {plan.explanation && <p className="text-white/70">{plan.explanation}</p>}
      {plan.clarification && (
        <p role="status" className="rounded border border-amber-400/30 bg-amber-500/10 p-1.5 text-amber-100">
          Нужно уточнение: {plan.clarification}
        </p>
      )}
      {preview.steps.length === 0 ? (
        <p className="text-white/50">Модель не предложила ни одного шага.</p>
      ) : (
        <ol className="space-y-1.5">
          {preview.steps.map((s) => {
            const params = s.params ?? plan.steps?.[s.index]?.params;
            return (
              <li key={s.index} className="rounded border border-white/[0.06] p-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-white/40">{s.index + 1}.</span>
                  <span className="flex-1 text-white/85">{s.title_ru ?? s.action_id}</span>
                  <RiskBadge risk={s.risk} />
                  <span className="text-white/60">{formatStepCost(s)}</span>
                </div>
                <p className="text-white/45">{describeParams(params)}</p>
                {s.cost?.basis && s.spends_money && <p className="text-white/35">Основание оценки: {s.cost.basis}</p>}
                {s.errors.map((er, i) => <p key={`e${i}`} className="text-red-300">Ошибка: {er}</p>)}
                {s.precondition_failures.map((f) => <p key={f.id} className="text-red-300">Не выполнено условие: {f.reason}</p>)}
              </li>
            );
          })}
        </ol>
      )}

      <p className={cn(unknownCost ? "text-amber-200" : "text-white/75")}>
        Стоимость всего плана: <strong>{total}</strong>
        {preview.budget && <span className="text-white/45"> · потолок {preview.budget.cap_credits} кр., осталось {preview.budget.remaining_credits} кр.</span>}
      </p>
      {preview.budget && !preview.budget.fits && <p role="alert" className="text-red-300">План не помещается в потолок расходов (код: over_budget).</p>}
      {unknownCost && <p className="text-amber-200">Цена части платных шагов неизвестна: сервер не запустит такой план.</p>}

      {preview.destructive_effects.length > 0 && (
        <div className="rounded border border-red-400/30 bg-red-500/10 p-1.5">
          <p className="font-semibold text-red-200">Будет стёрто безвозвратно:</p>
          <ul className="list-disc pl-4 text-red-100/90">
            {preview.destructive_effects.map((d) => <li key={`${d.index}-${d.action_id}`}>Шаг {d.index + 1}: {d.text}</li>)}
          </ul>
        </div>
      )}
      {preview.errors.length > 0 && (
        <ul role="alert" className="list-disc pl-4 text-red-300">{preview.errors.map((er, i) => <li key={i}>{er}</li>)}</ul>
      )}

      {confirming && (
        <div role="alertdialog" aria-label="Подтверждение выполнения" className="space-y-1 rounded border border-amber-400/40 bg-amber-500/10 p-2 text-amber-50">
          <p>
            Вы подтверждаете выполнение шагов: {preview.steps.length}.
            {spends ? ` Будет потрачено: ${total}.` : ""}
          </p>
          {preview.destructive_effects.length > 0 && (
            <p>Будет стёрто: {preview.destructive_effects.map((d) => d.text).join("; ")}.</p>
          )}
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" className="h-7 text-xs" disabled={busy || busyElsewhere} onClick={() => void go(true)}>
              {busy ? "Выполняю…" : "Да, подтверждаю и выполняю"}
            </Button>
            <Button size="sm" variant="outline" className="h-7 text-xs" disabled={busy} onClick={() => setConfirming(false)}>Назад</Button>
          </div>
        </div>
      )}

      {!confirming && (
        <div className="flex flex-wrap items-center gap-2">
          {hasId && canRunWithoutApproval(preview) && (
            <Button size="sm" className="h-7 text-xs" disabled={busy || busyElsewhere} onClick={() => void go(false)}>
              {busy ? "Запускаю…" : "Выполнить"}
            </Button>
          )}
          {hasId && preview.needs_approval && (
            <Button size="sm" variant="outline" className="h-7 border-amber-400/50 text-xs text-amber-100"
              disabled={busy || busyElsewhere || !canApprove(preview)} onClick={() => setConfirming(true)}>
              Подтвердить и выполнить…
            </Button>
          )}
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onDismiss}>Отклонить план</Button>
          {busyElsewhere && <span className="text-white/45">Дождитесь окончания текущего плана.</span>}
        </div>
      )}
      {error && <p role="alert" className="text-red-300">{error}</p>}
    </section>
  );
}
