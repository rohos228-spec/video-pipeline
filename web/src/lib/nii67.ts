/**
 * Карточка NII 67 на клиенте.
 * Форма совпадает с app/services/nii67_card.py: две двери, блоки A–F и свой,
 * один ГГ, общий финал. Сервер ещё раз нормализует карточку при сохранении.
 */

export const TEXT_LIMIT = 4000;
export const FINALE_LIMIT = 500;
export const SHORT_LIMIT = 180;

export const BLOCK_KINDS = [
  "world_frame",
  "enter_action",
  "world_character",
  "character_action",
  "gg_action",
  "exit",
  "custom",
] as const;

export type BlockKind = (typeof BLOCK_KINDS)[number];

export const CANONICAL_KINDS: BlockKind[] = BLOCK_KINDS.filter((kind) => kind !== "custom");

export const KIND_LABEL: Record<BlockKind, string> = {
  world_frame: "Первый кадр мира",
  enter_action: "Действие попадания",
  world_character: "Появление персонажа мира",
  character_action: "Действие персонажа",
  gg_action: "Действие ГГ",
  exit: "Выход",
  custom: "Свой блок",
};

export const KIND_MARK: Record<BlockKind, string> = {
  world_frame: "A",
  enter_action: "B",
  world_character: "C",
  character_action: "D",
  gg_action: "E",
  exit: "F",
  custom: "•",
};

export const CREATURE_TYPES = [
  { id: "human", label: "Человек" },
  { id: "beast", label: "Зверь" },
  { id: "spirit", label: "Дух" },
  { id: "machine", label: "Механизм" },
  { id: "myth", label: "Мифическое" },
  { id: "other", label: "Другое" },
] as const;

export const TEMPERAMENTS = [
  { id: "calm", label: "Спокойный" },
  { id: "mysterious", label: "Загадочный" },
  { id: "friendly", label: "Дружелюбный" },
  { id: "wary", label: "Осторожный" },
  { id: "harsh", label: "Жёсткий" },
] as const;

const CREATURE_IDS = new Set<string>(CREATURE_TYPES.map((item) => item.id));
const TEMPER_IDS = new Set<string>(TEMPERAMENTS.map((item) => item.id));

export interface Nii67Character {
  enabled: boolean;
  name: string;
  creature_type: string;
  temperament: string;
  position: string;
  behavior: string;
  appearance: string;
}

export interface Nii67Block {
  id: string;
  kind: BlockKind;
  collapsed: boolean;
  text: string;
  image_prompt: string;
  image_name: string;
  custom_title: string;
  exit_action: string;
  exit_animation: string;
  room_after: string;
  character: Nii67Character;
}

export interface Nii67Door {
  id: "door1" | "door2";
  title: string;
  branch: string;
  theme: string;
  theme_note: string;
  image_prompt: string;
  image_name: string;
  blocks: Nii67Block[];
}

export interface Nii67Card {
  version: 1;
  intro: { text: string };
  gg: { name: string; note: string };
  doors: Nii67Door[];
  finale: { standard: string; notes: string };
  generated_text: string;
  saved_at: string;
  last_save: "" | "draft" | "save";
}

export type BlockPatch = Partial<
  Pick<
    Nii67Block,
    | "text"
    | "collapsed"
    | "image_prompt"
    | "image_name"
    | "custom_title"
    | "exit_action"
    | "exit_animation"
    | "room_after"
  >
> & { character?: Partial<Nii67Character> };

function newId(prefix: string): string {
  const raw =
    globalThis.crypto?.randomUUID?.().replace(/-/g, "") ??
    Math.random().toString(16).slice(2).padEnd(12, "0");
  return `${prefix}_${raw.slice(0, 12)}`;
}

function clip(value: string, limit: number): string {
  return value.slice(0, limit);
}

export function emptyCharacter(): Nii67Character {
  return {
    enabled: false,
    name: "",
    creature_type: "",
    temperament: "",
    position: "",
    behavior: "",
    appearance: "",
  };
}

export function newBlock(kind: BlockKind): Nii67Block {
  return {
    id: newId("b"),
    kind,
    collapsed: false,
    text: "",
    image_prompt: "",
    image_name: "",
    custom_title: "",
    exit_action: "",
    exit_animation: "",
    room_after: "",
    character: emptyCharacter(),
  };
}

function defaultDoor(id: "door1" | "door2"): Nii67Door {
  const title = id === "door1" ? "Door 1" : "Door 2";
  const branch = id === "door1" ? "Ветвь 1" : "Ветвь 2";
  return {
    id,
    title,
    branch,
    theme: "",
    theme_note: "",
    image_prompt: "",
    image_name: "",
    blocks: CANONICAL_KINDS.map((kind) => newBlock(kind)),
  };
}

export function defaultCard(): Nii67Card {
  return {
    version: 1,
    intro: { text: "" },
    gg: { name: "", note: "" },
    doors: [defaultDoor("door1"), defaultDoor("door2")],
    finale: { standard: "", notes: "" },
    generated_text: "",
    saved_at: "",
    last_save: "",
  };
}

