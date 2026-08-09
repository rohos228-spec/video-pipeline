"use client";

/**
 * Палитра «+ Добавить»: ноды и группы нод в одном поповере.
 * Ноды — карточки по категориям с поиском. Группы — карточки с описанием,
 * составом и управлением: вставка на канвас, переименование/описание/
 * категория, удаление (пользовательские), создание из выделения канваса.
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
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { NodeGroupSummary } from "@/lib/types";
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

// sd_agent/sd_assemble — legacy-типы старых канвасов; scene-агенты теперь
// создаются группой «Сцены: веер агентов» как ноды «Работа с GPT».
const HIDDEN_NODE_TYPES = new Set(["excel_feed", "sd_agent", "sd_assemble"]);

const GROUP_CATEGORY_OPTIONS = NODE_CATEGORY_ORDER.filter((c) => c !== "hitl");

/**
 * Выбор категории без нативного <select>: системный дропдаун открывается
 * поверх поповера и съедает клики. Здесь — inline-список в потоке формы.
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
  const addableByCategory = useMemo(() => {
    const map = new Map<NodeCategory, string[]>();
    for (const t of addable) {
      const cat = getNodeSpec(t).category;
      map.set(cat, [...(map.get(cat) ?? []), t]);
    }
    return map;
  }, [addable]);

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

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) setSearch("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="h-8 gap-1 border border-primary/30 bg-primary/10 text-xs text-primary hover:bg-primary/20"
          title="Добавить ноду или группу нод на канвас"
        >
          <Plus className="h-3.5 w-3.5" />
          Добавить
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[380px] p-0">
        <div className="flex items-center gap-1 border-b border-white/10 p-2">
          <button
            type="button"
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
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
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
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
          <div className="relative ml-auto w-[150px]">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск…"
              className="h-7 pl-7 text-xs"
            />
          </div>
        </div>

        <ScrollArea className="h-[420px]">
          {tab === "nodes" ? (
            <div className="p-2">
              {NODE_CATEGORY_ORDER.filter((cat) =>
                (addableByCategory.get(cat) ?? []).some(matchNode),
              ).map((cat) => (
                <div key={cat} className="mb-2 last:mb-0">
                  <div className="px-1.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {NODE_CATEGORY_LABELS[cat]}
                  </div>
                  <div className="grid grid-cols-1 gap-1">
                    {(addableByCategory.get(cat) ?? [])
                      .filter(matchNode)
                      .map((t) => {
                        const spec = getNodeSpec(t);
                        const Icon = getNodeIcon(spec.iconKey);
                        return (
                          <button
                            key={t}
                            type="button"
                            className="flex items-start gap-2.5 rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors hover:border-primary/30 hover:bg-primary/5"
                            onClick={() => {
                              onAddNode(t);
                              setOpen(false);
                            }}
                          >
                            <span
                              className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                              style={{
                                background: `linear-gradient(145deg, hsl(${spec.accent} / 0.3), hsl(${spec.accent} / 0.08))`,
                                color: `hsl(${spec.accent})`,
                              }}
                            >
                              <Icon className="h-3.5 w-3.5" />
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
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <GroupsTab
              groups={(nodeGroups.data ?? []).filter(matchGroup)}
              loading={nodeGroups.isLoading}
              projectId={projectId}
              getSelectedNodeIds={getSelectedNodeIds}
              onInsert={insertGroup}
              onChanged={() => qc.invalidateQueries({ queryKey: ["node-groups"] })}
            />
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}

function GroupsTab({
  groups,
  loading,
  projectId,
  getSelectedNodeIds,
  onInsert,
  onChanged,
}: {
  groups: NodeGroupSummary[];
  loading: boolean;
  projectId: number | null;
  getSelectedNodeIds: () => string[];
  onInsert: (groupId: string) => Promise<void>;
  onChanged: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  return (
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
          className="flex items-center gap-2 rounded-lg border border-dashed border-primary/40 bg-primary/5 px-2.5 py-2 text-left text-xs text-primary transition-colors hover:bg-primary/10"
          onClick={() => setCreating(true)}
        >
          <PackagePlus className="h-4 w-4 shrink-0" />
          <span>
            <span className="block font-medium">Сохранить выделение как группу</span>
            <span className="mt-0.5 block text-[10px] text-muted-foreground">
              Выдели ноды на канвасе (Ctrl+клик) — в группу уйдут типы, промты,
              конфиги, позиции и связи между ними.
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
      {groups.map((g) => (
        <GroupCard
          key={g.id}
          group={g}
          busy={busyId === g.id}
          setBusy={(v) => setBusyId(v ? g.id : null)}
          onInsert={onInsert}
          onChanged={onChanged}
        />
      ))}
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
      toast.error("Ничего не выделено — выдели ноды на канвасе (Ctrl+клик)");
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
    <div className="flex flex-col gap-1.5 rounded-lg border border-primary/30 bg-primary/5 p-2">
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

function GroupCard({
  group,
  busy,
  setBusy,
  onInsert,
  onChanged,
}: {
  group: NodeGroupSummary;
  busy: boolean;
  setBusy: (v: boolean) => void;
  onInsert: (groupId: string) => Promise<void>;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [title, setTitle] = useState(group.title);
  const [description, setDescription] = useState(group.description);
  const [category, setCategory] = useState(group.category);
  const hue = groupHue(group.id);
  const catLabel =
    NODE_CATEGORY_LABELS[group.category as NodeCategory] ?? group.category;
  const shownNodes = group.nodes.slice(0, 5);
  const hiddenCount = group.nodes.length - shownNodes.length;

  const saveEdit = async () => {
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
      setEditing(false);
      onChanged();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    setBusy(true);
    try {
      await api.deleteNodeGroup(group.id);
      toast.success(`Группа «${group.title}» удалена`);
      onChanged();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  if (editing) {
    return (
      <div className="flex flex-col gap-1.5 rounded-lg border border-white/15 bg-white/[0.03] p-2">
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Название группы"
          className="h-7 text-xs"
          autoFocus
        />
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Описание"
          className="min-h-[48px] text-xs"
        />
        <CategoryPicker value={category} onChange={setCategory} />
        <div className="mt-0.5 flex gap-1.5">
          <Button
            size="sm"
            className="h-7 flex-1 gap-1 text-xs"
            disabled={busy}
            onClick={() => void saveEdit()}
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
            className="h-7 text-xs"
            disabled={busy}
            onClick={() => {
              setEditing(false);
              setTitle(group.title);
              setDescription(group.description);
              setCategory(group.category);
            }}
          >
            Отмена
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-white/10 bg-white/[0.02] p-2 transition-colors hover:border-white/20"
      style={{ borderLeftWidth: 3, borderLeftColor: `hsl(${hue} 70% 55% / 0.7)` }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <span className="truncate text-xs font-semibold">{group.title}</span>
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[9px] font-medium",
                group.builtin
                  ? "border-sky-400/30 bg-sky-500/10 text-sky-300"
                  : "border-emerald-400/30 bg-emerald-500/10 text-emerald-300",
              )}
            >
              {group.builtin ? "встроенная" : "своя"}
            </span>
          </div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">
            {catLabel} · {group.node_count} нод
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {!group.builtin && (
            <>
              <button
                type="button"
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-white/10 hover:text-foreground"
                title="Переименовать / описание / категория"
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                type="button"
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive"
                title="Удалить группу"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </>
          )}
        </div>
      </div>

      {group.description && (
        <p className="mt-1 line-clamp-2 text-[10px] leading-snug text-muted-foreground">
          {group.description}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap gap-1">
        {shownNodes.map((n) => (
          <span
            key={n.key}
            className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[9px] text-muted-foreground"
          >
            {n.label}
          </span>
        ))}
        {hiddenCount > 0 && (
          <span className="rounded-full border border-white/10 bg-black/30 px-1.5 py-0.5 text-[9px] text-muted-foreground">
            +{hiddenCount}
          </span>
        )}
      </div>

      {confirmDelete ? (
        <div className="mt-2 rounded-md border border-destructive/40 bg-destructive/10 p-1.5">
          <div className="text-[10px] text-destructive">
            Удалить группу «{group.title}»? Ноды на канвасах проектов останутся.
          </div>
          <div className="mt-1 flex gap-1">
            <Button
              size="sm"
              variant="destructive"
              className="h-6 flex-1 gap-1 text-[10px]"
              disabled={busy}
              onClick={() => void doDelete()}
            >
              {busy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3" />
              )}
              Удалить
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-[10px]"
              disabled={busy}
              onClick={() => setConfirmDelete(false)}
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          className="mt-2 h-7 w-full gap-1 border border-primary/25 bg-primary/5 text-xs text-primary hover:bg-primary/15"
          disabled={busy}
          onClick={() => void onInsert(group.id)}
        >
          {busy ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Boxes className="h-3 w-3" />
          )}
          На канвас
        </Button>
      )}
    </div>
  );
}
