"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import type { ProjectDetail } from "@/lib/types";

type RuntimeStreams = {
  worker_max_parallel: number;
  default_outsee_streams: number;
  default_check_streams: number;
  worker_busy?: number;
  create_max_parallel_outsee?: number;
};

function StreamButtons({
  values,
  local,
  disabled,
  onPick,
}: {
  values: number[];
  local: number;
  disabled?: boolean;
  onPick: (n: number) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((n) => (
        <button
          key={n}
          type="button"
          disabled={disabled}
          onClick={() => onPick(n)}
          className={
            "h-7 min-w-7 rounded-md px-2 text-xs font-medium transition-colors " +
            (local === n
              ? "bg-primary text-primary-foreground"
              : "bg-muted/60 text-muted-foreground hover:bg-accent/50")
          }
        >
          {n}
        </button>
      ))}
    </div>
  );
}

/** Отдельный блок потоков: глобальная параллель проектов + per-project Outsee/check. */
export function StreamsPanel({ project }: { project: ProjectDetail | null }) {
  const qc = useQueryClient();
  const runtime = useQuery({
    queryKey: ["runtime-streams"],
    queryFn: () => api.getRuntimeStreams(),
    refetchInterval: 8000,
  });

  const cfg = (runtime.data || {}) as RuntimeStreams;
  const workerCur = Math.max(1, Math.min(4, Number(cfg.worker_max_parallel ?? 1) || 1));
  const [workerLocal, setWorkerLocal] = useState(workerCur);

  useEffect(() => {
    setWorkerLocal(workerCur);
  }, [workerCur]);

  const patchGlobal = useMutation({
    mutationFn: (body: Partial<RuntimeStreams>) => api.patchRuntimeStreams(body),
    onSuccess: (data, vars) => {
      if (vars.worker_max_parallel != null) {
        toast.success(`Параллель проектов: ${vars.worker_max_parallel}`);
      } else if (vars.default_outsee_streams != null) {
        toast.success(`Default Outsee: ${vars.default_outsee_streams}`);
      } else if (vars.default_check_streams != null) {
        toast.success(`Default check: ${vars.default_check_streams}`);
      }
      void qc.invalidateQueries({ queryKey: ["runtime-streams"] });
      void qc.setQueryData(["runtime-streams"], data);
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const meta = (project?.meta || {}) as Record<string, unknown>;
  const outseeRaw = meta.outsee_streams ?? meta.img_streams ?? cfg.default_outsee_streams ?? 1;
  const outseeCur = Math.max(0, Math.min(4, Number(outseeRaw)));
  const checkRaw = meta.check_streams ?? cfg.default_check_streams ?? 2;
  const checkCur = Math.max(0, Math.min(10, Number(checkRaw)));
  const [outseeLocal, setOutseeLocal] = useState(
    Number.isFinite(outseeCur) ? outseeCur : 1,
  );
  const [checkLocal, setCheckLocal] = useState(
    Number.isFinite(checkCur) ? checkCur : 2,
  );
  // Пока PATCH только что сохранён — не откатывать local из stale refetch.
  const outseeHoldUntilRef = useRef(0);
  const checkHoldUntilRef = useRef(0);

  useEffect(() => {
    if (Date.now() < outseeHoldUntilRef.current) return;
    if (Number.isFinite(outseeCur)) setOutseeLocal(outseeCur);
  }, [outseeCur]);
  useEffect(() => {
    if (Date.now() < checkHoldUntilRef.current) return;
    if (Number.isFinite(checkCur)) setCheckLocal(checkCur);
  }, [checkCur]);

  const patchProject = useMutation({
    mutationFn: (body: { meta: Record<string, number> }) => {
      if (!project) throw new Error("Нет проекта");
      return api.patchProject(project.id, body);
    },
    onSuccess: (data, vars) => {
      const m = vars.meta;
      if (m.img_streams != null || m.outsee_streams != null) {
        const n = Number(m.img_streams ?? m.outsee_streams);
        toast.success(
          n === 0
            ? "Потоки Outsee: 0 (без генерации)"
            : `Потоки Outsee (проект): ${n}`,
        );
        outseeHoldUntilRef.current = Date.now() + 8000;
      }
      if (m.check_streams != null) {
        toast.success(
          m.check_streams === 0
            ? "Потоки проверки: 0"
            : `Потоки проверки (проект): ${m.check_streams}`,
        );
        checkHoldUntilRef.current = Date.now() + 8000;
      }
      if (project) {
        // Optimistic: сразу пишем meta в кэш — иначе refetch/старый project
        // откатывает кнопки потоков на default=1.
        qc.setQueryData<ProjectDetail>(["project", project.id], (prev) => {
          const base = data ?? prev;
          if (!base) return prev;
          return {
            ...base,
            meta: {
              ...(base.meta || {}),
              ...m,
            },
          };
        });
        void qc.invalidateQueries({ queryKey: ["project", project.id] });
      }
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const busy = Number(cfg.worker_busy ?? 0);
  const pending = patchGlobal.isPending || patchProject.isPending || runtime.isLoading;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-sky-500/25 bg-sky-500/[0.04] p-3">
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-sky-200/90">
        <Layers className="h-3.5 w-3.5" />
        Потоки
        {pending ? <Loader2 className="h-3 w-3 animate-spin opacity-70" /> : null}
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Параллель проектов — глобально. Outsee/check ниже — для текущего проекта
        (Create делит Outsee-пул ≤{cfg.create_max_parallel_outsee ?? 4}).
      </p>

      <div className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-2">
        <div className="mb-1 flex items-center justify-between gap-2">
          <div className="text-xs font-medium">Параллель проектов</div>
          <span className="text-[10px] text-muted-foreground">
            занято {busy}/{workerLocal}
          </span>
        </div>
        <p className="mb-2 text-[10px] leading-snug text-muted-foreground">
          Сколько проектов из очереди крутить сразу (1–4). paused/⏹ не блокируют
          хвост — свободные слоты уходят следующим.
        </p>
        <StreamButtons
          values={[1, 2, 3, 4]}
          local={workerLocal}
          disabled={patchGlobal.isPending}
          onPick={(n) => {
            setWorkerLocal(n);
            patchGlobal.mutate({ worker_max_parallel: n });
          }}
        />
      </div>

      {project ? (
        <>
          <div className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-2">
            <div className="mb-1.5 text-xs font-medium">Outsee (img + video) · проект</div>
            <p className="mb-2 text-[10px] leading-snug text-muted-foreground">
              0 — не звать API; 1 — по одному; 2–4 — параллельно внутри шага.
            </p>
            <StreamButtons
              values={[0, 1, 2, 3, 4]}
              local={outseeLocal}
              disabled={patchProject.isPending}
              onPick={(n) => {
                setOutseeLocal(n);
                patchProject.mutate({
                  meta: { img_streams: n, outsee_streams: n },
                });
              }}
            />
          </div>

          <div className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-2">
            <div className="mb-1.5 text-xs font-medium">Проверка GPT · проект</div>
            <p className="mb-2 text-[10px] leading-snug text-muted-foreground">
              Параллельные vision-батчи (0–10), каждый до 8 PNG.
            </p>
            <StreamButtons
              values={[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
              local={checkLocal}
              disabled={patchProject.isPending}
              onPick={(n) => {
                setCheckLocal(n);
                patchProject.mutate({ meta: { check_streams: n } });
              }}
            />
          </div>
        </>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          Выбери проект — появятся потоки Outsee и check для него.
        </p>
      )}
    </div>
  );
}
