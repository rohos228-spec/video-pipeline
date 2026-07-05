/**
 * Значение категории блока `{{BLOCK:cat}}` в `project.prompt_overrides.blocks`
 * (см. app/services/prompt_composer.py и docs/PROMPTS_BLOCKS.md).
 */

/**
 * Значение одной категории блока:
 *  - строка — имя файла в prompts/blocks/<cat>/<name>.md (как раньше);
 *  - объект — { name?, text?, weight? }. `weight` 0..1 задаёт приоритет
 *    блока в промте (1 = по умолчанию, без пометки). `text` — свой текст
 *    вместо файла (может содержать {{VAR:...}} — подставится как обычно).
 */
export type BlockSelection = string | { name?: string; text?: string; weight?: number };

/** Имя выбранного файла блока (пусто, если категория задана свободным текстом). */
export function blockName(sel: BlockSelection | undefined): string {
  if (!sel) return "";
  if (typeof sel === "string") return sel;
  return sel.name ?? "";
}

/** Вес блока 0..1 (1 = дефолт, без пометки приоритета в промте). */
export function blockWeight(sel: BlockSelection | undefined): number {
  if (!sel || typeof sel === "string") return 1;
  return typeof sel.weight === "number" ? sel.weight : 1;
}

/** Свой текст блока (вместо выбора файла из библиотеки). */
export function blockText(sel: BlockSelection | undefined): string {
  if (!sel || typeof sel === "string") return "";
  return sel.text ?? "";
}

export function blockIsCustomText(sel: BlockSelection | undefined): boolean {
  return typeof sel === "object" && sel !== null && !!sel.text;
}

/**
 * Собирает значение категории для сохранения. Если вес=1 и текст не задан —
 * возвращает простую строку (legacy-формат), чтобы не плодить объекты там,
 * где они не нужны — меньше шансов на ошибку при последующем чтении.
 */
export function makeBlockSelection(opts: {
  name?: string;
  text?: string;
  weight: number;
}): BlockSelection {
  const { name, text, weight } = opts;
  const w = Number.isFinite(weight) ? Math.min(1, Math.max(0, weight)) : 1;
  if (w >= 0.999 && !text) {
    return name ?? "";
  }
  const out: { name?: string; text?: string; weight?: number } = {};
  if (text) {
    out.text = text;
  } else if (name) {
    out.name = name;
  }
  if (w < 0.999) {
    out.weight = w;
  }
  return out;
}
