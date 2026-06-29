"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { Pause, Play, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";

export type TimeRange = { start: number; end: number };

type DragMode =
  | { kind: "select"; t0: number; t1: number; tool: "replace" | "clone" }
  | { kind: "playhead" }
  | { kind: "seek" };

type Props = {
  file: File | null;
  audioSrc: string | null;
  duration: number;
  replaceRanges: TimeRange[];
  cloneRanges: TimeRange[];
  activeTool: "replace" | "clone";
  selectedReplaceIdx: number;
  selectedCloneIdx: number;
  canvasHeight?: number;
  onReplaceRangesChange: (ranges: TimeRange[]) => void;
  onCloneRangesChange: (ranges: TimeRange[]) => void;
  onSelectionCommit?: (tool: "replace" | "clone", prev: TimeRange[]) => void;
  onDeleteRange?: (tool: "replace" | "clone", index: number) => void;
  onDuration?: (d: number) => void;
  className?: string;
};

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

const PLAYHEAD_HIT_PX = 12;

export function AudioWaveform({
  file,
  audioSrc,
  duration,
  replaceRanges,
  cloneRanges,
  activeTool,
  selectedReplaceIdx,
  selectedCloneIdx,
  canvasHeight = 160,
  onReplaceRangesChange,
  onCloneRangesChange,
  onSelectionCommit,
  onDeleteRange,
  onDuration,
  className,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [wrapWidth, setWrapWidth] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [draftRange, setDraftRange] = useState<TimeRange | null>(null);
  const dragRef = useRef<DragMode | null>(null);

  useEffect(() => {
    if (!file) {
      setPeaks([]);
      return;
    }
    let cancelled = false;
    (async () => {
      const ctx = new AudioContext();
      try {
        const buf = await file.arrayBuffer();
        const audio = await ctx.decodeAudioData(buf.slice(0));
        if (cancelled) return;
        onDuration?.(audio.duration);
        const ch = audio.getChannelData(0);
        const buckets = 1200;
        const block = Math.floor(ch.length / buckets) || 1;
        const next: number[] = [];
        for (let i = 0; i < buckets; i++) {
          let max = 0;
          const from = i * block;
          const to = Math.min(ch.length, from + block);
          for (let j = from; j < to; j++) max = Math.max(max, Math.abs(ch[j]));
          next.push(max);
        }
        setPeaks(next);
      } finally {
        await ctx.close();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [file, onDuration]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    el.pause();
    setPlaying(false);
    setCurrentTime(0);
  }, [audioSrc]);

  const xToTime = useCallback(
    (clientX: number) => {
      const canvas = canvasRef.current;
      if (!canvas || duration <= 0) return 0;
      const rect = canvas.getBoundingClientRect();
      const x = clamp((clientX - rect.left) / rect.width, 0, 1);
      return clamp(x * duration, 0, duration);
    },
    [duration],
  );

  const timeToX = useCallback(
    (t: number, w: number) => {
      if (duration <= 0) return 0;
      return (t / duration) * w;
    },
    [duration],
  );

  const seekTo = useCallback(
    (t: number) => {
      const el = audioRef.current;
      if (!el || duration <= 0) return;
      const next = clamp(t, 0, duration);
      el.currentTime = next;
      setCurrentTime(next);
    },
    [duration],
  );

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth;
    const h = canvasHeight;
    setWrapWidth(w);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const g = canvas.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.fillStyle = "hsl(240 8% 7%)";
    g.fillRect(0, 0, w, h);

    const mid = h / 2;
    g.strokeStyle = "hsl(240 6% 16%)";
    g.beginPath();
    g.moveTo(0, mid);
    g.lineTo(w, mid);
    g.stroke();

    if (peaks.length && duration > 0) {
      g.strokeStyle = "hsl(42 95% 58% / 0.85)";
      g.lineWidth = 1;
      g.beginPath();
      peaks.forEach((p, i) => {
        const x = (i / Math.max(peaks.length - 1, 1)) * w;
        const y = mid - p * (mid - 8);
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      });
      g.stroke();
      g.beginPath();
      peaks.forEach((p, i) => {
        const x = (i / Math.max(peaks.length - 1, 1)) * w;
        const y = mid + p * (mid - 8);
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      });
      g.stroke();
    }

    const drawRanges = (ranges: TimeRange[], color: string) => {
      ranges.forEach((range) => {
        if (duration <= 0) return;
        const x1 = timeToX(range.start, w);
        const x2 = timeToX(range.end, w);
        g.fillStyle = color;
        g.fillRect(x1, 0, Math.max(2, x2 - x1), h);
      });
    };

    drawRanges(cloneRanges, "hsl(200 80% 50% / 0.22)");
    drawRanges(replaceRanges, "hsl(42 95% 58% / 0.25)");

    if (draftRange && duration > 0) {
      const x1 = timeToX(draftRange.start, w);
      const x2 = timeToX(draftRange.end, w);
      g.fillStyle =
        activeTool === "clone" ? "hsl(200 80% 50% / 0.28)" : "hsl(42 95% 58% / 0.28)";
      g.fillRect(x1, 0, Math.max(2, x2 - x1), h);
    }

    if (duration > 0) {
      const px = timeToX(currentTime, w);
      g.strokeStyle = "hsl(0 90% 65%)";
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(px, 0);
      g.lineTo(px, h);
      g.stroke();
      g.fillStyle = "hsl(0 90% 65%)";
      g.beginPath();
      g.arc(px, 8, 5, 0, Math.PI * 2);
      g.fill();
    }
  }, [
    activeTool,
    canvasHeight,
    cloneRanges,
    currentTime,
    draftRange,
    duration,
    peaks,
    replaceRanges,
    timeToX,
  ]);

  useEffect(() => {
    draw();
    const ro = new ResizeObserver(draw);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [draw]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    const onTime = () => {
      setCurrentTime(el.currentTime);
      draw();
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onEnded);
    };
  }, [audioSrc, draw]);

  const playAudio = () => {
    const el = audioRef.current;
    if (!el || !audioSrc) return;
    void el.play();
  };

  const pauseAudio = () => audioRef.current?.pause();

  const stopAudio = () => {
    const el = audioRef.current;
    if (!el) return;
    el.pause();
    el.currentTime = 0;
    setCurrentTime(0);
    setPlaying(false);
  };

  const hitPlayhead = (clientX: number) => {
    const canvas = canvasRef.current;
    if (!canvas || duration <= 0) return false;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const px = timeToX(currentTime, rect.width);
    return Math.abs(x - px) <= PLAYHEAD_HIT_PX;
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (duration <= 0) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);

    if (hitPlayhead(e.clientX)) {
      dragRef.current = { kind: "playhead" };
      return;
    }

    const t = xToTime(e.clientX);
    dragRef.current = { kind: "select", t0: t, t1: t, tool: activeTool };
    setDraftRange({ start: t, end: t });
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;

    if (d.kind === "playhead" || d.kind === "seek") {
      seekTo(xToTime(e.clientX));
      return;
    }

    d.t1 = xToTime(e.clientX);
    const start = Math.min(d.t0, d.t1);
    const end = Math.max(d.t0, d.t1);
    setDraftRange({ start, end });
  };

  const onPointerUp = () => {
    const d = dragRef.current;
    if (!d) return;

    if (d.kind === "select") {
      const start = Math.min(d.t0, d.t1);
      const end = Math.max(d.t0, d.t1);
      setDraftRange(null);
      if (end - start > 0.02) {
        const range = { start, end };
        if (d.tool === "replace") {
          onSelectionCommit?.("replace", replaceRanges);
          onReplaceRangesChange([...replaceRanges, range]);
        } else {
          onSelectionCommit?.("clone", cloneRanges);
          onCloneRangesChange([...cloneRanges, range]);
        }
      } else {
        seekTo(start);
      }
    }
    dragRef.current = null;
  };

  const rangeChipStyle = (range: TimeRange) => {
    if (duration <= 0 || wrapWidth <= 0) return { display: "none" } as const;
    const x1 = timeToX(range.start, wrapWidth);
    const x2 = timeToX(range.end, wrapWidth);
    const center = (x1 + x2) / 2;
    return { left: `${center}px` };
  };

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="h-8 gap-1 px-2"
          disabled={!audioSrc || playing}
          onClick={playAudio}
        >
          <Play className="h-4 w-4" />
          Play
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="h-8 gap-1 px-2"
          disabled={!audioSrc || !playing}
          onClick={pauseAudio}
        >
          <Pause className="h-4 w-4" />
          Pause
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 w-8 p-0"
          disabled={!audioSrc}
          onClick={stopAudio}
          title="Стоп"
        >
          <Square className="h-3.5 w-3.5" />
        </Button>
        <span className="font-mono text-[11px] text-muted-foreground">
          {fmtTime(currentTime)} / {duration > 0 ? fmtTime(duration) : "0:00.0"}
        </span>
        <span className="text-[11px] text-muted-foreground">playhead — тащи · клик — seek</span>
      </div>

      <div
        ref={wrapRef}
        className="relative min-h-[80px] flex-1 overflow-hidden rounded-md border border-border/60"
        style={{ height: canvasHeight }}
      >
        <canvas
          ref={canvasRef}
          className="block h-full w-full cursor-crosshair touch-none"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        />
        <div className="pointer-events-none absolute inset-0">
          {replaceRanges.map((range, idx) => (
            <RangeChip
              key={`r-${idx}-${range.start}`}
              range={range}
              style={rangeChipStyle(range)}
              tone="replace"
              selected={selectedReplaceIdx === idx}
              onDelete={
                onDeleteRange
                  ? (e) => {
                      e.stopPropagation();
                      onDeleteRange("replace", idx);
                    }
                  : undefined
              }
            />
          ))}
          {cloneRanges.map((range, idx) => (
            <RangeChip
              key={`c-${idx}-${range.start}`}
              range={range}
              style={rangeChipStyle(range)}
              tone="clone"
              selected={selectedCloneIdx === idx}
              onDelete={
                onDeleteRange
                  ? (e) => {
                      e.stopPropagation();
                      onDeleteRange("clone", idx);
                    }
                  : undefined
              }
            />
          ))}
          {draftRange ? (
            <RangeChip
              range={draftRange}
              style={rangeChipStyle(draftRange)}
              tone={activeTool === "clone" ? "clone" : "replace"}
              selected
              draft
            />
          ) : null}
        </div>
      </div>
      {audioSrc ? <audio ref={audioRef} src={audioSrc} preload="auto" className="hidden" /> : null}
    </div>
  );
}

