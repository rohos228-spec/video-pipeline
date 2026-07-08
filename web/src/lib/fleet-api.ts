const TOKEN_KEY = "vp_fleet_token";

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fleetFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export async function fleetLogin(username: string, password: string) {
  return fleetFetch<{ ok: boolean; token?: string | null }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function fetchFleetConfig() {
  return fleetFetch<Record<string, unknown>>("/api/fleet/config");
}

export async function fetchFleetNodes() {
  return fleetFetch<
    Array<{
      id: number;
      name: string;
      base_url: string;
      is_main: boolean;
      role: string;
      status: string;
      last_seen: string | null;
      hostname: string | null;
    }>
  >("/api/fleet/nodes");
}

export async function fleetNodePipeline(nodeId: number) {
  return fleetFetch<{ projects?: unknown[] }>(`/api/fleet/nodes/${nodeId}/pipeline`);
}

export async function fleetPullProject(nodeId: number, projectId: number) {
  return fleetFetch(`/api/fleet/nodes/${nodeId}/projects/${projectId}/pull-to-main`, {
    method: "POST",
    body: JSON.stringify({ run_assemble: true }),
  });
}

export async function fleetPushToHub(projectId: number) {
  return fleetFetch<{ ok: boolean; started?: boolean; size_mb?: number }>(
    `/api/fleet/local/projects/${projectId}/push-to-hub`,
    { method: "POST" },
  );
}

export async function fleetSyncNode(nodeId: number) {
  return fleetFetch(`/api/fleet/nodes/${nodeId}/sync`, { method: "POST" });
}

export async function fleetNodeFiles(nodeId: number, path = ".") {
  return fleetFetch<{
    type: string;
    path: string;
    entries?: Array<{ name: string; type: string; size: number | null }>;
    size?: number;
  }>(`/api/fleet/nodes/${nodeId}/files?path=${encodeURIComponent(path)}`);
}

export async function fleetNodeFileContent(nodeId: number, path: string) {
  return fleetFetch<{ content: string; path: string }>(
    `/api/fleet/nodes/${nodeId}/files/content?path=${encodeURIComponent(path)}`,
  );
}
