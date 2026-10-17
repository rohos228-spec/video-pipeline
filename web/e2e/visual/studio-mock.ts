import type { Page, Route } from "@playwright/test";

const NOW = "2026-10-15T09:00:00.000Z";

const project = {
  id: 1,
  slug: "nicshe",
  title: "Ницше",
  topic: "Короткий ролик про Ницше",
  status: "script_ready",
  hero_mode: "auto",
  auto_mode: false,
  created_at: NOW,
  updated_at: NOW,
  mass_parent_id: null,
  mass_factory: false,
  mass_lane_position: null,
  sidebar_folder_id: null,
  sidebar_order: 0,
  gen_queue_position: null,
  general_plan: "Три сцены: кабинет, улица, финал.",
  script_text: "Текст закадра для визуального снимка.",
  hero_description: null,
  image_generator: "kie",
  aspect_ratio: "9:16",
  image_resolution: "2K",
  image_quality: "medium",
  image_relax: false,
  video_generator: "kie",
  video_resolution: "1080p",
  video_relax: false,
  hero_count: 1,
  hero_descriptions: [],
  hero_variations: [],
  hero_variation_modifiers: [],
  item_descriptions: [],
  item_variations: [],
  enrich_slots_count: 0,
  prompt_overrides: {},
  gpt_text_overrides: {},
  meta: {},
  montage_handoff_pending: false,
  generation_active: false,
};

const project2 = {
  ...project,
  id: 2,
  slug: "alkatraz",
  title: "Алькатрас",
  topic: "Остров",
  status: "paused",
};

const workflow = {
  id: 1,
  name: "Короткий ролик",
  description: null,
  version: 1,
  is_default: true,
  created_at: NOW,
  updated_at: NOW,
  meta: {},
  nodes: [
    { id: "n_topic", type: "topic", position: { x: 40, y: 180 }, data: { label: "Тема" } },
    { id: "n_plan", type: "plan", position: { x: 280, y: 180 }, data: { label: "План" } },
    { id: "n_script", type: "script", position: { x: 520, y: 180 }, data: { label: "Сценарий" } },
  ],
  edges: [
    { id: "e1", source: "n_topic", target: "n_plan", data: { kind: "after" } },
    { id: "e2", source: "n_plan", target: "n_script", data: { kind: "after" } },
  ],
};

