"use client";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  allowTypeOf, createProposal, describeChangeError, getPromptBase, kindLabel, paramNameRu, promptSourceRu, STEP_OF_NODE_TYPE, TIER_HINT_RU, TIER_LABEL_RU,
  type AllowList, type PromptBase, type Proposal, type ProposalIn, type ProposalKind, type Tier,
} from "@/lib/change-control";
import { cn } from "@/lib/utils";

interface NodeInfo { id: string; type: string; label: string; allowType: string | null }

interface Props {
  projectId: number;
  allow: AllowList | null;
  allowError: string;
  onCreated: (p: Proposal) => void;
  onCancel: () => void;
}

const FIELD = "rounded border border-white/20 bg-transparent px-1.5 py-1 placeholder:text-white/30";
const SELECT = "h-7 rounded border border-white/10 bg-black/40 px-1";
const TIERS: Tier[] = ["T1", "T2", "T0", "T3"];
const OUTPUT_MODES: [string, string][] = [["text", "текст"], ["project_file", "файл проекта"], ["sidecar", "боковой файл"]];
const EMIT_KINDS: [string, string][] = [["result", "результат"], ["reply_txt", "ответ (txt)"], ["analysis", "анализ"], ["inputs", "входные данные"]];

type Meta = Record<string, unknown>;
const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

function Counter({ n, min, max, label }: { n: number; min?: number; max: number; label: string }) {
  const bad = n > max || (min != null && n < min);
  return (
    <span aria-live="polite" data-testid="char-counter" className={cn("tabular-nums", bad ? "text-red-300" : "text-white/50")}>
      {label}: {n.toLocaleString("ru-RU")} из {max.toLocaleString("ru-RU")}{min != null ? ` (минимум ${min})` : ""}
    </span>
  );
}

