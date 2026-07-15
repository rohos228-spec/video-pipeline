"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchFleetConfig,
  fetchFleetNodes,
  fleetLogin,
  fleetNodePipeline,
  fleetPullProject,
  fleetQuickConnect,
  fleetSyncNode,
  clearAuthToken,
  getAuthToken,
  setAuthToken,
} from "@/lib/fleet-api";
import { FleetFilesPanel } from "@/components/fleet/fleet-files-panel";
import { FleetProjectRow } from "@/components/fleet/fleet-project-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ExternalLink, Loader2, Network, RefreshCw, Server } from "lucide-react";
import { toast } from "sonner";

type FleetNode = {
  id: number;
  name: string;
  base_url: string;
  is_main: boolean;
  role: string;
  status: string;
  last_seen: string | null;
  hostname: string | null;
  meta?: { pipeline_snapshot?: unknown[] };
};

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

function browserUrl(node: FleetNode, config: Record<string, unknown> | null): string {
  let url = (node.base_url || "").trim();
  if (/0\.0\.0\.0|127\.0\.0\.1|localhost/i.test(url)) {
    const pub = String(config?.public_url ?? "").trim();
    if (pub && !/0\.0\.0\.0/i.test(pub)) return pub;
    return url.replace("0.0.0.0", "127.0.0.1");
  }
  return url;
}

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
  const [loginLoading, setLoginLoading] = useState(false);
  const [connectIp, setConnectIp] = useState("100.100.240.106");
  const [connectLoading, setConnectLoading] = useState(false);

  const reload = useCallback(async () => {
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
      const list = await fetchFleetNodes();
      setNodes(list);
      const selfName = String(cfg.self_node ?? "");
      if (list.length) {
        setSelectedId((prev) => {
          if (prev != null && list.some((n) => n.id === prev)) return prev;
          const remote = list.find((n) => n.name !== selfName);
          return remote?.id ?? list[0].id;
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
  }, [reload]);

  const selected = nodes.find((n) => n.id === selectedId) ?? null;
  const selfNodeName = String(config?.self_node ?? "");
  const remoteNodes = nodes.filter((n) => n.name !== selfNodeName);
  const showQuickConnect = config?.role === "hub" && remoteNodes.length === 0;

  const isLocalNode = (node: FleetNode) => node.name === selfNodeName;
  const isBadRemoteUrl = (node: FleetNode) =>
    !isLocalNode(node) &&
    /127\.0\.0\.1|localhost/i.test(node.base_url || "");

  const loadPipeline = useCallback(async (nodeId: number, quiet = false) => {
    setPipelineLoading(true);
    setLoadError("");
    try {
      const data = await fleetNodePipeline(nodeId);
      const list = (data.projects as FleetProject[]) || [];
      setProjects(list);
      if (!quiet) {
        toast.message(`Загружено проектов: ${list.length}`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Не удалось загрузить проекты";
      setLoadError(msg);
      setProjects([]);
      if (!quiet) toast.error(msg.slice(0, 200));
    } finally {
      setPipelineLoading(false);
    }
  }, []);

  const selectNode = (nodeId: number) => {
    if (nodeId === selectedId) {
      void loadPipeline(nodeId);
      return;
    }
    setSelectedId(nodeId);
  };

  useEffect(() => {
    if (selectedId == null) return;
    void loadPipeline(selectedId, true);
  }, [selectedId, loadPipeline]);

  const openProject = (projectId: number) => {
    if (!selected) return;
    if (isLocalNode(selected) && onOpenProject) {
      onOpenProject(projectId, selected);
      return;
    }
    window.open(browserUrl(selected, config), "_blank", "noopener,noreferrer");
  };

  const handleMontage = (projectId: number, montageReady: boolean, slug: string) => {
    if (selectedId == null) return;
    void fleetPullProject(selectedId, projectId, { runAssemble: montageReady, slug })
      .then((res) => {
        const r = res as { message?: string; pending?: boolean; slug?: string; queued?: boolean };
        toast.success(
          r.message ||
            (r.pending
              ? "Запрос отправлен — жди ~5 сек"
              : `На монтаж: ${r.slug ?? "ok"}${r.queued ? " (очередь)" : ""}`),
        );
        void loadPipeline(selectedId);
      })
      .catch((err) => {
        toast.error(err instanceof Error ? err.message.slice(0, 180) : "Ошибка");
      });
  };
  const openStationStudio = () => {
    if (!selected) return;
    window.open(browserUrl(selected, config), "_blank", "noopener,noreferrer");
  };

  const onQuickConnect = async () => {
    const ip = connectIp.trim().replace(/^https?:\/\//, "").split("/")[0];
    const url = ip.includes(":") ? `http://${ip}` : `http://${ip}:8765`;
    setConnectLoading(true);
    try {
      const res = await fleetQuickConnect(url);
      toast.success(res.message || `Подключено: ${res.project_count} проектов`);
      await reload();
      if (res.node?.id) selectNode(res.node.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message.slice(0, 180) : "Ошибка подключения");
    } finally {
      setConnectLoading(false);
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

      {showQuickConnect ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-primary/20 bg-primary/5 px-4 py-2">
          <span className="text-xs text-muted-foreground">Дочерний ПК:</span>
          <Input
            className="h-8 w-44 text-xs"
            value={connectIp}
            onChange={(e) => setConnectIp(e.target.value)}
            placeholder="100.x.x.x"
          />
          <Button size="sm" className="h-8" disabled={connectLoading} onClick={() => void onQuickConnect()}>
            {connectLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Подключить"}
          </Button>
        </div>
      ) : null}
      {loadError ? (
        <p className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive">
          {loadError}
        </p>
      ) : null}
      {selected && isBadRemoteUrl(selected) ? (
        <p className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-800 dark:text-amber-200">
          Станция {selected.name} зарегистрирована как {selected.base_url}. На воркере задайте
          FLEET_PUBLIC_URL=http://&lt;tailscale-ip&gt;:8765, запустите FLEET-FIX-ALL.cmd и
          перезапустите Studio.
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr_1fr] divide-x divide-border">
        <aside className="overflow-y-auto p-2">
          <p className="mb-2 px-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            Станции
          </p>
          {!loading && nodes.length === 0 ? (
            <p className="px-2 text-xs text-muted-foreground">
              Станций пока нет. Перезапустите Studio или нажмите обновить.
            </p>
          ) : null}
          {nodes.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => selectNode(n.id)}
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
                <span className="block truncate text-[10px] text-muted-foreground/80">{n.base_url}</span>
                {Array.isArray(n.meta?.pipeline_snapshot) ? (
                  <span className="block text-[10px] text-primary">
                    {n.meta.pipeline_snapshot.length} proj (кэш)
                  </span>
                ) : null}
                <span
                  className={cn(
                    "text-[10px]",
                    n.status === "online" ? "text-green-600" : "text-muted-foreground",
                  )}
                >
                  {n.status}
                </span>
              </span>
            </button>
          ))}
        </aside>

        <section className="flex min-h-0 flex-col overflow-hidden p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-medium">
              Пайплайн{selected ? ` · ${selected.name}` : ""}
              {!pipelineLoading && selected ? ` (${projects.length})` : ""}
            </p>
            <div className="flex items-center gap-2">
              {selected ? (
                <Button size="sm" variant="ghost" className="h-7 text-[10px]" onClick={openStationStudio}>
                  <ExternalLink className="mr-1 h-3 w-3" />
                  Открыть Studio
                </Button>
              ) : null}
              {selected && !isLocalNode(selected) ? (
                <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                  удалённая станция
                </span>
              ) : null}
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto">
            {pipelineLoading ? (
              <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Загрузка проектов…
              </div>
            ) : null}
            {!pipelineLoading && projects.length === 0 ? (
              <p className="px-2 text-xs text-muted-foreground">
                {nodes.length <= 1 && config?.role === "hub"
                  ? "Второй ПК не подключён — подожди 30 сек или Sync"
                  : "Проектов нет на этой станции"}
              </p>
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
                onMontage={
                  selectedId != null && !p.montage_queued
                    ? () => handleMontage(p.id, Boolean(p.montage_ready), p.slug)
                    : undefined
                }
              />
            ))}
          </div>
          {selected ? (
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => void fleetSyncNode(selected.id).then(() => reload())}
            >
              Sync
            </Button>
          ) : null}
        </section>

        <FleetFilesPanel nodeId={selectedId} disabled={selectedId == null} />
      </div>
    </div>
  );
}
