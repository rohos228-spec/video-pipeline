# GPT / kie relay (VPS)

Тонкая прокладка: **на VPS нет video-pipeline**, только reverse-proxy на `api.kie.ai`.
ПК шлёт запросы на твой домен; VPS прозрачно форвардит (включая SSE).

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
```

4. Рестарт бэкенда. В логе: запросы идут на твой домен, не на `api.kie.ai` напрямую.

Проверка:

```bash
curl -sS -H "X-VP-Relay-Token: $RELAY_TOKEN" https://gpt.example.com/__relay_health
# → ok
```

## Что форвардится

Весь path на upstream `https://api.kie.ai` (Host: `api.kie.ai`):
- `/codex/v1/responses` (POST + SSE)
- `/api/v1/jobs/recordInfo` (retrieve после обрыва)
- прочие kie-пути

Буферизация SSE отключена (`flush_interval -1`).

## Без Docker

См. `Caddyfile` — можно поставить Caddy вручную с тем же конфигом и env `DOMAIN` / `RELAY_TOKEN`.

Альтернатива без домена: SSH-туннель с ПК

```bash
ssh -N -D 1080 user@vps
# GPT_PROXY_URL=socks5://127.0.0.1:1080
# GPT_BASE_URL=https://api.kie.ai
```

## Безопасность

- `RELAY_TOKEN` обязателен: без заголовка `X-VP-Relay-Token` — 401.
- Ключ kie (`Authorization: Bearer …`) по-прежнему только на ПК / в `.env`, на VPS не хранится.
- По желанию ограничь firewall входящий 443 только своим домашним IP.
