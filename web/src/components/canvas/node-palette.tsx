"use client";

/**
 * «Библиотека» — менеджер нод и групп нод (кнопка «+ Добавить» на тулбаре).
 * Ноды: категории слева, карточки в центре, детали ноды справа (клик по
 * карточке — просмотр, двойной клик — сразу на канвас). Группы: список слева,
 * справа просмотр дизайна группы (мини-канвас нод и связей), состав, промты
 * и управление: вставка, переименование/описание/категория, удаление,
 * создание из выделения канваса.
 */

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Check,
  Loader2,
  PackagePlus,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { NodeGroupDetail, NodeGroupSummary } from "@/lib/types";
import {
  getNodeSpec,
  NODE_CATALOG,
  NODE_CATEGORY_LABELS,
  NODE_CATEGORY_ORDER,
  type NodeCategory,
} from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { groupHue } from "@/lib/group-color";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { GroupPreview } from "./group-preview";

// sd_agent/sd_assemble — legacy-типы старых канвасов; scene-агенты теперь
// создаются группой «Сцены: веер агентов» как ноды «Работа с GPT».
const HIDDEN_NODE_TYPES = new Set(["excel_feed", "sd_agent", "sd_assemble"]);

const GROUP_CATEGORY_OPTIONS = NODE_CATEGORY_ORDER.filter((c) => c !== "hitl");

