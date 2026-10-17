"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, CircleAlert, Eye, EyeOff, Loader2, Lock, Mail, MailCheck, User, AtSign, ImageIcon } from "lucide-react";

export const AUTH_TOKEN_KEY = "vp-auth-token";
const A = "#2dd4bf";
const FONT = { fontFamily: "Rubik, sans-serif" } as const;

export type AuthUser = { id: number; email: string; username: string; name: string; role: string; email_verified: boolean; created_at: number; last_login?: number | null };

export function getToken(): string | null {
  try { return localStorage.getItem(AUTH_TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try {
    const secure = typeof location !== "undefined" && location.protocol === "https:" ? "; secure" : "";
    if (t) { localStorage.setItem(AUTH_TOKEN_KEY, t); document.cookie = `vp_session=${t}; path=/; max-age=${86400 * 7}; samesite=lax${secure}`; }
    else { localStorage.removeItem(AUTH_TOKEN_KEY); document.cookie = `vp_session=; path=/; max-age=0${secure}`; }
  } catch {}
}
export async function authApi<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const t = getToken();
  const r = await fetch(`/api/auth${path}`, {
    method: method ?? (body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data: any = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? "Проверьте поля формы" : `Ошибка ${r.status}`);
  }
  return data as T;
}
export async function logout() {
  try { await authApi("/logout", {}); } catch {}
  setToken(null);
}
export function useAuthUser() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!getToken()) { setLoading(false); return; }
    authApi<{ user: AuthUser }>("/me").then((r) => setUser(r.user)).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);
  return { user, loading, setUser };
}

/* ---------- validation ---------- */
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const USER_RE = /^[a-zA-Z0-9_.-]{3,32}$/;
function pwScore(p: string) {
  let s = 0;
  if (p.length >= 8) s++;
  if (p.length >= 12) s++;
  if (/[a-zа-я]/.test(p) && /[A-ZА-Я]/.test(p)) s++;
  if (/\d/.test(p)) s++;
  if (/[^A-Za-zА-Яа-я0-9]/.test(p)) s++;
  return Math.min(4, s);
}
function pwValid(p: string) { return p.length >= 8 && /[A-Za-zА-Яа-я]/.test(p) && /\d/.test(p); }

/* ---------- UI ---------- */
function Shell({ title, sub, children, foot }: { title: string; sub?: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="min-h-screen w-full bg-[#070909] text-white" style={FONT}>
      <style>{`@keyframes viaIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}@keyframes viaGlow{0%,100%{opacity:.35}50%{opacity:.6}}.via-in{animation:viaIn .45s cubic-bezier(.2,.8,.2,1) both}`}</style>
      <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
        <aside className="relative hidden overflow-hidden border-r border-white/[0.06] lg:block">
          <div className="absolute inset-0" style={{ background: `radial-gradient(60% 50% at 30% 30%, ${A}22, transparent 70%), radial-gradient(50% 40% at 80% 90%, ${A}14, transparent 70%)`, animation: "viaGlow 6s ease-in-out infinite" }} />
          <div className="relative flex h-full flex-col p-12">
            <a href="/home" className="flex items-center gap-3 text-[28px] font-bold tracking-wide"><span className="grid h-11 w-11 place-items-center rounded-[12px] text-[18px] text-black" style={{ background: A }}>V</span>VIA</a>
            <div className="mt-auto">
              <div className="mb-8 grid grid-cols-2 gap-3">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className={`flex items-center justify-center rounded-[16px] border border-dashed border-white/10 bg-white/[0.02] text-zinc-600 ${i === 0 ? "col-span-2 aspect-[16/7]" : "aspect-[4/3]"}`}>
                    <div className="flex flex-col items-center gap-2 text-[13px]"><ImageIcon className="h-6 w-6" />изображение</div>
                  </div>
                ))}
              </div>
              <h2 className="text-[40px] font-semibold leading-[1.1]">Контент-завод<br /><span style={{ color: A }}>мультфильмов и сериалов</span></h2>
              <p className="mt-4 max-w-md text-[17px] text-zinc-400">Сценарий, персонажи, кадры, анимация, озвучка и монтаж — в одном пайплайне.</p>
            </div>
          </div>
        </aside>
        <main className="flex items-center justify-center px-6 py-12">
          <div className="via-in w-full max-w-[460px]">
            <a href="/home" className="mb-8 flex items-center gap-3 text-[24px] font-bold lg:hidden"><span className="grid h-10 w-10 place-items-center rounded-[12px] text-black" style={{ background: A }}>V</span>VIA</a>
            <h1 className="text-[36px] font-semibold leading-tight">{title}</h1>
            {sub ? <p className="mt-2 text-[17px] text-zinc-400">{sub}</p> : null}
            <div className="mt-8">{children}</div>
            {foot ? <div className="mt-8 text-[16px] text-zinc-400">{foot}</div> : null}
          </div>
        </main>
      </div>
    </div>
  );
}

