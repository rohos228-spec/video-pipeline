/** Единый конфиг тарифов VIA: главная (#pricing) и панель «Платежи». */
export type PlanLimits = { credits: number; videos?: number; llm: number; agents?: number; img: number; vid: number; storage: string };
export type Plan = { id: string; name: string; price: number; group: "trial" | "factory"; note: string; limits: PlanLimits; perks?: string[]; base?: string; hot?: boolean };

export const fmtRub = (n: number) => n.toLocaleString("ru-RU").replace(/\u00a0|,/g, " ") + " ₽";
export const fmtNum = (n: number) => n.toLocaleString("ru-RU").replace(/\u00a0|,/g, " ");

export const PLAN_GROUPS = {
  trial: { title: "Попробовать", sub: "Разовые пакеты только для генерации" },
  factory: { title: "Заводы", sub: "Помесячно · контент-производство с оркестратором" },
} as const;

export const PLANS: Plan[] = [
  { id: "proba", name: "Проба", price: 890, group: "trial", note: "попробовать формат", limits: { credits: 500, videos: 10, llm: 4, img: 1, vid: 1, storage: "3 ГБ" } },
  { id: "start", name: "Старт", price: 1990, group: "trial", note: "первые ролики", limits: { credits: 2000, videos: 30, llm: 4, img: 2, vid: 2, storage: "5 ГБ" }, hot: true },
  { id: "pro", name: "Про", price: 4990, group: "trial", note: "серия роликов", limits: { credits: 5100, videos: 80, llm: 4, img: 3, vid: 3, storage: "10 ГБ" } },
  { id: "workshop", name: "Мастерская", price: 9900, group: "factory", note: "свой канал на потоке", limits: { credits: 10500, llm: 10, agents: 3, img: 4, vid: 4, storage: "50 ГБ" },
    perks: ["Ассистент-оркестратор", "Шаблоны пайплайнов", "HTML-отчёты", "Библиотека промтов с рейтингами"] },
  { id: "manufactory", name: "Мануфактура", price: 30000, group: "factory", note: "несколько каналов", base: "Мастерской", limits: { credits: 33000, llm: 20, agents: 5, img: 8, vid: 8, storage: "100 ГБ" },
    perks: ["Расписания и автозапуск серий", "Маскот-оркестратор", "Брендовые стили и персонажи", "Бэкапы проектов"] },
  { id: "factory", name: "Завод", price: 89000, group: "factory", note: "сеть каналов и команда", base: "Мануфактуры", hot: true, limits: { credits: 95000, llm: 40, agents: 10, img: 16, vid: 16, storage: "500 ГБ" },
    perks: ["Мультиагенты", "Командный доступ с ролями", "Приоритетная очередь генерации", "Аналитика", "Автопубликация", "Интеграция собственных механик", "Приоритетная поддержка"] },
  { id: "conveyor", name: "Конвейер", price: 139000, group: "factory", note: "максимальный масштаб", base: "Завода", limits: { credits: 160000, llm: 60, agents: 20, img: 32, vid: 32, storage: "2 ТБ" },
    perks: ["Интеграция собственных API", "Экспорт без брендинга VIA", "Приоритетная поддержка 24/7"] },
];

export const limitRows = (l: PlanLimits): [string, string][] => [
  ["Кредиты", fmtNum(l.credits)],
  ...(l.videos ? [["Видео", `до ${l.videos}`] as [string, string]] : []),
  ["Потоки LLM", String(l.llm)],
  ...(l.agents ? [["Агенты через оркестратор", String(l.agents)] as [string, string]] : []),
  ["Потоки картинок", String(l.img)],
  ["Потоки видео", String(l.vid)],
  ["Хранилище", l.storage],
];
