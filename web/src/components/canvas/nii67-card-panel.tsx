"use client";

import { useEffect, useRef, useState } from "react";
import { Nii67FrameList, type Nii67Frame } from "./nii67-frame-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Clapperboard,
  ListChecks,
  ChevronUp,
  Copy,
  DoorOpen,
  Eye,
  Flag,
  GripVertical,
  ImageIcon,
  Loader2,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { clearNii67ProjectViews } from "@/lib/nii67-project-state";
import { errorMessageFromUnknown } from "@/lib/error-message";
import {
  BLOCK_KINDS,
  CREATURE_TYPES,
  FINALE_LIMIT,
  PLAN_LIMIT,
  PLAN_OUTCOMES,
  type PlanOutcome,
  planReady,
  updatePlanDoor,
  updatePlanFinale,
  GENERATE_HINT,
  GENERATE_PENDING,
  KIND_LABEL,
  KIND_MARK,
  TEMPERAMENTS,
  type BlockKind,
  type Nii67Block,
  type Nii67Card,
  type Nii67Door,
  cardFromUnknown,
  defaultCard,
  deleteBlock,
  duplicateBlock,
  insertBlock,
  moveBlock,
  reorderBlock,
  updateBlock,
  updateDoor,
  updateFinale,
  updateIntroVideo,
  updatePortal,
} from "@/lib/nii67";
import { cn } from "@/lib/utils";

const fieldClass =
  "w-full resize-y rounded-lg border border-white/10 bg-black/35 px-2.5 py-2 text-[13px] leading-snug text-zinc-100 placeholder:text-zinc-500 focus:border-teal-400/50 focus:outline-none";

function Field({
  label,
  value,
  onChange,
  placeholder,
  maxLength,
  rows = 3,
  testId,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  maxLength?: number;
  rows?: number;
  testId?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <span>{label}</span>
        {maxLength ? (
          <span className="tabular-nums text-zinc-500">
            {value.length}/{maxLength}
          </span>
        ) : null}
      </span>
      <textarea
        data-testid={testId}
        value={value}
        rows={rows}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={fieldClass}
      />
    </label>
  );
}

function ShortField({
  label,
  value,
  onChange,
  placeholder,
  testId,
  maxLength = 180,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
  maxLength?: number;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        {label}
      </span>
      <input
        data-testid={testId}
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-white/10 bg-black/35 px-2.5 py-1.5 text-[13px] text-zinc-100 placeholder:text-zinc-500 focus:border-teal-400/50 focus:outline-none"
      />
    </label>
  );
}