function Field({ label, icon, value, onChange, type = "text", error, ok, placeholder, autoComplete, name, reveal, hint }: {
  label: string; icon: React.ReactNode; value: string; onChange: (v: string) => void; type?: string; error?: string | null; ok?: boolean;
  placeholder?: string; autoComplete?: string; name: string; reveal?: boolean; hint?: React.ReactNode;
}) {
  const [show, setShow] = useState(false);
  const t = reveal ? (show ? "text" : "password") : type;
  const border = error ? "border-red-500/60" : ok ? "border-teal-400/40" : "border-white/10 focus-within:border-teal-400/50";
  return (
    <label className="mb-5 block">
      <span className="mb-2 block text-[15px] text-zinc-300">{label}</span>
      <span className={`flex h-[56px] items-center gap-3 rounded-[14px] border bg-white/[0.03] px-4 transition-colors duration-200 ${border}`}>
        <span className="text-zinc-500">{icon}</span>
        <input name={name} type={t} value={value} placeholder={placeholder} autoComplete={autoComplete} onChange={(e) => onChange(e.target.value)}
          className="h-full min-w-0 flex-1 bg-transparent text-[17px] text-white outline-none placeholder:text-zinc-600" />
        {ok && !error ? <Check className="h-5 w-5" style={{ color: A }} /> : null}
        {reveal ? (
          <button type="button" aria-label={show ? "Скрыть пароль" : "Показать пароль"} onClick={() => setShow((s) => !s)} className="text-zinc-500 transition-colors hover:text-zinc-200">
            {show ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
          </button>
        ) : null}
      </span>
      {error ? <span className="mt-1.5 flex items-center gap-1.5 text-[14px] text-red-400"><CircleAlert className="h-4 w-4" />{error}</span> : hint ? <span className="mt-1.5 block text-[14px] text-zinc-500">{hint}</span> : null}
    </label>
  );
}

function Strength({ pw }: { pw: string }) {
  const s = pwScore(pw);
  const labels = ["Очень слабый", "Слабый", "Средний", "Хороший", "Надёжный"];
  const colors = ["#ef4444", "#f97316", "#eab308", "#5eead4", A];
  if (!pw) return null;
  return (
    <div className="-mt-3 mb-5">
      <div className="flex gap-1.5">{[0, 1, 2, 3].map((i) => <div key={i} className="h-1.5 flex-1 rounded-full transition-all duration-300" style={{ background: i < Math.max(1, s) ? colors[s] : "rgba(255,255,255,.08)" }} />)}</div>
      <div className="mt-1.5 text-[14px]" style={{ color: colors[s] }}>Надёжность: {labels[s]}</div>
    </div>
  );
}

function Submit({ loading, children, disabled }: { loading?: boolean; children: React.ReactNode; disabled?: boolean }) {
  return (
    <button type="submit" disabled={loading || disabled}
      className="flex h-[56px] w-full items-center justify-center gap-2 rounded-[14px] border text-[17px] font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50"
      style={{ borderColor: A + "55", background: A + "1f", color: "#ccfbf1" }}>
      {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : null}{children}
    </button>
  );
}
function Alert({ kind, children }: { kind: "error" | "ok" | "dev"; children: React.ReactNode }) {
  const st = kind === "error" ? "border-red-500/30 bg-red-500/[0.08] text-red-300" : kind === "ok" ? "border-teal-400/30 bg-teal-400/[0.07] text-teal-200" : "border-amber-400/25 bg-amber-400/[0.06] text-amber-200";
  return <div className={`via-in mb-5 rounded-[14px] border px-4 py-3 text-[15px] leading-relaxed break-words ${st}`}>{children}</div>;
}
const L = ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href} className="transition-colors hover:underline" style={{ color: A }}>{children}</a>;

