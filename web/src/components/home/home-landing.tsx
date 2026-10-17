"use client";

import type React from "react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { PLANS, PLAN_GROUPS, fmtRub, limitRows, type Plan } from "@/lib/pricing";
import { FactoryCard, FactoryStyles, IndustrialDivider } from "@/components/home/factory-cards";


const N = (t: string) => `/prompt-bg/node/${t}.webp`;
/**
 * Слоты изображений главной. Чтобы заполнить — укажите src (например "/home/hero.webp").
 * Пустой src = заготовка-плейсхолдер. Индексы 1..12 используются по всей странице.
 */
export const HOME_IMAGES: Record<number, { src: string; label: string }> = {
  1: { src: "", label: "Hero · концепт контент-завода" },
  2: { src: "", label: "Витрина · главный кадр" },
  3: { src: "", label: "Hero · кадр 2" },
  4: { src: "", label: "Витрина · кадр" },
  5: { src: "", label: "Hero · кадр 3" },
  6: { src: "", label: "Витрина · кадр" },
  7: { src: "", label: "Лента · кадр" },
  8: { src: "", label: "Витрина · кадр" },
  9: { src: "", label: "Лента · кадр" },
  10: { src: "", label: "Витрина · кадр" },
  11: { src: "", label: "Лента · кадр" },
  12: { src: "", label: "Лента · кадр" },
};

function Slot({ i, className = "", style }: { i: number; className?: string; style?: React.CSSProperties }) {
  const it = HOME_IMAGES[i];
  if (it?.src) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={it.src} alt="" className={`object-cover ${className}`} style={style} />;
  }
  return (
    <div className={`flex flex-col items-center justify-center gap-2 border border-white/[0.09] bg-[#0d1314] text-white/30 ${className}`} style={style}>
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" /></svg>
      <span className="text-sm">Изображение 16:9</span>
      <span className="text-xs text-white/20">#{i} · {it?.label}</span>
    </div>
  );
}

const PIPE: [string, string, string][] = [
  ["topic", "Тема", "Идея или список тем на неделю"],
  ["plan", "План", "Структура ролика и тайминг сцен"],
  ["script", "Сценарий", "Закадровый текст под формат и тон"],
  ["audio", "Озвучка", "Голос диктора по сценарию"],
  ["hero", "Персонажи", "Единые герои на весь сериал"],
  ["images", "Картинки", "Кадры для каждой сцены"],
  ["videos", "Анимация", "Оживление кадров в видео"],
  ["music", "Музыка", "Подложка под настроение"],
  ["sfx_gen", "Звуки", "SFX по событиям в кадре"],
  ["assemble", "Сборка", "Монтаж, субтитры, финальный рендер"],
  ["publish", "Публикация", "Выгрузка на площадки"],
];

const FORMATS = [
  ["Мультсериалы", "Одни и те же герои из серии в серию, стиль держится через ноду персонажей."],
  ["Shorts и Reels", "Вертикальные ролики пачками: одна тема — десятки вариантов."],
  ["Объясняющие видео", "Сложное простым языком: план, схема, диктор, иллюстрации."],
  ["Истории и документалки", "Длинные рассказы с атмосферными кадрами и музыкой."],
  ["Факты и подборки", "Быстрые форматы, которые легко штамповать каждый день."],
  ["Любой свой формат", "Собираете цепочку нод под себя и сохраняете как шаблон."],
];

const FEATURES: [string, string, string][] = [
  ["excel_gpt", "Нодовый холст", "Весь пайплайн виден как схема: каждая нода — шаг, связи — поток данных. Можно перезапустить любой шаг, не трогая остальные."],
  ["image_prompts", "Каталог промтов", "У каждой ноды готовые промты с описанием, названием и оценкой. Переменные стиля вынесены в настройки — меняете без правки текста."],
  ["animation_prompts", "Параллельная генерация", "Кадры, озвучка и анимация идут в несколько потоков одновременно. Число потоков выбирается прямо на ноде."],
  ["items", "Библиотека и группы", "Встроенные, пользовательские и свои ноды, готовые группы-цепочки. Собрали удачный процесс — переиспользуете его в каждом проекте."],
];

