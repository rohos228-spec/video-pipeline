"use client";
import {useEffect, useState} from "react";
import {CommandBar} from "./command-bar";
import {PlanCard} from "./plan-card";
import {RunProgress} from "./run-progress";
import {HistoryList} from "./history-list";
import {describeError, getPlan, listPlans, planFromText, previewFromError, type PlanPreviewResponse} from "@/lib/action-layer";
import {clarifiedRequest} from "@/lib/clarification-view";
import {readyIds, withoutUsed, type AttachmentItem} from "@/lib/attachment-view";

// Слой действий: команда -> предпросмотр плана -> (подтверждение оператора) -> выполнение -> журнал.
// Аварийный стоп оркестратора показывает соседняя KillSwitchSection (её состояние наружу не отдаётся,
// логику не дублируем); при включённом стопе сервер вернёт kill_switch_active, это видно в карточке и выполнении.
export function ActionLayerSection({projectId, sceneId, frameId}: {projectId: number | null; sceneId?: number | null; frameId?: number | null}) {
  const [plan, setPlan] = useState<PlanPreviewResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [requestText, setRequestText] = useState("");
  const [clarifying, setClarifying] = useState(false);
  const [clarifyError, setClarifyError] = useState("");
  // Прикреплённые файлы командной строки: уходят и с первым запросом, и с каждым повтором после уточнения.
  const [files, setFiles] = useState<AttachmentItem[]>([]);
  useEffect(() => { setFiles([]); }, [projectId]);

  // Повтор запроса (с вариантом уточнения или с прикреплёнными файлами) снова уходит в /plans/from-text.
  async function resend(text: string) {
    if (projectId == null || !text) return;
    setClarifying(true); setClarifyError("");
    try {
      const r = await planFromText(text, {project_id: projectId, selected_scene: sceneId ?? null, selected_frame: frameId ?? null}, readyIds(files));
      setPlan(r); setRequestText(text);
      setFiles((f) => withoutUsed(f, r.attachments_used));
    } catch (e) {
      const pv = previewFromError(e);
      if (pv) { setPlan({plan_id: null, preview: pv}); setRequestText(text); }
      else setClarifyError(describeError(e));
    } finally {
      setClarifying(false);
    }
  }

  // Нажатый вариант уточнения: тот же запрос и выбранный ответ.
  const clarify = (option: string) => requestText ? resend(clarifiedRequest(requestText, option)) : Promise.resolve();

  // После перезагрузки страницы возвращаем экран выполнения (с «Остановить»/«Продолжить»)
  // для идущего или прерванного плана этого проекта (или без проекта).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const {items} = await listPlans("running");
        const mine = items.find((p) => p.project_id == null || p.project_id === projectId);
        if (!mine || cancelled) return;
        const v = await getPlan(mine.id);
        if (cancelled || v.status !== "running") return;
        setRunId((cur) => cur ?? v.id);
        setRunning(true);
      } catch { /* список недоступен: экран выполнения просто не восстанавливается */ }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  return (
    <section data-testid="action-layer-panel" aria-label="Слой действий" className="mt-3 space-y-2 rounded border border-white/10 p-2 text-xs text-white/80">
      <h4>Действия по плану</h4>
      <p className="text-white/60">
        Опишите, что сделать: сначала вы увидите план, риск и стоимость. Платное и стирающее данные выполняется только после вашего подтверждения.
      </p>
      <CommandBar projectId={projectId} sceneId={sceneId} frameId={frameId} disabled={running}
        attachments={files} onAttachments={setFiles}
        onPlan={(r, text) => { setPlan(r); setRunId(null); setRequestText(text); setClarifyError(""); setFiles((f) => withoutUsed(f, r.attachments_used)); }} />
      {plan && !runId && (
        <PlanCard plan={plan} busyElsewhere={running}
          onClarify={projectId == null ? undefined : (o) => void clarify(o)} clarifying={clarifying} clarifyError={clarifyError}
          hasFiles={readyIds(files).length > 0} onResend={projectId == null ? undefined : () => void resend(requestText)}
          onStarted={(id) => { setRunId(id); setRunning(true); setPlan(null); setRefresh((n) => n + 1); }}
          onDismiss={() => { setPlan(null); setClarifyError(""); }} />
      )}
      {runId && <RunProgress planId={runId} onFinished={() => { setRunning(false); setRefresh((n) => n + 1); }} />}
      <HistoryList refreshKey={refresh} />
    </section>
  );
}
