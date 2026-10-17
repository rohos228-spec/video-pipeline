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
  isNodeResultAction,
  TASK_STATUS_LABEL,
  type ApprovalView,
  type StepView,
} from "@/lib/orchestratorSession";
import type { NodeDef, TaskStatus } from "@/lib/orchestratorTypes";
import { cn } from "@/lib/utils";
import { useOrchestratorChat } from "./use-orchestrator-chat";

const KILL_HONEST =
  "Неплатные шаги отменены, новые действия запрещены. Платный шаг остановится на границе.";

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
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);

  const task = chat.state.tasks.at(-1) ?? null;
  const openApprovals = chat.state.approvals.filter((item) => item.resolved == null);

  const send = () => {
    const text = draft;
    if (!text.trim()) return;
    setDraft("");
    void chat.sendMessage(text);
  };

  return (
    <div className="flex h-[calc(100vh-48px)] min-h-0 flex-col bg-background" data-orch-page="">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-white/[0.06] px-4">
        <div className="flex min-w-0 items-center gap-2">
          <Bot className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-sm font-semibold tracking-tight text-white">Оркестратор</span>
          <Badge variant="muted" className="normal-case tracking-normal">
            {chat.boot.session}
          </Badge>
          {task ? (
            <Badge variant={statusBadgeVariant(task.status)} data-testid="orch-task-status" className="normal-case tracking-normal">
              {TASK_STATUS_LABEL[task.status as TaskStatus] ?? task.status}
            </Badge>
          ) : (
            <span className="text-[11px] text-white/35" data-testid="orch-task-status">
              нет задачи
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
          className="shrink-0 border-b border-destructive/40 bg-destructive/15 px-4 py-2 text-xs text-white"
        >
          Kill switch: {chat.state.killNotice.includes("границе") ? chat.state.killNotice : `${chat.state.killNotice} ${KILL_HONEST}`}
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
          <section className="flex min-w-0 flex-1 flex-col border-r border-white/[0.06]">
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" data-testid="orch-transcript">
              {chat.state.messages.length === 0 ? (
                <p className="text-xs leading-relaxed text-white/35">
                  Живой чат супервизора. Сообщение можно отправить и посреди задачи: сначала придёт ack
                  «принял, разбираю», затем классификация interrupt / steer / append.
                </p>
              ) : null}
              {chat.state.messages.map((message) => (
                <div key={message.id} className="mb-3 text-[13px] leading-relaxed">
                  <span className={message.role === "user" ? "text-primary" : "text-white/45"}>
                    {message.role === "user" ? "ты" : "оркестратор"}:{" "}
                  </span>
                  <span
                    className="whitespace-pre-wrap text-white/90"
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
                  onApprove={() => void chat.decide(approval.id, approval.taskId, "approved")}
                  onReject={() => void chat.decide(approval.id, approval.taskId, "rejected")}
                />
              ))}
            </div>

            <div className="shrink-0 space-y-2 border-t border-white/[0.06] px-4 py-3">
              {chat.state.ack ? (
                <div data-testid="orch-ack" className="text-[11px] text-info">
                  {chat.state.ack.text || "принял, разбираю"}
                  {chat.state.ack.echo ? ` — «${chat.state.ack.echo}»` : ""}
                </div>
              ) : null}
              {chat.state.classification ? (
                <div className="flex items-center gap-2 text-[11px]" data-testid="orch-classification">
                  <Badge
                    variant={
                      chat.state.classification.mode === "interrupt"
                        ? "warning"
                        : chat.state.classification.mode === "steer"
                          ? "info"
                          : "success"
                    }
                    className="normal-case tracking-normal"
                  >
                    {chat.state.classification.mode}
                  </Badge>
                  <span className="text-white/55">
                    {chat.state.classification.detail || "классификация"}
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
              <p className="text-[10px] text-white/30">
                Поле не блокируется на время задачи. Ack — сразу, классификация — следом.
              </p>
            </div>
          </section>

          <aside className="flex w-[340px] shrink-0 flex-col bg-black/30" data-testid="orch-task-panel">
            <div className="border-b border-white/[0.06] px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/40">Агенты</div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {chat.state.agents.map((agent) => (
                  <div
                    key={agent.role}
                    data-testid={`orch-agent-${agent.role}`}
                    className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-2"
                  >
                    <div className="text-[11px] font-medium text-white/80">{AGENT_LABEL[agent.role]}</div>
                    <div className="mt-1 text-[10px] uppercase tracking-wide text-white/45">{agent.status}</div>
                    {agent.detail ? <div className="mt-1 truncate text-[10px] text-white/35">{agent.detail}</div> : null}
                  </div>
                ))}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-white/40">Задачи</div>
              {chat.state.tasks.length === 0 ? (
                <p className="mt-2 text-[11px] text-white/35">Шаги и чекпоинты появятся вместе с задачей.</p>
              ) : (
                chat.state.tasks.map((item) => (
                  <div key={item.id} className="mt-3 rounded-xl border border-white/[0.08] bg-card/80 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate text-xs font-semibold text-white/90">{item.title}</div>
                      <Badge variant={statusBadgeVariant(item.status)} className="normal-case tracking-normal">
                        {TASK_STATUS_LABEL[item.status] ?? item.status}
                      </Badge>
                    </div>
                    {item.summary ? (
                      <p className="mt-2 text-[11px] text-white/70" data-testid="orch-task-summary">
                        {item.summary}
                      </p>
                    ) : null}
                    {item.error ? <p className="mt-2 text-[11px] text-destructive">{item.error}</p> : null}
                    <ul className="mt-2 space-y-1.5">
                      {item.steps.map((step) => (
                        <li key={step.id} className="text-[11px] text-white/75">
                          <span className="text-white/40">{step.status === "running" ? "…" : step.status === "done" ? "●" : "○"}</span>{" "}
                          {step.title}
                          {step.paid ? " · платный" : ""}
                          {step.note ? ` · ${step.note}` : ""}
                          {step.node ? <NodeDefMarks node={step.node} /> : null}
                        </li>
                      ))}
                    </ul>
                    {item.checkpoints.map((checkpoint) => (
                      <div key={checkpoint.id} className="mt-2 rounded-md border border-white/[0.06] px-2 py-1.5 text-[11px] text-white/60">
                        чекпоинт: {checkpoint.summary}
                      </div>
                    ))}
                    {item.validators.map((validator, index) => (
                      <div key={`${validator.name}-${index}`} className="mt-1 text-[10px] text-white/40">
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
      <Card className="w-full max-w-md border-white/10 bg-card/95">
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
      className="mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2"
    >
      <div className="text-[11px] text-white/70">
        {step.action}
        {step.node.node_key ? ` · ${step.node.node_key}` : ""}
      </div>
      <NodeDefMarks node={step.node} />
    </div>
  );
}

function ApprovalCard({
  approval,
  onApprove,
  onReject,
}: {
  approval: ApprovalView;
  onApprove: () => void;
  onReject: () => void;
}) {
  const budget = approval.reason === "budget_exhausted";
  return (
    <div
      data-testid="orch-approval"
      className="mb-3 rounded-xl border border-warning/40 bg-warning/10 px-3 py-3"
    >
      <div className="text-xs font-semibold text-white" data-testid={budget ? "orch-budget" : undefined}>
        {budget ? "Бюджет исчерпан" : approval.title}
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-white/70">
        {budget
          ? "Лимит прогона выбран. Задача ждёт подтверждения, новые платные шаги не стартуют."
          : approval.reason}
      </p>
      <div className="mt-1 text-[10px] uppercase tracking-wide text-white/40">
        {approval.action}
        {approval.risk ? ` · ${approval.risk}` : ""}
        {approval.costEstimate != null ? ` · ~$${approval.costEstimate}` : ""}
      </div>
      <div className="mt-2 flex gap-2">
        <Button type="button" size="sm" data-testid="orch-approve" onClick={onApprove}>
          Approve
        </Button>
        <Button type="button" size="sm" variant="outline" data-testid="orch-reject" onClick={onReject}>
          Reject
        </Button>
      </div>
    </div>
  );
}