export function NodePalette({
  projectId,
  onAddNode,
  getSelectedNodeIds,
}: {
  projectId: number | null;
  onAddNode: (type: string) => void;
  getSelectedNodeIds: () => string[];
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"nodes" | "groups">("nodes");
  const [search, setSearch] = useState("");
  const [selectedNodeType, setSelectedNodeType] = useState<string | null>(null);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  const nodeGroups = useQuery({
    queryKey: ["node-groups"],
    queryFn: api.listNodeGroups,
    staleTime: 60_000,
  });

  const addable = useMemo(
    () =>
      Object.keys(NODE_CATALOG).filter(
        (t) => !t.startsWith("hitl_") && !HIDDEN_NODE_TYPES.has(t),
      ),
    [],
  );

  const query = search.trim().toLowerCase();
  const matchNode = (t: string) => {
    if (!query) return true;
    const spec = getNodeSpec(t);
    return (
      spec.label.toLowerCase().includes(query) ||
      spec.description.toLowerCase().includes(query) ||
      t.toLowerCase().includes(query)
    );
  };
  const matchGroup = (g: NodeGroupSummary) => {
    if (!query) return true;
    return (
      g.title.toLowerCase().includes(query) ||
      g.description.toLowerCase().includes(query) ||
      g.nodes.some((n) => n.label.toLowerCase().includes(query))
    );
  };

  const addNode = (type: string) => {
    onAddNode(type);
    const spec = getNodeSpec(type);
    toast.success(`Нода «${spec.label}» добавлена на канвас`);
  };

  const insertGroup = async (groupId: string) => {
    if (!projectId) {
      toast.error("Сначала выбери проект");
      return;
    }
    try {
      const res = await api.insertNodeGroup(projectId, groupId);
      await qc.invalidateQueries({ queryKey: ["project", projectId] });
      await qc.invalidateQueries({ queryKey: ["project-run", projectId] });
      toast.success(`Группа вставлена: ${res.nodes.length} нод после «${res.after}»`);
      setOpen(false);
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  };

  const groups = (nodeGroups.data ?? []).filter(matchGroup);
  const effectiveGroupId =
    selectedGroupId && groups.some((g) => g.id === selectedGroupId)
      ? selectedGroupId
      : (groups[0]?.id ?? null);

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) {
          setSearch("");
          setSelectedNodeType(null);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="h-8 gap-1.5 border-white/15 bg-zinc-800/80 px-2.5 text-xs text-foreground hover:bg-zinc-700 hover:text-white"
          title="Библиотека: добавить ноду или готовую группу нод на канвас"
        >
          <Plus className="h-3.5 w-3.5 text-emerald-400" />
          <span>Добавить</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[640px] max-h-[88vh] w-[960px] max-w-[94vw] flex-col gap-0 overflow-hidden p-0">
        <div className="flex shrink-0 items-center gap-2 border-b border-white/10 px-4 py-3">
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Boxes className="h-4 w-4 text-primary" />
            Библиотека
          </DialogTitle>
          <div className="ml-2 flex items-center gap-1">
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                tab === "nodes"
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setTab("nodes")}
            >
              Ноды
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                tab === "groups"
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setTab("groups")}
            >
              Группы
              {(nodeGroups.data ?? []).length > 0 && (
                <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[9px]">
                  {(nodeGroups.data ?? []).length}
                </span>
              )}
            </button>
          </div>
          <div className="relative ml-auto w-[220px]">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск по названию и описанию…"
              className="h-7 pl-7 text-xs"
            />
          </div>
        </div>

        {tab === "nodes" ? (
          <NodesTab
            addable={addable}
            matchNode={matchNode}
            selectedNodeType={selectedNodeType}
            onSelectNodeType={setSelectedNodeType}
            onAddNode={addNode}
          />
        ) : (
          <GroupsTab
            groups={groups}
            loading={nodeGroups.isLoading}
            projectId={projectId}
            selectedGroupId={effectiveGroupId}
            onSelectGroup={setSelectedGroupId}
            getSelectedNodeIds={getSelectedNodeIds}
            onInsert={insertGroup}
            onChanged={() => {
              void qc.invalidateQueries({ queryKey: ["node-groups"] });
              void qc.invalidateQueries({ queryKey: ["node-group"] });
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ── Ноды ─────────────────────────────────────────────────────────────── */

function NodesTab({
  addable,
  matchNode,
  selectedNodeType,
  onSelectNodeType,
  onAddNode,
}: {
  addable: string[];
  matchNode: (t: string) => boolean;
  selectedNodeType: string | null;
  onSelectNodeType: (t: string | null) => void;
  onAddNode: (t: string) => void;
}) {
  const [category, setCategory] = useState<NodeCategory | "all">("all");
  const byCategory = useMemo(() => {
    const map = new Map<NodeCategory, string[]>();
    for (const t of addable) {
      const cat = getNodeSpec(t).category;
      map.set(cat, [...(map.get(cat) ?? []), t]);
    }
    return map;
  }, [addable]);

  const visible = addable.filter(
    (t) => (category === "all" || getNodeSpec(t).category === category) && matchNode(t),
  );
  const selectedSpec = selectedNodeType ? getNodeSpec(selectedNodeType) : null;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="w-[168px] shrink-0 border-r border-white/10 p-2">
        <CategoryRailButton
          active={category === "all"}
          label="Все"
          count={addable.filter(matchNode).length}
          onClick={() => setCategory("all")}
        />
        {NODE_CATEGORY_ORDER.filter((c) => c !== "hitl" && byCategory.has(c)).map(
          (c) => (
            <CategoryRailButton
              key={c}
              active={category === c}
              label={NODE_CATEGORY_LABELS[c]}
              count={(byCategory.get(c) ?? []).filter(matchNode).length}
              onClick={() => setCategory(c)}
            />
          ),
        )}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="grid grid-cols-2 gap-1.5 p-3">
          {visible.map((t) => {
            const spec = getNodeSpec(t);
            const Icon = getNodeIcon(spec.iconKey);
            const active = selectedNodeType === t;
            return (
              <button
                key={t}
                type="button"
                title="Клик — просмотр; двойной клик — сразу на канвас"
                className={cn(
                  "flex items-start gap-2.5 rounded-xl border px-2.5 py-2 text-left transition-colors",
                  active
                    ? "border-primary/50 bg-primary/10"
                    : "border-white/10 bg-white/[0.02] hover:border-primary/30 hover:bg-primary/5",
                )}
                onClick={() => onSelectNodeType(t)}
                onDoubleClick={() => onAddNode(t)}
              >
                <span
                  className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                  style={{
                    background: `linear-gradient(145deg, hsl(${spec.accent} / 0.3), hsl(${spec.accent} / 0.08))`,
                    color: `hsl(${spec.accent})`,
                  }}
                >
                  <Icon className="h-4 w-4" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-xs font-medium">
                    {spec.label}
                  </span>
                  <span className="mt-0.5 line-clamp-2 block text-[10px] leading-snug text-muted-foreground">
                    {spec.description}
                  </span>
                </span>
              </button>
            );
          })}
          {visible.length === 0 && (
            <div className="col-span-2 py-10 text-center text-xs text-muted-foreground">
              Ничего не найдено.
            </div>
          )}
        </div>
      </ScrollArea>

      <div className="flex w-[260px] shrink-0 flex-col border-l border-white/10 p-3">
        {selectedSpec ? (
          <>
            <div className="flex items-center gap-2.5">
              <span
                className="flex h-10 w-10 items-center justify-center rounded-full"
                style={{
                  background: `linear-gradient(145deg, hsl(${selectedSpec.accent} / 0.3), hsl(${selectedSpec.accent} / 0.08))`,
                  color: `hsl(${selectedSpec.accent})`,
                }}
              >
                {(() => {
                  const Icon = getNodeIcon(selectedSpec.iconKey);
                  return <Icon className="h-5 w-5" />;
                })()}
              </span>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">
                  {selectedSpec.label}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {selectedSpec.type}
                </div>
              </div>
            </div>
            <div className="mt-2">
              <span className="rounded-full border border-white/10 bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                {NODE_CATEGORY_LABELS[selectedSpec.category]}
              </span>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
              {selectedSpec.description || "Без описания."}
            </p>
            <div className="mt-auto pt-3">
              <Button
                size="sm"
                className="h-8 w-full gap-1.5 text-xs"
                onClick={() => onAddNode(selectedSpec.type)}
              >
                <Plus className="h-3.5 w-3.5" />
                Добавить на канвас
              </Button>
              <div className="mt-1.5 text-center text-[9px] text-muted-foreground">
                двойной клик по карточке — добавить сразу
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-center text-[11px] text-muted-foreground">
            Кликни по ноде,
            <br />
            чтобы посмотреть детали
          </div>
        )}
      </div>
    </div>
  );
}

function CategoryRailButton({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors",
        active
          ? "bg-primary/15 font-medium text-primary"
          : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
      )}
      onClick={onClick}
    >
      <span className="truncate">{label}</span>
      <span className="ml-1 rounded-full bg-black/30 px-1.5 py-0.5 text-[9px]">
        {count}
      </span>
    </button>
  );
}

/* ── Группы ───────────────────────────────────────────────────────────── */

function GroupsTab({
  groups,
  loading,
  projectId,
  selectedGroupId,
  onSelectGroup,
  getSelectedNodeIds,
  onInsert,
  onChanged,
}: {
  groups: NodeGroupSummary[];
  loading: boolean;
  projectId: number | null;
  selectedGroupId: string | null;
  onSelectGroup: (id: string | null) => void;
  getSelectedNodeIds: () => string[];
  onInsert: (groupId: string) => Promise<void>;
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[300px] shrink-0 flex-col border-r border-white/10">
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-1.5 p-2">
          {creating ? (
            <CreateGroupForm
              projectId={projectId}
              getSelectedNodeIds={getSelectedNodeIds}
              onDone={(created) => {
                setCreating(false);
                if (created) onChanged();
              }}
            />
          ) : (
            <button
              type="button"
              className="flex items-center gap-2 rounded-xl border border-dashed border-primary/40 bg-primary/5 px-2.5 py-2 text-left text-xs text-primary transition-colors hover:bg-primary/10"
              onClick={() => setCreating(true)}
            >
              <PackagePlus className="h-4 w-4 shrink-0" />
              <span>
                <span className="block font-medium">
                  Сохранить выделение как группу
                </span>
                <span className="mt-0.5 block text-[10px] text-muted-foreground">
                  Выдели ноды рамкой (правая кнопка мыши) — в группу уйдут типы,
                  промты, конфиги, позиции и связи.
                </span>
              </span>
            </button>
          )}

          {loading && (
            <div className="flex items-center justify-center py-6 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          )}
          {!loading && groups.length === 0 && (
            <div className="px-2 py-6 text-center text-xs text-muted-foreground">
              Групп нет — сохрани первую из выделения.
            </div>
          )}
          {groups.map((g) => {
            const hue = groupHue(g.id);
            const active = g.id === selectedGroupId;
            return (
              <button
                key={g.id}
                type="button"
                className={cn(
                  "rounded-xl border px-2.5 py-2 text-left transition-colors",
                  active
                    ? "border-primary/50 bg-primary/10"
                    : "border-white/10 bg-white/[0.02] hover:border-white/25",
                )}
                style={{ borderLeftWidth: 3, borderLeftColor: `hsl(${hue} 70% 55% / 0.8)` }}
                onClick={() => onSelectGroup(g.id)}
              >
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-xs font-semibold">{g.title}</span>
                  <span
                    className={cn(
                      "shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                      g.builtin
                        ? "border-sky-400/30 bg-sky-500/10 text-sky-300"
                        : "border-emerald-400/30 bg-emerald-500/10 text-emerald-300",
                    )}
                  >
                    {g.builtin ? "встроенная" : "своя"}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {NODE_CATEGORY_LABELS[g.category as NodeCategory] ?? g.category} ·{" "}
                  {g.node_count} нод
                </div>
              </button>
            );
          })}
        </div>
      </ScrollArea>
      </div>

      <div className="min-h-0 flex-1">
        {selectedGroupId ? (
          <GroupDetailPane
            key={selectedGroupId}
            groupId={selectedGroupId}
            onInsert={onInsert}
            onChanged={onChanged}
            onDeleted={() => onSelectGroup(null)}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-center text-[11px] text-muted-foreground">
            Кликни по группе слева,
            <br />
            чтобы посмотреть её дизайн
          </div>
        )}
      </div>
    </div>
  );
}

function GroupDetailPane({
  groupId,
  onInsert,
  onChanged,
  onDeleted,
}: {
  groupId: string;
  onInsert: (groupId: string) => Promise<void>;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const detail = useQuery({
    queryKey: ["node-group", groupId],
    queryFn: () => api.getNodeGroup(groupId),
    staleTime: 30_000,
  });
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (detail.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  const g = detail.data;
  if (!g) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-destructive">
        Не удалось загрузить группу.
      </div>
    );
  }
  const hue = groupHue(g.id);

  const doDelete = async () => {
    setBusy(true);
    try {
      await api.deleteNodeGroup(g.id);
      toast.success(`Группа «${g.title}» удалена`);
      onChanged();
      onDeleted();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: `hsl(${hue} 80% 62%)` }}
              />
              <span className="truncate text-sm font-semibold">{g.title}</span>
              <span
                className={cn(
                  "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                  g.builtin
                    ? "border-sky-400/30 bg-sky-500/10 text-sky-300"
                    : "border-emerald-400/30 bg-emerald-500/10 text-emerald-300",
                )}
              >
                {g.builtin ? "встроенная" : "своя"}
              </span>
            </div>
            <div className="mt-1 text-[10px] text-muted-foreground">
              {NODE_CATEGORY_LABELS[g.category as NodeCategory] ?? g.category} ·{" "}
              {g.nodes.length} нод · вставляется после «
              {g.default_after_type || "хвоста цепочки"}»
              {g.updated_at
                ? ` · обновлена ${new Date(g.updated_at).toLocaleString("ru-RU")}`
                : ""}
            </div>
          </div>
          {!g.builtin && !editing && (
            <div className="flex shrink-0 items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 text-xs"
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3 w-3" />
                Изменить
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 text-xs text-destructive hover:bg-destructive/10"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          )}
        </div>

        {editing ? (
          <EditGroupForm
            group={g}
            busy={busy}
            setBusy={setBusy}
            onDone={(saved) => {
              setEditing(false);
              if (saved) onChanged();
            }}
          />
        ) : (
          <>
            {g.description && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {g.description}
              </p>
            )}

            <div>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Дизайн группы
              </div>
              <GroupPreview detail={g} />
              <div className="mt-1 text-[9px] text-muted-foreground">
                <span className="text-emerald-400">вход</span> — ноды, принимающие
                связь от канваса · <span className="text-sky-400">выход</span> —
                нода, от которой уходит дальше ·{" "}
                <span className="text-emerald-400">Ок</span>/
                <span className="text-red-400">Не ок</span> — ветки проверок
              </div>
            </div>

            <div>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Состав ({g.nodes.length})
              </div>
              <div className="flex flex-wrap gap-1">
                {g.nodes.map((n) => (
                  <span
                    key={n.key}
                    title={n.description || n.label}
                    className="rounded-full border border-white/10 bg-black/30 px-2 py-0.5 text-[10px] text-muted-foreground"
                  >
                    {n.label}
                    {n.prompt_variant ? (
                      <span className="ml-1 text-violet-300">
                        · {n.prompt_variant}
                      </span>
                    ) : null}
                  </span>
                ))}
              </div>
            </div>

            {confirmDelete ? (
              <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-3">
                <div className="text-xs text-destructive">
                  Удалить группу «{g.title}»? Ноды, уже стоящие на канвасах
                  проектов, останутся на месте.
                </div>
                <div className="mt-2 flex gap-1.5">
                  <Button
                    size="sm"
                    variant="destructive"
                    className="h-7 flex-1 gap-1 text-xs"
                    disabled={busy}
                    onClick={() => void doDelete()}
                  >
                    {busy ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    Удалить группу
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs"
                    disabled={busy}
                    onClick={() => setConfirmDelete(false)}
                  >
                    Отмена
                  </Button>
                </div>
              </div>
            ) : (
              !editing && (
                <Button
                  size="sm"
                  className="h-9 w-full gap-1.5 text-xs"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void onInsert(g.id).finally(() => setBusy(false));
                  }}
                >
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Boxes className="h-3.5 w-3.5" />
                  )}
                  Вставить группу на канвас
                </Button>
              )
            )}
          </>
        )}
      </div>
    </ScrollArea>
  );
}

/* ── Формы ────────────────────────────────────────────────────────────── */

function EditGroupForm({
  group,
  busy,
  setBusy,
  onDone,
}: {
  group: NodeGroupDetail;
  busy: boolean;
  setBusy: (v: boolean) => void;
  onDone: (saved: boolean) => void;
}) {
  const [title, setTitle] = useState(group.title);
  const [description, setDescription] = useState(group.description);
  const [category, setCategory] = useState(group.category);

  const save = async () => {
    if (!title.trim()) {
      toast.error("Название не может быть пустым");
      return;
    }
    setBusy(true);
    try {
      await api.updateNodeGroup(group.id, {
        title: title.trim(),
        description: description.trim(),
        category,
      });
      toast.success("Группа обновлена");
      onDone(true);
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-primary/30 bg-primary/5 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-primary">
        Редактирование группы
      </div>
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Название группы"
        className="h-8 text-xs"
        autoFocus
      />
      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Описание — что делает связка"
        className="min-h-[64px] text-xs"
      />
      <CategoryPicker value={category} onChange={setCategory} />
      <div className="mt-1 flex gap-1.5">
        <Button
          size="sm"
          className="h-8 flex-1 gap-1 text-xs"
          disabled={busy}
          onClick={() => void save()}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Check className="h-3 w-3" />
          )}
          Сохранить
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 text-xs"
          disabled={busy}
          onClick={() => onDone(false)}
        >
          Отмена
        </Button>
      </div>
    </div>
  );
}

