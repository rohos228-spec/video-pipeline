"use client";

import React, { useState } from "react";
import { Check, Copy, FileCode, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";

interface CodeBlockProps {
  language: string;
  code: string;
}

export function CodeBlock({ language, code }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  const displayLang = (language || "text").toLowerCase();
  const lines = code.split("\n");
  const showLineNumbers = lines.length > 2;

  return (
    <div className="my-3 overflow-hidden rounded-xl border border-white/[0.12] bg-[#121216]/95 shadow-lg group">
      <div className="flex items-center justify-between border-b border-white/[0.08] bg-white/[0.03] px-3.5 py-1.5 text-xs text-white/60">
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-white/70">
          {displayLang === "bash" || displayLang === "sh" || displayLang === "shell" ? (
            <Terminal className="h-3.5 w-3.5 text-[#22d3ee]" />
          ) : (
            <FileCode className="h-3.5 w-3.5 text-[#38bdf8]" />
          )}
          <span className="font-semibold">{displayLang}</span>
          <span className="text-[10px] text-white/30 lowercase font-normal">
            ({lines.length} {lines.length === 1 ? "строка" : lines.length < 5 ? "строки" : "строк"})
          </span>
        </div>
        <button
          type="button"
          onClick={onCopy}
          className={cn(
            "flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium transition-all outline-none",
            copied
              ? "bg-[#22d3ee]/20 text-[#22d3ee] border border-[#22d3ee]/40"
              : "text-white/60 hover:bg-white/[0.08] hover:text-white"
          )}
          title="Копировать код"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-[#22d3ee]" />
              <span>Скопировано</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Копировать код</span>
            </>
          )}
        </button>
      </div>
      <div className="overflow-x-auto p-3.5 font-mono text-[12px] leading-relaxed text-white/90 selection:bg-[#22d3ee]/30">
        {showLineNumbers ? (
          <div className="flex">
            <div className="select-none pr-3 text-right text-white/25 border-r border-white/[0.08] font-mono text-[12px] leading-relaxed">
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <pre className="m-0 pl-3 whitespace-pre flex-1 font-mono text-[12px] leading-relaxed">{code}</pre>
          </div>
        ) : (
          <pre className="m-0 whitespace-pre font-mono text-[12px] leading-relaxed">{code}</pre>
        )}
      </div>
    </div>
  );
}

