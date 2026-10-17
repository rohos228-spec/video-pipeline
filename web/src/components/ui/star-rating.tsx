"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

type RatingsResp = { default: number; ratings: Record<string, number> };

export function useRatings(kind: "node" | "prompt") {
  return useQuery({
    queryKey: ["ratings", kind],
    queryFn: async () => {
      const r = await fetch(`/api/ratings?kind=${kind}`);
      if (!r.ok) throw new Error(r.statusText);
      return (await r.json()) as RatingsResp;
    },
    staleTime: 30_000,
  });
}

/** 1–5 stars; everyone starts at 5. Hover previews, click votes (stored in data/ratings.json). */
export function StarRating({ kind, id, size = 14, className, interactive = true }: { kind: "node" | "prompt"; id: string; size?: number; className?: string; interactive?: boolean }) {
  const qc = useQueryClient();
  const q = useRatings(kind);
  const value = q.data?.ratings[id] ?? q.data?.default ?? 5;
  const [hover, setHover] = useState<number | null>(null);
  const vote = useMutation({
    mutationFn: async (v: number) => {
      const r = await fetch("/api/ratings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, id, value: v }) });
      if (!r.ok) throw new Error(await r.text());
      return v;
    },
    onMutate: (v) => {
      qc.setQueryData<RatingsResp>(["ratings", kind], (old) => ({ default: old?.default ?? 5, ratings: { ...(old?.ratings ?? {}), [id]: v } }));
    },
    onSuccess: (v) => toast.success(`Оценка: ${v} из 5`),
    onError: () => void qc.invalidateQueries({ queryKey: ["ratings", kind] }),
  });
  const shown = hover ?? value;
  return (
    <span
      data-star-rating={id}
      data-value={value}
      className={cn("inline-flex items-center gap-[2px] rounded-full bg-black/55 px-1.5 py-1 backdrop-blur-sm", interactive && "pointer-events-auto cursor-pointer", className)}
      onMouseLeave={() => setHover(null)}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      title={interactive ? "Наведи и кликни, чтобы оценить" : `${value} из 5`}
    >
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          role={interactive ? "button" : undefined}
          aria-label={`${n} из 5`}
          data-star={n}
          onMouseEnter={interactive ? () => setHover(n) : undefined}
          onClick={interactive ? (e) => { e.stopPropagation(); e.preventDefault(); vote.mutate(n); } : undefined}
          className="inline-flex"
        >
          <Star
            style={{ width: size, height: size }}
            className={cn("transition-colors", n <= shown ? "fill-teal-300 text-teal-300" : "fill-transparent text-white/35")}
          />
        </span>
      ))}
    </span>
  );
}