export function ProposeForm({ projectId, allow, allowError, onCreated, onCancel }: Props) {
  const [tier, setTier] = useState<Tier>("T1");
  const [kind, setKind] = useState<ProposalKind>("step_params");
  const [meta, setMeta] = useState<Meta | null>(null);
  const [nodes, setNodes] = useState<NodeInfo[] | null>(null);
  const [nodeKey, setNodeKey] = useState("");
  const [rationale, setRationale] = useState("");
  // кандидаты
  const [variant, setVariant] = useState("");
  const [variants, setVariants] = useState<string[] | null>(null);
  const [stepValues, setStepValues] = useState<Record<string, string>>({});
  const [outputMode, setOutputMode] = useState("");
  const [emitKinds, setEmitKinds] = useState<string[] | null>(null);
  const [checkFix, setCheckFix] = useState("");
  const [useSnapshot, setUseSnapshot] = useState("");
  const [text, setText] = useState("");
  const [base, setBase] = useState<PromptBase | null>(null);
  const [baseLoading, setBaseLoading] = useState(false);
  const [baseError, setBaseError] = useState("");
  const [ackMissing, setAckMissing] = useState(false);
  const [summary, setSummary] = useState("");
  const [diff, setDiff] = useState("");
  const [path, setPath] = useState("");
  // отправка
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [touched, setTouched] = useState(false);

  // граф проекта: узлы и текущие значения
  useEffect(() => {
    let cancelled = false;
    setMeta(null); setNodes(null);
    void (async () => {
      try {
        const proj = await api.getProject(projectId);
        if (cancelled) return;
        const m = asObj(proj.meta);
        setMeta(m);
        const raw = asObj(m.canvas_graph).nodes;
        const list: NodeInfo[] = Array.isArray(raw)
          ? raw.map((n) => asObj(n)).filter((n) => typeof n.id === "string").map((n) => ({
              id: String(n.id), type: String(n.type ?? ""),
              label: String(asObj(n.data).label ?? n.id), allowType: null,
            }))
          : [];
        setNodes(list);
      } catch {
        if (!cancelled) setNodes([]); // граф недоступен: узел вводится вручную
      }
    })();
    return () => { cancelled = true; };
  }, [projectId]);

  const nodeList = useMemo(
    () => (nodes ?? []).map((n) => ({ ...n, allowType: allowTypeOf(n.type, allow) })).filter((n) => n.allowType),
    [nodes, allow],
  );
  const node = nodeList.find((n) => n.id === nodeKey) ?? null;
  const needsNode = tier === "T1" || tier === "T2";
  const step = node?.allowType ? STEP_OF_NODE_TYPE[node.allowType] : null;

  const tierKinds = useMemo(() => {
    const all = allow?.tiers[tier]?.kinds ?? [];
    if (!needsNode) return all as ProposalKind[];
    if (!node?.allowType) return all as ProposalKind[];
    const onNode = Object.keys(allow?.node_types[node.allowType] ?? {});
    return all.filter((k) => onNode.includes(k)) as ProposalKind[];
  }, [allow, tier, needsNode, node]);

  // вид по умолчанию при смене уровня/ноды
  useEffect(() => {
    if (tierKinds.length && !tierKinds.includes(kind)) setKind(tierKinds[0]);
  }, [tierKinds, kind]);

  // варианты промпта для выбора
  useEffect(() => {
    setVariants(null); setVariant("");
    if (kind !== "prompt_variant" || !step) return;
    let cancelled = false;
    void (async () => {
      try {
        const files = await api.listPromptFiles(step);
        if (!cancelled) setVariants(files.map((f) => f.name));
      } catch { if (!cancelled) setVariants([]); }
    })();
    return () => { cancelled = true; };
  }, [kind, step]);

  // сброс введённого при смене ноды
  useEffect(() => { setStepValues({}); setBase(null); setBaseError(""); setAckMissing(false); }, [nodeKey]);

  const limits = allow?.prompt_limits;
  const stepLimits = step ? allow?.step_param_limits[step] ?? {} : {};
  const currentSteps = step ? asObj(asObj(meta?.node_step_params)[step]) : {};
  const currentOf = (f: string): number | null => {
    const v = Number(currentSteps[f]);
    return Number.isFinite(v) && v > 0 ? v : null;
  };
  const factor = allow?.max_step_param_factor ?? 2;

  /** Текущий базовый промпт ноды даёт сервер (то же разрешение варианта, что у проверок T2). */
  async function loadBase(overwrite: boolean) {
    if (!nodeKey) return;
    setBaseLoading(true); setBaseError(""); setAckMissing(false);
    try {
      const b = await getPromptBase(projectId, nodeKey);
      setBase(b);
      if (b.source !== "missing" && !b.truncated && (overwrite || !text)) setText(b.text);
    } catch (e) {
      setBase(null);
      setBaseError(describeChangeError(e));
    } finally {
      setBaseLoading(false);
    }
  }

  const wantsBase = tier === "T2" && kind === "prompt_text" && Boolean(nodeKey);
  useEffect(() => {
    if (wantsBase) void loadBase(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsBase, nodeKey, projectId]);

  // ---- проверка и сборка candidate ----
  const problems: string[] = [];
  const rat = rationale.trim();
  if (!rat) problems.push("Обоснование обязательно.");
  if (rationale.length > 1000) problems.push("Обоснование длиннее 1000 символов.");
  if (needsNode && !nodeKey) problems.push("Выберите ноду.");

  let candidate: Record<string, unknown> | null = null;
  if (tier === "T0") {
    if (!text.trim()) problems.push("Введите текст подсказки.");
    if (text.length > 4000) problems.push("Подсказка длиннее 4000 символов.");
    candidate = { text };
  } else if (tier === "T3") {
    if (!summary.trim()) problems.push("Опишите суть изменения.");
    if (summary.length > 500) problems.push("Суть длиннее 500 символов.");
    if (!diff.trim()) problems.push("Вставьте diff.");
    if (diff.length > 100000) problems.push("Diff длиннее 100000 символов.");
    candidate = { summary, diff, ...(path.trim() ? { path: path.trim() } : {}) };
  } else if (kind === "prompt_variant") {
    if (!variant) problems.push("Выберите вариант промпта.");
    candidate = { variant };
  } else if (kind === "prompt_text") {
    const min = limits?.min_chars ?? 40;
    const max = limits?.max_chars ?? 200000;
    if (text.trim().length < min) problems.push(`Текст короче ${min} символов.`);
    if (text.length > max) problems.push(`Текст длиннее ${max} символов.`);
    if (base && base.source !== "missing" && limits) {
      const ratio = text.length / Math.max(1, base.text_chars);
      if (ratio < limits.min_ratio) problems.push(`Текст меньше ${Math.round(limits.min_ratio * 100)}% от текущего (${base.text_chars} симв.).`);
      if (ratio > limits.max_ratio) problems.push(`Текст больше ${limits.max_ratio}× от текущего (${base.text_chars} симв.).`);
    }
    if (base?.source === "missing" && !ackMissing) problems.push("Текущий промпт не найден: подтвердите, что вставляете полный текст осознанно.");
    candidate = { text };
  } else if (kind === "step_params") {
    const values: Record<string, number> = {};
    for (const [f, raw] of Object.entries(stepValues)) {
      if (raw.trim() === "") continue;
      const n = Number(raw);
      const spec = stepLimits[f];
      if (!Number.isFinite(n)) { problems.push(`${paramNameRu(f)}: нужно число.`); continue; }
      if (spec?.type === "integer" && !Number.isInteger(n)) problems.push(`${paramNameRu(f)}: нужно целое число.`);
      if (spec && (n < spec.min || n > spec.max)) problems.push(`${paramNameRu(f)}: допустимо от ${spec.min} до ${spec.max}.`);
      const cur = currentOf(f);
      if (cur != null && (n < cur / factor || n > cur * factor)) problems.push(`${paramNameRu(f)}: за раз можно менять не более чем в ${factor} раза (сейчас ${cur}).`);
      if (cur != null && n === cur) problems.push(`${paramNameRu(f)}: значение уже ${cur}.`);
      values[f] = n;
    }
    if (Object.keys(values).length === 0) problems.push("Укажите хотя бы один параметр.");
    candidate = { step, values };
  } else if (kind === "node_options") {
    const values: Record<string, unknown> = {};
    if (outputMode) values.outputMode = outputMode;
    if (emitKinds) { values.emitKinds = emitKinds; if (emitKinds.length === 0) problems.push("Набор «что выдаёт нода» не может быть пустым."); }
    if (checkFix) values.checkFix = checkFix === "yes";
    if (useSnapshot) values.useSnapshot = useSnapshot === "yes";
    if (Object.keys(values).length === 0) problems.push("Измените хотя бы одну настройку.");
    candidate = { values };
  }

  // Подсказка по подстановкам: только предупреждение, окончательно решает сервер.
  const warnings: string[] = [];
  if (tier === "T2" && kind === "prompt_text" && base && base.source !== "missing" && !base.truncated) {
    const ph = (t: string) => new Set([...(t.match(/\{\{[A-Za-z0-9_:]+\}\}/g) ?? []), ...["[ВСТАВЬ ТЕМУ]", "(тема ролика)"].filter((m) => t.includes(m))]);
    const have = ph(text);
    const was = ph(base.text);
    const lost = [...was].filter((x) => !have.has(x));
    const added = [...have].filter((x) => !was.has(x));
    if (lost.length) warnings.push(`Похоже, потеряны обязательные подстановки: ${lost.join(", ")}. Сервер откажет в такой замене.`);
    if (added.length) warnings.push(`Похоже, добавлены новые подстановки: ${added.join(", ")}. Сервер их не разрешает.`);
  }

  async function submit() {
    if (!candidate || problems.length) return;
    setBusy(true); setError("");
    const body: ProposalIn = { tier, kind, rationale: rat, candidate, project_id: projectId, ...(needsNode ? { node_key: nodeKey } : {}) };
    try {
      const created = await createProposal(body);
      onCreated(created);
    } catch (e) {
      setError(describeChangeError(e));
    } finally {
      setBusy(false);
    }
  }

  const minPrompt = limits?.min_chars ?? 40;
  const maxPrompt = limits?.max_chars ?? 200000;

  return (
    <form data-testid="propose-form" aria-label="Новое предложение изменения" className="space-y-2 rounded border border-white/10 p-2"
      onChange={() => { setTouched(true); setError(""); }}
      onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <h5 className="font-semibold text-white/85">Новое предложение · ничего не применяется</h5>
      <p className="text-white/55">Предложение только записывается. Чтобы оно вступило в силу, его нужно отдельно применить и подтвердить.</p>
      {allowError && <p role="alert" className="text-red-300">Список разрешённых изменений недоступен: {allowError}. Форма может отказать при отправке.</p>}

      <div className="flex flex-wrap items-end gap-3">
        <label>
          <span className="block text-white/60">Уровень</span>
          <select aria-label="Уровень" value={tier} className={SELECT} onChange={(e) => setTier(e.target.value as Tier)}>
            {TIERS.map((t) => <option key={t} value={t}>{t} · {TIER_LABEL_RU[t]}</option>)}
          </select>
        </label>
        {tierKinds.length > 0 && (
          <label>
            <span className="block text-white/60">Вид изменения</span>
            <select aria-label="Вид изменения" value={kind} className={SELECT} onChange={(e) => setKind(e.target.value as ProposalKind)}>
              {tierKinds.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
            </select>
          </label>
        )}
        {needsNode && (
          <label>
            <span className="block text-white/60">Нода</span>
            {nodes === null ? <span className="text-white/45">загружаю граф…</span> : nodeList.length > 0 ? (
              <select aria-label="Нода" value={nodeKey} className={SELECT} onChange={(e) => setNodeKey(e.target.value)}>
                <option value="">выберите ноду</option>
                {nodeList.map((n) => <option key={n.id} value={n.id}>{n.label === n.id ? n.id : `${n.label} (${n.id})`}</option>)}
              </select>
            ) : (
              <input aria-label="Ключ ноды" value={nodeKey} onChange={(e) => setNodeKey(e.target.value.trim())} placeholder="например n_plan" className={cn(FIELD, "h-7 w-40")} />
            )}
          </label>
        )}
      </div>
      <p className="text-white/45">{TIER_HINT_RU[tier]}</p>
      {needsNode && nodes !== null && nodeList.length === 0 && (
        <p className="text-amber-200">Подходящих нод в графе проекта не найдено (разрешены Каркас, Площадка, Шоты, Работа с GPT) или граф недоступен: введите ключ ноды вручную, сервер проверит его.</p>
      )}

      {/* кандидат */}
      {tier === "T0" && (
        <label className="block">
          <span className="text-white/60">Текст подсказки</span>
          <textarea aria-label="Текст подсказки" rows={3} value={text} onChange={(e) => setText(e.target.value)} className={cn(FIELD, "mt-0.5 block w-full")} />
          <Counter n={text.length} max={4000} label="Символов" />
        </label>
      )}

      {tier === "T3" && (
        <div className="space-y-1.5">
          <label className="block">
            <span className="text-white/60">Суть изменения</span>
            <input aria-label="Суть изменения" value={summary} onChange={(e) => setSummary(e.target.value)} className={cn(FIELD, "mt-0.5 block w-full")} />
            <Counter n={summary.length} max={500} label="Символов" />
          </label>
          <label className="block">
            <span className="text-white/60">Файл (необязательно)</span>
            <input aria-label="Файл" value={path} onChange={(e) => setPath(e.target.value)} maxLength={300} placeholder="app/services/…" className={cn(FIELD, "mt-0.5 block w-full max-w-md font-mono")} />
          </label>
          <label className="block">
            <span className="text-white/60">Diff для разработчика</span>
            <textarea aria-label="Diff" rows={5} value={diff} onChange={(e) => setDiff(e.target.value)} className={cn(FIELD, "mt-0.5 block w-full font-mono text-[11px]")} />
            <Counter n={diff.length} max={100000} label="Символов" />
          </label>
          <p className="text-white/50">Это запись для разработчика: она не применяется автоматически никогда. Ключи, пароли и токены в diff сервер отклоняет.</p>
        </div>
      )}

      {tier === "T1" && kind === "prompt_variant" && (
        <label className="block">
          <span className="text-white/60">Готовый вариант промпта</span>
          {!step ? <span className="ml-2 text-white/45">сначала выберите ноду</span> : variants === null ? <span className="ml-2 text-white/45">загружаю…</span> : variants.length > 0 ? (
            <select aria-label="Вариант промпта" value={variant} className={cn(SELECT, "ml-2")} onChange={(e) => setVariant(e.target.value)}>
              <option value="">выберите вариант</option>
              {variants.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          ) : (
            <input aria-label="Вариант промпта" value={variant} onChange={(e) => setVariant(e.target.value.trim())} placeholder="имя существующего варианта" className={cn(FIELD, "ml-2 h-7")} />
          )}
        </label>
      )}

      {tier === "T1" && kind === "step_params" && (
        <div className="space-y-1">
          {!step ? <p className="text-white/45">Сначала выберите ноду.</p> : Object.entries(stepLimits).map(([f, spec]) => {
            const cur = currentOf(f);
            return (
              <label key={f} className="flex flex-wrap items-center gap-2">
                <span className="w-56 text-white/70">{paramNameRu(f)}</span>
                <input aria-label={paramNameRu(f)} type="number" inputMode="decimal" min={spec.min} max={spec.max} value={stepValues[f] ?? ""}
                  onChange={(e) => setStepValues((v) => ({ ...v, [f]: e.target.value }))} className={cn(FIELD, "h-7 w-28")} placeholder="не менять" />
                <span className="text-white/45">
                  {cur != null ? `сейчас ${cur}; допустимо ${Math.max(spec.min, Math.ceil(cur / factor))}–${Math.min(spec.max, Math.floor(cur * factor))}` : `сейчас не задано (менять нельзя); диапазон ${spec.min}–${spec.max}`}
                </span>
              </label>
            );
          })}
          {step && <p className="text-white/45">Менять можно только параметр, у которого уже есть значение, и не более чем в {factor} раза за одно применение.</p>}
        </div>
      )}

      {tier === "T1" && kind === "node_options" && (
        <div className="space-y-1">
          <label className="flex flex-wrap items-center gap-2">
            <span className="w-56 text-white/70">Режим вывода</span>
            <select aria-label="Режим вывода" value={outputMode} className={SELECT} onChange={(e) => setOutputMode(e.target.value)}>
              <option value="">не менять</option>
              {OUTPUT_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-56 text-white/70">Что выдаёт нода</span>
            <label className="flex items-center gap-1"><input type="checkbox" checked={emitKinds !== null} onChange={(e) => setEmitKinds(e.target.checked ? ["result"] : null)} />менять</label>
            {emitKinds && EMIT_KINDS.map(([v, l]) => (
              <label key={v} className="flex items-center gap-1">
                <input type="checkbox" checked={emitKinds.includes(v)} onChange={(e) => setEmitKinds((cur) => (cur ?? []).filter((x) => x !== v).concat(e.target.checked ? [v] : []))} />{l}
              </label>
            ))}
          </div>
          {([["checkFix", "Правка по результатам проверки", checkFix, setCheckFix], ["useSnapshot", "Использовать снимок", useSnapshot, setUseSnapshot]] as const).map(([f, l, val, set]) => (
            <label key={f} className="flex flex-wrap items-center gap-2">
              <span className="w-56 text-white/70">{l}</span>
              <select aria-label={l} value={val} className={SELECT} onChange={(e) => set(e.target.value)}>
                <option value="">не менять</option><option value="yes">да</option><option value="no">нет</option>
              </select>
            </label>
          ))}
        </div>
      )}

      {tier === "T2" && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-white/60">Полный новый текст промпта</span>
            <Button type="button" size="sm" variant="outline" className="h-6 text-xs" disabled={!nodeKey || baseLoading || !base || base.source === "missing" || base.truncated}
              onClick={() => { if (base) setText(base.text); }}>
              Вернуть текущий текст
            </Button>
          </div>
          {!nodeKey && <p className="text-white/45">Выберите ноду: текущий промпт подставится с сервера.</p>}
          {baseLoading && <p className="text-white/50">Читаю текущий промпт ноды…</p>}
          {baseError && (
            <p role="alert" data-testid="base-error" className="text-red-300">
              Не удалось получить текущий промпт: {baseError} Введите полный текст вручную; сервер сверит его с действительно текущим.
            </p>
          )}
          {base && (
            <div data-testid="base-info" className="space-y-0.5 text-white/60">
              <p>
                Текущий промпт: {base.variant ? `вариант «${base.variant}»` : "вариант не определён"} · источник: {promptSourceRu(base.source)} · {base.text_chars.toLocaleString("ru-RU")} симв.
              </p>
              {base.source !== "missing" && !base.truncated && <p className="text-white/45">Текст подставлен с сервера; правьте его. Сервер сверит основу по контрольной сумме и откажет, если текущий промпт успел измениться.</p>}
              {base.truncated && (
                <p role="note" data-testid="base-truncated" className="text-amber-200">
                  Текущий текст слишком длинный и отдан сервером не целиком, поэтому не подставлен. Введите полный новый текст вручную.
                </p>
              )}
              {base.source === "missing" && (
                <div role="note" data-testid="base-missing" className="space-y-1 rounded border border-amber-400/40 bg-amber-500/10 p-1.5 text-amber-50">
                  <p className="font-semibold">Текущий промпт не найден.</p>
                  <p>{base.missing_reason || "Файл выбранного варианта отсутствует или не читается."}</p>
                  <p>Подставлять нечего: размер нового текста сравнить не с чем, а новые подстановки вида {"{{...}}"} сервер всё равно отклонит. Вставьте полный текст осознанно.</p>
                  <label className="flex items-center gap-1.5">
                    <input type="checkbox" checked={ackMissing} onChange={(e) => setAckMissing(e.target.checked)} />
                    Понимаю, что текущего текста нет, и вставляю полный текст вручную
                  </label>
                </div>
              )}
            </div>
          )}
          <textarea aria-label="Текст промпта" rows={8} value={text} onChange={(e) => setText(e.target.value)} className={cn(FIELD, "block w-full font-mono text-[11px]")} />
          <div className="flex flex-wrap gap-x-4">
            <Counter n={text.length} min={minPrompt} max={maxPrompt} label="Символов" />
            {limits && <span className="text-white/45">не меньше {Math.round(limits.min_ratio * 100)}% и не больше {limits.max_ratio}× от текущего{base && base.source !== "missing" ? ` (${base.text_chars} симв. сейчас)` : ""}</span>}
          </div>
          <p className="text-white/45">Все подстановки вида {"{{...}}"} и маркеры формата ответа должны остаться, новых подстановок быть не должно. Это проверит сервер.</p>
        </div>
      )}

      <label className="block">
        <span className="text-white/60">Обоснование (обязательно)</span>
        <textarea aria-label="Обоснование" rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} className={cn(FIELD, "mt-0.5 block w-full")} placeholder="зачем это нужно и что должно улучшиться" />
        <Counter n={rationale.length} max={1000} label="Символов" />
      </label>

      {touched && problems.length > 0 && (
        <ul className="list-disc pl-4 text-amber-200" data-testid="form-problems">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
      {warnings.length > 0 && (
        <ul className="list-disc pl-4 text-amber-200" data-testid="form-warnings">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      )}
      {error && <p role="alert" data-testid="form-error" className="rounded border border-red-400/30 bg-red-500/10 p-1.5 text-red-200">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="outline" className="h-7 text-xs" disabled={busy || problems.length > 0 || !candidate}>
          {busy ? "Записываю…" : "Записать предложение"}
        </Button>
        <Button type="button" size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onCancel}>Закрыть форму</Button>
      </div>
    </form>
  );
}
