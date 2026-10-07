/**
 * Токен оператора (шаг A04): Studio получает его одним GET /api/auth/operator-token и добавляет
 * заголовок X-VP-Operator ко всем изменяющим запросам на /api/*. Сервер требует токен на опасных
 * ручках (approve, run, stop, рубильник, release-unknown, публикация). Чужая страница токен не
 * получит: сервер отдаёт его только своему origin, а CORS ограничен явным списком.
 *
 * Установка: перехватчик window.fetch ставится один раз при импорте модуля (api.ts и fleet-api.ts
 * импортируют его ради побочного эффекта), поэтому сырые fetch() в компонентах тоже покрыты.
 */

const OPERATOR_HEADER = "X-VP-Operator";
const TOKEN_PATH = "/api/auth/operator-token";
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

let cachedToken: string | null = null;
let inflight: Promise<string | null> | null = null;

function sessionAuthHeaders(): Record<string, string> {
  try {
    const t = window.localStorage.getItem("vp_fleet_token");
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

export async function loadOperatorToken(
  force = false,
  rawFetch: typeof fetch = window.fetch.bind(window),
): Promise<string | null> {
  if (cachedToken && !force) return cachedToken;
  if (inflight && !force) return inflight;
  inflight = (async () => {
    try {
      const res = await rawFetch(TOKEN_PATH, { headers: sessionAuthHeaders() });
      if (!res.ok) return null;
      const data = (await res.json()) as { token?: string | null };
      cachedToken = data.token ?? null;
      return cachedToken;
    } catch {
      return null;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function resetOperatorTokenForTests(): void {
  cachedToken = null;
  inflight = null;
}

function requestPath(input: RequestInfo | URL): string | null {
  try {
    const raw =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : (input as Request).url;
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    return url.pathname;
  } catch {
    return null;
  }
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  const fromInit = init?.method;
  if (fromInit) return fromInit.toUpperCase();
  if (typeof Request !== "undefined" && input instanceof Request) return input.method.toUpperCase();
  return "GET";
}

function withToken(init: RequestInit | undefined, token: string): RequestInit {
  const headers = new Headers(init?.headers ?? undefined);
  headers.set(OPERATOR_HEADER, token);
  return { ...init, headers };
}

/** Ставит перехватчик fetch (идемпотентно). */
export function installOperatorFetchGuard(): void {
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;
  const marker = "__vpOperatorGuard";
  const w = window as unknown as Record<string, unknown>;
  if (w[marker]) return;
  w[marker] = true;
  const original = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = requestPath(input);
    const method = requestMethod(input, init);
    if (!path || !path.startsWith("/api/") || path === TOKEN_PATH || SAFE.has(method)) {
      return original(input, init);
    }
    const token = await loadOperatorToken(false, original);
    const first = await original(input, token ? withToken(init, token) : init);
    if (first.status !== 403) return first;
    // Токен мог смениться после перезапуска backend: один раз обновляем и повторяем.
    let code: unknown = null;
    try {
      const body = (await first.clone().json()) as { detail?: { code?: string } };
      code = body?.detail?.code;
    } catch {
      return first;
    }
    if (code !== "operator_token_required") return first;
    const fresh = await loadOperatorToken(true, original);
    if (!fresh || fresh === token) return first;
    if (init?.body && typeof ReadableStream !== "undefined" && init.body instanceof ReadableStream) {
      return first;
    }
    return original(input, withToken(init, fresh));
  };
}

installOperatorFetchGuard();
