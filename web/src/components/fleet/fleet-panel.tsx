"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchFleetConfig,
  fetchFleetNodes,
  fetchFleetTransfersActive,
  fleetLogin,
  fleetNodePipeline,
  fleetPullProject,
  fleetSyncAllNodes,
  fleetSyncNode,
  clearAuthToken,
  getAuthToken,
  setAuthToken,
  type FleetNodeSummary,
  type FleetTransfer,
} from "@/lib/fleet-api";
import { subscribeWS } from "@/lib/api";
import { FleetFilesPanel } from "@/components/fleet/fleet-files-panel";
import { FleetProjectRow } from "@/components/fleet/fleet-project-status";
import { FleetTransferBar } from "@/components/fleet/fleet-transfer-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ExternalLink, Loader2, Network, RefreshCw, Server } from "lucide-react";

type FleetNode = FleetNodeSummary;

type FleetProject = {
  id: number;
  slug: string;
  topic?: string | null;
  status: string;
  montage_ready?: boolean;
  montage_queued?: boolean;
  montage_queue_position?: number | null;
  send_to_main_pc?: boolean;
};

export function FleetPanel({
  onOpenProject,
}: {
  onOpenProject?: (projectId: number, node: FleetNode) => void;
}) {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [nodes, setNodes] = useState<FleetNode[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [projects, setProjects] = useState<FleetProject[]>([]);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPass, setLoginPass] = useState("");
  const [authRequired, setAuthRequired] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [pipelineErr, setPipelineErr] = useState("");
  const [autoStatus, setAutoStatus] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [montageBusyId, setMontageBusyId] = useState<number | null>(null);
  const [montageMsg, setMontageMsg] = useState("");
  const [montageErr, setMontageErr] = useState("");
  const [transfers, setTransfers] = useState<FleetTransfer[]>([]);
  const [hubMontageHint, setHubMontageHint] = useState<{ slug: string; id: number } | null>(
    null,
  );
  const userPickedNode = useRef(false);
  const nodesRef = useRef<FleetNode[]>([]);
  nodesRef.current = nodes;

  const reload = useCallback(async (opts?: { sync?: boolean }) => {
    setLoading(true);
    setLoadError("");
    try {
      const cfg = await fetchFleetConfig();
      setConfig(cfg);
      setAuthRequired(Boolean(cfg.auth_required));
      if (cfg.auth_required && !getAuthToken()) {
        setLoggedIn(false);
        setNodes([]);
        return;
      }
      setLoggedIn(true);
      if (opts?.sync !== false) {
        try {
          const sync = await fleetSyncAllNodes();
          setAutoStatus(
            sync.reachable > 0
              ? `Автоподключение: ${sync.reachable}/${sync.total} станций доступны`
              : `Автоподключение: 0/${sync.total} — проверь Studio на воркере`,
          );
        } catch {
          setAutoStatus("");
        }
      }
      const { nodes: list, preferred_node_id: preferredId } = await fetchFleetNodes();
      setNodes(list);
      if (list.length) {
        setSelectedId((prev) => {
          if (userPickedNode.current && prev != null && list.some((n) => n.id === prev)) {
            return prev;
          }
          if (preferredId != null && list.some((n) => n.id === preferredId)) {
            return preferredId;
          }
          const remote = list.find(
            (n) => n.hub_reachable && !n.is_main && !String(n.role).includes("hub"),
          );
          if (remote) return remote.id;
          return prev ?? list[0].id;
        });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ошибка загрузки сети";
      if (/login required|invalid or expired/i.test(msg)) {
        clearAuthToken();
        setLoggedIn(false);
        setNodes([]);
      } else {
        setLoadError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const timer = window.setInterval(() => void reload({ sync: true }), 30_000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const refreshTransfers = useCallback(async () => {
    try {
      const data = await fetchFleetTransfersActive();
      setTransfers(data.transfers || []);
    } catch {
      // ignore — hub may be starting
    }
  }, []);

  useEffect(() => {
    if (!loggedIn) {
      setTransfers([]);
      return;
    }
    void refreshTransfers();
    const timer = window.setInterval(() => void refreshTransfers(), 1500);
    const unsub = subscribeWS("global", (event) => {
      const ev = event as FleetTransfer & { type?: string };
      if (ev?.type !== "fleet_transfer") return;
      if (ev.status === "active") {
        setTransfers((prev) => {
          const rest = prev.filter((t) => t.project_id !== ev.project_id);
          return [ev, ...rest];
        });
      } else if (ev.status === "done") {
        setTransfers((prev) => prev.filter((t) => t.project_id !== ev.project_id));
        const slug = ev.slug || "";
        const pid = ev.project_id;
        if (slug && pid) {
          setHubMontageHint({ slug, id: pid });
        }
        setMontageMsg(
          ev.message ||
            (slug
              ? `«${slug}» на hub (#${pid}) — монтаж запущен. Слева выбери hub → «Перейти».`
              : `Проект #${pid} на hub — монтаж запущен.`),
        );
        void refreshTransfers();
        void (async () => {
          const list = nodesRef.current;
          const self = String(config?.self_node ?? "");
          const isHub = (n: FleetNode) =>
            n.is_main || n.name === self || n.role.includes("hub");
          const hubNode = list.find((n) => n.is_main) ?? list.find((n) => isHub(n));
          if (!hubNode) return;
          setSelectedId(hubNode.id);
          try {
            const data = await fleetNodePipeline(hubNode.id);
            setProjects((data.projects as FleetProject[]) || []);
          } catch {
            // hub pipeline reload is best-effort
          }
        })();
      } else if (ev.status === "error") {
        setTransfers((prev) => prev.filter((t) => t.project_id !== ev.project_id));
        setMontageErr(ev.message || "Ошибка передачи bundle");
      }
    });
    return () => {
      window.clearInterval(timer);
      unsub();
    };
  }, [loggedIn, refreshTransfers, config?.self_node]);

  const selected = nodes.find((n) => n.id === selectedId) ?? null;
  const selfNodeName = String(config?.self_node ?? "");

  const isLocalNode = (node: FleetNode) =>
    node.is_main || node.name === selfNodeName || node.role.includes("hub");

  const loadPipeline = useCallback(async (nodeId: number, retry = true) => {
    setPipelineLoading(true);
    setPipelineErr("");
    try {
      const data = await fleetNodePipeline(nodeId);
      setProjects((data.projects as FleetProject[]) || []);
    } catch (err) {
      if (retry) {
        try {
          await fleetSyncNode(nodeId);
          await loadPipeline(nodeId, false);
          return;
        } catch {
          // fall through to error below
        }
      }
      setProjects([]);
      setPipelineErr(err instanceof Error ? err.message : "Ошибка загрузки пайплайна");
    } finally {
      setPipelineLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId == null) {
      setProjects([]);
      setPipelineErr("");
      return;
    }
    void loadPipeline(selectedId);
  }, [selectedId, loadPipeline]);

  const openProject = (projectId: number) => {
    if (!selected) return;
    if (isLocalNode(selected) && onOpenProject) {
      onOpenProject(projectId, selected);
      return;
    }
    window.open(selected.base_url, "_blank", "noopener,noreferrer");
  };

  const runMontage = async (projectId: number, projectSlug?: string) => {
    if (selectedId == null || !selected) return;
    setMontageBusyId(projectId);
    setMontageErr("");
    setMontageMsg("");
    try {
      const res = await fleetPullProject(selectedId, projectId);
      const hubNode = nodes.find((n) => n.is_main) ?? nodes.find((n) => isLocalNode(n));
      if (hubNode && !isLocalNode(selected)) {
        setSelectedId(hubNode.id);
        await loadPipeline(hubNode.id);
      } else {
        await loadPipeline(selectedId);
      }
      const name = res.slug || projectSlug || `#${projectId}`;
      if (res.message) {
        setMontageMsg(res.message);
      } else if (res.started) {
        setMontageMsg(
          `Загрузка «${name}» с ${selected.name}… После скачивания монтаж пойдёт на hub (не на NucBox).`,
        );
        void refreshTransfers();
      } else if (res.queued) {
        setMontageMsg(`${name} → очередь монтажа на hub (#${res.project_id ?? "?"})`);
      } else {
        setMontageMsg(`${name} импортирован на hub (id ${res.project_id ?? "?"})`);
      }
    } catch (err) {
      setMontageErr(err instanceof Error ? err.message : "Ошибка монтажа");
    } finally {
      setMontageBusyId(null);
    }
  };

  const onLogin = async () => {
    setLoginError("");
    setLoginLoading(true);
    try {
      const res = await fleetLogin(loginUser, loginPass);
      if (res.token) setAuthToken(res.token);
      setLoggedIn(true);
      await reload();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Ошибка входа";
      setLoginError(msg);
      setLoggedIn(false);
    } finally {
      setLoginLoading(false);
    }
  };

  if (authRequired && !loggedIn) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <form
          className="w-full max-w-sm space-y-3 rounded-lg border border-border bg-card p-6"
          onSubmit={(e) => {
            e.preventDefault();
            void onLogin();
          }}
        >
          <h2 className="text-lg font-semibold">Вход в сеть</h2>
          <p className="text-xs text-muted-foreground">
            Логин: admin · пароль из data\fleet-hub-credentials.txt
          </p>
          <Input
            placeholder="Логин"
            value={loginUser}
            autoComplete="username"
            onChange={(e) => setLoginUser(e.target.value)}
          />
          <Input
            type="password"
            placeholder="Пароль"
            value={loginPass}
            autoComplete="current-password"
            onChange={(e) => setLoginPass(e.target.value)}
          />
          {loginError ? (
            <p className="text-xs text-destructive">{loginError}</p>
          ) : null}
          <Button type="submit" className="w-full" disabled={loginLoading}>
            {loginLoading ? "Вход…" : "Войти"}
          </Button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Network className="h-4 w-4 text-primary" />
          Сеть · Tailscale
          {config?.montage_hub ? (
            <span className="text-xs text-muted-foreground">(ПК монтажа)</span>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={() => void reload()} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        </Button>
      </div>

      {autoStatus ? (
        <p className="border-b border-border bg-muted/30 px-4 py-1.5 text-[10px] text-muted-foreground">
          {autoStatus}
        </p>
      ) : null}
      <FleetTransferBar transfers={transfers} />
      {loadError ? (
        <p className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {loadError}
        </p>
      ) : null}
      {montageErr ? (
        <p className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {montageErr}
        </p>
      ) : null}
      {montageMsg ? (
        <p className="border-b border-primary/30 bg-primary/10 px-4 py-2 text-xs text-primary">
          {montageMsg}
        </p>
      ) : null}
      {hubMontageHint && onOpenProject ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2 text-xs text-warning">
          <span>
            Монтаж «{hubMontageHint.slug}» на hub (#{hubMontageHint.id}) — прогресс на канвасе
            hub, не на воркере.
          </span>
          <button
            type="button"
            className="rounded-md border border-warning/40 bg-warning/15 px-2 py-1 text-[10px] font-medium hover:bg-warning/25"
            onClick={() => {
              const hub =
                nodes.find((n) => n.is_main) ??
                nodes.find((n) => isLocalNode(n));
              if (hub) onOpenProject(hubMontageHint.id, hub);
            }}
          >
            Открыть на канвасе hub
          </button>
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr_1fr] divide-x divide-border">
        <aside className="overflow-y-auto p-2">
          <p className="mb-2 px-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            Станции
          </p>
          {!loading && nodes.length === 0 ? (
            <p className="px-2 text-xs text-muted-foreground">
              Станций пока нет — воркер подключится сам при запуске Studio.
            </p>
          ) : null}
          {nodes.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => {
                userPickedNode.current = true;
                setSelectedId(n.id);
              }}
              className={cn(
                "mb-1 flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs hover:bg-muted/60",
                selectedId === n.id && "bg-muted",
              )}
            >
              <Server className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <span className="block font-medium">
                  {n.name}
                  {n.is_main ? (
                    <span className="ml-1 text-[10px] text-primary">главный</span>
                  ) : null}
                </span>
                <span className="block text-[10px] text-muted-foreground">{n.role}</span>
                <span
                  className={cn(
                    "text-[10px]",
                    n.hub_reachable
                      ? "text-green-600"
                      : n.status === "online"
                        ? "text-amber-600"
                        : "text-muted-foreground",
                  )}
                >
                  {n.hub_reachable
                    ? "доступен"
                    : n.status === "online"
                      ? "online · hub не видит"
                      : n.status}
                </span>
              </span>
            </button>
          ))}
        </aside>

        <section className="flex min-h-0 flex-col overflow-hidden p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-medium">Пайплайн</p>
            {selected && !isLocalNode(selected) ? (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <ExternalLink className="h-3 w-3" />
                Перейти откроет Studio воркера
              </span>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto">
            {pipelineLoading ? (
              <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Загрузка проектов…
              </div>
            ) : null}
            {pipelineErr ? (
              <div className="rounded border border-destructive/30 bg-destructive/10 px-2 py-2 text-xs text-destructive">
                <p>{pipelineErr}</p>
                {/cannot connect|таймаут|timeout|не достучаться/i.test(pipelineErr) ? (
                  <p className="mt-2 text-[10px] text-destructive/90">
                    На ПК {selected?.name}: запусти Studio (run-backend). Hub сам проверит связь
                    каждые 30 сек. Нужны Tailscale (один аккаунт) и открытый порт 8765 — Studio
                    настроит при старте.
                  </p>
                ) : null}
              </div>
            ) : null}
            {!pipelineLoading && !pipelineErr && projects.length === 0 ? (
              <p className="px-2 text-xs text-muted-foreground">Проектов нет</p>
            ) : null}
            {projects.map((p) => (
              <FleetProjectRow
                key={p.id}
                slug={p.slug}
                topic={p.topic}
                status={p.status}
                montageReady={Boolean(p.montage_ready)}
                montageQueued={Boolean(p.montage_queued)}
                montageQueuePosition={p.montage_queue_position}
                onOpen={() => openProject(p.id)}
                montageBusy={montageBusyId === p.id}
                onMontage={
                  p.montage_ready && !p.montage_queued && selectedId != null
                    ? () => void runMontage(p.id, p.slug)
                    : undefined
                }
              />
            ))}
          </div>
        </section>

        <FleetFilesPanel nodeId={selectedId} disabled={selectedId == null} />
      </div>
    </div>
  );
}
