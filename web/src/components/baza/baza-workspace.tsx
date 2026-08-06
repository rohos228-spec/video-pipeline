"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  ShieldCheck,
  ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { StudioExcelGrid } from "@/components/studio/studio-excel-grid";
import {
  api,
  type DbExcelRow,
  type DbFrame,
  type DbGraph,
  type XlsxPreview,
} from "@/lib/api";
import {
  SHEET_GENERAL_V8,
  SHEET_PLAN_V8,
  XLSX_STUDIO_MAX_COLS,
  XLSX_STUDIO_MAX_ROWS,
} from "@/lib/xlsx-sheets";

/** Бывшие листы Excel → вкладки «Сущности». */
const ENTITY_SHEETS_DEFAULT = ["Персонажи", "Фоны", "Предметы"] as const;
const ENTITY_SHEET_TO_TYPE: Record<string, string> = {
  персонажи: "character",
  фоны: "background",
  предметы: "prop",
};
/** Листы кадров (остальное из книги), fallback если xlsx ещё нет. */
const FRAME_SHEETS_DEFAULT = [SHEET_GENERAL_V8, SHEET_PLAN_V8] as const;

function isEntitySheetName(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(
    ENTITY_SHEET_TO_TYPE,
    name.trim().toLowerCase(),
  );
}

function splitWorkbookSheets(sheets: string[]): {
  frameSheets: string[];
  entitySheets: string[];
} {
  const entitySheets = sheets.filter(isEntitySheetName);
  const frameSheets = sheets.filter((s) => !isEntitySheetName(s));
  return {
    frameSheets: frameSheets.length ? frameSheets : [...FRAME_SHEETS_DEFAULT],
    entitySheets: entitySheets.length ? entitySheets : [...ENTITY_SHEETS_DEFAULT],
  };
}

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

const EDGE_TYPE_RU: Record<string, string> = {
  next: "следующий",
  continues: "продолжение",
  references: "ссылка",
};
const EDGE_TYPES = Object.keys(EDGE_TYPE_RU);

/** Поля Frame.attrs — источник правды в «Базе» (не Excel). */
const ATTR_FIELD_DEFS: { key: string; label: string; rows?: number }[] = [
  { key: "place", label: "Место" },
  { key: "main_action", label: "Главное действие", rows: 3 },
  { key: "accent", label: "Акцент" },
  { key: "scene_sense", label: "Смысл сцены", rows: 3 },
  { key: "visual_type", label: "Тип сцены" },
  { key: "scene_feature", label: "Особенность сцены" },
  { key: "cluster", label: "Кластер" },
  { key: "characters", label: "Персонажи (коды)" },
  { key: "scene_structure", label: "Структура сцены" },
  { key: "edit_type", label: "Тип стыка" },
  { key: "scene_transition", label: "Переход в сцену" },
  { key: "scene_start_words", label: "Сцена · start words" },
  { key: "scene_end_words", label: "Сцена · end words" },
  { key: "shot01_id_scene", label: "shot01 · id сцены" },
  { key: "shot01_id_shot", label: "shot01 · id шота" },
  { key: "shot01_action", label: "shot01 · действие", rows: 2 },
  { key: "shot01_description", label: "shot01 · описание", rows: 2 },
  { key: "shot01_bg", label: "shot01 · фон" },
  { key: "shot01_props", label: "shot01 · предметы" },
  { key: "shot01_transition", label: "shot01 · логика перехода" },
  { key: "shot01_notes", label: "shot01 · заметки", rows: 2 },
];

const ru = (map: Record<string, string>, key: string | null | undefined) =>
  (key && map[key]) || key || "—";

function attrStr(attrs: Record<string, unknown> | null | undefined, key: string): string {
  const v = attrs?.[key];
  if (v == null) return "";
  return typeof v === "string" ? v : String(v);
}

function frameHasImg(f: DbFrame): boolean {
  if ((f.image_prompt || "").trim()) return true;
  return f.prompts.some((p) => p.kind === "img" && p.is_active && p.text.trim());
}