function isBlockKind(value: string): value is BlockKind {
  return (BLOCK_KINDS as readonly string[]).includes(value);
}

function asCharacter(raw: unknown): Nii67Character {
  const src = raw && typeof raw === "object" ? (raw as Partial<Nii67Character>) : {};
  const creature = typeof src.creature_type === "string" ? src.creature_type : "";
  const temper = typeof src.temperament === "string" ? src.temperament : "";
  return {
    enabled: src.enabled === true,
    name: clip(typeof src.name === "string" ? src.name : "", SHORT_LIMIT),
    creature_type: CREATURE_IDS.has(creature) ? creature : "",
    temperament: TEMPER_IDS.has(temper) ? temper : "",
    position: clip(typeof src.position === "string" ? src.position : "", TEXT_LIMIT),
    behavior: clip(typeof src.behavior === "string" ? src.behavior : "", TEXT_LIMIT),
    appearance: clip(typeof src.appearance === "string" ? src.appearance : "", TEXT_LIMIT),
  };
}

function asBlock(raw: unknown): Nii67Block | null {
  if (!raw || typeof raw !== "object") return null;
  const src = raw as Partial<Nii67Block>;
  const kind = typeof src.kind === "string" && isBlockKind(src.kind) ? src.kind : "custom";
  const id = typeof src.id === "string" && src.id.trim() ? src.id.trim().slice(0, 40) : newId("b");
  return {
    ...newBlock(kind),
    id,
    kind,
    collapsed: src.collapsed === true,
    text: clip(typeof src.text === "string" ? src.text : "", TEXT_LIMIT),
    image_prompt: clip(typeof src.image_prompt === "string" ? src.image_prompt : "", TEXT_LIMIT),
    image_name: clip(typeof src.image_name === "string" ? src.image_name : "", SHORT_LIMIT),
    custom_title: clip(typeof src.custom_title === "string" ? src.custom_title : "", SHORT_LIMIT),
    exit_action: clip(typeof src.exit_action === "string" ? src.exit_action : "", TEXT_LIMIT),
    exit_animation: clip(typeof src.exit_animation === "string" ? src.exit_animation : "", TEXT_LIMIT),
    room_after: clip(typeof src.room_after === "string" ? src.room_after : "", TEXT_LIMIT),
    character: asCharacter(src.character),
  };
}

/** Принять ответ сервера. Битая форма откатывается к шаблону двух дверей. */
export function cardFromUnknown(raw: unknown): Nii67Card {
  if (!raw || typeof raw !== "object") return defaultCard();
  const src = raw as Partial<Nii67Card>;
  const base = defaultCard();
  const doorsIn = Array.isArray(src.doors) ? src.doors : [];
  const byId = new Map<string, Nii67Door>();
  for (const item of doorsIn) {
    if (!item || typeof item !== "object") continue;
    const door = item as Partial<Nii67Door>;
    if (door.id !== "door1" && door.id !== "door2") continue;
    if (byId.has(door.id)) continue;
    const fallback = base.doors.find((entry) => entry.id === door.id) ?? defaultDoor(door.id);
    const blocks = Array.isArray(door.blocks)
      ? door.blocks.map(asBlock).filter((block): block is Nii67Block => block !== null).slice(0, 40)
      : fallback.blocks;
    byId.set(door.id, {
      ...fallback,
      id: door.id,
      title: clip(typeof door.title === "string" && door.title.trim() ? door.title : fallback.title, SHORT_LIMIT),
      branch: clip(
        typeof door.branch === "string" && door.branch.trim() ? door.branch : fallback.branch,
        SHORT_LIMIT,
      ),
      theme: clip(typeof door.theme === "string" ? door.theme : "", 240),
      theme_note: clip(typeof door.theme_note === "string" ? door.theme_note : "", TEXT_LIMIT),
      image_prompt: clip(typeof door.image_prompt === "string" ? door.image_prompt : "", TEXT_LIMIT),
      image_name: clip(typeof door.image_name === "string" ? door.image_name : "", SHORT_LIMIT),
      blocks,
    });
  }
  const intro = src.intro && typeof src.intro === "object" ? src.intro : { text: "" };
  const gg = src.gg && typeof src.gg === "object" ? src.gg : { name: "", note: "" };
  const finale = src.finale && typeof src.finale === "object" ? src.finale : { standard: "", notes: "" };
  const last = src.last_save === "draft" || src.last_save === "save" ? src.last_save : "";
  return {
    version: 1,
    intro: { text: clip(typeof intro.text === "string" ? intro.text : "", TEXT_LIMIT) },
    gg: {
      name: clip(typeof gg.name === "string" ? gg.name : "", SHORT_LIMIT),
      note: clip(typeof gg.note === "string" ? gg.note : "", TEXT_LIMIT),
    },
    doors: [
      byId.get("door1") ?? defaultDoor("door1"),
      byId.get("door2") ?? defaultDoor("door2"),
    ],
    finale: {
      standard: clip(typeof finale.standard === "string" ? finale.standard : "", FINALE_LIMIT),
      notes: clip(typeof finale.notes === "string" ? finale.notes : "", FINALE_LIMIT),
    },
    generated_text: clip(typeof src.generated_text === "string" ? src.generated_text : "", 20_000),
    saved_at: clip(typeof src.saved_at === "string" ? src.saved_at : "", 40),
    last_save: last,
  };
}