/* ---------- pages ---------- */
export function LoginPage() {
  const [login, setLogin] = useState(""); const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [ok, setOk] = useState(false);
  const [touched, setTouched] = useState(false);
  const next = typeof window !== "undefined" ? new URLSearchParams(location.search).get("next") || "/profile" : "/profile";
  const reset = typeof window !== "undefined" && new URLSearchParams(location.search).get("reset") === "1";
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setTouched(true); setErr(null);
    if (!login.trim() || !pw) return;
    setLoading(true);
    try {
      const r = await authApi<{ token: string | null }>("/login", { username: login.trim(), password: pw });
      setToken(r.token); setOk(true); setTimeout(() => { location.href = next; }, 700);
    } catch (e: any) { setErr(e.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Вход в VIA" sub="Рады видеть снова" foot={<>Нет аккаунта? <L href="/register">Зарегистрироваться</L></>}>
      <form onSubmit={submit} noValidate>
        {reset ? <Alert kind="ok">Пароль изменён. Войдите с новым паролем.</Alert> : null}
        {err ? <Alert kind="error">{err}</Alert> : null}
        {ok ? <Alert kind="ok">Вход выполнен, открываем студию…</Alert> : null}
        <Field name="login" label="Email или логин" icon={<User className="h-5 w-5" />} value={login} onChange={setLogin} autoComplete="username" placeholder="you@mail.com" error={touched && !login.trim() ? "Введите email или логин" : null} />
        <Field name="password" label="Пароль" icon={<Lock className="h-5 w-5" />} value={pw} onChange={setPw} reveal autoComplete="current-password" placeholder="••••••••" error={touched && !pw ? "Введите пароль" : null} />
        <div className="-mt-2 mb-6 text-right text-[15px]"><L href="/forgot-password">Забыли пароль?</L></div>
        <Submit loading={loading}>Войти</Submit>
        <a href="/" className="mt-4 flex h-[52px] items-center justify-center gap-2 rounded-[14px] border border-white/10 text-[16px] text-zinc-400 transition-colors hover:text-zinc-200"><ArrowLeft className="h-4 w-4" />В студию без входа</a>
      </form>
    </Shell>
  );
}

export function RegisterPage() {
  const [f, setF] = useState({ name: "", username: "", email: "", password: "", confirm: "" });
  const [agree, setAgree] = useState(false);
  const PLANS: Record<string, string> = { start: "Start", studio: "Studio", factory: "Factory" };
  const [plan, setPlan] = useState<string>("");
  useEffect(() => { const p = (new URLSearchParams(location.search).get("plan") || "").toLowerCase(); if (PLANS[p]) setPlan(p); }, []);
  const [touched, setTouched] = useState(false); const [err, setErr] = useState<string | null>(null); const [loading, setLoading] = useState(false);
  const set = (k: keyof typeof f) => (v: string) => setF((p) => ({ ...p, [k]: v }));
  const e = useMemo(() => ({
    username: !f.username ? "Придумайте логин" : !USER_RE.test(f.username) ? "3–32 символа: латиница, цифры, _ . -" : null,
    email: !f.email ? "Введите email" : !EMAIL_RE.test(f.email) ? "Некорректный email" : null,
    password: !f.password ? "Придумайте пароль" : !pwValid(f.password) ? "Минимум 8 символов, буквы и цифры" : null,
    confirm: f.confirm !== f.password || !f.confirm ? "Пароли не совпадают" : null,
    agree: !agree ? "Нужно согласие" : null,
  }), [f, agree]);
  const show = (k: keyof typeof e, v: string) => (touched || v ? e[k] : null);
  async function submit(ev: React.FormEvent) {
    ev.preventDefault(); setTouched(true); setErr(null);
    if (Object.values(e).some(Boolean)) return;
    setLoading(true);
    try {
      const r = await authApi<{ token: string }>("/register", { email: f.email, username: f.username, name: f.name, password: f.password, plan: plan || null });
      setToken(r.token);
      location.href = "/verify-email";
    } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Создать аккаунт" sub="Пара минут — и можно собирать первый ролик" foot={<>Уже есть аккаунт? <L href="/login">Войти</L></>}>
      <form onSubmit={submit} noValidate>
        <div className="mb-6">
          <span className="mb-2 block text-[15px] text-zinc-300">Тариф</span>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(PLANS).map(([k, v]) => (
              <button key={k} type="button" data-plan={k} onClick={() => setPlan(plan === k ? "" : k)}
                className="h-[48px] rounded-[12px] border text-[16px] transition-all duration-200"
                style={plan === k ? { borderColor: A + "77", background: A + "1f", color: "#ccfbf1" } : { borderColor: "rgba(255,255,255,.1)", color: "#a1a1aa" }}>{v}</button>
            ))}
          </div>
          <span className="mt-1.5 block text-[14px] text-zinc-500">{plan ? `Выбран тариф ${PLANS[plan]} — оплата после регистрации` : "Можно выбрать позже в профиле"}</span>
        </div>
        {err ? <Alert kind="error">{err}</Alert> : null}
        <Field name="name" label="Имя (необязательно)" icon={<User className="h-5 w-5" />} value={f.name} onChange={set("name")} autoComplete="name" placeholder="Как к вам обращаться" />
        <Field name="username" label="Логин" icon={<AtSign className="h-5 w-5" />} value={f.username} onChange={set("username")} autoComplete="username" placeholder="studio_admin" error={show("username", f.username)} ok={!e.username} />
        <Field name="email" label="Email" icon={<Mail className="h-5 w-5" />} value={f.email} onChange={set("email")} type="email" autoComplete="email" placeholder="you@mail.com" error={show("email", f.email)} ok={!e.email} />
        <Field name="password" label="Пароль" icon={<Lock className="h-5 w-5" />} value={f.password} onChange={set("password")} reveal autoComplete="new-password" placeholder="Минимум 8 символов" error={show("password", f.password)} />
        <Strength pw={f.password} />
        <Field name="confirm" label="Повторите пароль" icon={<Lock className="h-5 w-5" />} value={f.confirm} onChange={set("confirm")} reveal autoComplete="new-password" error={show("confirm", f.confirm)} ok={!e.confirm} />
        <label className="mb-6 flex cursor-pointer items-start gap-3 text-[15px] text-zinc-400">
          <input type="checkbox" name="agree" checked={agree} onChange={(x) => setAgree(x.target.checked)} className="mt-1 h-4 w-4 accent-teal-400" />
          <span>Принимаю условия использования и политику конфиденциальности{touched && e.agree ? <span className="block text-red-400">{e.agree}</span> : null}</span>
        </label>
        <Submit loading={loading}>Зарегистрироваться</Submit>
      </form>
    </Shell>
  );
}

