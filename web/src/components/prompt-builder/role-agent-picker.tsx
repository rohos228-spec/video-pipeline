"use client";

import { useCallback, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { AgentCatalogEntry } from "@/lib/prompt-builder/agents-catalog";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";

const HOVER_DELAY_MS = 400;

export function RoleAgentPicker({
  agents,
  selectedBlockId,
  onSelect,
  onPreview,
}: {
  agents: AgentCatalogEntry[];
  selectedBlockId: string;
  onSelect: (blockId: string) => void;
  onPreview?: (agent: AgentCatalogEntry | null) => void;
}) {
  const [hoverId, setHoverId] = useState<string | null>(null);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const clearTimer = useCallback(() => {
    if (showTimer.current) clearTimeout(showTimer.current);
  }, []);

  const schedulePreview = useCallback(
    (agent: AgentCatalogEntry) => {
      clearTimer();
      showTimer.current = setTimeout(() => onPreview?.(agent), HOVER_DELAY_MS);
    },
    [clearTimer, onPreview],
  );

  const selectedAgent = agentForBlock(selectedBlockId);

  return (
    <div className="relative mt-2">
      <div
        ref={listRef}
        className="pb-agent-scroll max-h-[280px] space-y-3 overflow-y-auto overscroll-contain pr-2"
      >
        {agents.map((agent) => {
          const selected = agent.blockId === selectedBlockId;
          const isHovered = hoverId === agent.blockId;

          return (
            <div
              key={agent.blockId}
              onMouseEnter={() => {
                setHoverId(agent.blockId);
                schedulePreview(agent);
              }}
              onMouseLeave={() => {
                clearTimer();
                setHoverId(null);
                onPreview?.(null);
              }}
            >
              <button
                type="button"
                onClick={() => onSelect(agent.blockId)}
                className={cn(
                  "pb-agent-card w-full text-left",
                  selected && "pb-agent-card-selected",
                  isHovered && "pb-agent-card-hover",
                )}
              >
                <p className="text-[14px] font-semibold leading-tight pb-text">{agent.name}</p>
                <p className="mt-2 text-[12px] leading-snug pb-text-muted">{agent.short}</p>
              </button>
            </div>
          );
        })}
      </div>

      {selectedAgent && (
        <p className="mt-2.5 text-[9px] pb-text-dim">
          Активен: {selectedAgent.name} · описание справа во вкладке «Настройки»
        </p>
      )}
    </div>
  );
}
