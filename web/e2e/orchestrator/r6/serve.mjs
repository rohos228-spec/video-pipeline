/**
 * Поднимает живой API стека #350 на отдельном порту.
 * Не 8765 и не 8766. Токен только из env, в лог не пишется.
 * Каталог данных пустой: сцены и кадры студии не читаются и не удаляются.
 */
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const port = Number(process.env.ORCH_R6_PORT || 8877);
const sha = process.env.ORCH_R6_REF || "483b1fe89b454b16b7eaac0c23f88d9d47a66bdb";
const workdir = process.env.ORCH_R6_WORKDIR || "/tmp/orch-r6-backend";
const dataDir = process.env.ORCH_R6_DATA_DIR || "/tmp/orch-r6-data";
const python = process.env.ORCH_R6_PYTHON || "/tmp/orch-r6-venv/bin/python";
const token = process.env.ORCH_R6_TOKEN || "";

if (port === 8765 || port === 8766) {
  console.error("ORCH_R6_PORT не может быть 8765 или 8766");
  process.exit(1);
}
if (port !== 8877) {
  console.error("testkit слушает только 8877");
  process.exit(1);
}
if (!token) {
  console.error("ORCH_R6_TOKEN не задан");
  process.exit(1);
}
if (!existsSync(python)) {
  console.error("нет интерпретатора ORCH_R6_PYTHON");
  process.exit(1);
}

function redact(text) {
  return token ? String(text).split(token).join("[redacted]") : String(text);
}

function ensureWorktree() {
  mkdirSync(dataDir, { recursive: true });
  for (const name of ["state.db", "state.db-wal", "state.db-shm"]) {
    rmSync(path.join(dataDir, name), { force: true });
  }
  const gitDir = path.join(workdir, ".git");
  if (!existsSync(gitDir)) {
    execFileSync("git", ["worktree", "add", "--detach", workdir, sha], {
      cwd: repoRoot,
      stdio: ["ignore", "inherit", "inherit"],
    });
    return;
  }
  execFileSync("git", ["checkout", "--detach", sha], {
    cwd: workdir,
    stdio: ["ignore", "inherit", "inherit"],
  });
}

ensureWorktree();

const childEnv = {
  ...process.env,
  PYTHONPATH: workdir,
  PYTHONNOUSERSITE: "1",
  WEB_HOST: "127.0.0.1",
  WEB_PORT: String(port),
  DATA_DIR: dataDir,
  SQLITE_PATH: path.join(dataDir, "state.db"),
  ORCH_API_TOKEN: token,
  ORCH_RUNTIME: "r6-fixture",
  ORCH_TEST: "1",
  HOME: dataDir,
};
delete childEnv.ORCH_R6_TOKEN;

const probe = execFileSync(
  python,
  [
    "-c",
    "from app.orchestrator.agents.runtime import get_runtime; print(type(get_runtime().router).__name__)",
  ],
  { cwd: dataDir, env: childEnv, encoding: "utf8" },
);
const routerImpl = probe.trim().split("\n").at(-1) || "unknown";
writeFileSync(path.join(dataDir, "router-impl.txt"), `${routerImpl}\n`, { mode: 0o600 });
console.error(`orch-r6 router=${routerImpl} port=${port} ref=${sha}`);

const child = spawn(python, ["-m", "app.orchestrator.agents.testkit"], {
  cwd: dataDir,
  env: childEnv,
  stdio: ["ignore", "pipe", "pipe"],
});

function pipe(stream) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    process.stderr.write(redact(chunk));
  });
}
pipe(child.stdout);
pipe(child.stderr);
child.on("exit", (code) => {
  console.error(`orch-r6 backend exited ${code}`);
  process.exit(code ?? 1);
});
process.on("SIGTERM", () => child.kill("SIGTERM"));
process.on("SIGINT", () => child.kill("SIGINT"));