export function VerifyEmailPage() {
  const [code, setCode] = useState(""); const [err, setErr] = useState<string | null>(null); const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false); const [done, setDone] = useState(false);
  const { user, loading: ul } = useAuthUser();
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    if (!/^\d{6}$/.test(code)) { setErr("Код — 6 цифр"); return; }
    setLoading(true);
    try { await authApi("/verify", { code }); setDone(true); setTimeout(() => (location.href = "/profile"), 1200); }
    catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  async function resend() {
    setErr(null); setMsg(null);
    try { const r = await authApi<{ mail_sent?: boolean }>("/verify/resend", {}); setMsg(r.mail_sent ? "Новый код отправлен" : "Код в интерфейс не выводится"); }
    catch (x: any) { setErr(x.message); }
  }
  return (
    <Shell title="Подтвердите email" sub={user ? `Мы отправили 6-значный код на ${user.email}` : ul ? "…" : "Войдите, чтобы подтвердить email"} foot={<><L href="/">Пропустить и перейти в студию</L></>}>
      <form onSubmit={submit} noValidate>
        {done ? <Alert kind="ok"><span className="flex items-center gap-2"><MailCheck className="h-5 w-5" />Email подтверждён</span></Alert> : null}
        {err ? <Alert kind="error">{err}</Alert> : null}
        {msg ? <Alert kind="ok">{msg}</Alert> : null}
        <input name="code" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="••••••"
          className="mb-6 h-[72px] w-full rounded-[14px] border border-white/10 bg-white/[0.03] text-center text-[34px] tracking-[0.5em] text-white outline-none transition-colors focus:border-teal-400/50" />
        <Submit loading={loading} disabled={!user}>Подтвердить</Submit>
        <button type="button" onClick={resend} disabled={!user} className="mt-4 h-[52px] w-full rounded-[14px] border border-white/10 text-[16px] text-zinc-400 transition-colors hover:text-zinc-200 disabled:opacity-40">Отправить код ещё раз</button>
      </form>
    </Shell>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState(""); const [touched, setTouched] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false); const [res, setRes] = useState<{ dev_note?: string | null; dev_link?: string | null } | null>(null);
  const bad = !email ? "Введите email" : !EMAIL_RE.test(email) ? "Некорректный email" : null;
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setTouched(true); setErr(null);
    if (bad) return;
    setLoading(true);
    try { setRes(await authApi("/forgot", { email })); } catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Восстановление пароля" sub="Пришлём ссылку для смены пароля" foot={<><L href="/login">← Вернуться ко входу</L></>}>
      <form onSubmit={submit} noValidate>
        {err ? <Alert kind="error">{err}</Alert> : null}
        {res ? <Alert kind="ok">Если аккаунт с таким email существует, мы отправили ссылку. Она действует 1 час.</Alert> : null}
        {res?.dev_note ? <Alert kind="dev">Режим разработки: {res.dev_note}.{res.dev_link ? <> <a id="dev-reset-link" href={res.dev_link} className="underline">Открыть ссылку сброса</a></> : null}</Alert> : null}
        <Field name="email" label="Email" icon={<Mail className="h-5 w-5" />} value={email} onChange={setEmail} type="email" autoComplete="email" placeholder="you@mail.com" error={touched ? bad : null} />
        <Submit loading={loading}>{res ? "Отправить ещё раз" : "Отправить ссылку"}</Submit>
      </form>
    </Shell>
  );
}

