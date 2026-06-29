const TOKEN_KEY = "vp_fleet_token";

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function fleetFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body && !(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = await res.text();
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (parsed.detail) detail = String(parsed.detail);
    } catch {
      // keep raw text
    }
    throw new Error(detail || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Login without requiring existing token. */
export async function fleetLogin(username: string, password: string) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = await res.text();
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (parsed.detail) detail = String(parsed.detail);
    } catch {
      // keep raw text
    }
    throw new Error(detail || "Login failed");
  }
  return res.json() as Promise<{ token: string; auth_required: boolean }>;
}

export async function fetchFleetConfig() {
  return fleetFetch<Record<string, unknown>>("/api/fleet/config");
}

export type FleetNodeSummary = {
  id: number;
  name: string;
  base_url: string;
  is_main: boolean;
  role: string;
  status: string;
  last_seen: string | null;
  hostname: string | null;
  hub_reachable?: boolean | null;
  hub_probe_error?: string | null;
};

export type FleetNodesResponse = {
  nodes: FleetNodeSummary[];
  preferred_node_id: number | null;
};

export async function fetchFleetNodes(): Promise<FleetNodesResponse> {
  const data = await fleetFetch<
    | { nodes: FleetNodeSummary[]; preferred_node_id?: number | null }
    | FleetNodeSummary[]
  >("/api/fleet/nodes");
  if (Array.isArray(data)) {
    return { nodes: data, preferred_node_id: null };
  }
  return { nodes: data.nodes, preferred_node_id: data.preferred_node_id ?? null };
}

export async function fleetSyncAllNodes() {
  return fleetFetch<{ ok: boolean; total: number; reachable: number }>(
    "/api/fleet/nodes/sync-all",
    { method: "POST" },
  );
}

export type FleetTransfer = {
  project_id: number;
  job?: string;
  phase: string;
  direction?: string;
  percent: number;
  sent_mb?: number;
  total_mb?: number;
  message?: string;
  source_node?: string;
  target?: string;
  slug?: string;
  status: string;
};

export async function fetchFleetTransfersActive() {
  return fleetFetch<{ transfers: FleetTransfer[] }>("/api/fleet/transfers/active");
}

export async function fleetSyncNode(nodeId: number) {
  return fleetFetch<{ ok: boolean }>(`/api/fleet/nodes/${nodeId}/sync`, { method: "POST" });
}

export async function fleetNodePipeline(nodeId: number) {
  return fleetFetch<{ projects: Array<Record<string, unknown>> }>(
    `/api/fleet/nodes/${nodeId}/pipeline`,
  );
}

export async function fleetNodeFiles(nodeId: number, path: string) {
  return fleetFetch<Record<string, unknown>>(
    `/api/fleet/nodes/${nodeId}/files?path=${encodeURIComponent(path)}`,
  );
}

export async function fleetNodeFileContent(nodeId: number, path: string) {
  return fleetFetch<{ path: string; size: number; content: string; encoding: string }>(
    `/api/fleet/nodes/${nodeId}/files/content?path=${encodeURIComponent(path)}`,
  );
}

export async function fleetNodeFileDelete(nodeId: number, path: string) {
  return fleetFetch<{ ok: boolean }>(
    `/api/fleet/nodes/${nodeId}/files?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
}

export async function fleetNodeFileUpload(nodeId: number, path: string, file: File) {
  const token = getAuthToken();
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `/api/fleet/nodes/${nodeId}/files/upload?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    },
  );
  if (!res.ok) {
    let detail = await res.text();
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (parsed.detail) detail = String(parsed.detail);
    } catch {
      // keep raw
    }
    throw new Error(detail || res.statusText);
  }
  return res.json() as Promise<{ ok: boolean; path: string; size: number }>;
}

export async function fleetNodeFileDownload(nodeId: number, path: string) {
  const token = getAuthToken();
  const res = await fetch(
    `/api/fleet/nodes/${nodeId}/files/download?path=${encodeURIComponent(path)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) {
    throw new Error(await res.text());
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = path.split("/").pop() || "download";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function fleetNodePowerShell(nodeId: number, command: string, cwd?: string) {
  return fleetFetch<{ stdout: string; stderr: string; exit_code: number }>(
    `/api/fleet/nodes/${nodeId}/powershell`,
    { method: "POST", body: JSON.stringify({ command, cwd }) },
  );
}

async function consumeFleetSseStream(
  res: Response,
  handlers: {
    onChunk: (type: "stdout" | "stderr" | "meta", text: string) => void;
    onExit?: (code: number) => void;
  },
  signal?: AbortSignal,
): Promise<number> {
  if (!res.ok) {
    throw new Error(await res.text());
  }
  const reader = res.body?.getReader();
  if (!reader) throw new Error("stream unavailable");

  const decoder = new TextDecoder();
  let buffer = "";
  let exitCode = 0;

  const dispatchEvents = (raw: string) => {
    const normalized = raw.replace(/\r\n/g, "\n");
    let rest = normalized;
    while (true) {
      const idx = rest.indexOf("\n\n");
      if (idx === -1) break;
      const block = rest.slice(0, idx);
      rest = rest.slice(idx + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          const payload = JSON.parse(line.slice(5).trim()) as {
            type: string;
            text?: string;
            code?: number;
          };
          if (payload.type === "exit") {
            exitCode = payload.code ?? 0;
            handlers.onExit?.(exitCode);
          } else if (
            payload.text &&
            (payload.type === "stdout" ||
              payload.type === "stderr" ||
              payload.type === "meta")
          ) {
            handlers.onChunk(payload.type, payload.text);
          }
        } catch {
          // skip malformed chunk
        }
      }
    }
    return rest;
  };

  while (true) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const { done, value } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: true });
      buffer = dispatchEvents(buffer);
    }
    if (done) break;
  }
  buffer += decoder.decode();
  dispatchEvents(buffer + "\n\n");
  return exitCode;
}

export async function fleetNodePipelineLogStream(
  nodeId: number,
  handlers: {
    onChunk: (text: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const token = getAuthToken();
  const res = await fetch(`/api/fleet/nodes/${nodeId}/logs/stream`, {
    method: "GET",
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      Accept: "text/event-stream",
    },
    signal,
    cache: "no-store",
  });
  await consumeFleetSseStream(
    res,
    {
      onChunk: (_type, text) => handlers.onChunk(text),
    },
    signal,
  );
}

export async function fleetNodePowerShellStream(
  nodeId: number,
  command: string,
  cwd: string,
  handlers: {
    onChunk: (type: "stdout" | "stderr", text: string) => void;
    onExit?: (code: number) => void;
  },
  signal?: AbortSignal,
): Promise<number> {
  const token = getAuthToken();
  const res = await fetch(`/api/fleet/nodes/${nodeId}/powershell/stream`, {
    method: "POST",
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ command, cwd }),
    signal,
    cache: "no-store",
  });
  return consumeFleetSseStream(
    res,
    {
      onChunk: (type, text) => {
        if (type === "meta") return;
        handlers.onChunk(type, text);
      },
      onExit: handlers.onExit,
    },
    signal,
  );
}

export async function fleetPullProject(nodeId: number, projectId: number) {
  return fleetFetch<{
    ok: boolean;
    slug?: string;
    queued?: boolean;
    project_id?: number;
    source_node?: string;
    local?: boolean;
    started?: boolean;
    reason?: string;
    message?: string;
  }>(`/api/fleet/nodes/${nodeId}/projects/${projectId}/pull-to-main`, {
    method: "POST",
    body: JSON.stringify({ run_assemble: true }),
  });
}
