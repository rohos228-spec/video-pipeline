import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  CollapsibleSection,
  Divider,
  Grid,
  H1,
  H3,
  Pill,
  Row,
  Stack,
  Swatch,
  Text,
  computeDAGLayout,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type Tab = "pipeline" | "verdict" | "excel1" | "excel2" | "excel3";

type Engine = "GPT" | "Magnific" | "11Labs" | "Suno" | "NeMo" | "FFmpeg" | "Studio" | "Fleet";

type FlowNode = { id: string; label: string; sub?: string; tone?: "info" | "warn" | "ok" };

type PipelineNode = {
  id: string;
  label: string;
  phase: string;
  phaseColor: "blue" | "purple" | "green" | "orange" | "neutral";
  engine: Engine;
  running: string;
  ready: string;
  does: string;
  output: string;
  gptGate?: boolean;
  fleetNote?: string;
  enrichSlot?: 1 | 2 | 3;
};

const NODES: PipelineNode[] = [
  {
    id: "topic",
    label: "Тема",
    phase: "Старт",
    phaseColor: "neutral",
    engine: "Studio",
    running: "new",
    ready: "new",
    does: "Пользователь задаёт тему ролика",
    output: "Project в БД",
  },
  {
    id: "plan",
    label: "План",
    phase: "Текст",
    phaseColor: "blue",
    engine: "GPT",
    running: "planning",
    ready: "plan_ready",
    does: "ChatGPT заполняет project.xlsx — лист «план», структура",
    output: "лист «план»",
    gptGate: true,
  },
  {
    id: "script",
    label: "Сценарий",
    phase: "Текст",
    phaseColor: "blue",
    engine: "GPT",
    running: "scripting",
    ready: "script_ready",
    does: "Закадровый текст → voiceover.txt + xlsx",
    output: "voiceover.txt",
    gptGate: true,
  },
  {
    id: "split",
    label: "Разбивка",
    phase: "Текст",
    phaseColor: "blue",
    engine: "GPT",
    running: "splitting",
    ready: "frames_ready",
    does: "Текст режется по кадрам — строка 49 и кадры в xlsx",
    output: "Frame в БД",
    gptGate: true,
  },
  {
    id: "hero",
    label: "Hero",
    phase: "Референсы",
    phaseColor: "purple",
    engine: "Magnific",
    running: "generating_hero",
    ready: "hero_ready",
    does: "GPT описывает героев → Magnific генерирует референсы",
    output: "images/hero",
    gptGate: true,
  },
  {
    id: "items",
    label: "Items",
    phase: "Референсы",
    phaseColor: "purple",
    engine: "Magnific",
    running: "generating_items",
    ready: "items_ready",
    does: "Опциональные предметы для сцен",
    output: "по необходимости",
    gptGate: true,
  },
  {
    id: "enrich_1",
    label: "Excel 1",
    phase: "Excel",
    phaseColor: "purple",
    engine: "GPT",
    running: "enriching_1",
    ready: "enrich_1_ready",
    does: "Round-trip xlsx: правила заполнения таблицы V7 (антисинонимы, строки плана)",
    output: "project.xlsx",
    gptGate: true,
    enrichSlot: 1,
  },
  {
    id: "enrich_2",
    label: "Excel 2",
    phase: "Excel",
    phaseColor: "purple",
    engine: "GPT",
    running: "enriching_2",
    ready: "enrich_2_ready",
    does: "Round-trip: все персонажи из «план» → лист «Персонажи» + ID в сценах",
    output: "project.xlsx",
    gptGate: true,
    enrichSlot: 2,
  },
  {
    id: "enrich_3",
    label: "Excel 3",
    phase: "Excel",
    phaseColor: "purple",
    engine: "GPT",
    running: "enriching_3",
    ready: "enrich_3_ready",
    does: "Round-trip: главный герой c01 из строки 49 → лист «Персонажи»",
    output: "project.xlsx",
    gptGate: true,
    enrichSlot: 3,
  },
  {
    id: "img_pr",
    label: "Img prompts",
    phase: "Медиа",
    phaseColor: "green",
    engine: "GPT",
    running: "generating_image_prompts",
    ready: "image_prompts_ready",
    does: "Промпты картинок по кадрам",
    output: "строки в xlsx",
    gptGate: true,
  },
  {
    id: "img",
    label: "Картинки",
    phase: "Медиа",
    phaseColor: "green",
    engine: "Magnific",
    running: "generating_images",
    ready: "images_ready",
    does: "Magnific — статичные кадры по промптам",
    output: "images/",
    gptGate: true,
  },
  {
    id: "anim_pr",
    label: "Anim prompts",
    phase: "Медиа",
    phaseColor: "green",
    engine: "GPT",
    running: "generating_animation_prompts",
    ready: "animation_prompts_ready",
    does: "Промпты анимации для каждого кадра",
    output: "xlsx",
    gptGate: true,
  },
  {
    id: "video",
    label: "Видео",
    phase: "Медиа",
    phaseColor: "green",
    engine: "Magnific",
    running: "generating_videos",
    ready: "videos_ready",
    does: "Magnific — ~8 сек клипы по кадрам",
    output: "videos/clip_*.mp4",
    gptGate: true,
  },
  {
    id: "audio",
    label: "Аудио",
    phase: "Звук",
    phaseColor: "orange",
    engine: "11Labs",
    running: "generating_audio",
    ready: "audio_ready",
    does: "Один voice_full на весь ролик",
    output: "voice_full.wav",
    fleetNote: "Hub: recover с диска",
  },
  {
    id: "music",
    label: "Музыка",
    phase: "Звук",
    phaseColor: "orange",
    engine: "Suno",
    running: "generating_music",
    ready: "music_ready",
    does: "GPT промпт → Suno через Magnific audio",
    output: "music/",
    fleetNote: "Agent стоп → bundle на hub",
  },
  {
    id: "assemble",
    label: "Сборка",
    phase: "Финал",
    phaseColor: "orange",
    engine: "NeMo",
    running: "assembling",
    ready: "assembled",
    does: "ASR CUDA → таймлайн R49 → FFmpeg clip_*",
    output: "final/*.mp4",
    fleetNote: "Только hub",
  },
  {
    id: "publish",
    label: "Publish",
    phase: "Финал",
    phaseColor: "orange",
    engine: "Studio",
    running: "publishing",
    ready: "published",
    does: "Публикация через MoreLogin",
    output: "соцсети",
  },
];