export function ResetPasswordPage() {
  const [token, setTok] = useState(""); const [valid, setValid] = useState<boolean | null>(null);
  const [pw, setPw] = useState(""); const [c, setC] = useState(""); const [touched, setTouched] = useState(false);
  const [err, setErr] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [done, setDone] = useState(false);
  useEffect(() => {
    const t = new URLSearchParams(location.search).get("token") || "";
    setTok(t);
    if (!t) { setValid(false); return; }
    authApi<{ valid: boolean }>(`/reset/check?token=${encodeURIComponent(t)}`).then((r) => setValid(r.valid)).catch(() => setValid(false));
  }, []);
  const ep = !pwValid(pw) ? "Минимум 8 символов, буквы и цифры" : null;
  const ec = c !== pw || !c ? "Пароли не совпадают" : null;
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setTouched(true); setErr(null);
    if (ep || ec) return;
    setLoading(true);
    try { await authApi("/reset", { token, password: pw }); setDone(true); setToken(null); setTimeout(() => (location.href = "/login?reset=1"), 1200); }
    catch (x: any) { setErr(x.message); } finally { setLoading(false); }
  }
  return (
    <Shell title="Новый пароль" sub="Придумайте надёжный пароль" foot={<><L href="/login">← Вернуться ко входу</L></>}>
      {valid === null ? <div className="flex items-center gap-2 text-zinc-400"><Loader2 className="h-5 w-5 animate-spin" />Проверяем ссылку…</div> : valid === false && !done ? (
        <><Alert kind="error">Ссылка недействительна или устарела.</Alert><a href="/forgot-password"><Submit>Запросить новую ссылку</Submit></a></>
      ) : (
        <form onSubmit={submit} noValidate>
          {err ? <Alert kind="error">{err}</Alert> : null}
          {done ? <Alert kind="ok">Пароль изменён. Переходим ко входу…</Alert> : null}
          <Field name="password" label="Новый пароль" icon={<Lock className="h-5 w-5" />} value={pw} onChange={setPw} reveal autoComplete="new-password" error={touched || pw ? ep : null} />
          <Strength pw={pw} />
          <Field name="confirm" label="Повторите пароль" icon={<Lock className="h-5 w-5" />} value={c} onChange={setC} reveal autoComplete="new-password" error={touched || c ? ec : null} ok={!ec} />
          <Submit loading={loading} disabled={done}>Сохранить пароль</Submit>
        </form>
      )}
    </Shell>
  );
}