const frames = [
  {
    id: 11,
    project_id: 1,
    number: 1,
    voiceover_text: "Он смотрит в окно и молчит.",
    meaning: "пауза",
    transition_from: null,
    transition_to: null,
    duration_seconds: 4,
    start_ts: 0,
    end_ts: 4,
    image_prompt: "окно, серый свет",
    animation_prompt: "медленный наезд",
    status: "ready",
    attrs: {},
  },
  {
    id: 12,
    project_id: 1,
    number: 2,
    voiceover_text: "Улица пустая, шаги слышно далеко.",
    meaning: null,
    transition_from: null,
    transition_to: null,
    duration_seconds: 5,
    start_ts: 4,
    end_ts: 9,
    image_prompt: "мокрый асфальт",
    animation_prompt: "статичный кадр",
    status: "ready",
    attrs: {},
  },
];

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function mockGet(pathname: string): unknown {
  if (pathname === "/api/studio-version") {
    return {
      build: 514,
      sha: "a8b5a03f",
      label: "v514 · a8b5a03",
      backend_git: "a8b5a03f",
      ui_stale: false,
      pipeline_ok: true,
      backend_ok: true,
      orchestrator_ok: true,
      text_llm_label: "Sol",
      text_llm_enabled: true,
      vps_relay: false,
    };
  }
  if (pathname === "/api/text-llm") {
    return {
      active_provider: "pay52",
      active_label: "GPT 5.6 Sol",
      active_model: "gpt-5.6-sol",
      models: [
        {
          id: "gpt-5.6-sol",
          provider: "pay52",
          label: "GPT 5.6 Sol",
          site: "52pay",
          model: "gpt-5.6-sol",
          key_configured: true,
          active: true,
        },
      ],
    };
  }
  if (pathname === "/api/projects") return [project, project2];
  if (pathname === "/api/projects/1") return project;
  if (pathname === "/api/projects/2") return project2;
  if (pathname === "/api/sidebar-layout") {
    return { folders: [], project_layout: {}, gen_queue: [], gen_queue_positions: {} };
  }
  if (pathname === "/api/workflows") {
    return [
      {
        id: workflow.id,
        name: workflow.name,
        description: null,
        version: 1,
        is_default: true,
        created_at: NOW,
        updated_at: NOW,
      },
    ];
  }
  if (pathname === "/api/workflows/1") return workflow;
  if (pathname === "/api/runs") return [];
  if (pathname === "/api/node-groups") return [];
  if (pathname === "/api/projects/1/frames" || pathname === "/api/projects/2/frames") return frames;
  if (pathname.startsWith("/api/hitl/")) return [];
  if (pathname === "/api/artifacts") return [];
  if (pathname.startsWith("/api/projects/") && pathname.endsWith("/assets")) return [];
  if (pathname.includes("/media-review")) return [];
  if (pathname === "/api/fleet/config") {
    return { auth_required: false, self_node: "studio", hub: "local" };
  }
  if (pathname === "/api/fleet/nodes") {
    return [
      {
        id: 1,
        name: "studio",
        base_url: "http://127.0.0.1:8765",
        is_main: true,
        role: "hub",
        status: "online",
        last_seen: NOW,
        hostname: "studio",
      },
    ];
  }
  if (pathname === "/api/fleet/nodes/1/pipeline") {
    return {
      projects: [
        { id: 1, slug: "nicshe", title: "Ницше", topic: project.topic, status: "script_ready" },
      ],
    };
  }
  if (pathname === "/api/gpt-workspace/sessions") {
    return {
      sessions: [
        {
          id: "s1",
          title: "Разбор сцены",
          created_at: NOW,
          updated_at: NOW,
          message_count: 1,
          status: "idle",
        },
      ],
    };
  }
  if (pathname === "/api/gpt-workspace/sessions/s1") {
    return {
      id: "s1",
      title: "Разбор сцены",
      created_at: NOW,
      updated_at: NOW,
      status: "idle",
      messages: [
        { id: "m1", role: "user", content: "Собери три кадра.", at: NOW },
        { id: "m2", role: "assistant", content: "Кабинет, улица, финал.", at: NOW },
      ],
      attachments: [],
      outputs: [],
    };
  }
  if (pathname === "/api/db/projects/1/graph" || pathname === "/api/db/projects/2/graph") {
    return {
      project: { id: 1, slug: "nicshe", title: "Ницше", topic: project.topic, status: "script_ready" },
      scenes: [
        {
          id: 1,
          sort_key: 1,
          title: "Кабинет",
          place: "комната",
          meaning: "пауза",
          scene_type: "dialogue",
          frame_ids: [11],
        },
      ],
      frames: [
        {
          id: 11,
          uuid: "frame-11",
          number: 1,
          sort_key: 1,
          scene_id: 1,
          status: "ready",
          duration_seconds: 4,
          voiceover_text: "Он смотрит в окно и молчит.",
          meaning: "пауза",
          image_prompt: "окно",
          animation_prompt: "наезд",
          image_url: null,
          attrs: {},
          texts: [],
          prompts: [],
          edges: [],
        },
      ],
      entities: [],
      excel_rows: {},
      scene_registry: [],
    };
  }
  if (pathname.includes("/xlsx/preview")) {
    return {
      path: "project.xlsx",
      sheets: ["план", "Персонажи"],
      active_sheet: "план",
      headers: ["кадр"],
      rows: [["1"]],
      start_row: 1,
    };
  }
  if (pathname === "/api/outsee-create/settings") {
    return {
      media_type: "image",
      image_slug: "gpt-image-2",
      video_slug: "veo-3-1-lite",
      audio_slug: "kie:suno-music",
      aspect: "9:16",
      image_resolution: "2K",
      image_quality: "medium",
      video_resolution: "1080p",
      duration: "5",
      generate_audio: false,
      orientation: "video",
      motion_quality: "std",
      instrumental: false,
      prompt: "",
      sora_size: "small",
    };
  }
  if (pathname === "/api/outsee/status") {
    return {
      configured: true,
      enabled_image: true,
      enabled_video: true,
      http_api: true,
      fallback_cdp: false,
      image_provider: "kie",
      video_provider: "kie",
      hint: "мок",
      queue: 0,
    };
  }
  if (pathname === "/api/create/queue") {
    return {
      max_parallel: 2,
      running_count: 0,
      waiting_count: 0,
      total_active: 0,
      running: [],
      waiting: [],
      jobs: [],
    };
  }
  if (pathname === "/api/kie-create/catalog") {
    return {
      credit_usd: 0.005,
      configured: true,
      categories: [{ id: "image", label: "Картинки" }],
      models: [
        {
          id: "gpt-image-2",
          label: "GPT Image 2",
          category: "image",
          media: "image",
          desc: "кадр",
          result: "image",
          fields: [],
        },
      ],
    };
  }
  if (pathname === "/api/kie-create/credits") {
    return { configured: true, credits: 120, usd: 0.6 };
  }
  if (pathname.startsWith("/api/outsee-create/history")) return [];
  if (pathname.endsWith("/prompts") || pathname.includes("/prompts")) return [];
  return [];
}

export async function installStudioMocks(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/orchestrator")) {
      await route.continue();
      return;
    }
    if (request.method() !== "GET") {
      const path = url.pathname;
      if (path.endsWith("/ensure-run")) {
        await json(route, { run_id: 1 });
        return;
      }
      if (/^\/api\/projects\/\d+$/.test(path) && request.method() === "PATCH") {
        await json(route, project);
        return;
      }
      const message = `studio-mock: unhandled ${request.method()} ${path}`;
      console.error(message);
      await json(route, { detail: "unhandled in studio-mock" }, 501);
      throw new Error(message);
    }
    await json(route, mockGet(url.pathname));
  });
  await page.routeWebSocket(/\/ws/, () => {
    /* соединение не поднимаем: логи остаются в «ожидание…» */
  });
}

export async function seedStudio(page: Page, projectId: number | null) {
  await page.addInitScript(
    (id: number | null) => {
      localStorage.setItem("vp-studio-selected-project-id", JSON.stringify(id));
      localStorage.setItem("vp-orchestrator-open", "false");
      localStorage.setItem("vp-orchestrator-expanded", "false");
      localStorage.setItem("vp-studio-sidebar-collapsed", "false");
      localStorage.setItem("vp-studio-sidebar-width", "304");
    },
    projectId,
  );
}

export async function installFrozenClock(page: Page) {
  await page.clock.install({ time: new Date("2026-10-17T12:00:00+03:00") });
}

export async function settleVisual(page: Page) {
  await page.addStyleTag({
    content: "*{animation:none!important;transition:none!important;caret-color:transparent!important}",
  });
}