function frameHasVideo(f: DbFrame): boolean {
  if ((f.animation_prompt || "").trim()) return true;
  return f.prompts.some((p) => p.kind === "video" && p.is_active && p.text.trim());
}

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
  /** Основной вид «Базы» — карточки DB; Excel только зеркало (опционально). */
  const [framesView, setFramesView] = useState<"cards" | "excel">("cards");
  /** Вкладки = бывшие листы Excel (кадры: «Общий план», «план»; сущности: «Персонажи»…). */
  const [frameSheets, setFrameSheets] = useState<string[]>([...FRAME_SHEETS_DEFAULT]);
  const [entitySheets, setEntitySheets] = useState<string[]>([...ENTITY_SHEETS_DEFAULT]);
  const [frameSheet, setFrameSheet] = useState<string>(SHEET_PLAN_V8);
  const [sheetPreview, setSheetPreview] = useState<XlsxPreview | null>(null);
  const [sheetLoading, setSheetLoading] = useState(false);
  const [sheetTick, setSheetTick] = useState(0);
  /** Отбрасываем ответы API от предыдущего проекта / закрытой «Базы». */
  const loadGenRef = useRef(0);

  const loadGraph = useCallback(async (pid: number, gen: number) => {
    setLoading(true);
    try {
      const next = await api.dbGraph(pid);
      if (loadGenRef.current !== gen) return;
      // Жёсткая привязка: в UI только граф запрошенного projectId.
      if (next?.project?.id !== pid) {
        setGraph(null);
        toast.error(`База: ответ API не от проекта #${pid}`);
        return;
      }
      setGraph(next);
    } catch (e) {
      if (loadGenRef.current !== gen) return;
      setGraph(null);
      toast.error(`Граф: ${e instanceof Error ? e.message : e}`);
    } finally {
      if (loadGenRef.current === gen) setLoading(false);
    }
  }, []);

  const loadSheetMeta = useCallback(async (pid: number, gen: number) => {
    try {
      const meta = await api.previewProjectXlsx(pid, {
        maxRows: 1,
        maxCols: 1,
        raw: true,
      });
      if (loadGenRef.current !== gen) return;
      const split = splitWorkbookSheets(meta.sheets ?? []);
      setFrameSheets(split.frameSheets);
      setEntitySheets(split.entitySheets);
      setFrameSheet((prev) =>
        split.frameSheets.includes(prev)
          ? prev
          : split.frameSheets.includes(SHEET_PLAN_V8)
            ? SHEET_PLAN_V8
            : split.frameSheets[0] ?? SHEET_PLAN_V8,
      );
    } catch {
      if (loadGenRef.current !== gen) return;
      setFrameSheets([...FRAME_SHEETS_DEFAULT]);
      setEntitySheets([...ENTITY_SHEETS_DEFAULT]);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      loadGenRef.current += 1;
      setGraph(null);
      setFrameId(null);
      setSheetPreview(null);
      setLoading(false);
      return;
    }
    // Сразу сбрасываем чужой граф — иначе до ответа API видно базу прошлого проекта.
    const gen = ++loadGenRef.current;
    setGraph(null);
    setFrameId(null);
    setSheetPreview(null);
    if (projectId != null) {
      void loadGraph(projectId, gen);
      void loadSheetMeta(projectId, gen);
    } else {
      setLoading(false);
    }
  }, [open, projectId, loadGraph, loadSheetMeta]);

  useEffect(() => {
    if (!open || projectId == null || tab !== "frames" || framesView !== "excel") return;
    let cancelled = false;
    setSheetLoading(true);
    void api
      .previewProjectXlsx(projectId, {
        sheet: frameSheet,
        maxRows: XLSX_STUDIO_MAX_ROWS,
        maxCols: XLSX_STUDIO_MAX_COLS,
        startRow: 1,
        raw: true,
      })
      .then((p) => {
        if (!cancelled) setSheetPreview(p);
      })
      .catch((e) => {
        if (!cancelled) {
          setSheetPreview(null);
          toast.error(`Лист Excel: ${e instanceof Error ? e.message : e}`);
        }
      })
      .finally(() => {
        if (!cancelled) setSheetLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId, tab, frameSheet, sheetTick, framesView]);

  /** Никогда не показываем кадры чужого проекта (защита от stale/race). */
  const scopedGraph = useMemo(() => {
    if (projectId == null || !graph) return null;
    return graph.project.id === projectId ? graph : null;
  }, [graph, projectId]);

  // Автовыбор первого кадра из DB (не из Excel-колонки).
  useEffect(() => {
    if (!scopedGraph?.frames.length) return;
    if (frameId != null && scopedGraph.frames.some((f) => f.id === frameId)) return;
    setFrameId(scopedGraph.frames[0]!.id);
  }, [scopedGraph, frameId]);

  const reload = useCallback(async () => {
    if (projectId == null) return;
    const gen = ++loadGenRef.current;
    setGraph(null);
    await loadGraph(projectId, gen);
    await loadSheetMeta(projectId, gen);
    setSheetTick((t) => t + 1);
  }, [loadGraph, loadSheetMeta, projectId]);

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
    const gen = loadGenRef.current;
    await loadGraph(projectId, gen);
    setSheetTick((t) => t + 1);
  }, [projectId, exportXlsx, loadGraph]);

  const frame: DbFrame | null = useMemo(
    () => scopedGraph?.frames.find((f) => f.id === frameId) ?? null,
    [scopedGraph, frameId],
  );

  const selectFrameByExcelCol = useCallback(
    (colIndex: number) => {
      if (!scopedGraph) return;
      // Лист «план»: кадр N → колонка N+2 (A=подписи, C=кадр1).
      const isPlan =
        frameSheet.trim().toLowerCase() === SHEET_PLAN_V8.toLowerCase() ||
        frameSheet.trim().toLowerCase() === "кадры";
      if (!isPlan) return;
      const frameNumber = colIndex - 1; // 0-based: C=2 → кадр 1
      if (frameNumber < 1) return;
      const f = scopedGraph.frames.find((x) => x.number === frameNumber);
      if (f) setFrameId(f.id);
    },
    [scopedGraph, frameSheet],
  );

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
          {projectId != null ? (
            <span className="text-xs text-white/50">
              #{projectId}
              {graph && graph.project.id === projectId
                ? ` ${graph.project.title || graph.project.slug} · ${graph.frames.length} кадров`
                : loading
                  ? " · загрузка…"
                  : ""}
            </span>
          ) : (
            <span className="text-xs text-white/40">текущий проект пайплайна</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <HarnessChip
            graph={scopedGraph}
            disabled={projectId == null}
            onRun={async () => {
              if (projectId == null) return;
              try {
                const r = await api.runHarnessVerify(projectId);
                const bad = (r.checks ?? []).filter((c) => !c.ok);
                if (bad.length) {
                  toast.error(
                    `Проверки НЕ ОК (${bad.length}): ${bad.map((c) => c.name).join(", ")}`,
                  );
                } else {
                  toast.success("Проверки ОК");
                }
              } catch (e) {
                toast.error(`Проверки: ${e instanceof Error ? e.message : e}`);
              }
              await reload();
            }}
          />
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
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden p-3">
          {projectId == null ? (
            <div className="flex h-full items-center justify-center text-center text-sm text-white/30">
              Открой проект в пайплайне —
              <br />
              «База» покажет карточки именно этого проекта.
            </div>
          ) : loading && !scopedGraph ? (
            <div className="flex h-full items-center justify-center text-sm text-white/30">
              Загрузка базы проекта #{projectId}…
            </div>
          ) : (
            <>
              <div className="mb-2 flex shrink-0 items-center gap-2">
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
                  <Users className="h-3.5 w-3.5" /> Сущности ({scopedGraph?.entities.length ?? 0})
                </button>
                <div className="flex-1" />
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={async () => {
                    if (projectId == null) return;
                    await api.dbAddScene(projectId, {
                      title: `Сцена ${(scopedGraph?.scenes.length ?? 0) + 1}`,
                    });
                    toast.success("Сцена добавлена");
                    void handleChanged();
                  }}
                >
                  <Layers className="h-3.5 w-3.5" /> Добавить сцену
                </Button>
              </div>

              {tab === "frames" ? (
                <div className="flex min-h-0 flex-1 flex-col gap-2">
                  <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                    <span className="mr-1 shrink-0 text-[10px] uppercase tracking-[0.18em] text-white/35">
                      Вид
                    </span>
                    <button
                      type="button"
                      onClick={() => setFramesView("cards")}
                      className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${
                        framesView === "cards"
                          ? "bg-primary/20 text-primary"
                          : "bg-white/[0.04] text-white/55 hover:bg-white/[0.08]"
                      }`}
                    >
                      Карточки DB
                    </button>
                    <button
                      type="button"
                      onClick={() => setFramesView("excel")}
                      className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${
                        framesView === "excel"
                          ? "bg-primary/20 text-primary"
                          : "bg-white/[0.04] text-white/55 hover:bg-white/[0.08]"
                      }`}
                    >
                      Зеркало Excel
                    </button>
                    {framesView === "excel" ? (
                      <>
                        <span className="mx-1 text-white/20">|</span>
                        {frameSheets.map((name) => (
                          <button
                            key={name}
                            type="button"
                            onClick={() => setFrameSheet(name)}
                            className={`shrink-0 rounded-md px-2.5 py-1 text-xs whitespace-nowrap ${
                              frameSheet === name
                                ? "bg-white/10 text-white"
                                : "bg-white/[0.04] text-white/55 hover:bg-white/[0.08]"
                            }`}
                          >
                            {name}
                          </button>
                        ))}
                      </>
                    ) : null}
                  </div>

                  {framesView === "cards" ? (
                    <FramesCardsPanel
                      graph={scopedGraph}
                      frameId={frameId}
                      onSelect={setFrameId}
                      projectId={projectId}
                      onChanged={handleChanged}
                    />
                  ) : sheetLoading && !sheetPreview ? (
                    <div className="flex flex-1 items-center justify-center text-sm text-white/30">
                      Загрузка листа…
                    </div>
                  ) : (sheetPreview?.rows?.length ?? 0) > 0 ? (
                    <StudioExcelGrid
                      className="min-h-0 max-h-none flex-1"
                      rows={sheetPreview?.rows ?? []}
                      startRow={sheetPreview?.start_row ?? 1}
                      colLetters={sheetPreview?.col_letters}
                      nowrap
                      editable
                      onCellClick={(_ri, ci) => selectFrameByExcelCol(ci)}
                      onCellCommit={async (ri, ci, value) => {
                        if (projectId == null) return;
                        const start = sheetPreview?.start_row ?? 1;
                        try {
                          await api.dbPatchSheetCell(projectId, {
                            sheet: frameSheet,
                            row: start + ri,
                            col: ci + 1,
                            value,
                          });
                          toast.success("Ячейка сохранена");
                          setSheetTick((t) => t + 1);
                          void loadGraph(projectId, loadGenRef.current);
                        } catch (e) {
                          toast.error(
                            `Ячейка: ${e instanceof Error ? e.message : e}`,
                          );
                          throw e;
                        }
                      }}
                    />
                  ) : (
                    <div className="flex flex-1 items-center justify-center text-center text-sm text-white/30">
                      Зеркало Excel пусто — это не ошибка.
                      <br />
                      Переключись на «Карточки DB»: данные уже в базе.
                    </div>
                  )}
                </div>
              ) : (
                <div className="min-h-0 flex-1 overflow-hidden">
                  <EntitiesPanel
                    graph={scopedGraph}
                    projectId={projectId}
                    sheetNames={entitySheets}
                    onChanged={handleChanged}
                  />
                </div>
              )}
            </>
          )}
        </section>

        {/* Детали кадра */}
        <aside className="w-[420px] shrink-0 overflow-y-auto border-l border-white/[0.06] p-3">
          {frame && scopedGraph ? (
            <FrameDetails
              frame={frame}
              excelRow={scopedGraph.excel_rows?.[String(frame.number)] ?? null}
              sceneRegistry={scopedGraph.scene_registry ?? []}
              allFrames={scopedGraph.frames}
              onChanged={handleChanged}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-center text-xs text-white/30">
              Выбери карточку кадра слева —
              <br />
              справа правятся поля из DB
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function HarnessChip({
  graph,
  disabled,
  onRun,
}: {
  graph: DbGraph | null;
  disabled: boolean;
  onRun: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const h = graph?.harness;
  const failed = h?.failed ?? [];
  const ok = h?.outcome === "verify_pass";
  const bad = h?.outcome === "verify_fail";
  return (
    <button
      type="button"
      disabled={disabled || busy}
      title={
        h?.updated_at
          ? `Последний прогон: ${h.updated_at}${h?.next_action && h.next_action !== "none" ? ` · ${h.next_action}` : ""}. Нажми — прогнать снова.`
          : "Прогонов проверок ещё не было. Нажми — прогнать."
      }
      onClick={async () => {
        setBusy(true);
        try {
          await onRun();
        } finally {
          setBusy(false);
        }
      }}
      className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] ${
        ok
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          : bad
            ? "border-red-500/30 bg-red-500/10 text-red-300"
            : "border-white/10 bg-white/[0.03] text-white/50"
      }`}
    >
      {ok ? (
        <ShieldCheck className="h-3.5 w-3.5" />
      ) : (
        <ShieldAlert className="h-3.5 w-3.5" />
      )}
      {busy
        ? "проверяю…"
        : ok
          ? `Проверки ОК (${h?.total ?? 0})`
          : bad
            ? `НЕ ОК: ${failed.length}`
            : "Проверки"}
    </button>
  );
}

function FramesCardsPanel({
  graph,
  frameId,
  onSelect,
  projectId,
  onChanged,
}: {
  graph: DbGraph | null;
  frameId: number | null;
  onSelect: (id: number) => void;
  projectId: number | null;
  onChanged: () => Promise<void>;
}) {
  if (!graph) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/30">
        Нет графа
      </div>
    );
  }
  const orphan = graph.frames.filter((f) => f.scene_id == null);
  const groups: { key: string; title: string; frames: DbFrame[] }[] = graph.scenes.map((sc) => ({
    key: `sc-${sc.id}`,
    title: sc.title || sc.place || `Сцена ${sc.sort_key}`,
    frames: graph.frames.filter((f) => f.scene_id === sc.id),
  }));
  if (orphan.length || !groups.length) {
    groups.push({
      key: "orphan",
      title: groups.length ? "Без сцены" : "Все кадры",
      frames: orphan.length ? orphan : graph.frames,
    });
  }
  // Если сцены есть, но кадры все без scene_id — уже добавили. Если кадры в сценах — не дублировать all.
  const shownIds = new Set(groups.flatMap((g) => g.frames.map((f) => f.id)));
  const missing = graph.frames.filter((f) => !shownIds.has(f.id));
  if (missing.length) {
    groups.push({ key: "rest", title: "Остальные", frames: missing });
  }

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
      {graph.frames.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-sm text-white/30">
          В базе нет кадров
        </div>
      ) : null}
      {groups.map((g) =>
        g.frames.length === 0 ? null : (
          <div key={g.key}>
            <div className="mb-1.5 flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/40">
              <Layers className="h-3 w-3" />
              {g.title}
              <span className="text-white/25">{g.frames.length}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
              {g.frames.map((f) => {
                const active = f.id === frameId;
                const place = attrStr(f.attrs, "place");
                const sense = attrStr(f.attrs, "scene_sense");
                return (
                  <div
                    key={f.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(f.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") onSelect(f.id);
                    }}
                    className={`cursor-pointer rounded-md border p-2.5 text-left transition ${
                      active
                        ? "border-primary/50 bg-primary/10"
                        : "border-white/[0.08] bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]"
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] text-white/70">#{f.number}</span>
                      <span className="truncate text-[10px] text-white/35">
                        {ru(STATUS_RU, f.status)}
                      </span>
                      <span className="ml-auto flex gap-1 text-[9px]">
                        <span className={frameHasImg(f) ? "text-emerald-400" : "text-white/25"}>
                          img {frameHasImg(f) ? "✓" : "—"}
                        </span>
                        <span className={frameHasVideo(f) ? "text-emerald-400" : "text-white/25"}>
                          vid {frameHasVideo(f) ? "✓" : "—"}
                        </span>
                      </span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-[11px] text-white/80">
                      {place || f.voiceover_text || "—"}
                    </div>
                    {sense ? (
                      <div className="mt-1 line-clamp-2 text-[10px] text-white/40">{sense}</div>
                    ) : null}
                    <div className="mt-2 flex items-center justify-between">
                      <span className="text-[9px] text-white/30">
                        {attrStr(f.attrs, "cluster")
                          ? `кластер ${attrStr(f.attrs, "cluster")}`
                          : colLetter(f.number + 2)}
                      </span>
                      <button
                        type="button"
                        title="Вставить кадр после"
                        className="rounded p-0.5 text-white/30 hover:bg-white/10 hover:text-white"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (projectId == null) return;
                          void (async () => {
                            await api.dbInsertFrame(projectId, f.id, f.scene_id);
                            toast.success("Кадр добавлен");
                            void onChanged();
                          })();
                        }}
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ),
      )}
    </div>
  );
}

