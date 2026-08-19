# Модели на нодах канваса — провайдер и токен

Пикер на ноде (`NodeModelPicker`) показывает каталог **vibecode.moe** (28 моделей).
Цены в UI = сырые vibecode **×3**.

Это **не** шапка Studio. В шапке отдельный селект (kie / vibecode Sol·5.5 / Kimi) —
он **не** задаёт модель текстовой ноды пайплайна.

---

## Два контура (не путать ключи)

| Что выбрал на ноде | Куда реально идёт запрос | Ключ в `.env` | Relay |
|---|---|---|---|
| Любая **текстовая** модель (GPT / Claude / Gemini / Grok / Kimi) | `POST {GPT_BASE_URL}/v1/chat/completions` → VPS → **vibecode.moe** | **`VIBECODE_API_KEY`** (`vk-…`) | **`GPT_RELAY_TOKEN`** + `GPT_BASE_URL` |
| Любая **картинка** (GPT Image / Nano Banana) | Outsee или Grsai, как `IMAGE_PROVIDER` | **`GRSAI_API_KEY`** или **`OUTSEE_API_KEY`** | не vibecode |

`GPT_API_KEY` (kie.ai, `/codex/v1/responses`) **не** используется пикером ноды.
Он нужен только шапке «GPT (kie.ai)» и шагам, где на ноде нет текстовой модели
(типично img/hero, если внутри зовут LLM без override).

`GPT_RELAY_TOKEN` — не ключ модели, а пароль VPS. Без него kie и vibecode
не пройдут relay. Сами `vk-…` / kie-ключ на VPS не кладутся.

Если на текстовой ноде модель не трогали — всё равно уходит **GPT 5.6 Sol**
через **`VIBECODE_API_KEY`**, не kie.

Картинку на текстовой ноде (plan/script/…) оркестратор **игнорирует** для LLM.
Текст на ноде Картинки/Hero/Items **игнорирует** для PNG — рисует
`IMAGE_PROVIDER` + выбранный image-id (или дефолт проекта).

---

## Текстовые модели → всегда vibecode (`VIBECODE_API_KEY`)

Дефолт текстовых нод: `gpt-5.6-sol`.

Цены ниже — как в UI ($ / 1M токенов, уже ×3).

### OpenAI (вкладки пикера)

| В UI | id API | вход | выход |
|---|---|---:|---:|
| GPT 5.6 Sol | `gpt-5.6-sol` | 1.28 | 7.68 |
| GPT 5.6 Terra | `gpt-5.6-terra` | 0.51 | 3.07 |
| GPT 5.6 Luna | `gpt-5.6-luna` | 0.08 | 0.47 |
| GPT 5.5 | `gpt-5.5` | 1.28 | 7.68 |
| GPT-5.5 OpenAI compact | `gpt-5.5-openai-compact` | 1.97 | 11.81 |
| GPT 5.4 Mini | `gpt-5.4-mini` | 0.19 | 1.15 |
| Codex Auto Review | `codex-auto-review` | 0.64 | 3.84 |

### Anthropic

| В UI | id API | вход | выход |
|---|---|---:|---:|
| Claude Fable 5 | `claude-fable-5` | 14.12 | 70.58 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 1.41 | 7.06 |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | 0.89 | 4.43 |
| Claude Sonnet 5 | `claude-sonnet-5` | 0.59 | 2.95 |
| Claude Opus 4.6 | `claude-opus-4-6` | 1.48 | 7.38 |
| Claude Opus 4.7 | `claude-opus-4-7` | 1.48 | 7.38 |
| Claude Opus 4.8 | `claude-opus-4-8` | 1.48 | 7.38 |
| Claude Opus 5 | `claude-opus-5` | 1.48 | 7.38 |

### Gemini

| В UI | id API | вход | выход |
|---|---|---:|---:|
| Gemini 3 Flash | `gemini-3-flash-preview` | 0.30 | 1.77 |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | 1.18 | 7.09 |
| Gemini 3.5 Flash | `gemini-3.5-flash` | 0.89 | 5.31 |
| Gemini 3.6 Flash | `gemini-3.6-flash` | 0.89 | 4.43 |