function mapDoor(card: Nii67Card, doorId: string, mapper: (door: Nii67Door) => Nii67Door): Nii67Card {
  return {
    ...card,
    doors: card.doors.map((door) => (door.id === doorId ? mapper(door) : door)),
  };
}

export function insertBlock(card: Nii67Card, doorId: string, index: number, kind: BlockKind): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (door.blocks.length >= 40) return door;
    const next = door.blocks.slice();
    const at = Math.max(0, Math.min(index, next.length));
    next.splice(at, 0, newBlock(kind));
    return { ...door, blocks: next };
  });
}

export function duplicateBlock(card: Nii67Card, doorId: string, blockId: string): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (door.blocks.length >= 40) return door;
    const index = door.blocks.findIndex((block) => block.id === blockId);
    if (index < 0) return door;
    const source = door.blocks[index];
    const copy: Nii67Block = {
      ...structuredClone(source),
      id: newId("b"),
      collapsed: false,
    };
    const next = door.blocks.slice();
    next.splice(index + 1, 0, copy);
    return { ...door, blocks: next };
  });
}

export function deleteBlock(card: Nii67Card, doorId: string, blockId: string): Nii67Card {
  return mapDoor(card, doorId, (door) => ({
    ...door,
    blocks: door.blocks.filter((block) => block.id !== blockId),
  }));
}

export function moveBlock(card: Nii67Card, doorId: string, blockId: string, delta: number): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    const index = door.blocks.findIndex((block) => block.id === blockId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= door.blocks.length) return door;
    const next = door.blocks.slice();
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    return { ...door, blocks: next };
  });
}

export function reorderBlock(
  card: Nii67Card,
  doorId: string,
  fromIndex: number,
  toIndex: number,
): Nii67Card {
  return mapDoor(card, doorId, (door) => {
    if (
      fromIndex === toIndex ||
      fromIndex < 0 ||
      toIndex < 0 ||
      fromIndex >= door.blocks.length ||
      toIndex >= door.blocks.length
    ) {
      return door;
    }
    const next = door.blocks.slice();
    const [item] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, item);
    return { ...door, blocks: next };
  });
}

export function updateBlock(
  card: Nii67Card,
  doorId: string,
  blockId: string,
  patch: BlockPatch,
): Nii67Card {
  return mapDoor(card, doorId, (door) => ({
    ...door,
    blocks: door.blocks.map((block) => {
      if (block.id !== blockId) return block;
      const character = asCharacter({ ...block.character, ...patch.character });
      return {
        ...block,
        text: clip(patch.text ?? block.text, TEXT_LIMIT),
        collapsed: patch.collapsed ?? block.collapsed,
        image_prompt: clip(patch.image_prompt ?? block.image_prompt, TEXT_LIMIT),
        image_name: clip(patch.image_name ?? block.image_name, SHORT_LIMIT),
        custom_title: clip(patch.custom_title ?? block.custom_title, SHORT_LIMIT),
        exit_action: clip(patch.exit_action ?? block.exit_action, TEXT_LIMIT),
        exit_animation: clip(patch.exit_animation ?? block.exit_animation, TEXT_LIMIT),
        room_after: clip(patch.room_after ?? block.room_after, TEXT_LIMIT),
        character,
      };
    }),
  }));
}

export function updateIntro(card: Nii67Card, text: string): Nii67Card {
  return { ...card, intro: { text: clip(text, TEXT_LIMIT) } };
}

export function updateGg(card: Nii67Card, patch: Partial<Nii67Card["gg"]>): Nii67Card {
  return {
    ...card,
    gg: {
      name: clip(patch.name ?? card.gg.name, SHORT_LIMIT),
      note: clip(patch.note ?? card.gg.note, TEXT_LIMIT),
    },
  };
}

export function updateDoor(
  card: Nii67Card,
  doorId: string,
  patch: Partial<Pick<Nii67Door, "theme" | "theme_note" | "image_prompt" | "image_name">>,
): Nii67Card {
  return mapDoor(card, doorId, (door) => ({
    ...door,
    theme: clip(patch.theme ?? door.theme, 240),
    theme_note: clip(patch.theme_note ?? door.theme_note, TEXT_LIMIT),
    image_prompt: clip(patch.image_prompt ?? door.image_prompt, TEXT_LIMIT),
    image_name: clip(patch.image_name ?? door.image_name, SHORT_LIMIT),
  }));
}

export function updateFinale(
  card: Nii67Card,
  patch: Partial<Nii67Card["finale"]>,
): Nii67Card {
  return {
    ...card,
    finale: {
      standard: clip(patch.standard ?? card.finale.standard, FINALE_LIMIT),
      notes: clip(patch.notes ?? card.finale.notes, FINALE_LIMIT),
    },
  };
}
