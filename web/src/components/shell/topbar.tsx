"use client";

import { useState, createContext, useContext } from "react";
import { Activity, Network, Film, Bot, Database, User, Settings, Code2, FolderOpen, HardDrive, CreditCard, Wallet } from "lucide-react";
import { LogPanel } from "@/components/logs/log-panel";
import { FramesGrid } from "@/components/frames/frames-grid";
import { StudioVersionBadge } from "@/components/shell/studio-version-badge";
import { TextLlmPicker } from "@/components/shell/text-llm-picker";
import { BugReportButton } from "@/components/shell/bug-report-button";
import { UserPanel, UserPrefsApplier } from "@/components/panels/top/user-panel";
import { StoragePanel } from "@/components/panels/left-top/storage-panel";
import { PaymentsPanel } from "@/components/panels/left-bottom/payments-panel";
import { PayoutsPanel } from "@/components/panels/left-bottom/payouts-panel";

interface UiState {
  framesProjectId: number | null;
  openFrames: (projectId: number) => void;
  openOutsee: (projectId?: number | null) => void;
}

const UiContext = createContext<UiState | null>(null);

export function useUi(): UiState {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error("useUi must be used within AppShell / Topbar");
  return ctx;
}

export function Topbar({ children }: { children?: React.ReactNode }) {
  const [logsOpen, setLogsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const [railPanel, setRailPanel] = useState<null | "Хранилище" | "Оплата" | "Выплаты">(null);
  const togglePanel = (k: "Хранилище" | "Оплата" | "Выплаты") => setRailPanel((v) => (v === k ? null : k));
  const [framesOpen, setFramesOpen] = useState(false);
  const [framesProjectId, setFramesProjectId] = useState<number | null>(null);

  const openFrames = (id: number) => {
    setFramesProjectId(id);
    setFramesOpen(true);
  };

  const openOutsee = (projectId?: number | null) => {
    window.dispatchEvent(
      new CustomEvent("studio-open-outsee", {
        detail: { projectId: projectId ?? null },
      }),
    );
  };

  return (
    <UiContext.Provider value={{ framesProjectId, openFrames, openOutsee }}>
      <div className="flex h-full min-h-0 w-full flex-1 flex-row">
      <nav className="flex w-[64px] shrink-0 flex-col items-center gap-1 border-r border-white/[0.06] bg-[#0e0e10] py-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <a href="/home" title="Главная"><img src="/via-logo.png" alt="VIA" className="mb-3 w-[46px] select-none" draggable={false} /></a>
        <RailBtn icon={<Film className="h-[18px] w-[18px]" />} label="Генерация" title="Полный интерфейс генерации" onClick={() => openOutsee()} />
        <RailBtn icon={<FolderOpen className="h-[18px] w-[18px]" />} label="Проекты" title="Показать / скрыть панель проектов" onClick={() => window.dispatchEvent(new CustomEvent("studio-toggle-projects-sidebar"))} />
        <RailBtn icon={<HardDrive className="h-[18px] w-[18px]" />} label="Хранилище" active={railPanel === "Хранилище"} onClick={() => togglePanel("Хранилище")} />
        <div className="flex-1" />
        <RailBtn icon={<CreditCard className="h-[18px] w-[18px]" />} label="Оплата" active={railPanel === "Оплата"} onClick={() => togglePanel("Оплата")} />
        <RailBtn icon={<Wallet className="h-[18px] w-[18px]" />} label="Выплаты" active={railPanel === "Выплаты"} onClick={() => togglePanel("Выплаты")} />
        {railPanel === "Хранилище" ? <StoragePanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel === "Оплата" ? <PaymentsPanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel === "Выплаты" ? <PayoutsPanel onClose={() => setRailPanel(null)} /> : null}
        {railPanel && railPanel !== "Хранилище" && railPanel !== "Оплата" && railPanel !== "Выплаты" ? (
          <div data-rail-panel={railPanel} className="fixed left-[72px] top-3 z-50 w-[320px] rounded-[14px] border border-white/10 bg-[#0a0a0a] p-4 shadow-2xl" style={{ fontFamily: "Rubik, sans-serif" }}>
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[15px] font-medium text-white">{railPanel}</span>
              <button type="button" className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-white" onClick={() => setRailPanel(null)} aria-label="Закрыть">×</button>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-black/60 px-3 py-6 text-center text-[13px] text-zinc-400">скоро</div>
          </div>
        ) : null}
        <button type="button" onClick={() => { window.location.href = "/profile"; }} className={"flex h-10 w-10 items-center justify-center hover:bg-white/[0.05] hover:text-white " + (userOpen ? "text-white" : "text-zinc-400")} title="Пользователь" aria-label="Пользователь">
          <User className="h-5 w-5" strokeWidth={1.6} />
        </button>
        <div className="relative">
          <button type="button" onClick={() => setSettingsOpen((v) => !v)} className={"flex h-10 w-10 items-center justify-center hover:bg-white/[0.05] hover:text-white " + (settingsOpen ? "text-white" : "text-zinc-400")} title="Настройки" aria-label="Настройки">
            <Settings className="h-5 w-5" strokeWidth={1.6} />
          </button>
          {settingsOpen ? (
            <div className="absolute bottom-0 left-full z-50 ml-2 w-[300px] border border-white/10 bg-[#141417] p-3 shadow-2xl">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[13px] font-medium uppercase tracking-[0.12em] text-zinc-300">Настройки</span>
                <button type="button" className="text-zinc-500 hover:text-white" onClick={() => setSettingsOpen(false)} aria-label="Закрыть">×</button>
              </div>
              <div className="mb-3 text-[13px] text-zinc-500">Текстовая модель</div>
              <TextLlmPicker />
              <div className="mt-3 flex items-center gap-2 text-[13px] text-zinc-500">Версия <StudioVersionBadge /></div>
            </div>
          ) : null}
        </div>
        <UserPrefsApplier />
        {userOpen ? <UserPanel onClose={() => setUserOpen(false)} /> : null}
      </nav>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <LogPanel open={logsOpen} onOpenChange={setLogsOpen} />
      <FramesGrid
        projectId={framesProjectId}
        open={framesOpen}
        onOpenChange={setFramesOpen}
      />
      {children != null ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      ) : null}
      </div>
      </div>
    </UiContext.Provider>
  );
}

function RailBtn({ icon, label, title, onClick, active }: { icon: React.ReactNode; label: string; title?: string; onClick: () => void; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={title ?? label} className={"flex w-[62px] flex-col items-center gap-1 py-1.5 text-[11px] tracking-[-0.02em] transition-colors " + (active ? "text-white" : "text-zinc-400") + " hover:bg-white/[0.05] hover:text-white"}>
      {icon}
      <span className="leading-none">{label}</span>
    </button>
  );
}
