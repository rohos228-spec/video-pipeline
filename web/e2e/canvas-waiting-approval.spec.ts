import { expect, test, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 3017;
const ORIGIN = `http://127.0.0.1:${PORT}`;

const NOW = "2026-10-17T18:00:00Z";

function projectDetail() {
  return {
    id: 13,
    slug: "parked-fable",
    title: "Запаркованный проект",
    topic: "Запаркованный проект",
    status: "paused",
    hero_mode: "auto",
    auto_mode: false,
    created_at: NOW,
    updated_at: NOW,
    general_plan: null,
    script_text: null,
    hero_description: null,
    image_generator: null,
    aspect_ratio: null,
    image_resolution: null,
    image_quality: null,
    image_relax: null,
    video_generator: null,
    video_resolution: null,
    video_relax: null,
    hero_count: null,
    hero_descriptions: [],
    hero_variations: [],
    hero_variation_modifiers: [],
    item_descriptions: [],
    item_variations: [],
    enrich_slots_count: 0,
    prompt_overrides: {},
    gpt_text_overrides: {},
    meta: {},
    generation_active: false,
  };
}

function workflowDetail() {
  return {
    id: 1,
    name: "default",
    description: null,
    version: 1,
    is_default: true,
    created_at: NOW,
    updated_at: NOW,
    meta: {},
    nodes: [
      {
        id: "plan",
        type: "plan",
        position: { x: 40, y: 80 },
        data: { label: "Ждёт апрув" },
      },
      {
        id: "script",
        type: "script",
        position: { x: 360, y: 80 },
        data: { label: "Будущий шаг" },
      },
    ],
    edges: [
      {
        id: "e1",
        source: "plan",
        target: "script",
        data: { kind: "after" },
      },
    ],
  };
}

function nodeRun(nodeKey: string, nodeType: string, status: string) {
  return {
    id: nodeKey === "plan" ? 1 : 2,
    workflow_run_id: 7,
    node_key: nodeKey,
    node_type: nodeType,
    status,
    progress: 0,
    progress_text: null,
    error: null,
    hitl_request_id: null,
    attempts: 0,
    started_at: null,
    finished_at: null,
    updated_at: NOW,
  };
}

function runDetail() {
  return {
    id: 7,
    workflow_id: 1,
    project_id: 13,
    status: "paused",
    started_at: NOW,
    finished_at: null,
    created_at: NOW,
    updated_at: NOW,
    nodes_snapshot: [],
    edges_snapshot: [],
    node_runs: [
      nodeRun("plan", "plan", "waiting_approval"),
      nodeRun("script", "script", "some_future_status"),
    ],
  };
}

function json(route: { fulfill: (opts: object) => Promise<void> }, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installApiMock(page: Page) {
  await page.route("**/ws/**", (route) => route.abort());
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const pathName = url.pathname.replace(/\/$/, "");
    const method = route.request().method();

    if (pathName === "/api/projects" && method === "GET") {
      return json(route, [projectDetail()]);
    }
    if (pathName === "/api/projects/13" && method === "GET") {
      return json(route, projectDetail());
    }
    if (pathName === "/api/sidebar-layout" && method === "GET") {
      return json(route, {
        folders: [],
        project_layout: {},
        gen_queue: [],
        gen_queue_positions: {},
      });
    }
    if (pathName === "/api/workflows" && method === "GET") {
      const wf = workflowDetail();
      return json(route, [
        {
          id: wf.id,
          name: wf.name,
          description: wf.description,
          version: wf.version,
          is_default: true,
          created_at: wf.created_at,
          updated_at: wf.updated_at,
        },
      ]);
    }
    if (pathName === "/api/workflows/1" && method === "GET") {
      return json(route, workflowDetail());
    }
    if (pathName === "/api/runs" && method === "GET") {
      return json(route, [runDetail()]);
    }
    if (pathName === "/api/runs/7" && method === "GET") {
      return json(route, runDetail());
    }
    if (pathName === "/api/projects/13/ensure-run" && method === "POST") {
      return json(route, { run_id: 7 });
    }
    if (pathName === "/api/hitl/project/13") return json(route, []);
    if (pathName === "/api/artifacts") return json(route, []);
    if (pathName === "/api/projects/13/assets") return json(route, []);
    if (pathName === "/api/projects/13/frames") return json(route, []);
    if (pathName === "/api/projects/13/media-review") return json(route, []);
    if (pathName === "/api/runtime-streams") {
      return json(route, {
        worker_max_parallel: 1,
        default_outsee_streams: 1,
        default_check_streams: 1,
        worker_busy: 0,
        create_max_parallel_outsee: 1,
        limits: {},
      });
    }
    if (pathName === "/api/projects/13/mass-factory/status") {
      return json(route, {
        active: false,
        topics: [],
        cursor: 0,
        revision: 0,
        filename: "",
        factory: false,
        busy_child_id: null,
        children: [],
      });
    }
    if (pathName === "/api/fleet/transfers/active") return json(route, { transfers: [] });
    if (pathName === "/api/text-llm") return json(route, {});
    if (method === "GET") return json(route, []);
    return json(route, { ok: true });
  });
}