### xAI

| В UI | id API | вход | выход |
|---|---|---:|---:|
| Grok 4.5 | `grok-4-5` | 0.20 | 0.59 |
| Grok 4.6 | `grok-4-6` | 0.20 | 0.59 |

### Moonshot

| В UI | id API | вход | выход |
|---|---|---:|---:|
| Kimi K3 | `kimi-k3` | 0.49 | 1.77 |

Kimi с ноды ≠ Kimi в шапке. Нода: vibecode `kimi-k3` + `VIBECODE_API_KEY`.
Шапка «Kimi K3 (TokenRouter)»: `TOKENROUTER_API_KEY`, модель `moonshotai/kimi-k3-free`.

---

## Картинки на нодах Hero / Items / Картинки

Пикер берёт **цены** с vibecode, генерация идёт в **`IMAGE_PROVIDER`**.

Дефолт этих нод: `gpt-image-2`. Цены UI — $ / фото, уже ×3.

| В UI | id в пикере | id генератора Studio | Ключ |
|---|---|---|---|
| GPT Image 2 SLOW | `gpt-image-2` | `gpt_image_2` | `GRSAI_API_KEY` если `IMAGE_PROVIDER=grsai`, иначе `OUTSEE_API_KEY` |
| GPT Image 2 FAST | `gpt-image-2-vip` | `gpt_image_2_vip` | то же |
| Nano Banana | `nano-banana` | `nano_banana` | то же |
| Nano Banana 2 (1K/2K/4K) | `nano-banana-2` | `nano_banana_2` | то же |
| Nano Banana Pro (1K/2K/4K) | `nano-banana-pro` | `nano_banana_pro` | то же |
| Nano Banana 2 Lite | `nano-banana-2-lite` | `nano_banana_2_lite` | то же |

`VIBECODE_API_KEY` для PNG **не** списывается.

В пикере ноды нет Seedream / GPT Image 1.5 / Nano Banana Fast — они только
в старых generation-options / Create.

Видео (Sora / Veo / Kling) пикер ноды не показывает:
`VIDEO_PROVIDER` + `GRSAI_API_KEY` / `OUTSEE_API_KEY`, Kling fallback — `KIE_API_KEY`.

---

## Шапка Studio (не ноды)

| Пункт | Провайдер | Ключ | Куда |
|---|---|---|---|
| GPT 5.6 Sol | vibecode | `VIBECODE_API_KEY` | VPS `/v1/chat/completions` |
| GPT 5.5 | vibecode | `VIBECODE_API_KEY` | то же |
| GPT (kie.ai) | kie | `GPT_API_KEY` | VPS `/codex/v1/responses` → api.kie.ai |
| Kimi K3 (TokenRouter) | tokenrouter | `TOKENROUTER_API_KEY` | api.tokenrouter.com (не VPS-vibecode) |

Пока на текстовой ноде висит модель из каталога (хотя бы дефолтный Sol) —
шапка kie/Kimi этот шаг не перебивает.

---

## Какие ключи должны быть на ПК

```env
GPT_BASE_URL=https://<твой-VPS>
GPT_RELAY_TOKEN=<тот же, что на VPS>
GPT_API_KEY=<kie>          # шапка kie / редкий fallback
VIBECODE_API_KEY=vk-…      # все текстовые модели на нодах
IMAGE_PROVIDER=grsai       # или outsee
GRSAI_API_KEY=…            # PNG с нод, если grsai
OUTSEE_API_KEY=…           # PNG с нод, если outsee
```

Код: `app/services/vibecode_catalog.py`, снимок `app/services/vibecode_models_snapshot.json`,
роут LLM `app/services/llm_override.py` + `app/services/gpt_api.py`,
картинки `effective_image_generator_id` → `IMAGE_PROVIDER`.
Relay: `deploy/gpt-relay/README.md`.
