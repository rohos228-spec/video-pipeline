"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Plus, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
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
const PANEL_W = 460;
const PANEL_H = 340;

const FALLBACK_KINDS: MontageRefKindChoice[] = [
  {
    id: "character",
    label: "персонаж",
    hint: "внешность, одежда, что делает в кадре",
  },
  { id: "item", label: "предмет", hint: "что это, материал, состояние" },
  { id: "background", label: "фон", hint: "место, время суток, задний план" },
  { id: "other", label: "реф кадра", hint: "что именно брать с картинки" },
];

type Thumb = {
  key: string;
  kind: string;
  title: string;
  imageUrl: string | null | undefined;
  /** Ручной реф можно убрать; авто-рефы (персонажи ячейки, родитель) — нет. */
  refId?: string;
  description?: string;
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
      description: "кадр-родитель ячейки: держит место и свет",
    });
  }
  for (const c of chars) {
    out.push({
      key: `char-${c.id}`,
      kind: "персонаж",
      title: c.name || c.id,
      imageUrl: c.image_url,
      description: `персонаж ${c.id} из листа ячейки`,
    });
  }
  for (const it of items) {
    out.push({
      key: `item-${it.id}`,
      kind: "предмет",
      title: it.name || it.id,
      imageUrl: it.image_url,
      description: `предмет ${it.id} из листа ячейки`,
    });
  }
  for (const m of manual) {
    out.push({
      key: `manual-${m.id}`,
      kind: m.kind_label || "реф",
      title: m.description || m.kind_label || "реф",
      imageUrl: m.image_url,
      refId: m.id,
      description: m.description,
    });
  }
  return out;
}

