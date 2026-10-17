"use client";

import { useState } from "react";
import { Bot, KeyRound, OctagonX, Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  AGENT_LABEL,
  groupPlanSteps,
  isNodeResultAction,
  isTerminalBlock,
  TASK_STATUS_LABEL,
  type ApprovalView,
  type KillNoticeView,
  type PlanStepView,
  type StepView,
} from "@/lib/orchestratorSession";
import type { ApprovalReason, NodeDef, TaskStatus } from "@/lib/orchestratorTypes";
import { orchTaskModeEnabled, readOrchProjectId } from "@/lib/orchestratorApi";
import { cn } from "@/lib/utils";
import { useOrchestratorChat } from "./use-orchestrator-chat";

/** Значения ApprovalReason из contracts.py. Статус задачи при этом — waiting_approval. */
const BUDGET_EXHAUSTED = "budget_exhausted" satisfies ApprovalReason;
const CRITIC_ESCALATION = "critic_escalation" satisfies ApprovalReason;

const KILL_HONEST =
  "Неплатные шаги отменены, новые действия запрещены. Платный шаг остановится на границе.";

/** Старое имя причины, пока WS3 дописывает замену на critic_escalation. */
const REVISE_LIMIT = "revise_limit";
const MISSING_PROJECT = "missing_project_id" satisfies ApprovalReason;
const RISK_APPROVAL = "risk" satisfies ApprovalReason;

const BLOCK_COPY: Record<string, { title: string; body: string; testId?: string }> = {
  [BUDGET_EXHAUSTED]: {
    title: "Бюджет исчерпан",
    body: "Лимит прогона выбран. Задача ждёт подтверждения, новые платные шаги не стартуют.",
    testId: "orch-budget",
  },
  [CRITIC_ESCALATION]: {
    title: "Критик не одобрил план после 2 правок",
    body: "Две правки не помогли. Задача ждёт решения, план сам не продолжится.",
    testId: "orch-critic",
  },
  [REVISE_LIMIT]: {
    title: "Критик не одобрил план после 2 правок",
    body: "Две правки не помогли. Задача ждёт решения, план сам не продолжится.",
    testId: "orch-critic",
  },
  [MISSING_PROJECT]: {
    title: "Не выбран проект: план содержит платный или изменяющий шаг",
    body: "Задача ждёт подтверждения. Платный или изменяющий шаг без проекта не стартует.",
    testId: "orch-missing-project",
  },
  [RISK_APPROVAL]: {
    title: "Шаг с риском: нужно подтверждение",
    body: "Реестр действий просит одобрение. Шаг не стартует, пока его не подтвердят.",
    testId: "orch-risk",
  },
  deletion_blocked: {
    title: "Удаление запрещено",
    body: "Шаг удаляет кадр или сцену. Одобрить нельзя, задача остановлена.",
    testId: "orch-deletion",
  },
  missing_run_id: {
    title: "Нет идентификатора прогона",
    body: "Платный шаг нельзя начать без run_id. Задача остановлена.",
    testId: "orch-missing-run",
  },
  empty_plan: {
    title: "План пустой",
    body: "В чекпоинте нет шагов. Продолжать нечего, задача остановлена.",
    testId: "orch-empty-plan",
  },
};

function killBannerText(notice: KillNoticeView): string {
  if (notice.phase === "stopping") return "Останавливаю…";
  if (notice.phase === "unconfirmed") {
    return notice.text.trim() || "Задача отменена, стоп-ключ не подтверждён.";
  }
  if (notice.phase === "error") return notice.text.trim() || "Остановка не подтверждена";
  const lead = "Остановлено";
  if (!notice.paidBoundary) {
    const extra = notice.text.trim();
    return extra ? `${lead}. ${extra}` : lead;
  }
  const extra = notice.text.trim();
  return extra ? `${lead}. ${KILL_HONEST} ${extra}` : `${lead}. ${KILL_HONEST}`;
}

function statusBadgeVariant(status: string): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (status === "done" || status === "idle") return "success";
  if (status === "failed" || status === "cancelled") return "destructive";
  if (status === "paused" || status === "waiting_approval" || status === "waiting") return "warning";
  if (status === "running" || status === "executing" || status === "planning" || status === "critic_review") return "info";
  return "muted";
}

