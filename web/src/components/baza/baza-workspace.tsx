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
  FileSpreadsheet,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, type DbExcelRow, type DbFrame, type DbGraph } from "@/lib/api";

const STATUS_RU: Record<string, string> = {
  planned: "запланирован",
  image_prompt_ready: "промт картинки готов",
  image_generated: "картинка сгенерирована",
  image_approved: "картинка одобрена",
  animation_prompt_ready: "промт видео готов",
  video_generated: "видео сгенерировано",
  video_approved: "видео одобрено",
  failed: "ошибка",
};
const STATUS_OPTIONS = Object.keys(STATUS_RU);

const TEXT_KIND_RU: Record<string, string> = {
  voiceover: "закадр",
  extra: "доп. текст",
  note: "заметка",
};
const TEXT_KINDS = Object.keys(TEXT_KIND_RU);

const PROMPT_KIND_RU: Record<string, string> = {
  img: "картинка",
  video: "видео",
  hero: "обложка",
};
const PROMPT_KINDS = Object.keys(PROMPT_KIND_RU);

const ENTITY_TYPE_RU: Record<string, string> = {
  character: "персонаж",
  background: "фон",
  prop: "предмет",
};
const ENTITY_TYPES = Object.keys(ENTITY_TYPE_RU);

const EDGE_TYPE_RU: Record<string, string> = {
  next: "следующий",
  continues: "продолжение",
  references: "ссылка",
};
const EDGE_TYPES = Object.keys(EDGE_TYPE_RU);

const ru = (map: Record<string, string>, key: string | null | undefined) =>
  (key && map[key]) || key || "—";

