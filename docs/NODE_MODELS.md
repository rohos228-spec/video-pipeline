# Модели на нодах канваса — провайдер и токен

Пикер на ноде (`NodeModelPicker`) показывает каталог **vibecode.moe** (28 моделей).
Цены в UI = сырые vibecode **×3**.

Это **не** шапка Studio. В шапке отдельный селект (kie / vibecode Sol·5.5 / Kimi) —
он **не** задаёт модель текстовой ноды пайплайна.

---

## Два контура (не путать ключи)

| Что выбрал на ноде | Куда реально идёт запрос | Ключ в `.env` |
|---|---|---|
| Любая **текстовая** модель (GPT / Claude / Gemini / Grok / Kimi) | VPS `/v1/chat/completions` → **vibecode.moe** | **`VIBECODE_API_KEY`** (`vk-…`) + `GPT_RELAY_TOKEN` |
| **GPT Image 2**, **Nano Banana 2** (+ Lite) | Outsee Developer API | **`OUTSEE_API_KEY`** |
| **Veo 3.1 Lite** (нода Видео) | Outsee Developer API | **`OUTSEE_API_KEY`** |
| **Kling 2.6** (нода Видео) | kie.ai Market | **`KIE_API_KEY`** |
| Прочие картинки (Nano Banana / Pro, Seedream, …) | как `IMAGE_PROVIDER` | `GRSAI_API_KEY` или `OUTSEE_API_KEY` |

`GPT_API_KEY` (kie.ai, `/codex/v1/responses`) **не** используется пикером ноды.
Он нужен только шапке «GPT (kie.ai)» и шагам, где на ноде нет текстовой модели
(типично img/hero, если внутри зовут LLM без override).

`GPT_RELAY_TOKEN` — не ключ модели, а пароль VPS. Без него kie и vibecode
не пройдут relay. Сами `vk-…` / kie-ключ на VPS не кладутся.

Если на текстовой ноде модель не трогали — всё равно уходит **GPT 5.6 Sol**
через **`VIBECODE_API_KEY`**, не kie.

Картинку на текстовой ноде (plan/script/…) оркестратор **игнорирует** для LLM.
Текст на ноде Картинки/Hero/Items **игнорирует** для PNG.

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

Дефолт: **GPT Image 2** (`gpt-image-2-vip`, бывший Fast; Slow убран). Цены UI — $ / фото, уже ×3.

| В UI | id в пикере | id генератора | Ключ |
|---|---|---|---|
| GPT Image 2 | `gpt-image-2-vip` | `gpt_image_2_vip` | **`OUTSEE_API_KEY`** |
| Nano Banana | `nano-banana` | `nano_banana` | `IMAGE_PROVIDER` |
| Nano Banana 2 (1K/2K/4K) | `nano-banana-2` | `nano_banana_2` | **`OUTSEE_API_KEY`** |
| Nano Banana Pro (1K/2K/4K) | `nano-banana-pro` | `nano_banana_pro` | `IMAGE_PROVIDER` |
| Nano Banana 2 Lite | `nano-banana-2-lite` | `nano_banana_2_lite` | **`OUTSEE_API_KEY`** |

Старый id `gpt-image-2` (Slow) с ноды мапится на `gpt-image-2-vip`.

## Видео на ноде Videos

| В UI | id | генератор | Ключ |
|---|---|---|---|
| Veo 3.1 Lite | `veo-3-1-lite` | `veo_3_1_lite` | **`OUTSEE_API_KEY`** |
| Kling 2.6 | `kling-2-6` | `kling_2_6` | **`KIE_API_KEY`** |

Прочий Sora/Kling 3 / Seedance — как `VIDEO_PROVIDER` (обычно Grsai).

`VIBECODE_API_KEY` для PNG/видео **не** списывается.

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
OUTSEE_API_KEY=…           # GPT Image 2, Nano Banana 2, Veo 3.1 Lite
KIE_API_KEY=…              # Kling 2.6
IMAGE_PROVIDER=grsai       # прочие картинки
GRSAI_API_KEY=…            # прочие PNG/видео, если не Outsee/Kie
```

Код: `app/services/media_route.py`, `app/services/vibecode_catalog.py`,
роут LLM `app/services/llm_override.py` + `app/services/gpt_api.py`.
Relay: `deploy/gpt-relay/README.md`.
