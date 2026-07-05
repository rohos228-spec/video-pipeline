/** Каталог агентов для блока «Роль» */
export type AgentCatalogEntry = {
  blockId: string;
  name: string;
  short: string;
  description: string;
  tags: string[];
};

export const AGENT_CATALOG: AgentCatalogEntry[] = [
  {
    blockId: "role_shorts_writer",
    name: "Сценарист Shorts",
    short: "Вертикальные ролики 60 сек, плотная озвучка",
    description:
      "Пишет структуру и текст под короткий формат. Hook в первые 3 секунды, без воды. Работает с таймингами блоков и объёмом знаков. Передаёт оркестратору черновик сценария для следующих агентов.",
    tags: ["plan", "script", "shorts"],
  },
  {
    blockId: "role_image_prompter",
    name: "Image Prompt Engineer",
    short: "Промты кадров, стиль без копирования сюжета",
    description:
      "Фиксирует визуальный стиль и технические ограничения image prompt. Не переносит пример-сюжет из шаблона. Согласует negative, длину промта и STYLE_LOCK с блоками особенностей и правил.",
    tags: ["img_pr", "hero", "visual"],
  },
  {
    blockId: "role_xlsx_agent",
    name: "Excel-агент",
    short: "Заполнение таблицы, anti-doubles",
    description:
      "Работает построчно с XLSX: plan, персонажи, реквизит. Не дублирует сущности между строками. Возвращает JSON-пatches ячеек. Требует строгих правил V7 и технического блока Row52.",
    tags: ["enrich", "excel"],
  },
  {
    blockId: "role_plan_architect",
    name: "Архитектор плана",
    short: "Структура ролика, тайминги, герои",
    description:
      "Собирает общий план: блоки по секундам, визуальные акценты, список героев. Отдаёт каркас для сценариста и enrich-агентов. Учитывает жанр и драматургию из соседних блоков.",
    tags: ["plan"],
  },
  {
    blockId: "role_animation_director",
    name: "Режиссёр анимации",
    short: "Движение камеры и объектов, до 8 сек",
    description:
      "Описывает animation prompt: камера, motion, без переписывания сюжета. Согласован с visual_style и camera_framing. Не смешивает статику image prompt с динамикой клипа.",
    tags: ["anim_pr"],
  },
  {
    blockId: "role_hero_designer",
    name: "Дизайнер героя",
    short: "Hero sheet, референс персонажа",
    description:
      "Создаёт описание главного героя для hero_sheet и последующих кадров. Связан с world и visual_style. Не генерирует построчный image_prompt — только канон персонажа.",
    tags: ["hero"],
  },
  {
    blockId: "role_enrich_orchestrator",
    name: "Enrich-оркестратор",
    short: "Связка enrich 1–5, целостность таблицы",
    description:
      "Координирует цепочку enrich-шагов: plan → персонажи → реквизит. Следит за block_id и связностью строк. Передаёт оркестратору статус заполнения перед финальной сборкой.",
    tags: ["enrich"],
  },
  {
    blockId: "role_qa_reviewer",
    name: "QA / Review",
    short: "Проверка промтов перед GPT",
    description:
      "Валидирует собранный скелет: конфликты стилей, длины, запреты. Не пишет контент — только verdict и правки для оркестратора.",
    tags: ["check"],
  },
];

export function agentForBlock(blockId: string): AgentCatalogEntry | undefined {
  return AGENT_CATALOG.find((a) => a.blockId === blockId);
}

export function agentsForStep(stepCode: string): AgentCatalogEntry[] {
  return AGENT_CATALOG.filter((a) => a.tags.includes(stepCode) || a.tags.length === 0);
}

/** Все агенты + fallback из блоков role без каталога */
export function mergeAgentsWithBlocks(
  blockIds: string[],
  stepCode: string,
): AgentCatalogEntry[] {
  const seen = new Set<string>();
  const ordered: AgentCatalogEntry[] = [];

  for (const id of blockIds) {
    const cat = AGENT_CATALOG.find((a) => a.blockId === id);
    if (cat && !seen.has(id)) {
      ordered.push(cat);
      seen.add(id);
    }
  }

  for (const a of AGENT_CATALOG) {
    if (seen.has(a.blockId)) continue;
    if (a.tags.includes(stepCode) || a.tags.some((t) => blockIds.some((bid) => bid.includes(t)))) {
      ordered.push(a);
      seen.add(a.blockId);
    }
  }

  for (const a of AGENT_CATALOG) {
    if (!seen.has(a.blockId)) {
      ordered.push(a);
    }
  }

  return ordered;
}
