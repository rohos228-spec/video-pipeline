"use client";
import {useState} from "react";
import {CommandBar} from "./command-bar";
import {PlanCard} from "./plan-card";
import {RunProgress} from "./run-progress";
import {HistoryList} from "./history-list";
import type {PlanPreviewResponse} from "@/lib/action-layer";

// Слой действий: команда -> предпросмотр плана -> (подтверждение оператора) -> выполнение -> журнал.
// Аварийный стоп оркестратора показывает соседняя KillSwitchSection (её состояние наружу не отдаётся,
// логику не дублируем); при включённом стопе сервер вернёт kill_switch_active, это видно в карточке и выполнении.
export function ActionLayerSection({projectId, sceneId, frameId}: {projectId: number | null; sceneId?: number | null; frameId?: number | null}) {
  const [plan, setPlan] = useState<PlanPreviewResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [refresh, setRefresh] = useState(0);

  return (
    <section data-testid="action-layer-panel" aria-label="Слой действий" className="mt-3 space-y-2 rounded border border-white/10 p-2">
      <h4>Действия по плану</h4>
      <p className="text-white/60">
        Опишите, что сделать: сначала вы увидите план, риск и стоимость. Платное и стирающее данные выполняется только после вашего подтверждения.
      </p>
      <CommandBar projectId={projectId} sceneId={sceneId} frameId={frameId} disabled={running}
        onPlan={(r) => { setPlan(r); setRunId(null); }} />
      {plan && !runId && (
        <PlanCard plan={plan} busyElsewhere={running}
          onStarted={(id) => { setRunId(id); setRunning(true); setPlan(null); setRefresh((n) => n + 1); }}
          onDismiss={() => setPlan(null)} />
      )}
      {runId && <RunProgress planId={runId} onFinished={() => { setRunning(false); setRefresh((n) => n + 1); }} />}
      <HistoryList refreshKey={refresh} />
    </section>
  );
}
