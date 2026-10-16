"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type GateState = "checking" | "open" | "login";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>("checking");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const st = await fetch("/api/auth/status").then((r) => r.json());
        if (!st?.auth_required) {
          if (!cancelled) setState("open");
          return;
        }
        const probe = await fetch("/api/projects");
        if (!cancelled) setState(probe.status === 401 ? "login" : "open");
      } catch {
        if (!cancelled) setState("open");
      }
    })();
    const onAuth = () => setState("login");
    window.addEventListener("vp-auth-required", onAuth);
    return () => {
      cancelled = true;
      window.removeEventListener("vp-auth-required", onAuth);
    };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user.trim(), password }),
      });
      if (res.ok) {
        window.location.reload();
        return;
      }
      if (res.status === 429) {
        const body = await res.json().catch(() => null);
        setError(body?.detail?.message ?? "Слишком много попыток входа. Подождите и повторите.");
      } else if (res.status === 401) {
        setError("Неверное имя или пароль.");
      } else {
        setError(`Вход не удался (код ${res.status}).`);
      }
    } catch {
      setError("Сервер не отвечает.");
    } finally {
      setBusy(false);
    }
  }

  if (state === "open") return <>{children}</>;
  if (state === "checking") return <div className="min-h-screen bg-background" />;
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <form
        onSubmit={submit}
        data-testid="login-form"
        className="w-full max-w-sm space-y-3 rounded-xl border border-white/10 bg-card p-5"
      >
        <h1 className="text-base font-semibold">Вход в Видео студию</h1>
        <Input
          aria-label="Имя"
          placeholder="Имя"
          autoComplete="username"
          value={user}
          onChange={(e) => setUser(e.target.value)}
        />
        <Input
          aria-label="Пароль"
          placeholder="Пароль"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && (
          <p role="alert" className="text-xs text-amber-200">
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy || !user.trim() || !password} className="w-full">
          Войти
        </Button>
      </form>
    </div>
  );
}