function CreateGroupForm({
  projectId,
  getSelectedNodeIds,
  onDone,
}: {
  projectId: number | null;
  getSelectedNodeIds: () => string[];
  onDone: (created: boolean) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<string>("planning");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!projectId) {
      toast.error("Сначала выбери проект");
      return;
    }
    const nodeIds = getSelectedNodeIds();
    if (nodeIds.length === 0) {
      toast.error("Ничего не выделено — выдели ноды рамкой (правая кнопка мыши)");
      return;
    }
    if (!title.trim()) {
      toast.error("Введи название группы");
      return;
    }
    setBusy(true);
    try {
      const g = await api.createNodeGroupFromSelection(projectId, {
        node_ids: nodeIds,
        title: title.trim(),
        description: description.trim(),
        category,
      });
      toast.success(`Группа «${g.title}» сохранена (${g.node_count} нод)`);
      onDone(true);
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-primary/30 bg-primary/5 p-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-primary">
        Новая группа из выделения
      </div>
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Название (например «Моя связка GPT»)"
        className="h-7 text-xs"
        autoFocus
      />
      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Описание — что делает связка (необязательно)"
        className="min-h-[48px] text-xs"
      />
      <CategoryPicker value={category} onChange={setCategory} />
      <div className="mt-0.5 flex gap-1.5">
        <Button
          size="sm"
          className="h-7 flex-1 gap-1 text-xs"
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Check className="h-3 w-3" />
          )}
          Создать группу
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-xs"
          disabled={busy}
          onClick={() => onDone(false)}
        >
          Отмена
        </Button>
      </div>
    </div>
  );
}

/**
 * Выбор категории без нативного <select>: системный дропдаун открывается
 * поверх диалога и съедает клики. Здесь — inline-список в потоке формы.
 */
function CategoryPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (cat: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const label =
    NODE_CATEGORY_LABELS[value as NodeCategory] ?? value ?? "Категория";
  return (
    <div className="rounded-md border border-white/10 bg-black/30">
      <button
        type="button"
        className="flex h-7 w-full items-center justify-between px-2 text-xs"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-muted-foreground">Категория:</span>
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-0.5 border-t border-white/10 p-1">
          {GROUP_CATEGORY_OPTIONS.map((c) => (
            <button
              key={c}
              type="button"
              className={cn(
                "rounded px-1.5 py-1 text-left text-[10px] transition-colors",
                c === value
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
              )}
              onClick={() => {
                onChange(c);
                setOpen(false);
              }}
            >
              {NODE_CATEGORY_LABELS[c]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}