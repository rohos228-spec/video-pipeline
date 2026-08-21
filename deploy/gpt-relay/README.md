# GPT / kie + vibecode relay (VPS)

Тонкая прокладка: **на VPS нет video-pipeline**, только reverse-proxy.
ПК шлёт запросы на твой домен; VPS прозрачно форвардит (включая SSE).
**Прокси на ПК не нужен** (`GPT_PROXY_URL` пустой).

## Быстрый старт (Docker + Caddy)

1. VPS (NL/DE/FI), Docker, DNS A-запись на IP (например `gpt.example.com`).
2. Скопируй эту папку на VPS:

```bash
scp -r deploy/gpt-relay user@vps:~/gpt-relay
ssh user@vps
cd ~/gpt-relay
cp .env.example .env
# DOMAIN=gpt.example.com
# RELAY_TOKEN=$(openssl rand -hex 24)
docker compose up -d
```

3. На ПК в `.env` Studio:

```env
GPT_BASE_URL=https://gpt.example.com
GPT_CHAT_PATH=/codex/v1/responses
GPT_RELAY_TOKEN=<тот же RELAY_TOKEN>
GPT_PROXY_URL=
GPT_API_KEY=<ключ kie>
VIBECODE_API_KEY=vk-…
```

4. Рестарт бэкенда. В логе: `GPT API: VPS-relay https://gpt.example.com (proxy=off)`.

Проверка:

```bash
curl -sS -H "X-VP-Relay-Token: $RELAY_TOKEN" https://gpt.example.com/__relay_health
# → ok
```

## Что форвардится

Весь path на upstream:
- `/v1/*` → `https://vibecode.moe` (chat/completions, модели с ноды: GPT 5.5 / 5.6 Sol, Claude, …)
- всё остальное (`/codex/v1/responses`, `/api/v1/jobs/recordInfo`, …) → `https://api.kie.ai`

Буферизация SSE отключена (`flush_interval -1`).

## Без Docker

См. `Caddyfile` — можно поставить Caddy вручную с тем же конфигом и env `DOMAIN` / `RELAY_TOKEN`.

## Безопасность

- `RELAY_TOKEN` обязателен: без заголовка `X-VP-Relay-Token` — 401.
- Ключи kie / vibecode (`Authorization: Bearer …`) только на ПК / в `.env`, на VPS не хранятся.
- По желанию ограничь firewall входящий 443 только своим домашним IP.
