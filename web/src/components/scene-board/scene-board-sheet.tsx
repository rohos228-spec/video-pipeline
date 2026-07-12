"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Clapperboard,
  Eye,
  EyeOff,
  ImageIcon,
  Loader2,
  Music2,
  Pencil,
  Save,
  UserRound,
  Video,
  Volume2,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type SceneBoardDTO,
  type SceneBoardMediaSlot,
  type SceneBoardRegenSelection,
  type SceneBoardRegenTarget,
  type SceneBoardRegenType,
  type SceneBoardScene,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const REGEN_TYPE_LABELS: Record<SceneBoardRegenType, string> = {
  media: "Только медиа",
  prompt_and_media: "Промт + медиа",
  full_scene: "Вся сцена",
};

const MISSING_LABELS: Record<string, string> = {
  voiceover_text: "нет текста",
  image_shot1: "нет кадра 1",
  image_shot2: "нет кадра 2",
  video_shot1: "нет видео 1",
  video_shot2: "нет видео 2",
  audio: "нет озвучки",
  timeslot: "нет таймслота",
  characters: "нет персонажей",
};

export function SceneBoardSheet({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [mediaCollapsed, setMediaCollapsed] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [note, setNote] = useState("");
  const [selections, setSelections] = useState<SceneBoardRegenSelection[]>([]);
  const [collapsedScenes, setCollapsedScenes] = useState<Set<number>>(new Set());

  const board = useQuery({
    queryKey: ["scene-board", projectId],
    queryFn: () => api.getSceneBoard(projectId!),
    enabled: open && projectId != null,
    refetchInterval: open ? 8000 : false,
  });

  useEffect(() => {
    if (!board.data) return;
    setNote(board.data.regen_draft.note || "");
    setSelections(board.data.regen_draft.selections || []);
  }, [board.data?.project_id, board.dataUpdatedAt]);

  useEffect(() => {
    if (!open) {
      setEditMode(false);
      setMediaCollapsed(false);
    }
  }, [open]);

  const saveDraft = useMutation({
    mutationFn: () =>
      api.saveSceneBoardRegenDraft(projectId!, { note, selections }),
    onSuccess: () => {
      toast.success("Черновик перегенерации сохранён");
      qc.invalidateQueries({ queryKey: ["scene-board", projectId] });
      setEditMode(false);
    },
    onError: (e) => toast.error(String(e)),
  });

  const toggleSceneCollapsed = (frameId: number) => {
    setCollapsedScenes((prev) => {
      const next = new Set(prev);
      if (next.has(frameId)) next.delete(frameId);
      else next.add(frameId);
      return next;
    });
  };

  const selectionFor = (frameId: number) =>
    selections.find((s) => s.frame_id === frameId);

  const toggleTarget = (
    scene: SceneBoardScene,
    target: SceneBoardRegenTarget,
  ) => {
    setSelections((prev) => {
      const existing = prev.find((s) => s.frame_id === scene.frame_id);
      if (!existing) {
        return [
          ...prev,
          {
            frame_id: scene.frame_id,
            number: scene.number,
            targets: [target],
            regen_type: "media",
          },
        ];
      }
      const has = existing.targets.includes(target);
      const targets = has
        ? existing.targets.filter((t) => t !== target)
        : [...existing.targets, target];
      if (targets.length === 0) {
        return prev.filter((s) => s.frame_id !== scene.frame_id);
      }
      return prev.map((s) =>
        s.frame_id === scene.frame_id ? { ...s, targets } : s,
      );
    });
  };

  const setRegenType = (frameId: number, regen_type: SceneBoardRegenType) => {
    setSelections((prev) =>
      prev.map((s) => (s.frame_id === frameId ? { ...s, regen_type } : s)),
    );
  };

  const missingCount = useMemo(() => {
    if (!board.data) return 0;
    return board.data.scenes.reduce((acc, s) => acc + s.missing.length, 0);
  }, [board.data]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="!max-w-[min(1100px,96vw)] w-[96vw] bg-[hsl(240_8%_6%)]"
      >
        <SheetHeader className="pr-12">
          <SheetTitle className="flex flex-wrap items-center gap-2">
            <Clapperboard className="h-4 w-4 text-amber-400/90" />
            Сцены проекта
            {board.data && (
              <Badge variant="muted" className="ml-1 h-5 normal-case tracking-normal">
                {board.data.frame_count}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription>
            {board.data
              ? `${board.data.topic} · #${board.data.project_id} · ${board.data.slug}`
              : "Вертикальный обзор как лист «план»: текст, кадры, видео, озвучка, таймслоты."}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-wrap items-center gap-2 border-b border-white/5 px-5 py-2.5">
          <Button
            size="sm"
            variant={mediaCollapsed ? "default" : "outline"}
            className="h-8 gap-1.5 text-xs"
            onClick={() => setMediaCollapsed((v) => !v)}
          >
            {mediaCollapsed ? (
              <Eye className="h-3.5 w-3.5" />
            ) : (
              <EyeOff className="h-3.5 w-3.5" />
            )}
            {mediaCollapsed ? "Показать медиа" : "Свернуть медиа"}
          </Button>
          <Button
            size="sm"
            variant={editMode ? "default" : "outline"}
            className="h-8 gap-1.5 text-xs"
            onClick={() => setEditMode((v) => !v)}
            disabled={!board.data?.scenes.length}
          >
            <Pencil className="h-3.5 w-3.5" />
            {editMode ? "Режим правки" : "Редактировать"}
          </Button>
          {editMode && (
            <Button
              size="sm"
              variant="default"
              className="h-8 gap-1.5 text-xs"
              disabled={saveDraft.isPending}
              onClick={() => saveDraft.mutate()}
            >
              {saveDraft.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              Сохранить черновик
            </Button>
          )}
          {missingCount > 0 && (
            <Badge variant="warning" className="ml-auto normal-case tracking-normal">
              пробелов: {missingCount}
            </Badge>
          )}
          {board.data?.music && (
            <div className="ml-auto flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Music2 className="h-3.5 w-3.5" />
              <span>
                Музыка: {board.data.music.label}
                {board.data.music.present
                  ? ` · ${board.data.music.level_percent}%`
                  : ""}
              </span>
            </div>
          )}
        </div>

        {editMode && (
          <div className="border-b border-white/5 px-5 py-3">
            <label className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
              Текст для GPT (что перегенерировать и почему)
            </label>
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              className="text-xs"
              placeholder="Например: в сцене 2 персонаж должен смотреть в камеру; видео 1 слишком тёмное…"
            />
            <p className="mt-1.5 text-[10px] text-muted-foreground">
              Выберите объекты на сценах ниже. Черновик сохраняется в проект; запуск
              перегенерации — следующим шагом.
            </p>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {!projectId ? (
            <Empty text="Сначала выберите проект." />
          ) : board.isLoading ? (
            <div className="flex h-40 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : board.isError ? (
            <Empty text={`Ошибка загрузки: ${String(board.error)}`} />
          ) : !board.data?.scenes.length ? (
            <Empty text="Сцен пока нет — пройдите разбивку (лист «план», строка закадрового текста)." />
          ) : (
            <div className="flex flex-col gap-3">
              {board.data.scenes.map((scene) => (
                <SceneCard
                  key={scene.frame_id}
                  scene={scene}
                  music={board.data!.music}
                  mediaCollapsed={mediaCollapsed}
                  collapsed={collapsedScenes.has(scene.frame_id)}
                  onToggleCollapsed={() => toggleSceneCollapsed(scene.frame_id)}
                  editMode={editMode}
                  selection={selectionFor(scene.frame_id)}
                  onToggleTarget={(t) => toggleTarget(scene, t)}
                  onRegenType={(t) => setRegenType(scene.frame_id, t)}
                />
              ))}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function SceneCard({
  scene,
  music,
  mediaCollapsed,
  collapsed,
  onToggleCollapsed,
  editMode,
  selection,
  onToggleTarget,
  onRegenType,
}: {
  scene: SceneBoardScene;
  music: SceneBoardDTO["music"];
  mediaCollapsed: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  editMode: boolean;
  selection?: SceneBoardRegenSelection;
  onToggleTarget: (t: SceneBoardRegenTarget) => void;
  onRegenType: (t: SceneBoardRegenType) => void;
}) {
  const showMedia = !mediaCollapsed && !collapsed;
  const selected = new Set(selection?.targets || []);

  return (
    <section
      className={cn(
        "overflow-hidden rounded-xl border border-white/10 bg-white/[0.03]",
        selection && "ring-1 ring-amber-400/40",
      )}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-white/5 px-3 py-2.5">
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-white/5 hover:text-foreground"
          title={collapsed ? "Развернуть" : "Свернуть"}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </button>
        <div className="flex h-7 min-w-7 items-center justify-center rounded-md bg-amber-400/15 px-2 font-mono text-xs font-semibold text-amber-200">
          {scene.number}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">Сцена {scene.number}</span>
            <Badge variant="muted" className="h-5 normal-case tracking-normal">
              {scene.status}
            </Badge>
            {scene.timeslot_label ? (
              <span className="font-mono text-[11px] text-emerald-300/90">
                {scene.timeslot_label}
                {scene.duration_seconds != null
                  ? ` · ${scene.duration_seconds.toFixed(1)}s`
                  : ""}
              </span>
            ) : (
              <span className="text-[11px] text-amber-300/80">таймслот не задан</span>
            )}
          </div>
        </div>
        {scene.missing.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {scene.missing.slice(0, 4).map((m) => (
              <Badge
                key={m}
                variant="warning"
                className="h-5 normal-case tracking-normal"
              >
                {MISSING_LABELS[m] || m}
              </Badge>
            ))}
            {scene.missing.length > 4 && (
              <Badge variant="muted" className="h-5 normal-case tracking-normal">
                +{scene.missing.length - 4}
              </Badge>
            )}
          </div>
        )}
      </header>

      {!collapsed && (
        <div className="grid gap-0 lg:grid-cols-[minmax(220px,1fr)_minmax(0,2fr)]">
          <div className="border-b border-white/5 p-3 lg:border-b-0 lg:border-r">
            <SlotLabel>Закадровый текст</SlotLabel>
            <p className="mt-1 whitespace-pre-wrap text-[12px] leading-relaxed text-foreground/90">
              {scene.voiceover_text?.trim() || (
                <span className="text-muted-foreground">— нет текста —</span>
              )}
            </p>
            {editMode && (
              <TargetToggle
                active={selected.has("voiceover_text")}
                onClick={() => onToggleTarget("voiceover_text")}
                label="Перегенерировать текст"
              />
            )}

            <div className="mt-4">
              <SlotLabel icon={<UserRound className="h-3 w-3" />}>
                Персонажи
              </SlotLabel>
              {(scene.characters.length === 0 && scene.items.length === 0) ? (
                <p className="mt-1 text-[11px] text-muted-foreground">не прикреплены</p>
              ) : (
                <div className="mt-2 flex flex-wrap gap-2">
                  {[...scene.characters, ...scene.items].map((c) => (
                    <div
                      key={`${c.kind}-${c.id}`}
                      className="flex w-[72px] flex-col overflow-hidden rounded-lg border border-white/10 bg-black/20"
                    >
                      <div className="aspect-square bg-black/40">
                        {c.preview_url ? (
                          <img
                            src={c.preview_url}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <div className="flex h-full items-center justify-center text-[9px] text-muted-foreground">
                            нет
                          </div>
                        )}
                      </div>
                      <span className="truncate px-1 py-0.5 font-mono text-[9px] text-muted-foreground">
                        {c.name}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {editMode && (
                <TargetToggle
                  active={selected.has("characters")}
                  onClick={() => onToggleTarget("characters")}
                  label="Перегенерировать персонажей"
                />
              )}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2">
              <AudioMini
                label="Озвучка"
                slot={scene.audio}
                editMode={editMode}
                selected={selected.has("audio")}
                onToggle={() => onToggleTarget("audio")}
              />
              <AudioMini
                label="Музыка"
                slot={
                  music.present
                    ? {
                        artifact_uuid: null,
                        path: music.path,
                        preview_url: music.preview_url,
                        present: true,
                      }
                    : { artifact_uuid: null, path: null, preview_url: null, present: false }
                }
                hint={music.label}
                editMode={editMode}
                selected={selected.has("music")}
                onToggle={() => onToggleTarget("music")}
              />
            </div>

            {editMode && selection && (
              <div className="mt-4">
                <SlotLabel>Тип перегенерации</SlotLabel>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {(Object.keys(REGEN_TYPE_LABELS) as SceneBoardRegenType[]).map(
                    (t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => onRegenType(t)}
                        className={cn(
                          "rounded-md border px-2 py-1 text-[10px] transition",
                          selection.regen_type === t
                            ? "border-amber-400/50 bg-amber-400/15 text-amber-100"
                            : "border-white/10 text-muted-foreground hover:border-white/20",
                        )}
                      >
                        {REGEN_TYPE_LABELS[t]}
                      </button>
                    ),
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="p-3">
            {showMedia ? (
              <div
                className={cn(
                  "grid gap-3",
                  scene.has_shot2
                    ? "sm:grid-cols-2 xl:grid-cols-4"
                    : "sm:grid-cols-2",
                )}
              >
                <MediaTile
                  title="Кадр 1"
                  icon={<ImageIcon className="h-3 w-3" />}
                  kind="image"
                  slot={scene.image_shot1}
                  editMode={editMode}
                  selected={selected.has("image_shot1")}
                  onToggle={() => onToggleTarget("image_shot1")}
                />
                {scene.has_shot2 && (
                  <MediaTile
                    title="Кадр 2"
                    icon={<ImageIcon className="h-3 w-3" />}
                    kind="image"
                    slot={scene.image_shot2}
                    editMode={editMode}
                    selected={selected.has("image_shot2")}
                    onToggle={() => onToggleTarget("image_shot2")}
                  />
                )}
                <MediaTile
                  title="Видео 1"
                  icon={<Video className="h-3 w-3" />}
                  kind="video"
                  slot={scene.video_shot1}
                  editMode={editMode}
                  selected={selected.has("video_shot1")}
                  onToggle={() => onToggleTarget("video_shot1")}
                />
                {scene.has_shot2 && (
                  <MediaTile
                    title="Видео 2"
                    icon={<Video className="h-3 w-3" />}
                    kind="video"
                    slot={scene.video_shot2}
                    editMode={editMode}
                    selected={selected.has("video_shot2")}
                    onToggle={() => onToggleTarget("video_shot2")}
                  />
                )}
              </div>
            ) : (
              <p className="py-6 text-center text-[11px] text-muted-foreground">
                Медиа свёрнуто
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function SlotLabel({
  children,
  icon,
}: {
  children: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
      {icon}
      {children}
    </div>
  );
}

function MediaTile({
  title,
  icon,
  kind,
  slot,
  editMode,
  selected,
  onToggle,
}: {
  title: string;
  icon: React.ReactNode;
  kind: "image" | "video";
  slot: SceneBoardMediaSlot | null | undefined;
  editMode: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const present = Boolean(slot?.present && slot.preview_url);
  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border bg-black/25",
        selected ? "border-amber-400/50" : "border-white/10",
      )}
    >
      <div className="flex items-center justify-between gap-1 border-b border-white/5 px-2 py-1.5">
        <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
          {icon}
          {title}
        </div>
        <Badge
          variant={present ? "success" : "warning"}
          className="h-4 normal-case tracking-normal"
        >
          {present ? "есть" : "нет"}
        </Badge>
      </div>
      <div className="aspect-[9/12] bg-black/40">
        {present && slot?.preview_url ? (
          kind === "video" ? (
            <video
              src={slot.preview_url}
              controls
              className="h-full w-full object-contain"
            />
          ) : (
            <img
              src={slot.preview_url}
              alt=""
              className="h-full w-full object-cover object-top"
            />
          )
        ) : (
          <div className="flex h-full items-center justify-center text-[11px] text-muted-foreground">
            отсутствует
          </div>
        )}
      </div>
      {editMode && (
        <button
          type="button"
          onClick={onToggle}
          className={cn(
            "w-full border-t border-white/5 px-2 py-1.5 text-[10px] transition",
            selected
              ? "bg-amber-400/15 text-amber-100"
              : "text-muted-foreground hover:bg-white/5",
          )}
        >
          {selected ? "Выбрано для перегенерации" : "Выбрать для перегенерации"}
        </button>
      )}
    </div>
  );
}

function AudioMini({
  label,
  slot,
  hint,
  editMode,
  selected,
  onToggle,
}: {
  label: string;
  slot: SceneBoardMediaSlot | null | undefined;
  hint?: string;
  editMode: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const present = Boolean(slot?.present);
  return (
    <div
      className={cn(
        "rounded-lg border p-2",
        selected ? "border-amber-400/50 bg-amber-400/5" : "border-white/10 bg-black/20",
      )}
    >
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {label === "Музыка" ? (
          <Music2 className="h-3 w-3" />
        ) : (
          <Volume2 className="h-3 w-3" />
        )}
        {label}
      </div>
      {present && slot?.preview_url ? (
        <audio controls className="mt-1.5 w-full h-8" src={slot.preview_url} />
      ) : (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          {hint || "нет файла"}
        </p>
      )}
      {editMode && (
        <button
          type="button"
          onClick={onToggle}
          className="mt-1.5 text-[10px] text-primary hover:underline"
        >
          {selected ? "Снять выбор" : "Выбрать"}
        </button>
      )}
    </div>
  );
}

function TargetToggle({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "mt-2 rounded-md border px-2 py-1 text-[10px] transition",
        active
          ? "border-amber-400/50 bg-amber-400/15 text-amber-100"
          : "border-white/10 text-muted-foreground hover:border-white/20",
      )}
    >
      {active ? `✓ ${label}` : label}
    </button>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-lg border border-dashed border-white/10 px-4 py-10 text-center text-sm text-muted-foreground">
      {text}
    </p>
  );
}
