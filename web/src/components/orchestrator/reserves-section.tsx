"use client";
import {useCallback, useEffect, useState} from "react";
import {ApiError} from "@/lib/api";
import {listUnresolvedReserves, releaseReserve, type UnresolvedReserve} from "@/lib/action-layer";
import {Button} from "@/components/ui/button";

function errorText(e: unknown): string {
  if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
    const d = (e.detail as {detail?: {message?: string}}).detail ?? (e.detail as {message?: string});
    if (d && typeof d.message === "string") return d.message;
  }
  return "Сервер отклонил снятие резерва. Обновите список и повторите.";
}

export function ReservesSection() {
  const [items, setItems] = useState<UnresolvedReserve[] | null>(null);
  const [total, setTotal] = useState(0);
  const [reason, setReason] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await listUnresolvedReserves();
      setItems(r.items); setTotal(r.total_held_credits); setError("");
    } catch {
      setError("Список резервов недоступен.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await load();
      if (!cancelled) timer = setTimeout(tick, 15000);
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [load]);

  if (!items || items.length === 0) return error ? <p role="alert" className="mt-3 text-xs text-amber-200">{error}</p> : null;

  async function release(r: UnresolvedReserve) {
    const key = `${r.plan_id}#${r.step_index}`;
    const why = (reason[key] ?? "").trim();
    if (why.length < 3) return;
    if (!window.confirm(`Снять резерв ${r.held_credits} кр. по шагу «${r.action_id}»? Вы сверили кабинет провайдера?`)) return;
    setBusy(key);
    try {
      const res = await releaseReserve(r.plan_id, r.step_index, why);
      setNote(`Резерв снят: ${res.released_credits} кр.`);
      setError("");
      setReason((m) => ({...m, [key]: ""}));
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <section data-testid="reserves-panel" className="mt-3 rounded border border-white/10 p-2 space-y-1.5 text-xs text-white/80">
      <h4>Удерживаемые резервы слоя действий · {total} кр.</h4>
      <p className="text-white/45">
        Работа этих шагов шла в воркере, итог в учёт не попал, поэтому сумма удерживается в потолке. Проверьте списания в кабинете
        провайдера и только потом снимайте резерв (деньги это не возвращает).
      </p>
      <ul className="space-y-1.5">
        {items.map((r) => {
          const key = `${r.plan_id}#${r.step_index}`;
          return (
            <li key={key} className="rounded border border-white/10 p-1.5 space-y-1 break-words">
              <div>{r.action_id} · план {r.plan_id.slice(0, 8)} · шаг {r.step_index + 1} · {r.held_credits} кр. · {r.status}</div>
              <div className="flex flex-wrap items-center gap-2">
                <input aria-label={`Причина снятия резерва: ${key}`} value={reason[key] ?? ""} maxLength={500}
                  onChange={(e) => setReason((m) => ({...m, [key]: e.target.value}))}
                  placeholder="что проверили в кабинете (не короче 3 символов)"
                  className="min-w-0 flex-1 basis-48 rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30"/>
                <Button size="sm" variant="outline" data-testid={`release-${key}`}
                  disabled={busy !== "" || (reason[key] ?? "").trim().length < 3} onClick={() => void release(r)}>
                  Снять резерв
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {note && <p role="status" className="text-emerald-200">{note}</p>}
      {error && <p role="alert" className="text-amber-200">{error}</p>}
    </section>
  );
}
