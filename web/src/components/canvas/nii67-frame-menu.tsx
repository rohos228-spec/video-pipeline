"use client";

export type Nii67Ref = { type: "style" | "character" | "location" | "motion"; name: string; attached: boolean };
export type Nii67Camera = "far" | "mid" | "close" | "side" | "POV";
export type Nii67Feel = "handheld_soft" | "handheld_action" | "handheld_intimate";
export type Nii67Frame = {
  slot_id: string;
  door?: string | null;
  interaction?: string;
  beat: string;
  start_frame: string;
  end_frame: string;
  camera: Nii67Camera;
  camera_feel: Nii67Feel;
  characters_on: string[];
  action: string;
  refs: Nii67Ref[];
  duration_hint: number;
  scene?: Record<string, string>;
};

const CAMERAS: Nii67Camera[] = ["far", "mid", "close", "side", "POV"];
const FEELS: Nii67Feel[] = ["handheld_soft", "handheld_action", "handheld_intimate"];
const SCENE_KEYS = ["goal", "obstacle", "action", "reaction", "change", "hook"] as const;
const SCENE_LABEL: Record<string, string> = {
  goal: "цель",
  obstacle: "препятствие",
  action: "действие",
  reaction: "реакция",
  change: "изменение",
  hook: "крючок",
};
const field = "w-full rounded border border-white/10 bg-zinc-900 px-1.5 py-1 text-xs text-zinc-100";

/** Меню кадра: start/end, действие, камера, ручная камера, refs (приложено/нужно). Редактируемое. */
export function Nii67FrameMenu({
  frame,
  prevCamera,
  onChange,
}: {
  frame: Nii67Frame;
  prevCamera?: Nii67Camera;
  onChange: (next: Nii67Frame) => void;
}) {
  const set = <K extends keyof Nii67Frame>(key: K, value: Nii67Frame[K]) => onChange({ ...frame, [key]: value });
  const sceneOk = SCENE_KEYS.filter((k) => (frame.scene?.[k] ?? "").trim().length > 0);
  const checks: [string, boolean][] = [
    ["start ≠ end", frame.start_frame.trim() !== "" && frame.start_frame.trim() !== frame.end_frame.trim()],
    ["нет двух close подряд", !(frame.camera === "close" && prevCamera === "close")],
    ["сцена 6/6", sceneOk.length === SCENE_KEYS.length],
  ];
  return (
    <div data-testid="nii67-frame" className="space-y-1 rounded border border-white/10 p-2 text-xs">
      <div className="flex items-center justify-between font-semibold">
        <span>{frame.slot_id}</span>
        <span className="text-zinc-400">{frame.duration_hint} с</span>
      </div>
      <label className="block">
        START
        <textarea className={field} rows={2} value={frame.start_frame} onChange={(e) => set("start_frame", e.target.value)} />
      </label>
      <label className="block">
        END
        <textarea className={field} rows={2} value={frame.end_frame} onChange={(e) => set("end_frame", e.target.value)} />
      </label>
      <label className="block">
        Действие
        <textarea className={field} rows={2} value={frame.action} onChange={(e) => set("action", e.target.value)} />
      </label>
      <div className="flex gap-1">
        <select className={field} value={frame.camera} onChange={(e) => set("camera", e.target.value as Nii67Camera)}>
          {CAMERAS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select className={field} value={frame.camera_feel} onChange={(e) => set("camera_feel", e.target.value as Nii67Feel)}>
          {FEELS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>
      <div className="text-zinc-400">В кадре: {frame.characters_on.join(", ")}</div>
      <ul>
        {frame.refs.map((r, i) => (
          <li key={`${r.type}-${r.name}-${i}`}>
            <label className={r.attached ? "text-emerald-400" : "text-amber-400"}>
              <input
                type="checkbox"
                checked={r.attached}
                onChange={(e) => set("refs", frame.refs.map((x, j) => (j === i ? { ...x, attached: e.target.checked } : x)))}
              />{" "}
              {r.attached ? "приложено" : "нужно"} — {r.type}: {r.name}
            </label>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2 text-[11px]">
        {checks.map(([label, ok]) => (
          <span key={label} className={ok ? "text-emerald-400" : "text-red-400"}>
            {ok ? "✓" : "✗"} {label}
          </span>
        ))}
        <span className="text-zinc-500">{SCENE_KEYS.map((k) => (sceneOk.includes(k) ? "" : `нет ${SCENE_LABEL[k]} `)).join("")}</span>
      </div>
    </div>
  );
}

/** Intro → две параллельные колонки Door 1 | Door 2 → финал. */
export function Nii67FrameList({ frames, onChange }: { frames: Nii67Frame[]; onChange: (next: Nii67Frame[]) => void }) {
  const update = (slot: string, next: Nii67Frame) => onChange(frames.map((f) => (f.slot_id === slot ? next : f)));
  const lane = (door: string | null | undefined) => frames.filter((f) => (f.door ?? null) === (door ?? null));
  const render = (list: Nii67Frame[]) =>
    list.map((f, i) => <Nii67FrameMenu key={f.slot_id} frame={f} prevCamera={list[i - 1]?.camera} onChange={(n) => update(f.slot_id, n)} />);
  const shared = lane(null);
  const intro = shared.filter((f) => f.slot_id !== "finale");
  const finale = shared.filter((f) => f.slot_id === "finale");
  return (
    <div className="space-y-2">
      {render(intro)}
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-2"><div className="font-semibold text-teal-300">Door 1</div>{render(lane("door1"))}</div>
        <div className="space-y-2"><div className="font-semibold text-teal-300">Door 2</div>{render(lane("door2"))}</div>
      </div>
      {render(finale)}
    </div>
  );
}