test.describe("канвас запаркованного проекта", () => {
  test.describe.configure({ timeout: 180_000 });
  let server: ChildProcess | null = null;
  let serverLog = "";

  test.beforeAll(async () => {
    const require = createRequire(path.join(webRoot, "package.json"));
    const nextBin = require.resolve("next/dist/bin/next");
    server = spawn(process.execPath, [nextBin, "dev", "--port", String(PORT)], {
      cwd: webRoot,
      env: { ...process.env, NODE_ENV: "development", BROWSER: "none" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    server.stdout?.on("data", (buf) => {
      serverLog += buf.toString();
    });
    server.stderr?.on("data", (buf) => {
      serverLog += buf.toString();
    });

    const deadline = Date.now() + 90_000;
    let lastErr = "";
    while (Date.now() < deadline) {
      if (server.exitCode != null) {
        throw new Error(`next dev exited ${server.exitCode}\n${serverLog}`);
      }
      try {
        const res = await fetch(ORIGIN);
        if (res.ok || res.status < 500) return;
        lastErr = `status ${res.status}`;
      } catch (e) {
        lastErr = String(e);
      }
      await new Promise((r) => setTimeout(r, 400));
    }
    throw new Error(`next dev не поднялся: ${lastErr}\n${serverLog}`);
  });

  test.afterAll(async () => {
    if (!server || server.killed) return;
    server.kill("SIGTERM");
    await new Promise((r) => setTimeout(r, 500));
    if (server.exitCode == null) server.kill("SIGKILL");
  });

  test("узлы waiting_approval и неизвестного статуса видны без TypeError", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(String(err)));
    page.on("console", (msg) => {
      if (msg.type() === "error" && /TypeError/.test(msg.text())) {
        errors.push(msg.text());
      }
    });

    await installApiMock(page);
    await page.goto(ORIGIN, { waitUntil: "domcontentloaded" });
    await expect(page.getByPlaceholder("Поиск проектов…")).toBeVisible({ timeout: 20_000 });

    const row = page.locator('[data-project-id="13"]');
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.click();

    const approval = page.locator(".react-flow__node").filter({ hasText: "Ждёт апрув" });
    const future = page.locator(".react-flow__node").filter({ hasText: "Будущий шаг" });
    await expect(approval).toBeVisible({ timeout: 20_000 });
    await expect(future).toBeVisible({ timeout: 20_000 });
    await expect(approval).toContainText("проверка");
    await expect(future).toContainText("ожидание");
    await expect(page.getByText("Application error")).toHaveCount(0);

    expect(errors, errors.join("\n")).toEqual([]);
  });
});