const VERDICT_FLOW: FlowNode[] = [
  { id: "v1", label: "Шаг завершён", sub: "status → *_ready" },
  { id: "v2", label: "Worker: maybe_auto_advance", sub: "каждые ~5 сек" },
  { id: "v3", label: "ai_control?", sub: "meta.ai_control + auto_mode" },
  { id: "v4", label: "Нет ai_control", sub: "auto_mode → сразу next *ing", tone: "ok" },
  { id: "v5", label: "Да → GPT Verdict", sub: "prompts/check_* или Studio шаблон", tone: "info" },
  { id: "v6", label: "Новый чат ChatGPT", sub: "вложения: xlsx / voiceover / PNG" },
  { id: "v7", label: "parse_gpt_verdict", sub: "regex «Вердикт: …»" },
  { id: "v8", label: "Одобрено", sub: "HITL approve → next running", tone: "ok" },
  { id: "v9", label: "Не одобрено", sub: "fix_text из ответа", tone: "warn" },
  { id: "v10", label: "Скачать fix-файл", sub: "xlsx или voiceover.txt из ответа GPT" },
  { id: "v11", label: "validate_xlsx + backup old", sub: "sync_project_xlsx → БД" },
  { id: "v12", label: "fix_applied → advance", sub: "или retry до 3 раундов", tone: "warn" },
];

const ENRICH_COMMON: FlowNode[] = [
  { id: "e1", label: "enriching_N", sub: "start_step / Worker" },
  { id: "e2", label: "ensure project.xlsx", sub: "data/videos/<slug>/" },
  { id: "e3", label: "Мастер-промт", sub: "prompts/05a|b|c_enrich_N/" },
  { id: "e4", label: "Сопр. сообщение", sub: "gpt_text_overrides[enrich_N]" },
  { id: "e5", label: "Новый чат + upload", sub: "[prompt.md, project.xlsx]" },
  { id: "e6", label: "Ждём xlsx в ответе", sub: "telegram_style_ask_and_download" },
  { id: "e7", label: "Retry ≤ 3", sub: "если нет вложения / пустой файл", tone: "warn" },
  { id: "e8", label: "sync_project_xlsx", sub: "лист «план» + кадры → БД" },
  { id: "e9", label: "enrich_N_ready", sub: "meta.enrich_completed_slots", tone: "ok" },
  { id: "e10", label: "GPT Verdict", sub: "check_plan → enrich_N", tone: "info" },
  { id: "e11", label: "auto-chain?", sub: "enrich_auto_chain_to → N+1", tone: "info" },
];