const FAQ = [
  ["Что такое контент-завод?", "Это когда ролики выпускаются не по одному вручную, а потоком: один настроенный пайплайн превращает список тем в готовые видео."],
  ["Можно ли делать мультфильмы с одними и теми же героями?", "Да. Нода «Персонажи» фиксирует внешность героев, и дальше все кадры генерируются с ними."],
  ["Нужно ли уметь монтировать?", "Нет. Сборка, субтитры, музыка и звуки собираются автоматически. Любой шаг можно открыть и поправить вручную."],
  ["Какие модели используются?", "Текстовые LLM для плана и сценария, генераторы изображений и видео для кадров и анимации. Модель выбирается на каждой ноде."],
  ["Сколько стоит?", "Тарифы ещё формируются — цены ниже указаны как заглушки."],
  ["Можно ли менять промты?", "Да: просматривать, редактировать, сохранять свои версии и выбирать активный промт прямо в настройках ноды."],
];

function Reveal({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && (setOn(true), io.disconnect()), { threshold: 0.12 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} className={className} style={{ transition: `opacity .7s ease ${delay}ms, transform .7s cubic-bezier(.2,.7,.2,1) ${delay}ms`, opacity: on ? 1 : 0, transform: on ? "none" : "translateY(28px)" }}>
      {children}
    </div>
  );
}

const Btn = ({ href, children, solid }: { href: string; children: ReactNode; solid?: boolean }) => (
  <a href={href} className={`inline-flex h-14 items-center rounded-2xl px-8 text-lg font-medium transition duration-300 ${solid ? "bg-[#2bb8a0]/25 text-[#bff3e8] ring-1 ring-[#2bb8a0]/40 hover:bg-[#2bb8a0]/35" : "bg-white/[0.04] text-white/75 ring-1 ring-white/10 hover:bg-white/[0.08]"}`}>
    {children}
  </a>
);

const PlanCard = ({ p, i }: { p: Plan; i: number }) => (
  <Reveal delay={i * 70} className="h-full">
    <div className={`group flex h-full flex-col rounded-[1.6rem] p-6 ring-1 transition duration-300 hover:-translate-y-1 hover:bg-[#2bb8a0]/[0.06] hover:ring-[#2bb8a0]/35 ${p.hot ? "bg-[#2bb8a0]/[0.06] ring-[#2bb8a0]/30" : "bg-white/[0.03] ring-white/[0.07]"}`}>
      <div className="flex items-center justify-between">
        <div className="text-xl font-medium text-white">{p.name}</div>
        {p.hot && <span className="rounded-full bg-[#2bb8a0]/15 px-2.5 py-0.5 text-xs text-[#9fe9da]">популярный</span>}
      </div>
      <div className="mt-1 text-sm text-white/45">{p.note}</div>
      <div className="mt-5 text-3xl font-bold text-white">{fmtRub(p.price)}<span className="text-base font-normal text-white/40">{p.group === "factory" ? " / месяц" : " разово"}</span></div>
      <dl className="mt-5 space-y-1.5 text-sm">
        {limitRows(p.limits).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 border-b border-white/[0.05] pb-1.5"><dt className="text-white/50">{k}</dt><dd className="text-white/85">{v}</dd></div>
        ))}
      </dl>
      {p.perks && (
        <ul className="mt-5 space-y-1.5 text-sm text-white/70">
          {p.base && <li className="text-[#5fd6bf]/80">Всё из {p.base}, плюс:</li>}
          {p.perks.map((x) => <li key={x} className="flex gap-2"><span className="text-[#5fd6bf]">✓</span>{x}</li>)}
        </ul>
      )}
      <div className="mt-auto pt-6"><a href={`/register?plan=${p.id}`} className={`flex h-11 w-full items-center justify-center rounded-xl text-[15px] font-medium ring-1 transition duration-300 ${p.hot ? "bg-[#2bb8a0]/20 text-[#bff3e8] ring-[#2bb8a0]/40 hover:bg-[#2bb8a0]/30" : "bg-white/[0.04] text-white/75 ring-white/10 hover:bg-[#2bb8a0]/15 hover:text-[#bff3e8] hover:ring-[#2bb8a0]/30"}`}>Выбрать</a></div>
    </div>
  </Reveal>
);

