import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const config = JSON.parse(fs.readFileSync(path.join(__dirname, "orchestra.config.json"), "utf8"));
const API_KEY = process.env.AITUNNEL_API_KEY || process.env.OPENAI_API_KEY;
const BASE_URL = config.baseUrl || "https://api.aitunnel.ru/v1";
const MODEL = config.model || "gemini-3.1-pro-preview";

function systemPrompt(role) {
  const base = `
Ты часть AI-оркестра для Cline в VS Code на Windows 10.
Пользователь работает через AITUNNEL API.
Проект использует свою версию Chrome/Chromium через проектные скрипты.
Запрещено самовольно открывать другой Chrome, менять browser executable path или устанавливать другой браузер.
Не читать .env, credentials, private keys, secrets.
Не пушить в main/master.
`;

  const roles = {
    orchestrator: `${base}
Ты ORCHESTRATOR. Разбиваешь задачу на этапы, координируешь роли и порядок действий.`,

    architect: `${base}
Ты ARCHITECT. Анализируешь структуру проекта, package.json, README, конфиги, команды запуска, тестов, build, lint, e2e.`,

    developer: `${base}
Ты DEVELOPER. Предлагаешь минимальные точные изменения кода без лишнего рефакторинга.`,

    debugger: `${base}
Ты DEBUGGER. Ищешь корневую причину бага по логам, stack trace, воспроизведению и коду.`,

    tester: `${base}
Ты TESTER. Определяешь проверки: lint, tests, typecheck, build, e2e/browser scripts. Chrome только через проектные команды.`,

    reviewer: `${base}
Ты REVIEWER. Проверяешь diff, риски, безопасность, случайные изменения, secrets, лишние зависимости.`,

    git_operator: `${base}
Ты GIT_OPERATOR. Отдельная ветка agent/<task-name>, git status, diff, commit, push только в отдельную ветку, PR через gh если разрешено.`
  };

  return roles[role] || roles.orchestrator;
}

async function askRole(role, task, context = "") {
  if (!API_KEY) {
    return "ERROR: AITUNNEL_API_KEY не найден в переменных окружения Windows.";
  }

  const body = {
    model: MODEL,
    temperature: config.temperature ?? 0.2,
    max_tokens: config.maxTokens ?? 3000,
    messages: [
      {
        role: "system",
        content: systemPrompt(role)
      },
      {
        role: "user",
        content: `ЗАДАЧА:\n${task}\n\nКОНТЕКСТ:\n${context}\n\nОтветь структурно:\n1. Роль\n2. Анализ\n3. Конкретные действия\n4. Риски\n5. Что Cline должен сделать дальше`
      }
    ]
  };

  const response = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  const raw = await response.text();

  if (!response.ok) {
    return `AITUNNEL ERROR\nHTTP ${response.status}\n${raw}`;
  }

  let json;
  try {
    json = JSON.parse(raw);
  } catch {
    return `AITUNNEL вернул не JSON:\n${raw}`;
  }

  const text = json?.choices?.[0]?.message?.content;

  if (!text || !String(text).trim()) {
    return `Модель вернула пустой ответ.\nRAW:\n${raw}`;
  }

  return String(text).trim();
}

async function runFullOrchestra(task, context = "") {
  const roles = ["orchestrator", "architect", "developer", "debugger", "tester", "reviewer", "git_operator"];
  let accumulated = context;
  const outputs = [];

  for (const role of roles) {
    const result = await askRole(role, task, accumulated);
    outputs.push(`===== ${role.toUpperCase()} =====\n${result}`);
    accumulated += `\n\n[${role.toUpperCase()}]\n${result}`;
  }

  return outputs.join("\n\n");
}

const server = new Server(
  { name: "ai-orchestra-aitunnel", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

const toolSchema = {
  type: "object",
  properties: {
    task: { type: "string" },
    context: { type: "string" }
  },
  required: ["task"]
};

const tools = [
  { name: "ai_orchestra_plan", description: "Запускает весь AI-оркестр.", inputSchema: toolSchema },
  { name: "ai_orchestrator", description: "Главный координатор задачи.", inputSchema: toolSchema },
  { name: "ai_architect", description: "Архитектор проекта.", inputSchema: toolSchema },
  { name: "ai_developer", description: "Разработчик.", inputSchema: toolSchema },
  { name: "ai_debugger", description: "Отладчик.", inputSchema: toolSchema },
  { name: "ai_tester", description: "Тестировщик.", inputSchema: toolSchema },
  { name: "ai_reviewer", description: "Ревьюер.", inputSchema: toolSchema },
  { name: "ai_git_operator", description: "Git-оператор.", inputSchema: toolSchema }
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const name = request.params.name;
  const args = request.params.arguments || {};
  const task = args.task || "";
  const context = args.context || "";

  let text;

  if (name === "ai_orchestra_plan") text = await runFullOrchestra(task, context);
  else if (name === "ai_orchestrator") text = await askRole("orchestrator", task, context);
  else if (name === "ai_architect") text = await askRole("architect", task, context);
  else if (name === "ai_developer") text = await askRole("developer", task, context);
  else if (name === "ai_debugger") text = await askRole("debugger", task, context);
  else if (name === "ai_tester") text = await askRole("tester", task, context);
  else if (name === "ai_reviewer") text = await askRole("reviewer", task, context);
  else if (name === "ai_git_operator") text = await askRole("git_operator", task, context);
  else text = `Unknown tool: ${name}`;

  return { content: [{ type: "text", text }] };
});

const transport = new StdioServerTransport();
await server.connect(transport);