const EXCEL1_RULES = [
  "Лист «план»: качество сцен по правилам V7",
  "Запрет синонимичных сцен (±10 соседей)",
  "Запрет повтора формулировок в строках 52, 54–63",
  "Строка 49 — закадровый блок / герой сцены",
  "GPT обязан приложить обновлённый xlsx",
];

const EXCEL2_RULES = [
  "Читает строку 49 листа «план»",
  "Определяет главного героя и вариации",
  "Все персонажи >2 появлений → лист «Персонажи»",
  "ID c01, c02… + поля: имя, внешность, одежда, характер",
  "Проставляет ID персонажей в сценах на «план»",
  "Не переписывает сценарий — только таблица",
];

const EXCEL3_RULES = [
  "Только лист «Персонажи» + строка 49 «план»",
  "Один главный герой → столбец c01",
  "Строки 1,3,4,5,6,7: id, имя, внешность, одежда, роль, правила",
  "Вид персонажа строго по сценарию (не «все коты»)",
  "Не трогать другие листы без необходимости",
];

const NODE_W = 260;
const NODE_H = 68;
const GATE_H = 26;
const FLOW_W = 300;
const FLOW_H = 52;

function enginePillTone(engine: Engine): "info" | "success" | "warning" | "neutral" {
  if (engine === "GPT") return "info";
  if (engine === "Magnific") return "success";
  if (engine === "NeMo" || engine === "FFmpeg") return "warning";
  return "neutral";
}