const H2 = ({ k, t, s }: { k: string; t: string; s?: string }) => (
  <Reveal className="mb-8 max-w-3xl">
    <div className="mb-4 text-sm font-medium uppercase tracking-[0.25em] text-[#5fd6bf]/80">{k}</div>
    <h2 className="text-4xl font-bold leading-tight text-white md:text-6xl">{t}</h2>
    {s && <p className="mt-5 text-xl leading-relaxed text-white/55">{s}</p>}
  </Reveal>
);

const NAV = [
  { id: "pipeline", label: "Пайплайн" },
  { id: "formats", label: "Форматы" },
  { id: "features", label: "Возможности" },
  { id: "who", label: "Для кого" },
  { id: "pricing", label: "Тарифы" },
  { id: "faq", label: "FAQ" },
  { id: "contacts", label: "Контакты" },
];

/** Публичные контакты. Пустое value — не показывается. */
const CONTACTS: { label: string; value: string; href: string }[] = [
  { label: "Email", value: "", href: "" },
  { label: "Telegram", value: "", href: "" },
];

function HomeHeader({ active }: { active: string }) {
  const [menu, setMenu] = useState(false);
  const link = (n: { id: string; label: string }, mobile = false) => (
    <a key={n.id} href={`#${n.id}`} onClick={() => setMenu(false)}
      className={`relative rounded-xl transition duration-300 ${mobile ? "px-4 py-3 text-xl" : "px-3 py-2 text-base"} ${active === n.id ? "bg-[#2bb8a0]/[0.12] text-[#bff3e8]" : "text-white/55 hover:bg-white/[0.04] hover:text-white"}`}>
      {n.label}
      {!mobile && <span className={`absolute inset-x-3 -bottom-[13px] h-0.5 rounded-full bg-[#5fd6bf] transition-opacity duration-300 ${active === n.id ? "opacity-100" : "opacity-0"}`} />}
    </a>
  );
  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#101516]/75 backdrop-blur-xl backdrop-saturate-150">
      <div className="mx-auto flex h-20 max-w-7xl items-center gap-6 px-6">
        <a href="#top" className="shrink-0">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/via-logo.png" alt="VIA" className="h-9" />
        </a>
        <nav className="hidden items-center gap-1 lg:flex">{NAV.map((n) => link(n))}</nav>
        <div className="ml-auto hidden items-center gap-3 lg:flex">
          <a href="/login" className="rounded-xl px-4 py-2.5 text-base text-white/70 transition hover:bg-white/[0.05] hover:text-white">Войти</a>
          <a href="/register" className="rounded-xl bg-[#2bb8a0]/20 px-5 py-2.5 text-base text-[#bff3e8] ring-1 ring-[#2bb8a0]/35 transition hover:bg-[#2bb8a0]/30">Регистрация</a>
        </div>
        <button aria-label="Меню" onClick={() => setMenu(!menu)} className="ml-auto flex h-11 w-11 items-center justify-center rounded-xl text-white/75 ring-1 ring-white/10 lg:hidden">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            {menu ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
          </svg>
        </button>
      </div>
      <div className="grid transition-all duration-500 lg:hidden" style={{ gridTemplateRows: menu ? "1fr" : "0fr" }}>
        <div className="overflow-hidden">
          <nav className="flex flex-col gap-1 border-t border-white/[0.06] px-6 py-4">
            {NAV.map((n) => link(n, true))}
            <div className="mt-3 grid grid-cols-2 gap-3">
              <a href="/login" className="rounded-xl bg-white/[0.04] py-3 text-center text-lg text-white/75 ring-1 ring-white/10">Войти</a>
              <a href="/register" className="rounded-xl bg-[#2bb8a0]/20 py-3 text-center text-lg text-[#bff3e8] ring-1 ring-[#2bb8a0]/35">Регистрация</a>
            </div>
          </nav>
        </div>
      </div>
    </header>
  );
}