function AttrsEditor({
  frame,
  onSave,
}: {
  frame: DbFrame;
  onSave: (attrs: Record<string, unknown>, label: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const def of ATTR_FIELD_DEFS) {
      next[def.key] = attrStr(frame.attrs, def.key);
    }
    // Любые прочие ключи attrs — тоже показать (read/edit).
    for (const [k, v] of Object.entries(frame.attrs || {})) {
      if (next[k] === undefined) {
        next[k] = typeof v === "string" ? v : v == null ? "" : String(v);
      }
    }
    setDraft(next);
  }, [frame.id, frame.attrs]);

  const known = new Set(ATTR_FIELD_DEFS.map((d) => d.key));
  const extras = Object.keys(draft).filter((k) => !known.has(k)).sort();

  const persist = (keys: string[]) => {
    const merged: Record<string, unknown> = { ...(frame.attrs || {}) };
    for (const k of keys) {
      merged[k] = draft[k] ?? "";
    }
    void onSave(merged, "Поля DB сохранены");
  };

  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.02] p-2">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-white/40">
          <Database className="h-3 w-3" />
          Поля кадра (DB attrs)
        </div>
        <button
          type="button"
          className="text-[10px] text-primary hover:underline"
          onClick={() => persist(Object.keys(draft))}
        >
          сохранить все
        </button>
      </div>
      <div className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto pr-1">
        {ATTR_FIELD_DEFS.map((def) => (
          <div key={def.key}>
            <div className="mb-0.5 flex items-center justify-between">
              <span className="text-[9px] uppercase tracking-wide text-white/35">{def.label}</span>
              <button
                type="button"
                className="text-[9px] text-white/35 hover:text-primary"
                onClick={() => persist([def.key])}
              >
                сохранить
              </button>
            </div>
            <textarea
              value={draft[def.key] ?? ""}
              onChange={(e) => setDraft((d) => ({ ...d, [def.key]: e.target.value }))}
              rows={def.rows ?? 2}
              className="w-full rounded-md border border-white/10 bg-black/40 p-1.5 text-[11px] text-white/85"
            />
          </div>
        ))}
        {extras.map((key) => (
          <div key={key}>
            <div className="mb-0.5 flex items-center justify-between">
              <span className="font-mono text-[9px] text-white/35">{key}</span>
              <button
                type="button"
                className="text-[9px] text-white/35 hover:text-primary"
                onClick={() => persist([key])}
              >
                сохранить
              </button>
            </div>
            <textarea
              value={draft[key] ?? ""}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
              rows={2}
              className="w-full rounded-md border border-white/10 bg-black/40 p-1.5 text-[11px] text-white/85"
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function SceneRegistryBlock({
  registry,
}: {
  registry: NonNullable<DbGraph["scene_registry"]>;
}) {
  if (!registry.length) {
    return (
      <div className="rounded-md border border-dashed border-white/10 bg-white/[0.01] p-2 text-[11px] text-white/30">
        scene_registry пуст — агент scene_grammar ещё не записал сцены по словам в meta.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.02] p-2">
      <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-white/40">
        Сцены по словам ({registry.length})
      </div>
      <div className="flex max-h-48 flex-col gap-1.5 overflow-y-auto pr-1">
        {registry.map((sc, i) => (
          <div key={String(sc.id_scene ?? i)} className="rounded bg-black/30 p-1.5">
            <div className="font-mono text-[10px] text-primary/80">
              {String(sc.id_scene ?? `scene_${i + 1}`)}
              {sc.structure ? ` · ${String(sc.structure)}` : ""}
              {sc.edit_type ? ` · ${String(sc.edit_type)}` : ""}
            </div>
            <div className="mt-0.5 text-[10px] text-white/55">
              «{String(sc.start_words || "—")}» → «{String(sc.end_words || "—")}»
            </div>
            {sc.transition ? (
              <div className="mt-0.5 text-[10px] text-white/35">{String(sc.transition)}</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function ExcelRowsBlock({ row }: { row: DbExcelRow | null }) {
  const [open, setOpen] = useState(false);
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
    <div className="rounded-md border border-white/[0.06] bg-white/[0.01] p-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 text-left text-[10px] uppercase tracking-[0.18em] text-white/35 hover:text-white/55"
      >
        <FileSpreadsheet className="h-3 w-3" />
        Зеркало Excel {row ? `(кол. ${colLetter(row.column)})` : ""} · {open ? "свернуть" : "показать"}
      </button>
      {open ? (
        row == null ? (
          <div className="mt-1 text-[11px] text-white/30">
            Excel пуст — смотри поля DB выше. Экспорт в шапке при необходимости.
          </div>
        ) : (
          <div className="mt-1 flex max-h-48 flex-col gap-1 overflow-y-auto pr-1">
            {items.map(([label, value]) => (
              <div key={label} className="rounded bg-black/30 p-1.5">
                <div className="text-[9px] uppercase tracking-wide text-white/35">{label}</div>
                <div className="whitespace-pre-wrap text-[11px] text-white/80">
                  {value || <span className="text-white/25">—</span>}
                </div>
              </div>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}

function FrameDetails({
  frame,
  excelRow,
  sceneRegistry,
  allFrames,
  onChanged,
}: {
  frame: DbFrame;
  excelRow: DbExcelRow | null;
  sceneRegistry: NonNullable<DbGraph["scene_registry"]>;
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
          Кадр {frame.number} · {frame.uuid}
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

      <SceneRegistryBlock registry={sceneRegistry} />

      <AttrsEditor
        frame={frame}
        onSave={async (attrs, label) => {
          await save({ attrs }, label);
        }}
      />

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

      <ExcelRowsBlock row={excelRow} />

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
  sheetNames,
  onChanged,
}: {
  graph: DbGraph | null;
  projectId: number | null;
  sheetNames: string[];
  onChanged: () => Promise<void>;
}) {
  const sheets = sheetNames.length ? sheetNames : [...ENTITY_SHEETS_DEFAULT];
  const [sheet, setSheet] = useState(sheets[0] ?? "Персонажи");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<XlsxPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!sheets.includes(sheet)) setSheet(sheets[0] ?? "Персонажи");
  }, [sheets, sheet]);

  useEffect(() => {
    if (projectId == null) return;
    let cancelled = false;
    setLoading(true);
    void api
      .previewProjectXlsx(projectId, {
        sheet,
        maxRows: XLSX_STUDIO_MAX_ROWS,
        maxCols: XLSX_STUDIO_MAX_COLS,
        startRow: 1,
        raw: true,
      })
      .then((p) => {
        if (!cancelled) setPreview(p);
      })
      .catch(() => {
        if (!cancelled) setPreview(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, sheet, tick]);

  const type = ENTITY_SHEET_TO_TYPE[sheet.trim().toLowerCase()] ?? "character";
  const filtered = (graph?.entities ?? []).filter((en) => en.type === type);

  return (
    <div className="flex h-full min-h-0 flex-col text-xs">
      {/* Вкладки = листы Excel сущностей */}
      <div className="mb-2 flex shrink-0 flex-nowrap items-center gap-1.5 overflow-x-auto">
        <span className="mr-1 shrink-0 text-[10px] uppercase tracking-[0.18em] text-white/35">
          Лист
        </span>
        {sheets.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSheet(s)}
            className={`shrink-0 rounded-md px-2.5 py-1 text-xs whitespace-nowrap ${
              sheet === s
                ? "bg-primary/20 text-primary"
                : "bg-white/[0.04] text-white/55 hover:bg-white/[0.08]"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="mb-2 flex shrink-0 gap-1.5">
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
          className="h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-black/40 px-2 text-xs"
        />
        <Button
          size="sm"
          variant="outline"
          disabled={projectId == null}
          title="Добавить сущность в базу"
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
            await onChanged();
            setTick((t) => t + 1);
          }}
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>

      {loading && !preview ? (
        <div className="flex flex-1 items-center justify-center text-white/30">Загрузка листа…</div>
      ) : (preview?.rows?.length ?? 0) > 0 ? (
        <StudioExcelGrid
          className="min-h-0 max-h-none flex-1"
          rows={preview?.rows ?? []}
          startRow={preview?.start_row ?? 1}
          colLetters={preview?.col_letters}
          nowrap
          editable
          onCellCommit={async (ri, ci, value) => {
            if (projectId == null) return;
            const start = preview?.start_row ?? 1;
            try {
              await api.dbPatchSheetCell(projectId, {
                sheet,
                row: start + ri,
                col: ci + 1,
                value,
              });
              toast.success("Ячейка сохранена");
              setTick((t) => t + 1);
              await onChanged();
            } catch (e) {
              toast.error(`Ячейка: ${e instanceof Error ? e.message : e}`);
              throw e;
            }
          }}
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden">
          <div className="flex h-full flex-nowrap items-stretch gap-2 pb-1">
            {filtered.map((en) => (
              <div
                key={en.id}
                className="w-[220px] shrink-0 rounded-md border border-white/[0.08] bg-white/[0.02] p-2"
              >
                <div className="flex items-center gap-1.5 whitespace-nowrap">
                  <span className="rounded bg-white/10 px-1 text-[9px] text-white/50">
                    {sheet}
                  </span>
                  {en.code ? (
                    <span className="font-mono text-[10px] text-white/40">{en.code}</span>
                  ) : null}
                  <span className="min-w-0 flex-1 truncate font-semibold text-white/80">
                    {en.name ?? "—"}
                  </span>
                  <button
                    type="button"
                    title="Удалить сущность"
                    onClick={async () => {
                      await api.dbDeleteEntity(en.id);
                      void onChanged();
                    }}
                    className="shrink-0 rounded p-0.5 text-white/30 hover:text-red-400"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            ))}
            {filtered.length === 0 ? (
              <div className="flex items-center text-white/30 whitespace-nowrap">
                Лист «{sheet}» пуст — добавь сущность выше или заполни Excel.
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
