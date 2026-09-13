"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Plus, Trash2, Upload, X } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import type {
  MontageBoardCharacterRef,
  MontageBoardFrame,
  MontageBoardParentRef,
  MontageManualRef,
  MontageRefKindChoice,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCENT = "rgba(209,254,23,1)";

const FALLBACK_KINDS: MontageRefKindChoice[] = [
  { id: "character", label: "персонаж" },
  { id: "item", label: "предмет" },
  { id: "background", label: "фон" },
  { id: "other", label: "реф кадра" },
];

type Thumb = {
  key: string;
  kind: string;
  title: string;
  imageUrl: string | null | undefined;
};

function thumbsForFrame(fr: MontageBoardFrame): Thumb[] {
  const parent: MontageBoardParentRef | null = fr.ref_parent ?? null;
  const chars: MontageBoardCharacterRef[] = fr.group_character_refs ?? [];
  const items: MontageBoardCharacterRef[] = fr.item_refs ?? [];
  const manual: MontageManualRef[] = fr.manual_refs ?? [];
  const out: Thumb[] = [];
  if (parent) {
    out.push({
      key: `parent-${parent.number}`,
      kind: "родитель",
      title: parent.label || `родитель #${parent.number}`,
      imageUrl: parent.image_url,
    });
  }
  for (const c of chars) {
    out.push({
      key: `char-${c.id}`,
      kind: "персонаж",
      title: c.name || c.id,
      imageUrl: c.image_url,
    });
  }
  for (const it of items) {
    out.push({
      key: `item-${it.id}`,
      kind: "предмет",
      title: it.name || it.id,
      imageUrl: it.image_url,
    });
  }
  for (const m of manual) {
    out.push({
      key: `manual-${m.id}`,
      kind: m.kind_label || "реф",
      title: m.name || m.kind_label || "реф",
      imageUrl: m.image_url,
    });
  }
  return out;
}

/**
 * Рефы кадра — маленькой полосой прямо под его картинкой, без отдельной строки
 * доски: имя каждого рефа видно в подсказке, а `+` открывает окно, где можно
 * приложить уже готового персонажа / предмет проекта или загрузить новый файл
 * под своим именем. Имя дальше показывается на доске и уходит в промт кадра.
 */