function colLetter(n: number): string {
  let s = "";
  let x = n;
  while (x > 0) {
    const m = (x - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    x = Math.floor((x - 1) / 26);
  }
  return s || String(n);
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number | null;
}

export function BazaWorkspace({ open, onOpenChange, projectId }: Props) {
  const [graph, setGraph] = useState<DbGraph | null>(null);
  const [frameId, setFrameId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"frames" | "entities">("frames");

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
    if (!open) return;
    setFrameId(null);
    if (projectId != null) {
      void loadGraph(projectId);
    } else {
      setGraph(null);
    }
  }, [open, projectId, loadGraph]);

  const reload = useCallback(async () => {
    if (projectId != null) await loadGraph(projectId);
  }, [loadGraph, projectId]);

  const exportXlsx = useCallback(async () => {
    if (projectId == null) return null;
    try {
      return await api.dbExportXlsx(projectId);
    } catch (e) {
      toast.error(`Экспорт в Excel: ${e instanceof Error ? e.message : e}`);
      return null;
    }
  }, [projectId]);

  const handleChanged = useCallback(async () => {
    if (projectId == null) return;
    await exportXlsx();
    await loadGraph(projectId);
  }, [projectId, exportXlsx, loadGraph]);


  const frame: DbFrame | null = useMemo(
    () => graph?.frames.find((f) => f.id === frameId) ?? null,
    [graph, frameId],
  );

  const framesByScene = useMemo(() => {
    if (!graph) return [];
    const groups: { sceneId: number | null; title: string; frames: DbFrame[] }[] = [];
    for (const s of graph.scenes) {
      groups.push({
        sceneId: s.id,
        title: s.title || `Сцена ${s.sort_key}`,
        frames: s.frame_ids
          .map((id) => graph.frames.find((f) => f.id === id))
          .filter((f): f is DbFrame => f != null),
      });
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
          <span className="text-sm font-semibold">База проекта</span>
          {graph ? (
            <span className="text-xs text-white/50">
              #{graph.project.id} {graph.project.title || graph.project.slug} ·{" "}
              {graph.frames.length} кадров
            </span>
          ) : (
            <span className="text-xs text-white/40">текущий проект пайплайна</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={projectId == null}
            className="gap-2 text-xs"
            title="Записать текущую базу в project.xlsx (лист «план»)"
            onClick={async () => {
              const r = await exportXlsx();
              if (r) toast.success(`Экспортировано в Excel: ${r.frames} кадров, ${r.cells} ячеек`);
            }}
          >
            <FileSpreadsheet className="h-3.5 w-3.5" />
            Экспорт в Excel
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void reload()}
            disabled={projectId == null}
            className="gap-2 text-xs"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Обновить
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Сцены и кадры текущего проекта */}
        <section className="min-w-0 flex-1 overflow-y-auto p-3">
          {projectId == null ? (
            <div className="flex h-full items-center justify-center text-center text-sm text-white/30">
              Открой проект в пайплайне —
              <br />
              «База» покажет карточки именно этого проекта.
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
                    void handleChanged();
                  }}
                >
                  <Layers className="h-3.5 w-3.5" /> Добавить сцену
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
                          excelRow={graph?.excel_rows?.[String(f.number)] ?? null}
                          selected={f.id === frameId}
                          onSelect={() => setFrameId(f.id)}
                          onInsertAfter={async () => {
                            if (projectId == null) return;
                            const created = await api.dbInsertFrame(projectId, f.id);
                            toast.success(`Кадр вставлен (ключ ${created.sort_key})`);
                            setFrameId(created.id);
                            void handleChanged();
                          }}
                        />
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                <EntitiesPanel graph={graph} projectId={projectId} onChanged={handleChanged} />
              )}
            </>
          )}
        </section>

        {/* Детали кадра */}
        <aside className="w-[420px] shrink-0 overflow-y-auto border-l border-white/[0.06] p-3">
          {frame && graph ? (
            <FrameDetails
              frame={frame}
              excelRow={graph.excel_rows?.[String(frame.number)] ?? null}
              allFrames={graph.frames}
              onChanged={handleChanged}
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
  excelRow,
  selected,
  onSelect,
  onInsertAfter,
}: {
  frame: DbFrame;
  excelRow: DbExcelRow | null;
  selected: boolean;
  onSelect: () => void;
  onInsertAfter: () => void;
}) {
  const hasImg = Boolean(excelRow?.r45_image_prompt || frame.image_prompt);
  const hasVid = Boolean(excelRow?.r48_video_prompt || frame.animation_prompt);
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
          кадр {frame.number} · кол. {colLetter(frame.number + 2)}
        </span>
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-[9px] tracking-wide text-white/60">
          {ru(STATUS_RU, frame.status)}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 min-h-[2em] text-xs text-white/80">
        {frame.voiceover_text || frame.meaning || <span className="text-white/25">(пусто)</span>}
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[10px] text-white/40">
        <span>
          R45 {hasImg ? "✓" : "—"} · R48 {hasVid ? "✓" : "—"}
          {frame.duration_seconds != null ? ` · ${frame.duration_seconds.toFixed(1)}с` : ""}
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

function ExcelRowsBlock({ row }: { row: DbExcelRow | null }) {
  const items: [string, string | null][] = row
    ? [
        [`R45 · промт картинки 1`, row.r45_image_prompt],
        [`R46 · промт картинки 2`, row.r46_image_prompt_2],
        [`R48 · промт видео 1`, row.r48_video_prompt],
        [`R64 · промт видео 2`, row.r64_video_prompt_2],
        [`R49 · закадровый текст`, row.r49_voiceover],
        [`R50 · время на кадр`, row.r50_duration],
        [`R15 · таймкод`, row.r15_timecode],
        [`R8/23/38 · персонажи`, row.persons],
        [`R9/24/39 · предметы`, row.items],
      ]
    : [];
  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.02] p-2">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-white/40">
        <FileSpreadsheet className="h-3 w-3" />
        Excel-строки кадра {row ? `(колонка ${colLetter(row.column)})` : ""}
      </div>
      {row == null ? (
        <div className="text-[11px] text-white/30">
          project.xlsx не найден или лист «план» пуст — строки не прочитаны.
        </div>
      ) : (
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto pr-1">
          {items.map(([label, value]) => (
            <div key={label} className="rounded bg-black/30 p-1.5">
              <div className="text-[9px] uppercase tracking-wide text-white/35">{label}</div>
              <div className="whitespace-pre-wrap text-[11px] text-white/80">
                {value || <span className="text-white/25">—</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FrameDetails({
  frame,
  excelRow,
  allFrames,
  onChanged,
}: {
  frame: DbFrame;
  excelRow: DbExcelRow | null;
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
          Кадр {frame.number} · колонка {colLetter(frame.number + 2)} · {frame.uuid}
        </div>
        <div className="flex gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="h-8 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {ru(STATUS_RU, s)}
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
            title="Сохранить статус и длительность"
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

      <ExcelRowsBlock row={excelRow} />

      <LabeledArea
        label="Закадровый текст (R49)"
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
          Доп. тексты ({frame.texts.length})
        </div>
        {frame.texts.map((t) => (
          <div key={t.id} className="mb-1 flex items-start gap-1.5 rounded-md bg-white/[0.03] p-1.5">
            <span className="mt-0.5 rounded bg-white/10 px-1 text-[9px] text-white/50">
              {ru(TEXT_KIND_RU, t.kind)}
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
            className="h-8 w-28 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {TEXT_KINDS.map((k) => (
              <option key={k} value={k}>
                {ru(TEXT_KIND_RU, k)}
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
            title="Добавить текст"
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
          Версии промтов ({frame.prompts.length})
        </div>
        {frame.prompts.map((p) => (
          <div
            key={p.id}
            className={`mb-1 rounded-md p-1.5 ${p.is_active ? "bg-primary/10 ring-1 ring-primary/30" : "bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-1.5">
              <span className="rounded bg-white/10 px-1 text-[9px] text-white/50">
                {ru(PROMPT_KIND_RU, p.kind)} · v{p.version}
              </span>
              {p.is_active ? (
                <span className="text-[9px] text-primary">активна</span>
              ) : (
                <button
                  type="button"
                  className="text-[9px] text-white/40 hover:text-primary"
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
            className="h-8 w-24 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {PROMPT_KINDS.map((k) => (
              <option key={k} value={k}>
                {ru(PROMPT_KIND_RU, k)}
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
            title="Добавить версию промта"
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
            <span className="rounded bg-white/10 px-1 text-[9px] text-white/50">
              {ru(EDGE_TYPE_RU, e.type)}
            </span>
            <span className="flex-1 text-white/70">
              → кадр {allFrames.find((f) => f.id === e.to_frame_id)?.number ?? e.to_frame_id}
            </span>
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
            className="h-8 w-32 rounded-md border border-white/10 bg-black/40 px-1 text-xs"
          >
            {EDGE_TYPES.map((k) => (
              <option key={k} value={k}>
                {ru(EDGE_TYPE_RU, k)}
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
                  кадр {f.number}
                </option>
              ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={!edgeTo}
            title="Добавить связь"
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
              {ru(ENTITY_TYPE_RU, t)}
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
          title="Добавить сущность"
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
              <span className="rounded bg-white/10 px-1 text-[9px] text-white/50">
                {ru(ENTITY_TYPE_RU, en.type)}
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
