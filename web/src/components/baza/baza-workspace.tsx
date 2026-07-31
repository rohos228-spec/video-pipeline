"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  X,
  Database,
  RefreshCw,
  Plus,
  Trash2,
  Check,
  Link2,
  Film,
  Users,
  Layers,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, type DbFrame, type DbGraph, type DbOverview } from "@/lib/api";

const STATUS_OPTIONS = [
  "planned",
  "image_prompt_ready",
  "image_generated",
  "image_approved",
  "animation_prompt_ready",
  "video_generated",
  "video_approved",
  "failed",
];

const TEXT_KINDS = ["voiceover", "extra", "note"];
const PROMPT_KINDS = ["img", "video", "hero"];
const ENTITY_TYPES = ["character", "background", "prop"];
const EDGE_TYPES = ["next", "continues", "references"];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function BazaWorkspace({ open, onOpenChange }: Props) {
  const [overview, setOverview] = useState<DbOverview | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [graph, setGraph] = useState<DbGraph | null>(null);
  const [frameId, setFrameId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"frames" | "entities">("frames");

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api.dbOverview());
    } catch (e) {
      toast.error(`База: ${e instanceof Error ? e.message : e}`);
    }
  }, []);

  const loadGraph = useCallback(async (pid: number) => {
    setLoading(true);
    try {
      setGraph(await api.dbGraph(pid));
    } catch (e) {
      toast.error(`Граф: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void loadOverview();
  }, [open, loadOverview]);

  useEffect(() => {
    if (open && projectId != null) void loadGraph(projectId);
  }, [open, projectId, loadGraph]);

  const reload = useCallback(async () => {
    await loadOverview();
    if (projectId != null) await loadGraph(projectId);
  }, [loadOverview, loadGraph, projectId]);

  const frame: DbFrame | null = useMemo(
    () => graph?.frames.find((f) => f.id === frameId) ?? null,
    [graph, frameId],
  );

  const framesByScene = useMemo(() => {
    if (!graph) return [];
    const known = new Map(graph.scenes.map((s) => [s.id, s]));
    const groups: { sceneId: number | null; title: string; frames: DbFrame[] }[] = [];
    for (const s of graph.scenes) {
      groups.push({
        sceneId: s.id,
        title: s.title || `Сцена ${s.sort_key}`,
        frames: s.frame_ids
          .map((id) => graph.frames.find((f) => f.id === id))
          .filter((f): f is DbFrame => f != null),
      });
      known.delete(s.id);
    }
    const orphans = graph.frames.filter(
      (f) => !graph.scenes.some((s) => s.frame_ids.includes(f.id)),
    );
    if (orphans.length) groups.push({ sceneId: null, title: "Без сцены", frames: orphans });
    return groups;
  }, [graph]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-[#0a0a0a] text-white">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-white/[0.06] px-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-md p-1.5 text-white/60 hover:bg-white/10 hover:text-white"
            title="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
          <Database className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold">База данных</span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-white/40">
            карточки · дробный порядок · связи
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void reload()} className="gap-2 text-xs">
            <RefreshCw className="h-3.5 w-3.5" />
            Обновить
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Проекты */}
        <aside className="w-64 shrink-0 overflow-y-auto border-r border-white/[0.06] p-2">
          <div className="px-2 pb-2 text-[10px] uppercase tracking-[0.18em] text-white/40">
            Проекты
          </div>
          {(overview?.projects ?? []).map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                setProjectId(p.id);
                setFrameId(null);
              }}
              className={`mb-1 w-full rounded-md px-2.5 py-2 text-left text-xs transition-colors ${
                projectId === p.id ? "bg-primary/15 text-primary" : "text-white/70 hover:bg-white/5"
              }`}
            >
              <div className="font-semibold">
                #{p.id} {p.title || p.slug}
              </div>
              <div className="mt-0.5 text-[10px] text-white/40">
                кадров {p.frames} · сцен {p.scenes} · связей {p.edges}
              </div>
            </button>
          ))}
        </aside>

        {/* Сцены и кадры */}
        <section className="min-w-0 flex-1 overflow-y-auto p-3">
          {projectId == null ? (
            <div className="flex h-full items-center justify-center text-sm text-white/30">
              Выбери проект слева
            </div>
          ) : loading && !graph ? (
            <div className="flex h-full items-center justify-center text-sm text-white/30">
              Загрузка графа…
            </div>
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setTab("frames")}
                  className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs ${
                    tab === "frames" ? "bg-white/10 text-white" : "text-white/50 hover:bg-white/5"
                  }`}
                >
                  <Film className="h-3.5 w-3.5" /> Кадры
                </button>
                <button
                  type="button"
                  onClick={() => setTab("entities")}
                  className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs ${
                    tab === "entities" ? "bg-white/10 text-white" : "text-white/50 hover:bg-white/5"
                  }`}
                >
                  <Users className="h-3.5 w-3.5" /> Сущности ({graph?.entities.length ?? 0})
                </button>
                <div className="flex-1" />
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={async () => {
                    if (projectId == null) return;
                    await api.dbAddScene(projectId, { title: `Сцена ${(graph?.scenes.length ?? 0) + 1}` });
                    toast.success("Сцена добавлена");
                    void reload();
                  }}
                >
                  <Layers className="h-3.5 w-3.5" /> + Сцена
                </Button>
              </div>

              {tab === "frames" ? (
                framesByScene.map((g) => (
                  <div key={g.sceneId ?? "none"} className="mb-4">
                    <div className="mb-1.5 flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/40">
                      <Layers className="h-3 w-3" /> {g.title}
                      <span className="text-white/25">({g.frames.length})</span>
                    </div>
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-2">
                      {g.frames.map((f) => (
                        <FrameCard
                          key={f.id}
                          frame={f}
                          selected={f.id === frameId}
                          onSelect={() => setFrameId(f.id)}
                          onInsertAfter={async () => {
                            if (projectId == null) return;
                            const created = await api.dbInsertFrame(projectId, f.id);
                            toast.success(`Кадр вставлен (sort_key ${created.sort_key})`);
                            setFrameId(created.id);
                            void reload();
                          }}
                        />
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                <EntitiesPanel graph={graph} projectId={projectId} onChanged={reload} />
              )}
            </>
          )}
        </section>

        {/* Детали кадра */}
        <aside className="w-[380px] shrink-0 overflow-y-auto border-l border-white/[0.06] p-3">
          {frame && graph ? (
            <FrameDetails
              frame={frame}
              allFrames={graph.frames}
              onChanged={reload}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-center text-xs text-white/30">
              Выбери карточку кадра,
              <br />
              чтобы настроить её
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function FrameCard({
  frame,
  selected,
  onSelect,
  onInsertAfter,
}: {
  frame: DbFrame;
  selected: boolean;
  onSelect: () => void;
  onInsertAfter: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      className={`group cursor-pointer rounded-md border p-2 text-left transition-colors ${
        selected
          ? "border-primary/50 bg-primary/10"
          : "border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.05]"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-mono text-white/40">
          #{frame.number} · {frame.uuid?.slice(0, 8) ?? "—"} · k={frame.sort_key ?? "?"}
        </span>
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-white/60">
          {frame.status ?? "?"}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 min-h-[2em] text-xs text-white/80">
        {frame.voiceover_text || frame.meaning || <span className="text-white/25">(пусто)</span>}
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[10px] text-white/40">
        <span>
          {frame.duration_seconds != null ? `${frame.duration_seconds.toFixed(1)}с · ` : ""}
          текстов {frame.texts.length} · промтов {frame.prompts.length}
          {frame.edges.length ? ` · →${frame.edges.length}` : ""}
        </span>
        <button
          type="button"
          title="Вставить кадр после этого"
          onClick={(e) => {
            e.stopPropagation();
            onInsertAfter();
          }}
          className="rounded p-1 text-white/40 opacity-0 transition-opacity hover:bg-primary/20 hover:text-primary group-hover:opacity-100"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function FrameDetails({
  frame,
  allFrames,
  onChanged,
}: {
  frame: DbFrame;
  allFrames: DbFrame[];
  onChanged: () => Promise<void>;
}) {
  const [status, setStatus] = useState(frame.status ?? "planned");
  const [duration, setDuration] = useState(String(frame.duration_seconds ?? ""));
  const [voiceover, setVoiceover] = useState(frame.voiceover_text);
  const [meaning, setMeaning] = useState(frame.meaning ?? "");
  const [newText, setNewText] = useState("");
  const [newTextKind, setNewTextKind] = useState("extra");
  const [newPrompt, setNewPrompt] = useState("");
  const [newPromptKind, setNewPromptKind] = useState("img");
  const [edgeTo, setEdgeTo] = useState("");
  const [edgeType, setEdgeType] = useState("next");

  useEffect(() => {
    setStatus(frame.status ?? "planned");
    setDuration(String(frame.duration_seconds ?? ""));
    setVoiceover(frame.voiceover_text);
    setMeaning(frame.meaning ?? "");
    setNewText("");
    setNewPrompt("");
    setEdgeTo("");
  }, [frame.id, frame.status, frame.duration_seconds, frame.voiceover_text, frame.meaning]);

  const save = async (body: Record<string, unknown>, label: string) => {
    try {
      await api.dbPatchFrame(frame.id, body);
      toast.success(label);
      void onChanged();
    } catch (e) {
      toast.error(`${label}: ${e instanceof Error ? e.message : e}`);
    }
  };

  return (
    <div className="flex flex-col gap-4 text-xs">
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-white/40">
          Кадр #{frame.number} · {frame.uuid}
        </div>
        <div className="flex gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            placeholder="сек"
            className="h-8 w-16 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            className="text-xs"
            onClick={() =>
              void save(
                {
                  status,
                  duration_seconds: duration ? Number(duration) : null,
                },
                "Сохранено",
              )
            }
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <LabeledArea
        label="Закадровый текст"
        value={voiceover}
        onChange={setVoiceover}
        onSave={() => void save({ voiceover_text: voiceover }, "Закадр сохранён")}
      />
      <LabeledArea
        label="Смысл кадра"
        value={meaning}
        onChange={setMeaning}
        onSave={() => void save({ meaning }, "Смысл сохранён")}
      />

      {/* Тексты */}
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-white/40">
          Тексты ({frame.texts.length})
        </div>
        {frame.texts.map((t) => (
          <div key={t.id} className="mb-1 flex items-start gap-1.5 rounded-md bg-white/[0.03] p-1.5">
            <span className="mt-0.5 rounded bg-white/10 px-1 text-[9px] uppercase text-white/50">
              {t.kind}
            </span>
            <span className="flex-1 whitespace-pre-wrap text-white/75">{t.text}</span>
            <button
              type="button"
              title="Удалить текст"
              onClick={async () => {
                await api.dbDeleteText(t.id);
                void onChanged();
              }}
              className="rounded p-0.5 text-white/30 hover:text-red-400"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
        <div className="mt-1 flex gap-1.5">
          <select
            value={newTextKind}
            onChange={(e) => setNewTextKind(e.target.value)}
            className="h-8 w-24 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {TEXT_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <input
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            placeholder="доп. текст к кадру…"
            className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={!newText.trim()}
            onClick={async () => {
              await api.dbAddText(frame.id, newTextKind, newText.trim());
              setNewText("");
              toast.success("Текст добавлен");
              void onChanged();
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Промты */}
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-white/40">
          Промты с версиями ({frame.prompts.length})
        </div>
        {frame.prompts.map((p) => (
          <div
            key={p.id}
            className={`mb-1 rounded-md p-1.5 ${p.is_active ? "bg-primary/10 ring-1 ring-primary/30" : "bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-1.5">
              <span className="rounded bg-white/10 px-1 text-[9px] uppercase text-white/50">
                {p.kind} v{p.version}
              </span>
              {p.is_active ? (
                <span className="text-[9px] uppercase text-primary">активна</span>
              ) : (
                <button
                  type="button"
                  className="text-[9px] uppercase text-white/40 hover:text-primary"
                  onClick={async () => {
                    await api.dbActivatePrompt(p.id);
                    void onChanged();
                  }}
                >
                  сделать активной
                </button>
              )}
            </div>
            <div className="mt-1 line-clamp-3 whitespace-pre-wrap text-white/70">{p.text}</div>
          </div>
        ))}
        <div className="mt-1 flex gap-1.5">
          <select
            value={newPromptKind}
            onChange={(e) => setNewPromptKind(e.target.value)}
            className="h-8 w-20 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {PROMPT_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <input
            value={newPrompt}
            onChange={(e) => setNewPrompt(e.target.value)}
            placeholder="новая версия промта…"
            className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            disabled={!newPrompt.trim()}
            onClick={async () => {
              const r = await api.dbAddPrompt(frame.id, newPromptKind, newPrompt.trim());
              setNewPrompt("");
              toast.success(`Версия v${r.version} добавлена и активна`);
              void onChanged();
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Связи */}
      <div>
        <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-white/40">
          Связи ({frame.edges.length})
        </div>
        {frame.edges.map((e) => (
          <div key={e.id} className="mb-1 flex items-center gap-1.5 rounded-md bg-white/[0.03] p-1.5">
            <Link2 className="h-3 w-3 text-white/40" />
            <span className="rounded bg-white/10 px-1 text-[9px] uppercase text-white/50">{e.type}</span>
            <span className="flex-1 text-white/70">→ кадр #{allFrames.find((f) => f.id === e.to_frame_id)?.number ?? e.to_frame_id}</span>
            <button
              type="button"
              title="Удалить связь"
              onClick={async () => {
                await api.dbDeleteEdge(e.id);
                void onChanged();
              }}
              className="rounded p-0.5 text-white/30 hover:text-red-400"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
        <div className="mt-1 flex gap-1.5">
          <select
            value={edgeType}
            onChange={(e) => setEdgeType(e.target.value)}
            className="h-8 w-28 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {EDGE_TYPES.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <select
            value={edgeTo}
            onChange={(e) => setEdgeTo(e.target.value)}
            className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            <option value="">к кадру…</option>
            {allFrames
              .filter((f) => f.id !== frame.id)
              .map((f) => (
                <option key={f.id} value={f.id}>
                  #{f.number} · k={f.sort_key}
                </option>
              ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={!edgeTo}
            onClick={async () => {
              await api.dbAddEdge(frame.id, Number(edgeTo), edgeType);
              toast.success("Связь добавлена");
              void onChanged();
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function LabeledArea({
  label,
  value,
  onChange,
  onSave,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.18em] text-white/40">{label}</span>
        <button type="button" onClick={onSave} className="text-[10px] text-primary hover:underline">
          сохранить
        </button>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        className="w-full rounded-md border border-white/10 bg-black/40 p-2 text-xs text-white/85"
      />
    </div>
  );
}

function EntitiesPanel({
  graph,
  projectId,
  onChanged,
}: {
  graph: DbGraph | null;
  projectId: number | null;
  onChanged: () => Promise<void>;
}) {
  const [type, setType] = useState("character");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");

  return (
    <div className="text-xs">
      <div className="mb-2 flex gap-1.5">
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="h-8 w-32 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
        >
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="код (c01)"
          className="h-8 w-20 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="имя сущности"
          className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
        />
        <Button
          size="sm"
          variant="outline"
          disabled={projectId == null}
          onClick={async () => {
            if (projectId == null) return;
            await api.dbAddEntity(projectId, {
              type,
              code: code || null,
              name: name || null,
              attrs: {},
            });
            setCode("");
            setName("");
            toast.success("Сущность добавлена");
            void onChanged();
          }}
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2">
        {(graph?.entities ?? []).map((en) => (
          <div key={en.id} className="rounded-md border border-white/[0.08] bg-white/[0.02] p-2">
            <div className="flex items-center gap-1.5">
              <span className="rounded bg-white/10 px-1 text-[9px] uppercase text-white/50">
                {en.type}
              </span>
              {en.code ? (
                <span className="font-mono text-[10px] text-white/40">{en.code}</span>
              ) : null}
              <span className="flex-1 font-semibold text-white/80">{en.name ?? "—"}</span>
              <button
                type="button"
                title="Удалить сущность"
                onClick={async () => {
                  await api.dbDeleteEntity(en.id);
                  void onChanged();
                }}
                className="rounded p-0.5 text-white/30 hover:text-red-400"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
        ))}
        {graph && graph.entities.length === 0 ? (
          <div className="text-white/30">Сущностей пока нет — добавь первую выше.</div>
        ) : null}
      </div>
    </div>
  );
}