export function OrchestratorWorkspace() {
  const chat = useOrchestratorChat();
  const [draft, setDraft] = useState("");
  const [taskMode, setTaskMode] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);

  const task = chat.state.tasks.at(-1) ?? null;
  const openApprovals = chat.state.approvals.filter((item) => item.resolved == null);

  const send = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    void chat.sendMessage(text, orchTaskModeEnabled() && taskMode ? "task" : undefined);
  };

  return (
    <div className="flex h-[calc(100vh-48px)] min-h-0 flex-col bg-background" data-orch-page="">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-input px-4">
        <div className="flex min-w-0 items-center gap-2">
          <Bot className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-semibold tracking-tight text-foreground">Оркестратор</span>
          <Badge variant="muted" className="normal-case tracking-normal">
            {chat.boot.session}
          </Badge>
          {task ? (
            <Badge variant={statusBadgeVariant(task.status)} data-testid="orch-task-status" className="normal-case tracking-normal">
              {TASK_STATUS_LABEL[task.status as TaskStatus] ?? task.status}
            </Badge>
          ) : (
            <span className="text-[11px] text-muted-foreground/70" data-testid="orch-task-status">
              нет задачи
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-xs"
            data-testid="orch-reports-link"
            onClick={() => {
              const params = new URLSearchParams(window.location.search);
              const project = readOrchProjectId();
              if (project != null) params.set("project", String(project));
              else params.delete("project");
              const q = params.toString();
              window.location.assign(q ? `/orchestrator/reports?${q}` : "/orchestrator/reports");
            }}
          >
            Отчёты
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={() => {
              setTokenDraft(chat.token);
              setTokenOpen(true);
            }}
          >
            <KeyRound className="h-3.5 w-3.5" />
            Токен
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            className="h-9 gap-1.5 px-3 text-xs font-semibold"
            data-testid="orch-kill"
            disabled={chat.needsToken}
            onClick={() => setKillOpen(true)}
            title="Остановить неплатные шаги. Платный шаг остановится на границе."
          >
            <OctagonX className="h-3.5 w-3.5" />
            Kill switch
          </Button>
        </div>
      </div>

      {chat.conn === "reconnecting" || chat.conn === "restored" ? (
        <div
          data-testid="orch-reconnect"
          className={cn(
            "shrink-0 border-b px-4 py-2 text-xs",
            chat.conn === "reconnecting"
              ? "border-warning/30 bg-warning/10 text-warning"
              : "border-success/30 bg-success/10 text-success",
          )}
        >
          {chat.conn === "reconnecting"
            ? "Соединение прервано — переподключаюсь…"
            : "Соединение восстановлено, пропущенные события повторены"}
        </div>
      ) : null}

      {chat.state.killNotice ? (
        <div
          data-testid="orch-kill-status"
          data-kill-phase={chat.state.killNotice.phase}
          className="shrink-0 border-b border-destructive/40 bg-destructive/15 px-4 py-2 text-xs text-foreground"
        >
          <div>Kill switch: {killBannerText(chat.state.killNotice)}</div>
          {chat.state.killNotice.phase === "error" ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-2 h-7 text-[11px]"
              data-testid="orch-kill-retry"
              onClick={() => void chat.kill()}
            >
              Повторить
            </Button>
          ) : null}
        </div>
      ) : null}

      {chat.needsToken ? (
        <TokenGate
          error={chat.authError}
          value={tokenDraft || chat.token}
          onChange={setTokenDraft}
          onSubmit={() => chat.submitToken(tokenDraft || chat.token)}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          <section className="flex min-w-0 flex-1 flex-col border-r border-input">
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="orch-transcript">
              {chat.state.messages.length === 0 ? (
                <p className="text-xs leading-relaxed text-muted-foreground/70">
                  Живой чат супервизора. Сообщение можно отправить и посреди задачи: сначала придёт ack
                  «принял, разбираю», затем классификация interrupt / steer / append.
                </p>
              ) : null}
              {chat.state.messages.map((message) => (
                <div key={message.id} className="mb-3 text-[13px] leading-relaxed">
                  <span className={message.role === "user" ? "text-primary" : "text-muted-foreground"}>
                    {message.role === "user" ? "ты" : "оркестратор"}:{" "}
                  </span>
                  <span
                    className="whitespace-pre-wrap text-foreground/90"
                    data-testid={message.role === "assistant" ? "orch-assistant" : undefined}
                  >
                    {message.text}
                  </span>
                </div>
              ))}
              {chat.state.tasks.flatMap((item) =>
                item.steps
                  .filter((step) => step.node && isNodeResultAction(step.action))
                  .map((step) => <NodeResultCard key={`${item.id}:${step.id}`} step={step} />),
              )}
              {openApprovals.map((approval) => (
                <ApprovalCard
                  key={approval.id}
                  approval={approval}
                  pending={chat.decisionPendingId === approval.id}
                  error={chat.decisionError?.id === approval.id ? chat.decisionError.message : ""}
                  onApprove={() => void chat.decide(approval.id, approval.taskId, "approved")}
                  onReject={() => void chat.decide(approval.id, approval.taskId, "rejected")}
                  onClose={() => chat.dismissBlock(approval.id)}
                />
              ))}
            </div>

            {orchTaskModeEnabled() ? (
              <label className="flex shrink-0 items-start gap-2 px-4 pb-2 pt-1 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  data-testid="orch-mode-task"
                  checked={taskMode}
                  onChange={(event) => setTaskMode(event.target.checked)}
                />
                <span>
                  Новая задача
                  <span className="block text-[10px] text-muted-foreground/70" data-testid="orch-mode-task-hint">
                    Встаёт в очередь за текущей и не прерывает её.
                  </span>
                </span>
              </label>
            ) : null}
            <div className="shrink-0 space-y-2 border-t border-input px-4 py-3">
              {chat.state.ack ? (
                <div
                  data-testid="orch-ack"
                  data-ack-ms={chat.ackMs == null ? undefined : String(Math.round(chat.ackMs))}
                  data-ack-echo={chat.state.ack.echo}
                  className="text-[11px] text-info"
                >
                  {chat.state.ack.text || "принял, разбираю"}
                  {chat.state.ack.echo ? ` — «${chat.state.ack.echo}»` : ""}
                </div>
              ) : null}
              {chat.state.classification ? (
                <div className="flex items-center gap-2 text-[11px]" data-testid="orch-classification">
                  <Badge
                    variant={
                      chat.state.classification.killSwitch || chat.state.classification.mode === "interrupt"
                        ? "warning"
                        : chat.state.classification.mode === "steer"
                          ? "info"
                          : "success"
                    }
                    className="normal-case tracking-normal"
                  >
                    {chat.state.classification.killSwitch ? "остановка" : chat.state.classification.mode}
                  </Badge>
                  <span className="text-muted-foreground">
                    {chat.state.classification.killSwitch
                      ? "kill switch из сообщения"
                      : chat.state.classification.detail || "классификация"}
                    {chat.state.classification.deterministic ? " · правило" : ""}
                  </span>
                </div>
              ) : null}
              {chat.sendError ? <p className="text-[11px] text-destructive">{chat.sendError}</p> : null}
              <div className="flex items-end gap-2">
                <Textarea
                  data-testid="orch-composer"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      send();
                    }
                  }}
                  placeholder="Пиши и во время задачи — interrupt, steer или append"
                  className="min-h-[44px] flex-1 resize-none py-2 text-xs"
                  rows={2}
                />
                <Button
                  type="button"
                  size="sm"
                  className="h-10 gap-1.5 text-xs"
                  data-testid="orch-send"
                  disabled={!draft.trim()}
                  onClick={send}
                >
                  <Send className="h-3.5 w-3.5" />
                  {chat.pending ? "…" : "Отправить"}
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground/60">
                Поле не блокируется на время задачи. Ack — сразу, классификация — следом.
              </p>
            </div>
          </section>

          <aside className="flex w-[340px] shrink-0 flex-col bg-black/30" data-testid="orch-task-panel">
            <div className="border-b border-input px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">Агенты</div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {chat.state.agents.map((agent) => (
                  <div
                    key={agent.role}
                    data-testid={`orch-agent-${agent.role}`}
                    className="rounded-lg border border-border bg-white/[0.03] px-2.5 py-2"
                  >
                    <div className="text-[11px] font-medium text-foreground/80">{AGENT_LABEL[agent.role]}</div>
                    <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">{agent.status}</div>
                    {agent.detail ? <div className="mt-1 truncate text-[10px] text-muted-foreground/70">{agent.detail}</div> : null}
                  </div>
                ))}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">Задачи</div>
              {chat.state.tasks.length === 0 ? (
                <p className="mt-2 text-[11px] text-muted-foreground/70">Шаги и чекпоинты появятся вместе с задачей.</p>
              ) : (
                chat.state.tasks.map((item) => (
                  <div key={item.id} className="mt-3 rounded-xl border border-border bg-card/80 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate text-xs font-semibold text-foreground/90">{item.title}</div>
                      <Badge variant={statusBadgeVariant(item.status)} className="normal-case tracking-normal">
                        {TASK_STATUS_LABEL[item.status] ?? item.status}
                      </Badge>
                    </div>
                    {item.summary ? (
                      <p className="mt-2 text-[11px] text-foreground/70" data-testid="orch-task-summary">
                        {item.summary}
                      </p>
                    ) : null}
                    {item.error ? <p className="mt-2 text-[11px] text-destructive">{item.error}</p> : null}
                    {item.plan.length > 0 ? <PlanList steps={item.plan} /> : null}
                    <ul className="mt-2 space-y-1.5">
                      {item.steps.map((step) => (
                        <li key={step.id} className="text-[11px] text-foreground/75">
                          <span className="text-muted-foreground/70">{step.status === "running" ? "…" : step.status === "done" ? "●" : "○"}</span>{" "}
                          {step.title}
                          {step.paid ? " · платный" : ""}
                          {step.note ? ` · ${step.note}` : ""}
                          {step.node ? <NodeDefMarks node={step.node} /> : null}
                        </li>
                      ))}
                    </ul>
                    {item.checkpoints.map((checkpoint) => (
                      <div key={checkpoint.id} className="mt-2 rounded-md border border-input px-2 py-1.5 text-[11px] text-muted-foreground">
                        чекпоинт: {checkpoint.summary}
                      </div>
                    ))}
                    {item.validators.map((validator, index) => (
                      <div key={`${validator.name}-${index}`} className="mt-1 text-[10px] text-muted-foreground/70">
                        {validator.name}: {validator.pass ? "ok" : "fail"}
                        {validator.details ? ` — ${validator.details}` : ""}
                      </div>
                    ))}
                  </div>
                ))
              )}
            </div>
          </aside>
        </div>
      )}

      <Dialog open={tokenOpen} onOpenChange={setTokenOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Токен оркестратора</DialogTitle>
            <DialogDescription>
              Bearer из ORCH_API_TOKEN. Хранится в этом браузере и уходит только заголовком Authorization.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={tokenDraft}
            onChange={(event) => setTokenDraft(event.target.value)}
            placeholder="токен"
            autoComplete="off"
          />
          <DialogFooter>
            <Button
              type="button"
              onClick={() => {
                chat.submitToken(tokenDraft);
                setTokenOpen(false);
              }}
              disabled={!tokenDraft.trim()}
            >
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={killOpen} onOpenChange={setKillOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Остановить оркестратор?</DialogTitle>
            <DialogDescription>
              Неплатные шаги отменятся сразу, новые действия будут запрещены. Платный шаг остановится на
              границе — оборвать оплату посередине нельзя.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setKillOpen(false)}>
              Не сейчас
            </Button>
            <Button
              type="button"
              variant="destructive"
              data-testid="orch-kill-confirm"
              onClick={() => {
                setKillOpen(false);
                void chat.kill();
              }}
            >
              Остановить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function TokenGate({
  error,
  value,
  onChange,
  onSubmit,
}: {
  error: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-6" data-testid="orch-token-gate">
      <Card className="w-full max-w-md border-border bg-card/95">
        <CardHeader>
          <CardTitle className="text-base">Доступ к оркестратору</CardTitle>
          <CardDescription>
            Запросы /api/orchestrator закрыты Bearer-токеном. Введите его один раз — он сохранится в этом браузере.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? (
            <p className="text-xs text-destructive" data-testid="orch-token-error">
              {error}
            </p>
          ) : null}
          <Input
            data-testid="orch-token-input"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="ORCH_API_TOKEN"
            autoComplete="off"
            onKeyDown={(event) => {
              if (event.key === "Enter") onSubmit();
            }}
          />
          <Button type="button" data-testid="orch-token-submit" onClick={onSubmit} disabled={!value.trim()}>
            Войти
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function formatCost(value: number): string {
  return `~$${value}`;
}

function PlanList({ steps }: { steps: PlanStepView[] }) {
  const groups = groupPlanSteps(steps);
  return (
    <div className="mt-2" data-testid="orch-plan">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">План</div>
      {groups.map((group) => (
        <div
          key={group.key}
          className="mt-1.5"
          data-testid={group.parallel ? "orch-plan-parallel" : "orch-plan-serial"}
          data-parallel-group={group.group || undefined}
        >
          {group.parallel ? (
            <div className="text-[10px] text-info">параллельно · {group.group}</div>
          ) : null}
          {group.steps.map((step, index) => (
            <div key={`${group.key}-${index}`} data-testid="orch-plan-step" className="text-[11px] text-foreground/75">
              {step.action}
              <span data-testid="orch-plan-risk"> · риск {step.risk}</span>
              <span data-testid="orch-plan-cost"> · {formatCost(step.estimatedCost)}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function NodeDefMarks({ node }: { node: NodeDef }) {
  const fromDb = node.source === "db";
  if (!fromDb && !node.stale_base) return null;
  const version = typeof node.db_version === "number" ? node.db_version : null;
  return (
    <div className="mt-1 flex flex-col items-start gap-1">
      {fromDb ? (
        <Badge data-testid="orch-node-badge" variant="info" className="normal-case tracking-normal">
          {version != null ? `из БД, v${version}` : "из БД"}
        </Badge>
      ) : null}
      {node.stale_base ? (
        <p data-testid="orch-node-stale" className="text-[10px] leading-snug text-warning">
          git-шаблон изменился после версии в БД
        </p>
      ) : null}
    </div>
  );
}

function NodeResultCard({ step }: { step: StepView }) {
  if (!step.node) return null;
  return (
    <div
      data-testid="orch-node-result"
      className="mb-3 rounded-xl border border-border bg-white/[0.03] px-3 py-2"
    >
      <div className="text-[11px] text-foreground/70">
        {step.action}
        {step.node.node_key ? ` · ${step.node.node_key}` : ""}
      </div>
      <NodeDefMarks node={step.node} />
    </div>
  );
}

function ApprovalCard({
  approval,
  pending,
  error,
  onApprove,
  onReject,
  onClose,
}: {
  approval: ApprovalView;
  pending: boolean;
  error: string;
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}) {
  const copy = BLOCK_COPY[approval.reason];
  const terminal = isTerminalBlock(approval.reason);
  const title = copy?.title || approval.title || "Нужно подтверждение";
  const body = copy?.body || approval.reason || approval.title;
  return (
    <div
      data-testid="orch-approval"
      data-terminal={terminal ? "true" : "false"}
      className={`mb-3 rounded-xl border px-3 py-3 ${
        terminal ? "border-destructive/40 bg-destructive/10" : "border-warning/40 bg-warning/10"
      }`}
    >
      <div className="text-xs font-semibold text-foreground" data-testid={copy?.testId}>
        {title}
      </div>
      {body ? <p className="mt-1 text-[11px] leading-relaxed text-foreground/70">{body}</p> : null}
      <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
        {approval.action}
        {approval.risk ? ` · ${approval.risk}` : ""}
        {approval.costEstimate != null ? ` · ~$${approval.costEstimate}` : ""}
      </div>
      <div className="mt-2 flex gap-2">
        {terminal ? (
          <Button type="button" size="sm" variant="outline" data-testid="orch-block-close" onClick={onClose}>
            Закрыть
          </Button>
        ) : approval.serverId ? (
          <>
            <Button type="button" size="sm" data-testid="orch-approve" disabled={pending} onClick={onApprove}>
              Approve
            </Button>
            <Button type="button" size="sm" variant="outline" data-testid="orch-reject" disabled={pending} onClick={onReject}>
              Reject
            </Button>
          </>
        ) : (
          <p className="text-[11px] text-muted-foreground" data-testid="orch-approval-noid">
            Нет approval_id от сервера — подтвердить нельзя.
          </p>
        )}
      </div>
      {error ? (
        <p data-testid="orch-approval-error" className="mt-2 text-[11px] text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