function ChipRow({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: readonly { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
  testId: string;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400">{label}</div>
      <div className="flex flex-wrap gap-1">
        {options.map((option) => {
          const active = value === option.id;
          return (
            <button
              key={option.id}
              type="button"
              data-testid={`${testId}-${option.id}`}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] transition",
                active
                  ? "border-teal-300/70 bg-teal-400/20 text-teal-50"
                  : "border-white/10 text-zinc-300 hover:border-white/30",
              )}
              onClick={() => onChange(active ? "" : option.id)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ImageSlot({
  prompt,
  fileName,
  previewUrl,
  onPrompt,
  onFile,
  testId,
  placeholder,
}: {
  prompt: string;
  fileName: string;
  previewUrl?: string;
  onPrompt: (value: string) => void;
  onFile: (file: File) => void;
  testId: string;
  placeholder: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-teal-400/30 bg-black/25 p-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-teal-200/80">
        <ImageIcon className="h-3 w-3" />
        Слот картинки
      </div>
      <div className="mb-2 flex items-center gap-2">
        <div className="flex h-16 w-24 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/10 bg-zinc-950">
          {previewUrl ? (
            // локальный предпросмотр выбранного файла; в карточку пишется только имя
            <img src={previewUrl} alt="" className="h-full w-full object-cover" />
          ) : (
            <ImageIcon className="h-4 w-4 text-zinc-600" />
          )}
        </div>
        <label className="min-w-0 flex-1 text-[11px] text-zinc-400">
          <span className="mb-1 block truncate">{fileName || "Файл не выбран"}</span>
          <input
            data-testid={`${testId}-file`}
            type="file"
            accept="image/*"
            className="block w-full text-[11px] text-zinc-300 file:mr-2 file:rounded-md file:border-0 file:bg-teal-500/20 file:px-2 file:py-1 file:text-[11px] file:text-teal-100"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
            }}
          />
        </label>
      </div>
      <textarea
        data-testid={`${testId}-prompt`}
        value={prompt}
        rows={2}
        maxLength={4000}
        placeholder={placeholder}
        onChange={(event) => onPrompt(event.target.value)}
        className={fieldClass}
      />
    </div>
  );
}

function UploadSlot({
  label,
  accept,
  testId,
  fileName,
  busy,
  onFile,
}: {
  label: string;
  accept: string;
  testId: string;
  fileName: string;
  busy: boolean;
  onFile: (file: File) => void;
}) {
  return (
    <label className="block rounded-lg border border-dashed border-teal-400/30 bg-black/25 p-2 text-[11px] text-zinc-400">
      <span className="mb-1 block font-medium uppercase tracking-wide text-teal-200/80">{label}</span>
      <span className="mb-1 block truncate">{busy ? "Загрузка…" : fileName || "Файл не выбран"}</span>
      <input
        data-testid={testId}
        type="file"
        accept={accept}
        disabled={busy}
        className="block w-full text-[11px] text-zinc-300 file:mr-2 file:rounded-md file:border-0 file:bg-teal-500/20 file:px-2 file:py-1 file:text-[11px] file:text-teal-100"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
        }}
      />
    </label>
  );
}

function InsertMenu({
  testId,
  onInsert,
}: {
  testId: string;
  onInsert: (kind: BlockKind) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="py-1">
      <button
        type="button"
        data-testid={testId}
        className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed border-teal-400/30 px-2 py-1.5 text-[11px] font-medium text-teal-200 hover:bg-teal-400/10"
        onClick={() => setOpen((value) => !value)}
      >
        <Plus className="h-3 w-3" />
        Вставить блок
      </button>
      {open ? (
        <div className="mt-1 overflow-hidden rounded-lg border border-white/10 bg-zinc-950">
          {BLOCK_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              data-testid={`${testId}-${kind}`}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px] text-zinc-200 hover:bg-white/[0.05]"
              onClick={() => {
                onInsert(kind);
                setOpen(false);
              }}
            >
              <span className="w-4 text-center font-semibold text-teal-300">{KIND_MARK[kind]}</span>
              {KIND_LABEL[kind]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function BlockCard({
  block,
  index,
  count,
  dragging,
  previewUrl,
  onDragStart,
  onDrop,
  onPatch,
  onDuplicate,
  onDelete,
  onMove,
  onFile,
}: {
  block: Nii67Block;
  index: number;
  count: number;
  dragging: boolean;
  previewUrl?: string;
  onDragStart: () => void;
  onDrop: () => void;
  onPatch: (patch: Parameters<typeof updateBlock>[3]) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onMove: (delta: number) => void;
  onFile: (file: File) => void;
}) {
  const character = block.character;
  return (
    <article
      data-testid={`nii67-block-${block.id}`}
      className={cn(
        "rounded-xl border border-white/10 bg-[#0d1720] shadow-sm",
        dragging && "border-teal-300/50 opacity-70",
      )}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        onDrop();
      }}
    >
      <div className="flex flex-wrap items-center gap-1.5 px-2 py-2">
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            data-testid={`nii67-up-${block.id}`}
            className="rounded-md p-1 text-zinc-400 hover:bg-white/5 hover:text-zinc-100 disabled:opacity-30"
            aria-label="Выше"
            disabled={index === 0}
            onClick={() => onMove(-1)}
          >
            <ChevronUp className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            data-testid={`nii67-down-${block.id}`}
            className="rounded-md p-1 text-zinc-400 hover:bg-white/5 hover:text-zinc-100 disabled:opacity-30"
            aria-label="Ниже"
            disabled={index >= count - 1}
            onClick={() => onMove(1)}
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            draggable
            data-testid={`nii67-drag-${block.id}`}
            className="cursor-grab rounded-md p-1 text-zinc-500 hover:bg-white/5 active:cursor-grabbing"
            aria-label="Перетащить"
            onDragStart={(event) => {
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", block.id);
              onDragStart();
            }}
          >
            <GripVertical className="h-3.5 w-3.5" />
          </button>
        </div>
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-teal-400/15 text-[12px] font-bold text-teal-200">
          {KIND_MARK[block.kind]}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-zinc-100">
          {block.kind === "custom" && block.custom_title.trim()
            ? block.custom_title
            : KIND_LABEL[block.kind]}
        </span>
        <button
          type="button"
          data-testid={`nii67-duplicate-${block.id}`}
          className="rounded-md px-1.5 py-1 text-[11px] text-zinc-300 hover:bg-white/5"
          onClick={onDuplicate}
        >
          <Copy className="mr-1 inline h-3 w-3" />
          Дублировать
        </button>
        <button
          type="button"
          data-testid={`nii67-collapse-${block.id}`}
          className="rounded-md px-1.5 py-1 text-[11px] text-zinc-300 hover:bg-white/5"
          onClick={() => onPatch({ collapsed: !block.collapsed })}
        >
          {block.collapsed ? "Развернуть" : "Свернуть"}
        </button>
        <button
          type="button"
          data-testid={`nii67-delete-${block.id}`}
          className="rounded-md px-1.5 py-1 text-[11px] text-rose-300 hover:bg-rose-500/10"
          onClick={onDelete}
        >
          <Trash2 className="mr-1 inline h-3 w-3" />
          Удалить
        </button>
      </div>
      {block.collapsed ? null : (
        <div className="flex flex-col gap-2 border-t border-white/5 px-2.5 py-2.5">
          {block.kind === "custom" ? (
            <ShortField
              label="Название своего блока"
              value={block.custom_title}
              testId={`nii67-custom-title-${block.id}`}
              placeholder="Например: переход, деталь, пауза"
              onChange={(custom_title) => onPatch({ custom_title })}
            />
          ) : null}
          {block.kind === "world_frame" ? (
            <ImageSlot
              testId={`nii67-frame-${block.id}`}
              prompt={block.image_prompt}
              fileName={block.image_name}
              previewUrl={previewUrl}
              placeholder="Промпт первого кадра этого мира"
              onPrompt={(image_prompt) => onPatch({ image_prompt })}
              onFile={onFile}
            />
          ) : null}
          {block.kind === "world_character" ? (
            <div className="flex flex-col gap-2 rounded-lg border border-white/10 bg-black/20 p-2">
              <button
                type="button"
                data-testid={`nii67-character-enabled-${block.id}`}
                aria-pressed={character.enabled}
                className={cn(
                  "rounded-lg border px-2 py-1.5 text-left text-[12px]",
                  character.enabled
                    ? "border-teal-300/50 bg-teal-400/10 text-teal-50"
                    : "border-white/10 text-zinc-300",
                )}
                onClick={() => onPatch({ character: { enabled: !character.enabled } })}
              >
                {character.enabled
                  ? "Персонаж этого мира включён"
                  : "Без персонажа мира — нажмите, чтобы добавить"}
              </button>
              {character.enabled ? (
                <>
                  <ShortField
                    label="Имя персонажа мира"
                    value={character.name}
                    testId={`nii67-character-name-${block.id}`}
                    placeholder="Не главный герой"
                    onChange={(name) => onPatch({ character: { name } })}
                  />
                  <ChipRow
                    label="Тип существа"
                    options={CREATURE_TYPES}
                    value={character.creature_type}
                    testId={`nii67-creature-${block.id}`}
                    onChange={(creature_type) => onPatch({ character: { creature_type } })}
                  />
                  <ChipRow
                    label="Характер"
                    options={TEMPERAMENTS}
                    value={character.temperament}
                    testId={`nii67-temper-${block.id}`}
                    onChange={(temperament) => onPatch({ character: { temperament } })}
                  />
                  <Field
                    label="Позиция"
                    value={character.position}
                    rows={2}
                    testId={`nii67-character-position-${block.id}`}
                    placeholder="Справа от двери, в тени"
                    onChange={(position) => onPatch({ character: { position } })}
                  />
                  <Field
                    label="Поведение"
                    value={character.behavior}
                    rows={2}
                    testId={`nii67-character-behavior-${block.id}`}
                    placeholder="Стоит неподвижно и смотрит"
                    onChange={(behavior) => onPatch({ character: { behavior } })}
                  />
                  <Field
                    label="Эффект появления"
                    value={character.appearance}
                    rows={2}
                    testId={`nii67-character-appearance-${block.id}`}
                    placeholder="Дымка, частицы света"
                    onChange={(appearance) => onPatch({ character: { appearance } })}
                  />
                </>
              ) : null}
            </div>
          ) : null}
          {block.kind === "exit" ? (
            <Field
              label="Что произошло с героем (по описанию генерируется иллюстрация)"
              value={block.text}
              rows={3}
              testId={`nii67-illustration-${block.id}`}
              maxLength={4000}
              placeholder="Итог этой двери для героя"
              onChange={(text) => onPatch({ text })}
            />
          ) : (
          <Field
            label={
              block.kind === "gg_action"
                ? "Действие единственного ГГ в этой двери"
                : block.kind === "enter_action"
                  ? "Текст оператора"
                  : "Текст блока"
            }
            value={block.text}
            testId={`nii67-text-${block.id}`}
            maxLength={4000}
            placeholder={
              block.kind === "enter_action"
                ? "Что происходит при попадании в мир"
                : block.kind === "gg_action"
                  ? "Сомнение, шаг, взгляд — тот же герой, что в шапке"
                  : "Условия, действие, деталь кадра"
            }
            onChange={(text) => onPatch({ text })}
          />
          )}
          {block.kind === "gg_action" ? (
            <p className="text-[11px] text-zinc-500">
              На весь ролик один главный герой. Здесь только его действие в этой двери.
            </p>
          ) : null}
        </div>
      )}
    </article>
  );
}

function DoorColumn({
  door,
  drag,
  previews,
  onDrag,
  onChange,
  onFile,
}: {
  door: Nii67Door;
  drag: { doorId: string; index: number } | null;
  previews: Record<string, string>;
  onDrag: (next: { doorId: string; index: number } | null) => void;
  onChange: (mutator: (card: Nii67Card) => Nii67Card) => void;
  onFile: (key: string, file: File, applyName: (name: string) => void) => void;
}) {
  return (
    <section
      data-testid={`nii67-door-${door.id}`}
      className="flex min-w-0 flex-col rounded-2xl border border-teal-400/20 bg-[#0b141c] p-2.5"
    >
      <header className="mb-2 flex items-start justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-semibold text-zinc-50">
            <DoorOpen className="h-4 w-4 text-teal-300" />
            {door.title}
          </div>
          <div className="text-[11px] text-zinc-400">{door.branch}</div>
        </div>
        <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-zinc-400">
          {door.id === "door1" ? "Путь 1" : "Путь 2"}
        </span>
      </header>
      <div className="mb-2 flex flex-col gap-2">
        <ShortField
          label="Мир"
          value={door.theme}
          maxLength={240}
          testId={`nii67-theme-${door.id}`}
          placeholder="Появится после генерации"
          onChange={(theme) => onChange((card) => updateDoor(card, door.id, { theme }))}
        />
        <Field
          label="Закон мира"
          value={door.theme_note}
          rows={2}
          testId={`nii67-law-${door.id}`}
          placeholder="Один закон мира — появится после генерации"
          onChange={(theme_note) => onChange((card) => updateDoor(card, door.id, { theme_note }))}
        />
      </div>
      <InsertMenu
        testId={`nii67-insert-${door.id}-0`}
        onInsert={(kind) => onChange((card) => insertBlock(card, door.id, 0, kind))}
      />
      <div className="flex flex-col gap-1.5">
        {door.blocks.length === 0 ? (
          <p className="px-1 py-3 text-center text-[12px] text-zinc-500">
            В этой двери пока нет блоков. Вставьте первый.
          </p>
        ) : null}
        {door.blocks.map((block, index) => (
          <div key={block.id}>
            <BlockCard
              block={block}
              index={index}
              count={door.blocks.length}
              dragging={drag?.doorId === door.id && drag.index === index}
              previewUrl={previews[`block:${block.id}`]}
              onDragStart={() => onDrag({ doorId: door.id, index })}
              onDrop={() => {
                if (!drag || drag.doorId !== door.id) return;
                const from = drag.index;
                onDrag(null);
                onChange((card) => reorderBlock(card, door.id, from, index));
              }}
              onPatch={(patch) => onChange((card) => updateBlock(card, door.id, block.id, patch))}
              onDuplicate={() => onChange((card) => duplicateBlock(card, door.id, block.id))}
              onDelete={() => onChange((card) => deleteBlock(card, door.id, block.id))}
              onMove={(delta) => onChange((card) => moveBlock(card, door.id, block.id, delta))}
              onFile={(file) =>
                onFile(`block:${block.id}`, file, (image_name) =>
                  onChange((card) => updateBlock(card, door.id, block.id, { image_name })),
                )
              }
            />
            <InsertMenu
              testId={`nii67-insert-${door.id}-${index + 1}`}
              onInsert={(kind) => onChange((card) => insertBlock(card, door.id, index + 1, kind))}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

/** Редактор сценария NII 67 на ноде «Сценарий». */
export function Nii67CardPanel({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [card, setCard] = useState<Nii67Card>(() => defaultCard());
  const [dirty, setDirty] = useState(false);
  const [drag, setDrag] = useState<{ doorId: string; index: number } | null>(null);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [frames, setFrames] = useState<Nii67Frame[]>([]);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const dirtyRef = useRef(false);
  const framesDirtyRef = useRef(false);
  const cardRef = useRef(card);
  const editGen = useRef(0);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const previewsRef = useRef(previews);
  cardRef.current = card;
  previewsRef.current = previews;

  const cardQ = useQuery({
    queryKey: ["nii67", projectId],
    queryFn: () => api.getNii67(projectId),
    enabled: projectId > 0,
  });

  useEffect(() => {
    dirtyRef.current = false;
    framesDirtyRef.current = false;
    editGen.current += 1;
    setDirty(false);
    setCard(defaultCard());
    setPreviewText(null);
    const cleared = clearNii67ProjectViews(previewsRef.current);
    setFrames(cleared.frames);
    setPreviews(cleared.previews);
  }, [projectId]);

  useEffect(() => {
    if (!cardQ.data) return;
    if (!dirtyRef.current) setCard(cardFromUnknown(cardQ.data.card));
    if (!framesDirtyRef.current && Array.isArray(cardQ.data.frames)) setFrames(cardQ.data.frames);
  }, [cardQ.data]);

  useEffect(
    () => () => {
      Object.values(previewsRef.current).forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );

  function onFramesChange(next: Nii67Frame[]) {
    framesDirtyRef.current = true;
    setFrames(next);
  }

  function change(mutator: (current: Nii67Card) => Nii67Card) {
    editGen.current += 1;
    dirtyRef.current = true;
    setDirty(true);
    setCard((prev) => mutator(prev));
  }

  const [uploading, setUploading] = useState<string | null>(null);
  async function upload(
    slot: "intro_video" | "portal_image" | "portal_video",
    file: File,
    applyName: (name: string) => void,
  ) {
    setUploading(slot);
    const pid = projectId;
    const gen = editGen.current;
    try {
      const res = await api.uploadNii67(pid, slot, file);
      if (pid !== projectRef.current || gen !== editGen.current) return;
      applyName(res.name);
      toast.success("Файл сохранён в проект");
    } catch (error) {
      toast.error(errorMessageFromUnknown(error));
    } finally {
      setUploading(null);
    }
  }

  function rememberFile(key: string, file: File, applyName: (name: string) => void) {
    const url = URL.createObjectURL(file);
    setPreviews((prev) => {
      if (prev[key]) URL.revokeObjectURL(prev[key]);
      return { ...prev, [key]: url };
    });
    applyName(file.name);
  }

  const save = useMutation({
    mutationFn: async (mode: "draft" | "save") => {
      const gen = editGen.current;
      const pid = projectId;
      const res = await api.putNii67(pid, cardRef.current, mode);
      return { res, gen, mode, pid };
    },
    onSuccess: ({ res, gen, mode, pid }) => {
      if (pid !== projectRef.current) return;
      if (editGen.current === gen) {
        dirtyRef.current = false;
        setDirty(false);
        setCard(cardFromUnknown(res.card));
      }
      toast.success(mode === "draft" ? "Черновик сохранён" : "Карточка сохранена");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const preview = useMutation({
    mutationFn: () => api.previewNii67(projectId, cardRef.current),
    onSuccess: (res) => setPreviewText(res.text),
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const saveFrames = useMutation({
    mutationFn: async (list: Nii67Frame[]) => {
      const pid = projectId;
      const res = await api.putNii67Frames(pid, list);
      return { res, pid };
    },
    onSuccess: ({ res, pid }) => {
      if (pid !== projectRef.current) return;
      framesDirtyRef.current = false;
      setFrames(res.frames);
      toast.success("Кадры сохранены");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Не удалось сохранить кадры"),
  });

  const makePlan = useMutation({
    mutationFn: async () => {
      const gen = editGen.current;
      const pid = projectId;
      const res = await api.planNii67(pid, cardRef.current);
      return { res, gen, pid };
    },
    onSuccess: ({ res, gen, pid }) => {
      if (pid !== projectRef.current) return;
      if (editGen.current === gen) {
        dirtyRef.current = false;
        setDirty(false);
        setCard(cardFromUnknown(res.card));
      } else {
        const plan = cardFromUnknown(res.card).plan;
        setCard((prev) => ({ ...prev, plan }));
      }
      toast.success("План ролика готов — проверьте и поправьте текст");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const generate = useMutation({
    mutationFn: async () => {
      const gen = editGen.current;
      const pid = projectId;
      const res = await api.storyboardNii67(pid, cardRef.current);
      return { res, gen, pid };
    },
    onSuccess: ({ res, gen, pid }) => {
      if (pid !== projectRef.current) return;
      if (editGen.current === gen) {
        dirtyRef.current = false;
        setDirty(false);
        setCard(cardFromUnknown(res.card));
      }
      setPreviewText(res.general_plan);
      framesDirtyRef.current = false;
      if (Array.isArray(res.frames)) setFrames(res.frames);
      toast.success("Раскадровка готова и записана в общий план");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
      void qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const reset = useMutation({
    mutationFn: () => api.resetNii67(projectId),
    onSuccess: (res) => {
      editGen.current += 1;
      dirtyRef.current = false;
      framesDirtyRef.current = false;
      setDirty(false);
      setCard(cardFromUnknown(res.card));
      setPreviewText(null);
      setFrames([]);
      setPreviews((prev) => clearNii67ProjectViews(prev).previews);
      toast.success("Карточка сброшена");
      void qc.invalidateQueries({ queryKey: ["nii67", projectId] });
    },
    onError: (error) => toast.error(errorMessageFromUnknown(error)),
  });

  const busy = save.isPending || preview.isPending || generate.isPending || makePlan.isPending || reset.isPending;
  const canStoryboard = planReady(card.plan);
  const ggLabel = card.gg.name.trim() || "1 ГГ";

  const renderPlan = (compact: boolean) => (
            <section data-testid={compact ? "nii67-plan-inline" : "nii67-plan-block"} className={cn("rounded-2xl border border-teal-400/30 bg-[#0c161e] p-3", compact ? "m-2" : "mb-3")}>
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm font-semibold text-zinc-50">
                  <ListChecks className="h-4 w-4 text-teal-300" />
                  План ролика
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid={compact ? "nii67-plan-inline-btn" : "nii67-plan"}
                  disabled={busy}
                  onClick={() => makePlan.mutate()}
                >
                  {makePlan.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  План ролика
                </Button>
              </div>
              <p className="mb-2 text-[12px] text-zinc-400">
                Шаг 1. Кнопка «План ролика» предложит мир, события и исход для каждой двери. Текст можно править или
                написать целиком вручную. Шаг 2 — «Раскадровка» внизу строит кадры строго по этому тексту.
              </p>
              <div className={cn("grid gap-3", compact ? "grid-cols-1" : "md:grid-cols-2")}>
                {(["door1", "door2"] as const).map((id) => (
                  <div key={id} data-testid={`nii67-plan-${id}${compact ? "-inline" : ""}`} className="rounded-xl border border-white/10 bg-black/20 p-2">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
                      {id === "door1" ? "Левая дверь" : "Правая дверь"}
                    </div>
                    <Field
                      label="Мир"
                      value={card.plan[id].world}
                      maxLength={PLAN_LIMIT}
                      rows={2}
                      testId={`nii67-plan-${id}-world`}
                      placeholder="Куда попадает герой: узнаваемая реальность"
                      onChange={(world) => change((current) => updatePlanDoor(current, id, { world }))}
                    />
                    <Field
                      label="Что происходит / пик"
                      value={card.plan[id].happens}
                      maxLength={PLAN_LIMIT}
                      rows={4}
                      testId={`nii67-plan-${id}-happens`}
                      placeholder="Что видит, как мир действует на тело, поворот, пик, что делает герой"
                      onChange={(happens) => change((current) => updatePlanDoor(current, id, { happens }))}
                    />
                    <label className="mt-1 block text-[11px] text-zinc-400">
                      Исход
                      <select
                        data-testid={`nii67-plan-${id}-outcome`}
                        className={cn(fieldClass, "mt-1 resize-none")}
                        value={card.plan[id].outcome}
                        onChange={(event) =>
                          change((current) => updatePlanDoor(current, id, { outcome: event.target.value as PlanOutcome }))
                        }
                      >
                        {PLAN_OUTCOMES.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ))}
              </div>
              <div className="mt-2">
                <Field
                  label="Финал"
                  value={card.plan.finale}
                  maxLength={PLAN_LIMIT}
                  rows={2}
                  testId="nii67-plan-finale"
                  placeholder="Что стало с героем в каждой ветке — по этому тексту строится финальная иллюстрация"
                  onChange={(finale) => change((current) => updatePlanFinale(current, finale))}
                />
              </div>
              {compact ? (
                <Button
                  type="button"
                  size="sm"
                  data-testid="nii67-storyboard-inline"
                  disabled={busy || !canStoryboard}
                  className="mt-2 w-full border-teal-300/50 bg-teal-400 text-zinc-950 hover:bg-teal-300"
                  title={canStoryboard ? "Построить раскадровку строго по плану" : "Сначала заполните план обеих дверей"}
                  onClick={() => generate.mutate()}
                >
                  {generate.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Clapperboard className="h-3.5 w-3.5" />}
                  Раскадровка
                </Button>
              ) : null}
            </section>
  );


  return (
    <div
      className="nodrag nopan nowheel border-t border-teal-400/20 bg-teal-500/[0.04]"
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
      onWheel={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        data-testid="nii67-open"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-teal-100 transition hover:bg-teal-500/10"
        onClick={() => setOpen(true)}
      >
        <DoorOpen className="h-3.5 w-3.5 shrink-0 text-teal-300" />
        <span className="font-semibold">Карточка NII 67</span>
        <span className="ml-auto text-zinc-400">{dirty ? "не сохранено" : "две двери"}</span>
      </button>

      {!open ? renderPlan(true) : null}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          overlayClassName="z-[150]"
          className="z-[160] flex h-[min(920px,92vh)] w-[min(1180px,96vw)] max-w-none flex-col gap-0 overflow-hidden border-teal-500/25 bg-[#071018] p-0 text-zinc-100"
        >
          <header className="flex shrink-0 items-start justify-between gap-3 border-b border-white/10 px-4 py-3 pr-12">
            <div className="min-w-0">
              <DialogTitle className="text-base font-semibold tracking-tight text-zinc-50">
                Видео Pipeline
              </DialogTitle>
              <p className="mt-0.5 text-[12px] text-zinc-400">
                Создайте сценарий для одного главного героя (1 ГГ)
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-save-draft"
                disabled={busy}
                onClick={() => save.mutate("draft")}
              >
                <Save className="h-3.5 w-3.5" />
                Сохранить черновик
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-preview"
                disabled={busy}
                onClick={() => preview.mutate()}
              >
                {preview.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                Предпросмотр
              </Button>
              <span className="rounded-full border border-teal-300/40 bg-teal-400/10 px-2.5 py-1 text-[11px] font-semibold text-teal-100">
                {ggLabel}
              </span>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {cardQ.isLoading ? (
              <p className="mb-2 text-[12px] text-zinc-500">Загружаю карточку…</p>
            ) : null}
            {dirty ? (
              <p className="mb-2 text-[12px] text-amber-200/90">Есть несохранённые правки</p>
            ) : null}
            {generate.isPending ? (
              <p data-testid="nii67-generating" className="mb-2 text-[12px] text-teal-100">
                {GENERATE_PENDING}
              </p>
            ) : null}
            {previewText !== null ? (
              <section className="mb-3 rounded-xl border border-teal-400/30 bg-black/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-teal-100">Предпросмотр сценария</span>
                  <button
                    type="button"
                    className="text-[11px] text-zinc-400 hover:text-zinc-200"
                    onClick={() => setPreviewText(null)}
                  >
                    Закрыть
                  </button>
                </div>
                <pre
                  data-testid="nii67-preview-text"
                  className="max-h-56 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed text-zinc-200"
                >
                  {previewText}
                </pre>
              </section>
            ) : null}

            {renderPlan(false)}

            <section data-testid="nii67-intro-block" className="mb-3 rounded-2xl border border-white/10 bg-[#0c161e] p-3">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
                Интро
              </div>
              <UploadSlot
                label="Исходное видео"
                accept="video/*"
                testId="nii67-intro-video"
                fileName={card.intro.video_name}
                busy={uploading === "intro_video"}
                onFile={(file) =>
                  upload("intro_video", file, (name) => change((current) => updateIntroVideo(current, name)))
                }
              />
              <p className="mt-2 text-[11px] leading-snug text-zinc-500">{GENERATE_HINT}</p>
            </section>

            <section data-testid="nii67-portal-block" className="mb-3 rounded-2xl border border-white/10 bg-[#0c161e] p-3">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-teal-200/90">
                Двери
              </div>
              <div className="mb-2 grid gap-2 md:grid-cols-2">
                <UploadSlot
                  label="Фото дверей (одно на обе)"
                  accept="image/*"
                  testId="nii67-portal-image"
                  fileName={card.portal.image_name}
                  busy={uploading === "portal_image"}
                  onFile={(file) =>
                    upload("portal_image", file, (image_name) =>
                      change((current) => updatePortal(current, { image_name })),
                    )
                  }
                />
                <UploadSlot
                  label="Видео трансформации (одно на обе)"
                  accept="video/*"
                  testId="nii67-portal-video"
                  fileName={card.portal.video_name}
                  busy={uploading === "portal_video"}
                  onFile={(file) =>
                    upload("portal_video", file, (video_name) =>
                      change((current) => updatePortal(current, { video_name })),
                    )
                  }
                />
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div className="flex flex-col gap-2">
                  <Field
                    label="Дизайн левой двери"
                    value={card.portal.left_design}
                    rows={2}
                    testId="nii67-portal-left-design"
                    placeholder="Как выглядит левая дверь"
                    onChange={(left_design) => change((current) => updatePortal(current, { left_design }))}
                  />
                  <Field
                    label="Анимация трансформации левой"
                    value={card.portal.left_anim}
                    rows={2}
                    testId="nii67-portal-left-anim"
                    placeholder="Как левая дверь превращается"
                    onChange={(left_anim) => change((current) => updatePortal(current, { left_anim }))}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Field
                    label="Дизайн правой двери"
                    value={card.portal.right_design}
                    rows={2}
                    testId="nii67-portal-right-design"
                    placeholder="Как выглядит правая дверь"
                    onChange={(right_design) => change((current) => updatePortal(current, { right_design }))}
                  />
                  <Field
                    label="Анимация трансформации правой"
                    value={card.portal.right_anim}
                    rows={2}
                    testId="nii67-portal-right-anim"
                    placeholder="Как правая дверь превращается"
                    onChange={(right_anim) => change((current) => updatePortal(current, { right_anim }))}
                  />
                </div>
              </div>
            </section>

            <div className="mb-3 text-center">
              <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-teal-300/90">
                Разделение
              </div>
              <p className="text-[12px] text-zinc-400">
                сценарий раздваивается и идёт параллельно
              </p>
            </div>

            <div className="grid items-start gap-3 lg:grid-cols-2">
              {card.doors.map((door) => (
                <DoorColumn
                  key={door.id}
                  door={door}
                  drag={drag}
                  previews={previews}
                  onDrag={setDrag}
                  onChange={change}
                  onFile={rememberFile}
                />
              ))}
            </div>

            <section className="mt-3 rounded-2xl border border-teal-400/25 bg-[#0c161e] p-3">
              <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-zinc-50">
                <Flag className="h-4 w-4 text-teal-300" />
                Общий финал
              </div>
              <p className="mb-2 text-[12px] text-zinc-400">
                Финальная часть сценария (стандарт + заметки). Обе двери сходятся сюда.
              </p>
              <div className="grid gap-2 md:grid-cols-2">
                <Field
                  label="Стандарт финала"
                  value={card.finale.standard}
                  maxLength={FINALE_LIMIT}
                  testId="nii67-finale-standard"
                  placeholder="Финальный кадр, посыл, эмоциональная точка..."
                  onChange={(standard) => change((current) => updateFinale(current, { standard }))}
                />
                <Field
                  label="Заметки"
                  value={card.finale.notes}
                  maxLength={FINALE_LIMIT}
                  testId="nii67-finale-notes"
                  placeholder="Дополнительные идеи, важные детали, исключения..."
                  onChange={(notes) => change((current) => updateFinale(current, { notes }))}
                />
              </div>
            </section>
          </div>

          {frames.length > 0 ? (
            <section data-testid="nii67-frames" className="max-h-[45vh] shrink-0 overflow-y-auto border-t border-white/10 px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Кадры ({frames.length}) · 60 с · 9:16</h3>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid="nii67-frames-save"
                  disabled={saveFrames.isPending}
                  onClick={() => saveFrames.mutate(frames)}
                >
                  Сохранить кадры
                </Button>
              </div>
              <Nii67FrameList frames={frames} onChange={onFramesChange} />
            </section>
          ) : null}
          <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-white/10 px-4 py-3">
            <p className="max-w-md text-[11px] text-zinc-500">
              Блоки — последовательные элементы, вставляются между любыми другими.
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                data-testid="nii67-reset"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm("Сбросить карточку NII 67 к пустому шаблону двух дверей?")) return;
                  reset.mutate();
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Сбросить
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                data-testid="nii67-save"
                disabled={busy}
                onClick={() => save.mutate("save")}
              >
                {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Сохранить
              </Button>
              <Button
                type="button"
                size="sm"
                data-testid="nii67-storyboard"
                disabled={busy || !canStoryboard}
                title={canStoryboard ? "Построить раскадровку строго по плану" : "Сначала заполните план обеих дверей"}
                className="border-teal-300/50 bg-teal-400 text-zinc-950 hover:bg-teal-300"
                onClick={() => generate.mutate()}
              >
                {generate.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Clapperboard className="h-3.5 w-3.5" />
                )}
                Раскадровка
              </Button>
            </div>
          </footer>
        </DialogContent>
      </Dialog>
    </div>
  );
}