function RangeChip({
  range,
  style,
  tone,
  selected,
  draft,
  onDelete,
}: {
  range: TimeRange;
  style: { left: string } | { display: "none" };
  tone: "replace" | "clone";
  selected?: boolean;
  draft?: boolean;
  onDelete?: (e: React.MouseEvent) => void;
}) {
  if ("display" in style) return null;
  const len = Math.max(0, range.end - range.start);
  const toneClass =
    tone === "clone"
      ? "border-sky-400/50 bg-sky-500/20 text-sky-100"
      : "border-amber-400/50 bg-amber-500/20 text-amber-100";

  return (
    <div className="absolute top-1 -translate-x-1/2" style={style}>
      <div
        className={cn(
          "group pointer-events-auto relative rounded border px-1.5 py-0.5 text-[10px] font-mono shadow-md backdrop-blur-sm",
          toneClass,
          selected && "ring-1 ring-white/40",
          draft && "animate-pulse",
        )}
      >
        {onDelete ? (
          <button
            type="button"
            className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full border border-red-500/60 bg-red-600/90 text-white opacity-0 transition-opacity group-hover:opacity-100"
            title="Удалить выделение"
            onClick={onDelete}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <X className="h-2.5 w-2.5" />
          </button>
        ) : null}
        <div className="truncate">
          {range.start.toFixed(1)}–{range.end.toFixed(1)}s
        </div>
        <div className="text-[9px] opacity-80">{len.toFixed(2)} s</div>
      </div>
    </div>
  );
}