/**
 * Рефы кадра — маленькой полосой прямо под его картинкой, без отдельной строки
 * доски. Описание у каждого рефа остаётся: без него непонятно, что генератор
 * должен с картинки взять. Добавление / удаление — во всплывающей панели,
 * которая открывается по наведению на неприметный `+`.
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
  const hostRef = useRef<HTMLDivElement>(null);
  const hideRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [box, setBox] = useState<{ left: number; top: number } | null>(null);
  const [kind, setKind] = useState("character");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const items = thumbsForFrame(frame);
  const manual = frame.manual_refs ?? [];
  const kindList = kinds && kinds.length ? kinds : FALLBACK_KINDS;
  const activeKind = kindList.find((k) => k.id === kind) ?? kindList[0];

  const place = useCallback(() => {
    const el = hostRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const left = Math.round(
      Math.min(
        Math.max(8, r.left - 6),
        Math.max(8, window.innerWidth - PANEL_W - 8),
      ),
    );
    const below = r.bottom + PANEL_H + 8 <= window.innerHeight;
    const top = below
      ? Math.round(r.bottom + 6)
      : Math.round(
          Math.max(8, Math.min(r.top - 6, window.innerHeight - PANEL_H - 8)),
        );
    setBox({ left, top });
  }, []);

  const show = useCallback(() => {
    if (hideRef.current) clearTimeout(hideRef.current);
    place();
  }, [place]);

  const hide = useCallback(() => {
    if (hideRef.current) clearTimeout(hideRef.current);
    // Пауза, чтобы курсор успел дойти от «+» до самой панели.
    hideRef.current = setTimeout(() => setBox(null), 160);
  }, []);

  useEffect(
    () => () => (hideRef.current ? clearTimeout(hideRef.current) : undefined),
    [],
  );

  const open = box !== null;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setBox(null);
    };
    const onScroll = () => place();
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, place]);

  const add = async () => {
    if (projectId == null || !file) return;
    const text = description.trim();
    if (!text) {
      toast.error("Опиши реф: что это и что брать в кадр");
      return;
    }
    setBusy(true);
    try {
      await api.addMontageFrameRef(projectId, frame.number, {
        kind,
        description: text,
        file,
      });
      setDescription("");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      toast.success(`Реф добавлен кадру #${frame.number}`);
      onChanged();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (refId: string) => {
    if (projectId == null) return;
    setBusy(true);
    try {
      await api.deleteMontageFrameRef(projectId, frame.number, refId);
      onChanged();
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={hostRef} className="mt-1" onMouseEnter={show} onMouseLeave={hide}>
      <div className="flex items-center gap-1">
        {items.map((t) => (
          <button
            key={t.key}
            type="button"
            className="group relative h-9 w-9 shrink-0 overflow-hidden rounded-md border border-white/12 bg-black/30 transition hover:border-amber-400/50"
            title={`${t.kind}: ${t.description || t.title}`}
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
            {t.refId ? (
              <span
                className="absolute bottom-0 left-0 right-0 h-1"
                style={{ backgroundColor: ACCENT }}
              />
            ) : null}
          </button>
        ))}
        <button
          type="button"
          aria-label={`Референсы кадра #${frame.number}`}
          title="Референсы кадра: добавить, описать, убрать"
          onClick={() => (open ? setBox(null) : show())}
          className={cn(
            "flex h-9 shrink-0 items-center gap-0.5 rounded-md border border-dashed px-1.5 text-[9px] uppercase tracking-wide transition",
            open
              ? "border-white/35 bg-white/[0.07] text-white/70"
              : "border-white/12 text-white/30 hover:border-white/30 hover:text-white/60",
          )}
        >
          <Plus className="h-3 w-3" />
          реф
        </button>
      </div>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              onMouseEnter={show}
              onMouseLeave={hide}
              style={{ left: box.left, top: box.top, width: PANEL_W }}
              className="fixed z-[10060] rounded-xl border border-white/12 bg-[#0b0b0b]/98 p-2.5 shadow-2xl backdrop-blur"
            >
              <p className="flex items-baseline gap-2">
                <span className="text-[10px] uppercase tracking-wide text-white/40">
                  референсы кадра #{frame.number}
                </span>
                <span className="text-[10px] text-white/25">
                  генератор берёт максимум два — ручные идут первыми
                </span>
              </p>

              {manual.length ? (
                <ul className="mt-1.5 space-y-1">
                  {manual.map((m) => (
                    <li
                      key={m.id}
                      className="flex items-start gap-2 rounded-lg border border-white/10 bg-black/30 p-1.5"
                    >
                      <span className="h-10 w-10 shrink-0 overflow-hidden rounded-md border border-white/10 bg-black/40">
                        {m.image_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={m.image_url}
                            alt={m.description}
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
                        <span className="block text-[10px] leading-snug text-white/70">
                          {m.description}
                        </span>
                      </span>
                      <button
                        type="button"
                        title="Убрать реф"
                        disabled={disabled || busy}
                        onClick={() => void remove(m.id)}
                        className="rounded-md p-1 text-white/30 transition hover:bg-white/10 hover:text-rose-200 disabled:opacity-40"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1.5 text-[10px] text-white/35">
                  своих рефов у кадра нет — ниже можно добавить
                </p>
              )}

              <div className="mt-2 rounded-lg border border-white/10 bg-black/25 p-1.5">
                <div className="flex flex-wrap items-center gap-1">
                  {kindList.map((k) => (
                    <button
                      key={k.id}
                      type="button"
                      disabled={disabled || busy}
                      onClick={() => setKind(k.id)}
                      className={cn(
                        "rounded-md border px-1.5 py-0.5 text-[10px] leading-none transition disabled:opacity-40",
                        kind === k.id
                          ? "border-transparent font-semibold text-black"
                          : "border-white/12 text-white/65 hover:border-white/30 hover:text-white",
                      )}
                      style={
                        kind === k.id ? { backgroundColor: ACCENT } : undefined
                      }
                    >
                      {k.label}
                    </button>
                  ))}
                </div>
                <textarea
                  className="mt-1.5 min-h-[52px] w-full rounded-md border border-white/12 bg-black/40 px-2 py-1 text-[11px] leading-snug outline-none focus:border-white/30 disabled:opacity-40"
                  placeholder={
                    activeKind?.hint || "опиши, что брать с этой картинки"
                  }
                  value={description}
                  disabled={disabled || busy}
                  onChange={(e) => setDescription(e.target.value)}
                />
                <div className="mt-1.5 flex items-center gap-2">
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
                    className="inline-flex items-center gap-1 rounded-md border border-white/15 px-2 py-1 text-[10px] text-white/70 transition hover:border-white/35 hover:text-white disabled:opacity-40"
                  >
                    <Upload className="h-3 w-3" />
                    {file ? "другой файл" : "выбрать файл"}
                  </button>
                  <span className="min-w-0 flex-1 truncate text-[10px] text-white/35">
                    {file ? file.name : "png / jpg / webp"}
                  </span>
                  <button
                    type="button"
                    disabled={disabled || busy || !file || !description.trim()}
                    onClick={() => void add()}
                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-semibold text-black transition disabled:opacity-40"
                    style={{ backgroundColor: ACCENT }}
                  >
                    {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                    добавить
                  </button>
                </div>
                <p className="mt-1 text-[10px] leading-snug text-white/30">
                  описание уходит в промт кадра: {activeKind?.hint}
                </p>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