function FlowDiagram({
  nodes,
  edges,
  width = FLOW_W,
  height = FLOW_H,
  title,
}: {
  nodes: FlowNode[];
  edges: { from: string; to: string }[];
  width?: number;
  height?: number;
  title?: string;
}) {
  const t = useHostTheme();
  const layout = computeDAGLayout({
    nodes: nodes.map((n) => ({ id: n.id })),
    edges,
    direction: "vertical",
    nodeWidth: width,
    nodeHeight: height,
    rankGap: 14,
    nodeGap: 0,
    padding: 12,
  });
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));

  return (
    <Stack gap={8}>
      {title ? (
        <Text size="small" weight="semibold">
          {title}
        </Text>
      ) : null}
      <div style={{ overflowX: "auto" }}>
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="img"
          aria-label={title ?? "flow"}
        >
          {layout.edges.map((e) => (
            <line
              key={`${e.from}-${e.to}`}
              x1={e.sourceX}
              y1={e.sourceY}
              x2={e.targetX}
              y2={e.targetY}
              stroke={t.stroke.secondary}
              strokeWidth={1.5}
            />
          ))}
          {layout.nodes.map((ln) => {
            const data = byId[ln.id];
            if (!data) return null;
            const fill =
              data.tone === "ok"
                ? t.fill.quaternary
                : data.tone === "warn"
                  ? t.fill.tertiary
                  : data.tone === "info"
                    ? t.bg.elevated
                    : t.bg.elevated;
            return (
              <g key={ln.id}>
                <rect
                  x={ln.x}
                  y={ln.y}
                  width={width}
                  height={height}
                  rx={6}
                  fill={fill}
                  stroke={t.stroke.primary}
                  strokeWidth={1}
                />
                <text
                  x={ln.x + 10}
                  y={ln.y + 18}
                  fill={t.text.primary}
                  fontSize={11}
                  fontWeight={600}
                >
                  {data.label}
                </text>
                {data.sub ? (
                  <text x={ln.x + 10} y={ln.y + 34} fill={t.text.secondary} fontSize={9}>
                    {data.sub.length > 48 ? `${data.sub.slice(0, 48)}…` : data.sub}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      </div>
    </Stack>
  );
}

function chainEdges(ids: string[]) {
  const edges: { from: string; to: string }[] = [];
  for (let i = 0; i < ids.length - 1; i += 1) {
    edges.push({ from: ids[i], to: ids[i + 1] });
  }
  return edges;
}

function PipelineCanvas({
  selectedId,
  onSelect,
}: {
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const t = useHostTheme();

  const layoutNodes = NODES.flatMap((n) => {
    const items: { id: string }[] = [{ id: n.id }];
    if (n.gptGate) items.push({ id: `${n.id}__gate` });
    return items;
  });

  const layoutEdges: { from: string; to: string }[] = [];
  for (let i = 0; i < NODES.length; i += 1) {
    const n = NODES[i];
    if (n.gptGate) {
      layoutEdges.push({ from: n.id, to: `${n.id}__gate` });
      const next = NODES[i + 1];
      if (next) layoutEdges.push({ from: `${n.id}__gate`, to: next.id });
    } else if (i < NODES.length - 1) {
      layoutEdges.push({ from: n.id, to: NODES[i + 1].id });
    }
  }

  const layout = computeDAGLayout({
    nodes: layoutNodes,
    edges: layoutEdges,
    direction: "vertical",
    nodeWidth: NODE_W,
    nodeHeight: NODE_H,
    rankGap: 32,
    nodeGap: 0,
    padding: 16,
  });

  const dataById = Object.fromEntries(NODES.map((n) => [n.id, n]));

  const phases = ["Старт", "Текст", "Референсы", "Excel", "Медиа", "Звук", "Финал"];
  const phaseBands: { y: number; h: number; label: string }[] = [];
  for (const phase of phases) {
    const inPhase = layout.nodes.filter((ln) => {
      if (ln.id.endsWith("__gate")) return false;
      return dataById[ln.id]?.phase === phase;
    });
    if (!inPhase.length) continue;
    const ys = inPhase.map((n) => n.y);
    const maxY = Math.max(...inPhase.map((n) => n.y + NODE_H));
    phaseBands.push({
      y: Math.min(...ys) - 8,
      h: maxY - Math.min(...ys) + 16,
      label: phase,
    });
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <svg
        width={layout.width + 110}
        height={layout.height}
        viewBox={`0 0 ${layout.width + 110} ${layout.height}`}
        role="img"
        aria-label="Pipeline"
      >
        {phaseBands.map((b) => (
          <g key={b.label}>
            <rect
              x={0}
              y={b.y}
              width={layout.width + 110}
              height={b.h}
              rx={8}
              fill={t.fill.quaternary}
              stroke={t.stroke.tertiary}
              strokeWidth={1}
            />
            <text x={12} y={b.y + 16} fill={t.text.tertiary} fontSize={10} fontWeight={600}>
              {b.label.toUpperCase()}
            </text>
          </g>
        ))}

        {layout.edges.map((e) => (
          <line
            key={`${e.from}-${e.to}`}
            x1={e.sourceX}
            y1={e.sourceY}
            x2={e.targetX}
            y2={e.targetY}
            stroke={t.stroke.secondary}
            strokeWidth={1.5}
          />
        ))}

        {layout.nodes.map((ln) => {
          if (ln.id.endsWith("__gate")) {
            const gx = ln.x + NODE_W / 2;
            return (
              <g key={ln.id}>
                <rect
                  x={ln.x + NODE_W / 2 - 72}
                  y={ln.y}
                  width={144}
                  height={GATE_H}
                  rx={13}
                  fill={t.fill.tertiary}
                  stroke={t.stroke.secondary}
                  strokeWidth={1}
                  strokeDasharray="4 3"
                />
                <text
                  x={gx}
                  y={ln.y + GATE_H / 2 + 4}
                  textAnchor="middle"
                  fill={t.text.secondary}
                  fontSize={9}
                >
                  GPT Verdict → *_ready
                </text>
              </g>
            );
          }

          const data = dataById[ln.id];
          if (!data) return null;
          const selected = selectedId === ln.id;
          const isExcel = data.enrichSlot !== undefined;

          return (
            <g
              key={ln.id}
              style={{ cursor: "pointer" }}
              onClick={() => onSelect(ln.id)}
              role="button"
              tabIndex={0}
            >
              <rect
                x={ln.x}
                y={ln.y}
                width={NODE_W}
                height={NODE_H}
                rx={8}
                fill={selected ? t.accent.control : t.bg.elevated}
                stroke={selected ? t.accent.primary : isExcel ? t.stroke.secondary : t.stroke.primary}
                strokeWidth={selected ? 2 : 1}
              />
              <text
                x={ln.x + 12}
                y={ln.y + 20}
                fill={selected ? t.text.onAccent : t.text.primary}
                fontSize={12}
                fontWeight={600}
              >
                {data.label}
              </text>
              <text
                x={ln.x + 12}
                y={ln.y + 38}
                fill={selected ? t.text.onAccent : t.text.secondary}
                fontSize={9}
              >
                {data.does.length > 44 ? `${data.does.slice(0, 44)}…` : data.does}
              </text>
              <text
                x={ln.x + 12}
                y={ln.y + 56}
                fill={selected ? t.text.onAccent : t.text.tertiary}
                fontSize={8}
              >
                {data.running} → {data.ready}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function RuleList({ items }: { items: string[] }) {
  return (
    <Stack gap={4}>
      {items.map((item) => (
        <Row key={item} gap={8} align="start">
          <Text size="small" tone="tertiary">
            —
          </Text>
          <Text size="small">{item}</Text>
        </Row>
      ))}
    </Stack>
  );
}

function VerdictTab() {
  return (
    <Stack gap={16}>
      <Callout tone="info" title="Когда срабатывает">
        После каждого *_ready, если включены auto_mode + ai_control. Шаблон проверки —
        prompts/check_plan, check_script, check_hero, check_images или кастом из Studio
        (meta.gpt_verdict_templates).
      </Callout>
      <FlowDiagram
        title="Цепочка GPT Verdict"
        nodes={VERDICT_FLOW}
        edges={[
          ...chainEdges(["v1", "v2", "v3"]),
          { from: "v3", to: "v4" },
          { from: "v3", to: "v5" },
          ...chainEdges(["v5", "v6", "v7"]),
          { from: "v7", to: "v8" },
          { from: "v7", to: "v9" },
          ...chainEdges(["v9", "v10", "v11", "v12"]),
        ]}
        width={320}
        height={48}
      />
      <Grid columns={2} gap={12}>
        <Card>
          <CardHeader>Вердикт: Одобрено</CardHeader>
          <CardBody>
            <Text size="small">
              parse_gpt_verdict → HITL approve → advance_after_gpt_verdict → следующий
              running-статус (planning, enriching_2, generating_images…).
            </Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Вердикт: Не одобрено</CardHeader>
          <CardBody>
            <Text size="small">
              GPT прикладывает исправленный xlsx или voiceover.txt. Бот: backup old →
              validate_xlsx → sync → fix_applied=true → advance без повторного enrich-run.
              До 3 раундов на один шаг.
            </Text>
          </CardBody>
        </Card>
      </Grid>
      <CollapsibleSection title="Вложения по шагам" count={4}>
        <Stack gap={6}>
          <Text size="small">plan, split, enrich_1–5, img_pr, anim_pr → project.xlsx</Text>
          <Text size="small">script → xlsx + voiceover.txt</Text>
          <Text size="small">hero, items, images → xlsx + до 12 PNG референсов</Text>
          <Text size="small">enrich_1–3 используют check_plan как шаблон проверки</Text>
        </Stack>
      </CollapsibleSection>
    </Stack>
  );
}

function ExcelTab({ slot, rules, promptPath, focus }: {
  slot: 1 | 2 | 3;
  rules: string[];
  promptPath: string;
  focus: string;
}) {
  const ids = ENRICH_COMMON.map((n) => n.id);
  return (
    <Stack gap={16}>
      <Callout tone="neutral" title={`Excel ${slot} — ${focus}`}>
        Код: enrich_xlsx.py slot={slot}. Статусы enriching_{slot} → enrich_{slot}_ready.
        После items_ready цепочка 1→2→3→img_pr (слоты 4–5 опциональны).
      </Callout>
      <FlowDiagram
        title={`Round-trip Excel ${slot}`}
        nodes={ENRICH_COMMON}
        edges={chainEdges(ids)}
        width={320}
        height={46}
      />
      <Row gap={16} wrap>
        <Stack gap={6} style={{ flex: 1, minWidth: 200 }}>
          <H3>Что заполняет GPT</H3>
          <RuleList items={rules} />
        </Stack>
        <Stack gap={6} style={{ flex: 1, minWidth: 200 }}>
          <H3>Промт</H3>
          <Text size="small">{promptPath}</Text>
          <Divider />
          <Text size="small" tone="tertiary">
            Сопр. текст: gpt_text_overrides.enrich_{slot} или ENRICH_DEFAULT_ACCOMPANYING_TEXT
          </Text>
          <Text size="small" tone="tertiary">
            При успехе: сброс meta.excel_hero → Hero перечитает «Персонажи»
          </Text>
        </Stack>
      </Row>
    </Stack>
  );
}

export default function VideoPipelineOverview() {
  const t = useHostTheme();
  const [tab, setTab] = useCanvasState<Tab>("vp-tab", "pipeline");
  const [selectedId, setSelectedId] = useCanvasState("vp-node", "enrich_1");
  const selected = NODES.find((n) => n.id === selectedId) ?? NODES[6];

  const tabs: { id: Tab; label: string }[] = [
    { id: "pipeline", label: "Пайплайн" },
    { id: "verdict", label: "GPT Verdict" },
    { id: "excel1", label: "Excel 1" },
    { id: "excel2", label: "Excel 2" },
    { id: "excel3", label: "Excel 3" },
  ];

  return (
    <Stack gap={16} style={{ padding: "8px 4px 32px", maxWidth: 720 }}>
      <Stack gap={6}>
        <Pill tone="info">video-pipeline</Pill>
        <H1>Pipeline + GPT + Excel</H1>
        <Text tone="secondary">
          Magnific — картинки, видео, audio Suno. Центр данных — project.xlsx. Worker
          ~5с: running → *_ready → [GPT Verdict] → next.
        </Text>
      </Stack>

      <Row gap={8} wrap>
        {tabs.map((item) => (
          <Pill
            key={item.id}
            tone={tab === item.id ? "info" : "neutral"}
            active={tab === item.id}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </Pill>
        ))}
      </Row>

      {tab === "pipeline" ? (
        <Stack gap={12}>
          <PipelineCanvas selectedId={selectedId} onSelect={setSelectedId} />
          <Stack
            gap={10}
            style={{
              padding: 12,
              borderRadius: 8,
              border: `1px solid ${t.stroke.primary}`,
              background: t.bg.elevated,
            }}
          >
            <Row gap={8} align="center">
              <Swatch color={selected.phaseColor} />
              <Text weight="semibold">{selected.label}</Text>
              <Pill tone={enginePillTone(selected.engine)} size="small">
                {selected.engine}
              </Pill>
              {selected.gptGate ? (
                <Pill tone="warning" size="small">
                  GPT gate
                </Pill>
              ) : null}
            </Row>
            <Text tone="secondary">{selected.does}</Text>
            <Text size="small">
              {selected.running} → {selected.ready} · {selected.output}
            </Text>
            {selected.enrichSlot ? (
              <Button variant="secondary" onClick={() => setTab(`excel${selected.enrichSlot}` as Tab)}>
                Открыть схему Excel {selected.enrichSlot}
              </Button>
            ) : selected.gptGate ? (
              <Button variant="secondary" onClick={() => setTab("verdict")}>
                Открыть GPT Verdict
              </Button>
            ) : null}
          </Stack>
        </Stack>
      ) : null}

      {tab === "verdict" ? <VerdictTab /> : null}
      {tab === "excel1" ? (
        <ExcelTab
          slot={1}
          rules={EXCEL1_RULES}
          promptPath="prompts/05a_enrich_1/ — Правило_заполнения_таблицы_V7"
          focus="качество листа «план»"
        />
      ) : null}
      {tab === "excel2" ? (
        <ExcelTab
          slot={2}
          rules={EXCEL2_RULES}
          promptPath="prompts/05b_enrich_2/ — агент по созданию персонажей"
          focus="все персонажи + ID в сценах"
        />
      ) : null}
      {tab === "excel3" ? (
        <ExcelTab
          slot={3}
          rules={EXCEL3_RULES}
          promptPath="prompts/05c_enrich_3/default.md"
          focus="главный герой c01"
        />
      ) : null}
    </Stack>
  );
}
