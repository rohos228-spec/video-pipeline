"use client";
import {useState} from "react";
import {Button} from "@/components/ui/button";
import {
  approvePlan, canApprove, canRunWithoutApproval, describeError, describeParams, formatStepCost, formatTotalCost,
  runPlan, RISK_LABEL_RU, serverTextRu, useActionTitles, type PlanPreview, type PlanPreviewResponse, type RiskTier,
} from "@/lib/action-layer";
import {attachmentInfo, clarificationOptions, clarificationQuestion} from "@/lib/clarification-view";
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
  /** Нажат вариант ответа на уточнение: родитель повторно отправляет запрос + вариант. */
  onClarify?: (option: string) => void;
  clarifying?: boolean;
  clarifyError?: string;
}

export function PlanCard(props: Props) {
  const {plan, onDismiss} = props;
  if (!plan.preview) return <ClarificationCard {...props} />;
  return <PlanCardInner {...props} preview={plan.preview} />;
}

function ClarificationCard({plan, onDismiss, onClarify, clarifying, clarifyError}: Props) {
  const titles = useActionTitles();
  const question = clarificationQuestion(plan);
  const options = clarificationOptions(plan);
  const attach = plan.attachment_request;
  return (
    <section aria-label="Уточнение" data-testid="action-plan-card" className="space-y-2 rounded border border-amber-400/30 bg-amber-500/10 p-2 text-xs">
      <p className="font-semibold text-amber-100">Планировщик просит уточнить запрос. План не создан, ничего не запущено.</p>
      {question && <p className="text-amber-50/90" data-testid="clarification-question">{question}</p>}
      {options.length > 0 && onClarify && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Варианты ответа" data-testid="clarification-options">
          {options.map((o) => (
            <Button key={o} type="button" size="sm" variant="outline" className="h-7 border-amber-400/50 text-xs text-amber-50"
              disabled={clarifying} onClick={() => onClarify(o)}>
              {o}
            </Button>
          ))}
        </div>
      )}
      {attach && (
        <p className="text-amber-50/80" data-testid="attachment-info">
          {attachmentInfo(attach, titles[attach.action_id])}
        </p>
      )}
      {clarifying && <p className="text-white/60" data-testid="clarification-busy">Составляю план с уточнением…</p>}
      {clarifyError && <p role="alert" className="text-red-300">{clarifyError}</p>}
      <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={clarifying} onClick={onDismiss}>Закрыть</Button>
    </section>
  );
}

function PlanCardInner({plan, preview, busyElsewhere, onStarted, onDismiss}: Props & {preview: PlanPreview}) {
  const titles = useActionTitles();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const planId = plan.plan_id;
  const hasId = Boolean(planId);

  async function go(withApproval: boolean) {
    setBusy(true); setError("");
    try {
      if (withApproval) await approvePlan(planId as string); // только из клика оператора
      await runPlan(planId as string);
      onStarted(planId as string);
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
    <section aria-label="Предпросмотр плана" data-testid="action-plan-card" className="space-y-2 rounded border border-white/10 p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <h5 className="font-semibold text-white/85">План до выполнения · ничего ещё не запущено</h5>
        <RiskBadge risk={preview.risk_max} />
      </div>
      {plan.explanation_ru && <p className="text-white/70">{plan.explanation_ru}</p>}
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
                  <span className="flex-1 text-white/85">{s.title_ru ?? titles[s.action_id] ?? s.action_id}
                    <span className="ml-1.5 text-[10px] text-white/35">{s.action_id}</span></span>
                  <RiskBadge risk={s.risk} />
                  <span className="text-white/60">{formatStepCost(s)}</span>
                </div>
                <p className="text-white/45">{describeParams(params)}</p>
                {s.cost?.basis && s.spends_money && <p className="text-white/35">Основание оценки: {s.cost.basis}</p>}
                {s.errors.map((er, i) => <p key={`e${i}`} className="text-red-300">Ошибка: {serverTextRu(er)}</p>)}
                {s.precondition_failures.map((f) => <p key={f.id} className="text-red-300">Не выполнено условие: {serverTextRu(f.reason)}</p>)}
              </li>
            );
          })}
        </ol>
      )}

      <p className={cn(unknownCost ? "text-amber-200" : "text-white/75")}>
        Стоимость всего плана: <strong>{total}</strong>
        {preview.budget && <span className="text-white/45"> · потолок {preview.budget.cap_credits} кр., осталось {preview.budget.remaining_credits} кр.</span>}
      </p>
      {preview.budget && !preview.budget.fits && !unknownCost && <p role="alert" className="text-red-300">План не помещается в потолок расходов (код: over_budget).</p>}
      {unknownCost && <p className="text-amber-200">Цена части платных шагов неизвестна: сервер не запустит такой план.</p>}

      {preview.destructive_effects.length > 0 && (
        <div className="rounded border border-red-400/30 bg-red-500/10 p-1.5">
          <p className="font-semibold text-red-200">Будет стёрто безвозвратно:</p>
          <ul className="list-disc pl-4 text-red-100/90">
            {preview.destructive_effects.map((d) => <li key={`${d.index}-${d.action_id}`}>Шаг {d.index + 1}: {d.text}</li>)}
          </ul>
        </div>
      )}
      {preview.errors.filter((er) => !/^step \d+:/.test(er)).length > 0 && (
        <ul role="alert" className="list-disc pl-4 text-red-300">{preview.errors.filter((er) => !/^step \d+:/.test(er)).map((er, i) => <li key={i}>{serverTextRu(er)}</li>)}</ul>
      )}

      {confirming && (
        <div role="alertdialog" aria-label="Подтверждение выполнения" className="space-y-1 rounded border border-amber-400/40 bg-amber-500/10 p-2 text-amber-50">
          <p>
            Вы подтверждаете выполнение шагов: {preview.steps.length}.
            {` Стоимость: ${total.replace(/\.$/, "")}${spends ? " (будет потрачено)" : ""}.`}
          </p>
          {preview.destructive_effects.length > 0 && (
            <p>Будет стёрто: {preview.destructive_effects.map((d) => d.text.replace(/\.$/, "")).join("; ")}.</p>
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
