"use client";
import {useEffect, useState} from "react";
import {http} from "@/lib/api";
import {Button} from "@/components/ui/button";
import {cn} from "@/lib/utils";

interface KillSwitchHistoryEntry {
  revision: number; active: boolean; actor: string; rationale: string; source: string; created_at: string;
}
interface KillSwitchState {
  active: boolean; changed_by: string | null; rationale: string | null; source: string | null;
  revision: number; changed_at: string | null; history?: KillSwitchHistoryEntry[];
  watchdog?: {enabled: boolean; model_request_timeout_s: number; execution_idle_s: number; kill_switch_on_stall: boolean};
}

const path = (project: number) => `/api/projects/${project}/observations/kill-switch`;

export function KillSwitchSection({projectId}: {projectId: number | null}) {
  const project = projectId ?? 0;
  const [state, setState] = useState<KillSwitchState | null>(null);
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await http<KillSwitchState>(path(project));
        if (!cancelled) { setState(next); setError(""); }
      } catch {
        if (!cancelled) setError("Состояние аварийного стопа недоступно.");
      }
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project]);

  async function change(active: boolean) {
    setBusy(true);
    try {
      const next = await http<KillSwitchState>(path(project), {method: "POST", body: JSON.stringify({active, rationale})});
      setState(next); setError(""); setRationale(""); setConfirming(false);
    } catch {
      setError("Сервер отклонил изменение: нужно основание не короче 3 символов.");
    } finally {
      setBusy(false);
    }
  }

  const active = Boolean(state?.active);
  const last = state?.history?.[0];
  return (
    <section data-testid="kill-switch-panel" className={cn("mt-3 rounded border p-2 space-y-2 text-xs text-white/80", active ? "border-red-400/50 bg-red-500/10" : "border-white/10")}>
      <h4>Аварийный стоп оркестратора · <span className={active ? "font-semibold text-red-300" : undefined}>{state ? (active ? "ВКЛЮЧЁН" : "выключен") : "состояние неизвестно"}</span></h4>
      <p className="text-white/60">
        Включённый стоп запрещает любой новый запрос супервизора, шаг модели решений, восстановление и новый тик управляемого
        проекта до отправки и списания. Уже отправленный запрос не отменяется. Чтение, журнал и диагностика работают. Обычные
        (неуправляемые) проекты не затрагиваются. Выключение возобновляет работу без других действий.
      </p>
      {active && (
        <p role="status" className="text-red-200">
          Новые запросы супервизора заблокированы. Включил: {state?.changed_by ?? "неизвестно"}{state?.source === "watchdog" ? " (сторож)" : ""} · основание: {state?.rationale ?? "—"}
        </p>
      )}
      <label className="block">
        Основание оператора
        <input aria-label="Основание аварийного стопа" value={rationale} onChange={(e) => setRationale(e.target.value)}
          placeholder="не короче 3 символов" maxLength={1000}
          className="ml-2 w-72 max-w-full rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30"/>
      </label>
      {active ? (
        <Button size="sm" variant="outline" disabled={busy || !state || rationale.trim().length < 3} onClick={() => void change(false)}>
          Снять аварийный стоп
        </Button>
      ) : confirming ? (
        <span className="space-x-2">
          <Button size="sm" variant="destructive" disabled={busy || rationale.trim().length < 3} onClick={() => void change(true)}>
            Подтвердить: остановить всё
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirming(false)}>Отмена</Button>
        </span>
      ) : (
        <Button size="sm" variant="outline" disabled={busy || !state || rationale.trim().length < 3} onClick={() => setConfirming(true)}>
          Включить аварийный стоп
        </Button>
      )}
      {state?.watchdog && (
        <p className="text-white/40">
          Сторож: {state.watchdog.enabled ? "включён" : "выключен"} · запрос без ответа &gt; {state.watchdog.model_request_timeout_s} с ·
          тишина в журнале &gt; {state.watchdog.execution_idle_s} с · только помечает, ничего не повторяет ·
          автоматический стоп при зависании: {state.watchdog.kill_switch_on_stall ? "включён" : "выключен"}.
        </p>
      )}
      {last && <p className="text-white/40">Последнее изменение: {last.active ? "включён" : "снят"} · {last.actor} · {last.rationale}</p>}
      {error && <p role="alert" className="text-amber-200">{error}</p>}
    </section>
  );
}