export function HomeLanding({ fontClass = "" }: { fontClass?: string }) {
  const [open, setOpen] = useState<number | null>(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState("");
  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;
    const ids = NAV.map((n) => n.id);
    const onScroll = () => {
      let cur = "";
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= 140) cur = id;
      }
      if (root.scrollTop + root.clientHeight >= root.scrollHeight - 4) cur = ids[ids.length - 1];
      setActive(cur);
    };
    onScroll();
    root.addEventListener("scroll", onScroll, { passive: true });
    if (location.hash) setTimeout(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: "smooth" }), 300);
    return () => root.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <div ref={scrollRef} className={`${fontClass} fixed inset-0 overflow-y-auto scroll-smooth scroll-pt-24 bg-[#101516] text-white`} style={{ backgroundImage: "radial-gradient(1200px 600px at 70% -10%, rgba(43,184,160,.16), transparent 60%), radial-gradient(900px 500px at 0% 40%, rgba(43,184,160,.06), transparent 60%)" }}>
      <style>{`@keyframes viaFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}@keyframes viaPan{from{transform:translateX(0)}to{transform:translateX(-50%)}}@keyframes viaRun{from{left:-10rem}to{left:100%}}`}</style>

      <HomeHeader active={active} />

      {/* HERO */}
      <section id="top" className="mx-auto grid max-w-7xl items-center gap-14 px-6 pb-12 pt-12 lg:grid-cols-[1.1fr_1fr]">
        <Reveal>
          <div className="mb-6 inline-flex rounded-full bg-[#2bb8a0]/10 px-4 py-2 text-sm text-[#7fe3cf] ring-1 ring-[#2bb8a0]/25">Контент-завод для видео</div>
          <h1 className="text-5xl font-bold leading-[1.05] md:text-7xl">Любые мультфильмы, сериалы и ролики — <span className="text-[#5fd6bf]">потоком</span></h1>
          <p className="mt-7 max-w-xl text-xl leading-relaxed text-white/60">VIA превращает тему в готовое видео: план, сценарий, озвучка, персонажи, кадры, анимация, музыка, звуки, сборка и публикация — в одном пайплайне на нодовом холсте.</p>
          <div className="mt-10 flex flex-wrap gap-4"><Btn href="/" solid>Начать проект</Btn><Btn href="#pipeline">Как это работает</Btn></div>
        </Reveal>
        <Reveal delay={150} className="relative h-[420px]">
          {[[1, "left-0 top-6 w-[62%] rotate-[-3deg]", 0], [3, "right-0 top-0 w-[52%] rotate-[4deg]", 1.2], [5, "left-[18%] bottom-0 w-[60%] rotate-[1deg]", 2.4]].map(([i, cls, d]) => (
            <Slot i={i as number} key={i as number} className={`absolute aspect-video rounded-3xl shadow-2xl shadow-black/50 ring-1 ring-white/10 ${cls}`} style={{ animation: `viaFloat 7s ease-in-out ${d}s infinite` }} />
          ))}
        </Reveal>
      </section>

      {/* SHOWCASE */}
      <section className="relative mx-auto max-w-7xl px-6 py-12">
        <div className="pointer-events-none absolute inset-x-0 top-10 -z-0 mx-auto h-[420px] max-w-5xl rounded-full bg-[#2bb8a0]/[0.07] blur-3xl" />
        <div className="relative grid gap-4 lg:grid-cols-[1.35fr_1fr]">
          <Reveal className="group relative overflow-hidden rounded-[2rem] ring-1 ring-white/10">
            <Slot i={2} className="aspect-video h-full w-full transition duration-[1.6s] group-hover:scale-105" />
            <div className="absolute inset-0 bg-gradient-to-t from-[#0c1213] via-[#0c1213]/20 to-transparent" />
            <div className="absolute inset-0 bg-gradient-to-br from-[#2bb8a0]/15 via-transparent to-transparent mix-blend-screen" />
            <div className="absolute bottom-0 left-0 right-0 flex flex-wrap items-end justify-between gap-4 p-7">
              <div>
                <div className="text-sm uppercase tracking-[0.25em] text-[#7fe3cf]/80">Кадры из реальных проектов</div>
                <div className="mt-2 text-3xl font-bold md:text-4xl">Всё это собрано на заводе VIA</div>
              </div>
              <div className="flex gap-2">
                {["Персонажи", "Кадры", "Анимация"].map((t) => (
                  <span key={t} className="rounded-full bg-black/40 px-4 py-1.5 text-sm text-white/75 ring-1 ring-white/10 backdrop-blur">{t}</span>
                ))}
              </div>
            </div>
          </Reveal>
          <div className="grid grid-cols-2 gap-4">
            {[4, 6, 8, 10].map((i, k) => (
              <Reveal key={i} delay={80 + k * 70} className="group relative overflow-hidden rounded-3xl ring-1 ring-white/10">
                <Slot i={i} className="aspect-video h-full w-full transition duration-[1.4s] group-hover:scale-110" />
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-80 transition group-hover:opacity-40" />
              </Reveal>
            ))}
          </div>
        </div>
        <div className="relative mt-4 overflow-hidden rounded-3xl" style={{ maskImage: "linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent)" }}>
          {[0, 1].map((row) => (
            <div key={row} className="flex w-max gap-4 py-2" style={{ animation: `viaPan ${row ? 80 : 65}s linear infinite ${row ? "reverse" : ""}` }}>
              {[...Array(24)].map((_, k) => (
                <Slot i={((k + row * 5) % 12) + 1} key={k} className="h-32 aspect-video rounded-2xl opacity-80 ring-1 ring-white/10 transition hover:opacity-100" />
              ))}
            </div>
          ))}
        </div>
      </section>

      {/* PIPELINE */}
      <section id="pipeline" className="relative mx-auto max-w-7xl px-6 py-12">
        <div className="grid items-end gap-6 lg:grid-cols-[1fr_auto]">
          <H2 k="Один пайплайн" t="От темы до публикации" s="Каждый шаг — нода со своей моделью, промтом и настройками. Результат одного шага сразу уходит в следующий, независимые шаги идут параллельно." />
          <Reveal className="mb-10 hidden gap-3 lg:flex">
            {["11 шагов", "1 холст", "N потоков"].map((t) => (
              <span key={t} className="rounded-2xl bg-white/[0.04] px-5 py-3 text-lg text-white/70 ring-1 ring-white/10">{t}</span>
            ))}
          </Reveal>
        </div>
        <div className="relative overflow-hidden rounded-[2rem] bg-gradient-to-br from-[#14201f] via-[#111819] to-[#0f1415] p-5 ring-1 ring-[#2bb8a0]/15 md:p-7">
          <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-[#2bb8a0]/10 blur-3xl" />
          <div className="pointer-events-none absolute left-0 right-0 top-1/2 hidden h-px bg-gradient-to-r from-transparent via-[#2bb8a0]/40 to-transparent lg:block" />
          <div className="pointer-events-none absolute top-1/2 hidden h-px w-40 bg-gradient-to-r from-transparent via-[#7fe3cf] to-transparent lg:block" style={{ animation: "viaRun 6s linear infinite" }} />
          <div className="relative grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
            {PIPE.map(([t, name, d], i) => (
              <Reveal key={t} delay={i * 45}>
                <div className="group relative h-44 overflow-hidden rounded-2xl bg-[#0d1314] ring-1 ring-white/[0.08] transition duration-500 hover:-translate-y-1 hover:shadow-[0_18px_40px_-12px_rgba(43,184,160,.35)] hover:ring-[#2bb8a0]/45">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={N(t)} alt="" className="absolute inset-0 h-full w-full object-cover opacity-90 transition duration-700 group-hover:scale-110 group-hover:opacity-100" />
                  <div className="absolute inset-0 bg-gradient-to-t from-[#0b1011] via-[#0b1011]/45 to-transparent" />
                  <div className="absolute left-4 top-3 flex h-8 w-8 items-center justify-center rounded-full bg-black/45 text-sm text-[#7fe3cf] ring-1 ring-[#2bb8a0]/35 backdrop-blur">{i + 1}</div>
                  <div className="absolute bottom-4 left-4 right-4">
                    <div className="text-xl font-medium">{name}</div>
                    <div className="mt-0.5 line-clamp-2 text-sm text-white/60">{d}</div>
                  </div>
                </div>
              </Reveal>
            ))}
            <Reveal delay={PIPE.length * 45}>
              <a href="/" className="flex h-44 flex-col justify-end rounded-2xl border border-dashed border-[#2bb8a0]/35 bg-[#2bb8a0]/[0.06] p-4 transition duration-500 hover:bg-[#2bb8a0]/[0.12]">
                <div className="text-xl font-medium text-[#7fe3cf]">+ Свой шаг</div>
                <div className="mt-0.5 text-sm text-white/55">Любая нода из библиотеки</div>
              </a>
            </Reveal>
          </div>
        </div>
      </section>

      {/* FORMATS */}
      <section id="formats" className="mx-auto max-w-7xl px-6 py-12">
        <H2 k="Любой контент" t="Мультфильмы, сериалы, шортсы, объяснялки" s="VIA не привязан к одному жанру. Меняете промты и стиль — получаете другой формат на том же заводе." />
        <div className="grid gap-6 md:grid-cols-3">
          {FORMATS.map(([t, d], i) => (
            <Reveal key={t} delay={i * 70}>
              <div className="overflow-hidden rounded-3xl bg-white/[0.03] ring-1 ring-white/[0.07]">
                <Slot i={((i * 2 + 1) % 12) + 1} className="aspect-video w-full" />
                <div className="p-7"><div className="text-2xl font-medium">{t}</div><p className="mt-3 text-lg leading-relaxed text-white/55">{d}</p></div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" className="mx-auto max-w-7xl px-6 py-12">
        <H2 k="Возможности" t="Как устроен завод" />
        <div className="space-y-6">
          {FEATURES.map(([img, t, d], i) => (
            <Reveal key={t}>
              <div className={`grid items-center gap-10 rounded-[2rem] bg-white/[0.025] p-6 ring-1 ring-white/[0.06] md:grid-cols-2 md:p-10 ${i % 2 ? "md:[&>*:first-child]:order-2" : ""}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={N(img)} alt="" className="aspect-[16/9] w-full rounded-3xl object-cover" />
                <div><div className="text-3xl font-bold md:text-4xl">{t}</div><p className="mt-5 text-xl leading-relaxed text-white/60">{d}</p></div>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* SCALE & SAVINGS */}
      <section className="mx-auto max-w-7xl px-6 py-12">
        <H2 k="Масштаб" t="Много роликов в день без команды монтажёров" s="Вместо сценариста, иллюстратора, диктора и монтажёра на каждый ролик — один настроенный пайплайн, который вы запускаете на список тем." />
        <div className="grid gap-6 md:grid-cols-3">
          {[["Время", "Рутинные шаги — генерация кадров, озвучка, сборка — идут без вашего участия и параллельно."], ["Деньги", "Вы платите за генерации, а не за часы работы людей на каждом этапе."], ["Качество", "Удачные промты и стиль сохраняются, поэтому каждый следующий ролик не хуже предыдущего."]].map(([t, d], i) => (
            <Reveal key={t} delay={i * 80}><div className="h-full rounded-3xl bg-gradient-to-b from-[#2bb8a0]/[0.08] to-transparent p-8 ring-1 ring-[#2bb8a0]/15"><div className="text-3xl font-bold text-[#7fe3cf]">{t}</div><p className="mt-4 text-lg leading-relaxed text-white/60">{d}</p></div></Reveal>
          ))}
        </div>
      </section>

      {/* WHO */}
      <section id="who" className="mx-auto max-w-7xl px-6 py-12">
        <H2 k="Для кого" t="Кому подойдёт VIA" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {["Авторы YouTube-каналов", "Сети faceless-каналов", "Студии анимации и продакшены", "Маркетологи и SMM-команды"].map((t, i) => (
            <Reveal key={t} delay={i * 60}><div className="rounded-3xl bg-white/[0.03] p-7 text-xl font-medium ring-1 ring-white/[0.07]">{t}</div></Reveal>
          ))}
        </div>
      </section>

      {/* PRICING */}
      <section id="pricing" className="mx-auto max-w-7xl px-6 py-12">
        <FactoryStyles />
        <H2 k="Тарифы" t="Тарифы" s="Начните с пробного пакета или запустите свой завод контента." />
        {(["trial", "factory"] as const).map((g) => {
          const list = PLANS.filter((p) => p.group === g);
          return (
            <div key={g} className="mb-12">
              {g === "factory" && <IndustrialDivider />}
              <div className="mb-5 flex flex-wrap items-baseline gap-3">
                <h3 className={g === "factory" ? "text-4xl font-bold tracking-tight text-white" : "text-xl font-medium text-white/80"}>{PLAN_GROUPS[g].title}</h3>
                <span className="text-white/45">{PLAN_GROUPS[g].sub}</span>
              </div>
              <div className={`grid gap-4 ${g === "trial" ? "md:grid-cols-3" : "md:grid-cols-2 xl:grid-cols-4"}`}>
                {list.map((p, i) => g === "factory" ? <Reveal key={p.id} delay={i * 80} className="h-full"><FactoryCard p={p} /></Reveal> : <PlanCard key={p.id} p={p} i={i} />)}
              </div>
            </div>
          );
        })}
      </section>

      {/* FAQ */}
      <section id="faq" className="mx-auto max-w-4xl px-6 py-12">
        <H2 k="FAQ" t="Частые вопросы" />
        <div className="space-y-3">
          {FAQ.map(([q, a], i) => (
            <div key={q} className="rounded-3xl bg-white/[0.03] ring-1 ring-white/[0.07]">
              <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center justify-between px-7 py-6 text-left text-xl font-medium">
                {q}<span className={`text-[#5fd6bf] transition duration-300 ${open === i ? "rotate-45" : ""}`}>+</span>
              </button>
              <div className="grid transition-all duration-500" style={{ gridTemplateRows: open === i ? "1fr" : "0fr" }}>
                <div className="overflow-hidden"><p className="px-7 pb-6 text-lg leading-relaxed text-white/55">{a}</p></div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* CONTACTS */}
      <section id="contacts" className="mx-auto max-w-7xl px-6 py-12">
        <H2 k="Контакты" t="Связаться с командой VIA" s="Вопросы по студии, тарифам и подключению — выберите удобный путь." />
        <div className="grid gap-4 md:grid-cols-3">
          {[
            ["Попробовать студию", "Зарегистрируйтесь и соберите первый ролик на готовом пайплайне.", "/register", "Регистрация"],
            ["Уже есть аккаунт", "Войдите, чтобы продолжить проекты и настроить ноды.", "/login", "Войти"],
            ["Нашли проблему", "В студии есть кнопка «Фикс багов» — сообщение уходит команде вместе с контекстом проекта.", "/", "Открыть студию"],
          ].map(([t, d, href, cta], i) => (
            <Reveal key={t} delay={i * 70}>
              <div className="flex h-full flex-col rounded-3xl bg-white/[0.03] p-7 ring-1 ring-white/[0.07]">
                <div className="text-2xl font-medium">{t}</div>
                <p className="mt-3 flex-1 text-lg leading-relaxed text-white/55">{d}</p>
                <div className="mt-6"><Btn href={href}>{cta}</Btn></div>
              </div>
            </Reveal>
          ))}
        </div>
        {CONTACTS.filter((c) => c.value).length > 0 && (
          <div className="mt-4 flex flex-wrap gap-3">
            {CONTACTS.filter((c) => c.value).map((c) => (
              <a key={c.label} href={c.href} className="rounded-2xl bg-white/[0.04] px-5 py-3 text-lg text-white/70 ring-1 ring-white/10 hover:text-white">{c.label}: {c.value}</a>
            ))}
          </div>
        )}
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-7xl px-6 py-12">
        <Reveal>
          <div className="relative overflow-hidden rounded-[2.5rem] p-14 text-center ring-1 ring-[#2bb8a0]/25 md:p-24">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={N("publish")} alt="" className="absolute inset-0 h-full w-full object-cover opacity-40" />
            <div className="absolute inset-0 bg-[#101516]/60" />
            <div className="relative">
              <h2 className="text-4xl font-bold md:text-6xl">Запустите свой контент-завод</h2>
              <p className="mx-auto mt-6 max-w-2xl text-xl text-white/60">Одна тема на входе — готовый ролик на выходе. Дальше — сколько угодно.</p>
              <div className="mt-10"><Btn href="/" solid>Открыть студию VIA</Btn></div>
            </div>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-white/[0.05]">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-8 px-6 py-10 text-white/40">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/via-logo.png" alt="VIA" className="h-7 opacity-70" />
          <span>Контент-завод для видео</span>
          {NAV.map((n) => <a key={n.id} href={`#${n.id}`} className="hover:text-white">{n.label}</a>)}
          <span className="ml-auto flex gap-6"><a href="/login" className="hover:text-white">Войти</a><a href="/register" className="hover:text-white">Регистрация</a></span>
        </div>
      </footer>
    </div>
  );
}