function parseInlineFormatting(text: string): React.ReactNode[] {
  const elements: React.ReactNode[] = [];
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      elements.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    const key = `inline-${match.index}`;

    if (token.startsWith("`") && token.endsWith("`")) {
      elements.push(
        <code
          key={key}
          className="rounded-md border border-white/15 bg-white/[0.06] px-1.5 py-0.5 font-mono text-[12px] font-medium text-[#22d3ee]"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith("**") && token.endsWith("**")) {
      elements.push(
        <strong key={key} className="font-semibold text-white">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith("*") && token.endsWith("*")) {
      elements.push(
        <em key={key} className="italic text-white/80">
          {token.slice(1, -1)}
        </em>
      );
    } else if (token.startsWith("[") && token.includes("](")) {
      const parts = token.match(/\[(.*?)\]\((.*?)\)/);
      if (parts) {
        elements.push(
          <a
            key={key}
            href={parts[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-[#22d3ee] underline decoration-[#22d3ee]/40 underline-offset-2 hover:text-[#38bdf8] hover:decoration-[#22d3ee]"
          >
            {parts[1]}
          </a>
        );
      } else {
        elements.push(token);
      }
    } else {
      elements.push(token);
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    elements.push(text.substring(lastIndex));
  }

  return elements;
}

export function MarkdownRenderer({ content }: { content: string }) {
  if (!content) return null;

  const lines = content.split("\n");
  const nodes: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeLang = "";
  let codeBuffer: string[] = [];

  let inTable = false;
  let tableRows: string[][] = [];

  const flushTable = (key: string) => {
    if (tableRows.length === 0) return;
    const header = tableRows[0];
    const body = tableRows.slice(1);

    nodes.push(
      <div key={key} className="my-3 overflow-x-auto rounded-xl border border-white/15 bg-[#121216]/95 shadow-md">
        <table className="w-full text-left text-xs text-white/80">
          <thead className="border-b border-white/10 bg-white/[0.04] font-semibold text-white">
            <tr>
              {header.map((col, idx) => (
                <th key={idx} className="px-3.5 py-2.5">
                  {parseInlineFormatting(col.trim())}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.06]">
            {body.map((row, rIdx) => (
              <tr key={rIdx} className={cn("transition-colors hover:bg-white/[0.03]", rIdx % 2 === 1 && "bg-white/[0.015]")}>
                {row.map((cell, cIdx) => (
                  <td key={cIdx} className="px-3.5 py-2">
                    {parseInlineFormatting(cell.trim())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
    tableRows = [];
    inTable = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Check code blocks
    if (line.trim().startsWith("```")) {
      if (!inCodeBlock) {
        if (inTable) flushTable(`table-${i}`);
        inCodeBlock = true;
        codeLang = line.trim().slice(3).trim();
        codeBuffer = [];
      } else {
        nodes.push(
          <CodeBlock
            key={`code-${i}`}
            language={codeLang}
            code={codeBuffer.join("\n")}
          />
        );
        inCodeBlock = false;
        codeLang = "";
        codeBuffer = [];
      }
      continue;
    }

    if (inCodeBlock) {
      codeBuffer.push(line);
      continue;
    }

    // Check tables
    if (line.trim().startsWith("|") && line.trim().endsWith("|")) {
      if (/^\|[\s\-:|]+\|$/.test(line.trim())) {
        continue;
      }
      const cols = line
        .trim()
        .slice(1, -1)
        .split("|")
        .map((c) => c.trim());
      tableRows.push(cols);
      inTable = true;
      continue;
    } else if (inTable) {
      flushTable(`table-${i}`);
    }

    // Headers
    if (line.startsWith("### ")) {
      nodes.push(
        <h3 key={i} className="mb-1.5 mt-4 text-sm font-bold tracking-tight text-white">
          {parseInlineFormatting(line.slice(4))}
        </h3>
      );
      continue;
    }
    if (line.startsWith("## ")) {
      nodes.push(
        <h2 key={i} className="mb-2 mt-5 text-base font-bold tracking-tight text-white">
          {parseInlineFormatting(line.slice(3))}
        </h2>
      );
      continue;
    }
    if (line.startsWith("# ")) {
      nodes.push(
        <h1 key={i} className="mb-2.5 mt-6 text-lg font-extrabold tracking-tight text-white">
          {parseInlineFormatting(line.slice(2))}
        </h1>
      );
      continue;
    }

    // Blockquote
    if (line.startsWith("> ")) {
      nodes.push(
        <blockquote
          key={i}
          className="my-2 border-l-2 border-[#22d3ee]/70 bg-[#22d3ee]/10 py-1 pl-3 pr-2 text-xs italic text-white/90 rounded-r-md"
        >
          {parseInlineFormatting(line.slice(2))}
        </blockquote>
      );
      continue;
    }

    // Unordered List
    if (/^\s*[-*•]\s+/.test(line)) {
      const content = line.replace(/^\s*[-*•]\s+/, "");
      nodes.push(
        <div key={i} className="my-1 flex items-start gap-2 text-xs leading-relaxed text-white/90">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#22d3ee]" />
          <div className="flex-1">{parseInlineFormatting(content)}</div>
        </div>
      );
      continue;
    }

    // Numbered List
    if (/^\s*\d+\.\s+/.test(line)) {
      const match = line.match(/^\s*(\d+)\.\s+(.*)$/);
      if (match) {
        nodes.push(
          <div key={i} className="my-1 flex items-start gap-2 text-xs leading-relaxed text-white/90">
            <span className="font-mono text-[11px] font-semibold text-[#22d3ee]">
              {match[1]}.
            </span>
            <div className="flex-1">{parseInlineFormatting(match[2])}</div>
          </div>
        );
        continue;
      }
    }

    // Empty line
    if (!line.trim()) {
      nodes.push(<div key={i} className="h-2" />);
      continue;
    }

    // Regular paragraph
    nodes.push(
      <p key={i} className="my-1 text-xs leading-relaxed text-white/90 selection:bg-[#22d3ee]/30">
        {parseInlineFormatting(line)}
      </p>
    );
  }

  if (inCodeBlock) {
    nodes.push(
      <CodeBlock
        key="code-unfinished"
        language={codeLang}
        code={codeBuffer.join("\n")}
      />
    );
  }
  if (inTable) {
    flushTable("table-unfinished");
  }

  return <div className="space-y-0.5">{nodes}</div>;
}
