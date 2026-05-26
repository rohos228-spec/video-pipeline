"use client";

import {
  ArrowRight,
  Ban,
  Download,
  Eye,
  FileSpreadsheet,
  MessageSquareText,
  Play,
  Plus,
  Trash2,
  Unlink,
  X,
} from "lucide-react";
import type { NodePromptSlot } from "@/lib/node-prompts";
import {
  gptTextSlotForNode,
  isCustomPromptSlot,
  nodeTypeRequiresExcel,
  resolvePromptSlots,
} from "@/lib/node-prompts";
import { nodeSupportsGptText } from "@/lib/gpt-text-steps";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { NodeVMenuExcelPreview } from "./node-v-menu-excel";

export function NodeVMenu({
  open,
  nodeType,
  slots,
  disabled,
  projectId,
  onSelectPrompt,
  onOpenGptText,
  onAddPrompt,
  onRemovePrompt,
  onViewAllPrompts,
  onDownloadPrompts,
  onRunNode,
  onOpenAssets,
  onDetachNode,
  onToggleDisable,
  onDeleteNode,
  onClose,
  hasAssets,
}: {
  open: boolean;
  nodeType: string;
  slots: NodePromptSlot[];
  disabled: boolean;
  onSelectPrompt: (slot: NodePromptSlot) => void;
  onOpenGptText: () => void;
  onAddPrompt: () => void;
  onRemovePrompt: (slot: NodePromptSlot) => void;
  onViewAllPrompts: () => void;
  onDownloadPrompts: () => void;
  onRunNode: () => void;
  onOpenAssets?: () => void;
  onDetachNode: () => void;
  onToggleDisable: () => void;
  onDeleteNode: () => void;
  onClose: () => void;
  hasAssets: boolean;
  projectId?: number | null;
}) {
  if (!open) return null;

  const menuSlots = resolvePromptSlots(nodeType, slots);
  const excelSlot = menuSlots.find((s) => s.kind === "excel");
  const showExcelPreview = nodeTypeRequiresExcel(nodeType) && projectId != null;
  const gptTextSlot = gptTextSlotForNode(nodeType);
  const showGptText = nodeSupportsGptText(nodeType) && gptTextSlot;

  return (
    <div className="node-v-menu nodrag nopan nowheel absolute left-1/2 top-[calc(100%+8px)] z-[100] w-[min(340px,calc(100vw-2rem))] -translate-x-1/2 animate-in fade-in slide-in-from-top-2 duration-200">
      <div className="rounded-2xl border border-white/12 bg-gradient-to-b from-[hsl(240_8%_9%/0.98)] to-[hsl(240_10%_5%/0.99)] p-3 shadow-2xl shadow-black/60 backdrop-blur-xl">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-amber-400/90">
            Мастер-промты
          </span>
          <div className="flex items-center gap-1">
            <span className="text-[9px] text-muted-foreground">{menuSlots.length} шт.</span>
            <button
              type="button"
              className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-white/10 hover:text-foreground"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                onClose();
              }}
              title="Закрыть меню"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {menuSlots.length > 0 ? (
          <div className="mb-3 overflow-x-auto pb-1">
            <div className="flex min-w-min items-center gap-1">
              {menuSlots.map((slot, i) => (
                <div key={slot.id} className="relative flex items-center gap-1">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectPrompt(slot);
                    }}
                    className={cn(
                      "flex w-[88px] shrink-0 flex-col items-center rounded-xl border px-2 py-2 text-center transition-all",
                      slot.kind === "excel"
                        ? "border-emerald-500/40 bg-emerald-500/10 hover:border-emerald-400/60 hover:bg-emerald-500/20"
                        : "border-white/10 bg-white/[0.04] hover:border-amber-400/40 hover:bg-amber-400/10",
                    )}
                    title={slot.description || slot.title}
                  >
                    {slot.kind === "excel" ? (
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/25 text-emerald-300">
                        <FileSpreadsheet className="h-3.5 w-3.5" />
                      </span>
                    ) : (
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/20 text-[10px] font-bold text-primary">
                        {(excelSlot ? 1 : 0) +
                          menuSlots.slice(0, i).filter((s) => s.kind !== "excel").length +
                          1}
                      </span>
                    )}
                    <span className="mt-1 line-clamp-2 text-[9px] font-medium leading-tight">
                      {slot.kind === "excel" ? "Excel" : slot.title}
                    </span>
                    <span
                      className={cn(
                        "mt-0.5 text-[8px]",
                        slot.kind === "excel" ? "text-emerald-400/90" : "text-muted-foreground",
                      )}
                    >
                      {slotKindLabel(slot.kind)}
                    </span>
                  </button>
                  {isCustomPromptSlot(slot) && slot.kind !== "excel" && (
                    <button
                      type="button"
                      className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full border border-white/20 bg-destructive/90 text-white shadow"
                      title="Удалить промт"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemovePrompt(slot);
                      }}
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  )}
                  {i < menuSlots.length - 1 && (
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-amber-500/40" aria-hidden />
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onAddPrompt();
                }}
                className="flex h-[72px] w-[52px] shrink-0 flex-col items-center justify-center rounded-xl border border-dashed border-white/15 text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
                title="Добавить промт"
              >
                <Plus className="h-4 w-4" />
                <span className="mt-1 text-[8px]">ещё</span>
              </button>
            </div>
          </div>
        ) : (
          <p className="mb-3 text-[10px] text-muted-foreground">
            Для этой ноды нет файловых мастер-промтов.
          </p>
        )}

        {showExcelPreview && excelSlot && (
          <NodeVMenuExcelPreview
            open={open}
            projectId={projectId!}
            nodeType={nodeType}
            onOpen={() => onSelectPrompt(excelSlot)}
          />
        )}

        {showGptText && (
          <div className="mb-3 border-t border-white/8 pt-3">
            <span className="mb-1.5 block text-[9px] font-semibold uppercase tracking-widest text-violet-300/90">
              Текст для GPT
            </span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpenGptText();
              }}
              className="flex w-full items-start gap-2 rounded-xl border border-violet-400/25 bg-violet-500/10 px-3 py-2.5 text-left transition hover:border-violet-400/50 hover:bg-violet-500/15"
            >
              <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-violet-300" />
              <span className="min-w-0">
                <span className="block text-[11px] font-medium">Текстовый вариант</span>
                <span className="mt-0.5 block text-[9px] leading-snug text-muted-foreground">
                  Сопроводительное сообщение в диалог ChatGPT — редактируется отдельно от файлов
                  промтов
                </span>
              </span>
            </button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-1 border-t border-white/8 pt-2">
          <MenuAction icon={Eye} label="Просмотр промтов" onClick={onViewAllPrompts} />
          <MenuAction icon={Download} label="Скачать промты" onClick={onDownloadPrompts} />
          <MenuAction icon={Play} label="Запустить шаг" onClick={onRunNode} />
          {hasAssets && onOpenAssets && (
            <MenuAction icon={Eye} label="Файлы и превью" onClick={onOpenAssets} />
          )}
          {excelSlot && (
            <MenuAction
              icon={FileSpreadsheet}
              label="Просмотр Excel"
              onClick={() => onSelectPrompt(excelSlot)}
            />
          )}
          <MenuAction icon={Unlink} label="Открепить связи" onClick={onDetachNode} />
          <MenuAction
            icon={Ban}
            label={disabled ? "Включить ноду" : "Отключить ноду"}
            onClick={onToggleDisable}
          />
          <MenuAction
            icon={Trash2}
            label="Удалить ноду"
            onClick={onDeleteNode}
            destructive
          />
        </div>
      </div>
      <div
        className="absolute -top-1.5 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-white/12 bg-[hsl(240_8%_9%)]"
        aria-hidden
      />
    </div>
  );
}

function MenuAction({
  icon: Icon,
  label,
  onClick,
  destructive,
}: {
  icon: typeof Eye;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn(
        "h-8 justify-start gap-1.5 px-2 text-[10px] font-normal",
        destructive && "text-destructive hover:text-destructive",
      )}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <Icon className="h-3 w-3 shrink-0" />
      {label}
    </Button>
  );
}

function slotKindLabel(kind: NodePromptSlot["kind"]): string {
  if (kind === "gpt") return "файл";
  if (kind === "text") return "текст GPT";
  if (kind === "excel") return "project.xlsx";
  if (kind === "blocks") return "outsee";
  if (kind === "frame_prompts") return "кадры";
  return kind;
}
