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
  /** Вкладки = бывшие листы Excel (кадры: «Общий план», «план»; сущности: «Персонажи»…). */
  const [frameSheets, setFrameSheets] = useState<string[]>([...FRAME_SHEETS_DEFAULT]);
  const [entitySheets, setEntitySheets] = useState<string[]>([...ENTITY_SHEETS_DEFAULT]);
  const [frameSheet, setFrameSheet] = useState<string>(SHEET_PLAN_V8);
  const [sheetPreview, setSheetPreview] = useState<XlsxPreview | null>(null);
  const [sheetLoading, setSheetLoading] = useState(false);
  const [sheetTick, setSheetTick] = useState(0);

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

  const loadSheetMeta = useCallback(async (pid: number) => {
    try {
      const meta = await api.previewProjectXlsx(pid, {
        maxRows: 1,
        maxCols: 1,
        raw: true,
      });
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
      setFrameSheets([...FRAME_SHEETS_DEFAULT]);
      setEntitySheets([...ENTITY_SHEETS_DEFAULT]);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setFrameId(null);
    setSheetPreview(null);
    if (projectId != null) {
      void loadGraph(projectId);
      void loadSheetMeta(projectId);
    } else {
      setGraph(null);
    }
  }, [open, projectId, loadGraph, loadSheetMeta]);

  useEffect(() => {
    if (!open || projectId == null || tab !== "frames") return;
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
  }, [open, projectId, tab, frameSheet, sheetTick]);

  const reload = useCallback(async () => {
    if (projectId == null) return;
    await loadGraph(projectId);
    await loadSheetMeta(projectId);
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
    await loadGraph(projectId);
    setSheetTick((t) => t + 1);
  }, [projectId, exportXlsx, loadGraph]);

  const frame: DbFrame | null = useMemo(
    () => graph?.frames.find((f) => f.id === frameId) ?? null,
    [graph, frameId],
  );

  const selectFrameByExcelCol = useCallback(
    (colIndex: number) => {
      if (!graph) return;
      // Лист «план»: кадр N → колонка N+2 (A=подписи, C=кадр1).
      const isPlan =
        frameSheet.trim().toLowerCase() === SHEET_PLAN_V8.toLowerCase() ||
        frameSheet.trim().toLowerCase() === "кадры";
      if (!isPlan) return;
      const frameNumber = colIndex - 1; // 0-based: C=2 → кадр 1
      if (frameNumber < 1) return;
      const f = graph.frames.find((x) => x.number === frameNumber);
      if (f) setFrameId(f.id);
    },
    [graph, frameSheet],
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
          <HarnessChip
            graph={graph}
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
          ) : loading && !graph ? (
            <div className="flex h-full items-center justify-center text-sm text-white/30">
              Загрузка графа…
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
                <div className="flex min-h-0 flex-1 flex-col gap-2">
                  {/* Вкладки = листы Excel, которые остались у кадров */}
                  <div className="flex shrink-0 flex-nowrap items-center gap-1.5 overflow-x-auto">
                    <span className="mr-1 shrink-0 text-[10px] uppercase tracking-[0.18em] text-white/35">
                      Лист
                    </span>
                    {frameSheets.map((name) => (
                      <button
                        key={name}
                        type="button"
                        onClick={() => setFrameSheet(name)}
                        className={`shrink-0 rounded-md px-2.5 py-1 text-xs whitespace-nowrap ${
                          frameSheet === name
                            ? "bg-primary/20 text-primary"
                            : "bg-white/[0.04] text-white/55 hover:bg-white/[0.08]"
                        }`}
                      >
                        {name}
                      </button>
                    ))}
                  </div>

                  {sheetLoading && !sheetPreview ? (
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
                          void loadGraph(projectId);
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
                      Лист «{frameSheet}» пуст или project.xlsx ещё не создан.
                      <br />
                      Экспортируй базу в Excel или запусти шаг пайплайна.
                    </div>
                  )}
                </div>
              ) : (
                <div className="min-h-0 flex-1 overflow-hidden">
                  <EntitiesPanel
                    graph={graph}
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
          {frame && graph ? (
            <FrameDetails
              frame={frame}
              excelRow={graph.excel_rows?.[String(frame.number)] ?? null}
              allFrames={graph.frames}
              onChanged={handleChanged}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-center text-xs text-white/30">
              На листе «план» кликни колонку кадра,
              <br />
              чтобы настроить его справа
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