export function FrameRefsStrip({
  projectId,
  frame,
  kinds,
  disabled,
  onPreview,
  onChanged,
}: {
  projectId: number | null;
  frame: MontageBoardFrame;
  kinds: MontageRefKindChoice[] | undefined;
  disabled?: boolean;
  onPreview: (p: { url: string; kind: "image"; label: string }) => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState("character");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const items = thumbsForFrame(frame);
  const manual = frame.manual_refs ?? [];
  const kindList = kinds && kinds.length ? kinds : FALLBACK_KINDS;

  const assets = useQuery({
    queryKey: ["montage-ref-assets", projectId],
    queryFn: () => api.montageRefAssets(projectId as number),
    enabled: open && projectId != null,
    staleTime: 30_000,
  });
  const attached = new Set(
    manual.map((m) => m.name.trim().toLowerCase()).filter(Boolean),
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const guard = useCallback(
    async (run: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await run();
        onChanged();
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  const link = (asset: { file: string; kind: string; name: string }) =>
    void guard(async () => {
      if (projectId == null) return;
      await api.linkMontageFrameRef(projectId, frame.number, {
        file: asset.file,
        kind: asset.kind,
        name: asset.name,
      });
      toast.success(`${asset.name} → кадр #${frame.number}`);
    });

  const upload = () =>
    void guard(async () => {
      if (projectId == null || !file) return;
      const text = name.trim();
      if (!text) {
        toast.error("Дай рефу имя — под ним он будет виден в монтаже");
        return;
      }
      await api.addMontageFrameRef(projectId, frame.number, {
        kind,
        name: text,
        file,
      });
      setName("");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      toast.success(`${text} → кадр #${frame.number}`);
    });

  const remove = (refId: string) =>
    void guard(async () => {
      if (projectId == null) return;
      await api.deleteMontageFrameRef(projectId, frame.number, refId);
    });

  return (
    <div className="mt-1">
      <div className="flex items-center gap-1">
        {items.map((t) => (
          <button
            key={t.key}
            type="button"
            className="group relative h-9 w-9 shrink-0 overflow-hidden rounded-md border border-white/12 bg-black/30 transition hover:border-amber-400/50"
            title={`${t.kind}: ${t.title}`}
            onClick={() => {
              if (t.imageUrl) {
                onPreview({ url: t.imageUrl, kind: "image", label: t.title });
              }
            }}
          >
            {t.imageUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={t.imageUrl}
                alt={t.title}
                className="h-full w-full object-cover transition group-hover:brightness-110"
              />
            ) : (
              <span className="flex h-full w-full items-center justify-center text-[8px] leading-none text-white/30">
                нет
                <br />
                фото
              </span>
            )}
          </button>
        ))}
        <button
          type="button"
          aria-label={`Референсы кадра #${frame.number}`}
          title="Референсы кадра: приложить готовый или загрузить новый"
          onClick={() => setOpen(true)}
          className="flex h-9 w-7 shrink-0 items-center justify-center rounded-md border border-dashed border-white/12 text-white/30 transition hover:border-white/30 hover:text-white/70"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              className="fixed inset-0 z-[10060] flex items-center justify-center bg-black/70 p-6"
              onClick={() => setOpen(false)}
            >
              <div
                onClick={(e) => e.stopPropagation()}
                className="flex max-h-[80vh] w-[44rem] flex-col overflow-hidden rounded-2xl border border-white/12 bg-[#0b0b0b] shadow-2xl"
              >
                <header className="flex items-baseline gap-2 border-b border-white/10 px-4 py-3">
                  <span className="text-sm font-semibold text-white/85">
                    Референсы кадра #{frame.number}
                  </span>
                  <span className="text-[11px] text-white/35">
                    генератор берёт максимум два — приложенные идут первыми
                  </span>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    title="Закрыть"
                    className="ml-auto rounded-md p-1 text-white/40 transition hover:bg-white/10 hover:text-white"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </header>

                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                  <p className="text-[10px] uppercase tracking-wide text-white/35">
                    приложено к кадру
                  </p>
                  {manual.length ? (
                    <ul className="mt-1.5 flex flex-wrap gap-2">
                      {manual.map((m) => (
                        <li
                          key={m.id}
                          className="flex w-[13rem] items-center gap-2 rounded-lg border border-white/10 bg-black/30 p-1.5"
                        >
                          <span className="h-10 w-10 shrink-0 overflow-hidden rounded-md border border-white/10 bg-black/40">
                            {m.image_url ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={m.image_url}
                                alt={m.name}
                                className="h-full w-full object-cover"
                              />
                            ) : null}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span
                              className="block text-[9px] uppercase tracking-wide"
                              style={{ color: ACCENT }}
                            >
                              {m.kind_label}
                            </span>
                            <span className="block truncate text-[11px] text-white/80">
                              {m.name}
                            </span>
                          </span>
                          <button
                            type="button"
                            title="Убрать реф"
                            disabled={disabled || busy}
                            onClick={() => remove(m.id)}
                            className="rounded-md p-1 text-white/30 transition hover:bg-white/10 hover:text-rose-200 disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[11px] text-white/35">
                      пока ничего не приложено
                    </p>
                  )}

                  <p className="mt-4 text-[10px] uppercase tracking-wide text-white/35">
                    уже есть в проекте — нажми, чтобы приложить
                  </p>
                  {assets.isLoading ? (
                    <p className="mt-1 text-[11px] text-white/35">читаю папки проекта…</p>
                  ) : (assets.data?.assets.length ?? 0) === 0 ? (
                    <p className="mt-1 text-[11px] text-white/35">
                      в проекте пока нет готовых персонажей и предметов — загрузи файл ниже
                    </p>
                  ) : (
                    <ul className="mt-1.5 flex flex-wrap gap-2">
                      {(assets.data?.assets ?? []).map((a) => {
                        const already = attached.has(a.name.trim().toLowerCase());
                        return (
                          <li key={a.file}>
                            <button
                              type="button"
                              disabled={disabled || busy || already}
                              onClick={() => link(a)}
                              title={
                                already
                                  ? `${a.name} уже приложен`
                                  : `Приложить ${a.kind_label}: ${a.name}`
                              }
                              className={cn(
                                "flex w-[7.5rem] flex-col items-stretch gap-1 rounded-lg border p-1.5 text-left transition",
                                already
                                  ? "border-white/10 bg-white/[0.02] opacity-45"
                                  : "border-white/10 bg-black/30 hover:border-white/30 hover:bg-white/[0.06]",
                              )}
                            >
                              <span className="h-16 w-full overflow-hidden rounded-md border border-white/10 bg-black/40">
                                {a.image_url ? (
                                  // eslint-disable-next-line @next/next/no-img-element
                                  <img
                                    src={a.image_url}
                                    alt={a.name}
                                    className="h-full w-full object-cover"
                                  />
                                ) : null}
                              </span>
                              <span className="text-[9px] uppercase tracking-wide text-white/35">
                                {a.kind_label} · {a.code}
                              </span>
                              <span className="truncate text-[11px] text-white/80">
                                {a.name}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  <p className="mt-4 text-[10px] uppercase tracking-wide text-white/35">
                    загрузить новый — файл и имя
                  </p>
                  <div className="mt-1.5 rounded-lg border border-white/10 bg-black/25 p-2">
                    <div className="flex flex-wrap items-center gap-1">
                      {kindList.map((k) => (
                        <button
                          key={k.id}
                          type="button"
                          disabled={disabled || busy}
                          onClick={() => setKind(k.id)}
                          className={cn(
                            "rounded-md border px-2 py-0.5 text-[11px] leading-none transition disabled:opacity-40",
                            kind === k.id
                              ? "border-transparent font-semibold text-black"
                              : "border-white/12 text-white/65 hover:border-white/30 hover:text-white",
                          )}
                          style={kind === k.id ? { backgroundColor: ACCENT } : undefined}
                        >
                          {k.label}
                        </button>
                      ))}
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      <input
                        ref={fileRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                      />
                      <button
                        type="button"
                        disabled={disabled || busy}
                        onClick={() => fileRef.current?.click()}
                        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/15 px-2 py-1 text-[11px] text-white/70 transition hover:border-white/35 hover:text-white disabled:opacity-40"
                      >
                        <Upload className="h-3 w-3" />
                        {file ? "другой файл" : "выбрать файл"}
                      </button>
                      <input
                        className="min-w-0 flex-1 rounded-md border border-white/12 bg-black/40 px-2 py-1 text-[11px] outline-none focus:border-white/30 disabled:opacity-40"
                        placeholder={file ? `имя для ${file.name}` : "имя рефа в монтаже"}
                        value={name}
                        disabled={disabled || busy}
                        onChange={(e) => setName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && file && name.trim()) {
                            e.preventDefault();
                            upload();
                          }
                        }}
                      />
                      <button
                        type="button"
                        disabled={disabled || busy || !file || !name.trim()}
                        onClick={upload}
                        className="inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-semibold text-black transition disabled:opacity-40"
                        style={{ backgroundColor: ACCENT }}
                      >
                        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                        приложить
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
