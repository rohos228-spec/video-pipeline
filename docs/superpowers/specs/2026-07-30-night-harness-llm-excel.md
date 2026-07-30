# Night harness + LLM/Excel contract

**Date:** 2026-07-30  
**Goal:** Unified GPT↔app exchange, safe Excel context budget, autonomous verify/repair harness without wipe.

## Ops telemetry

- `project.meta["ops_telemetry"]` — last state (`run_id`, `phase`, `last_step`, `checks`, `artifact_ok`, `next_action`, `updated_at`)
- `data/videos/<slug>/ops/events.jsonl` — append-only history
- Do **not** invent a parallel SQLite table this night

## LLM contract (`app/services/llm_contract.py`)

Markers SoT:

| Marker | Role |
|--------|------|
| `# Лист:` | TSV sheet header |
| `@row=N` | Absolute Excel row |
| `--- XLSX_WRITEBACK ---` | Check report vs TSV fix |
| `WRITEBACK_HINT` | Accompany inject for file/xlsx modes |
| `<<<VOICEOVER>>>…<<<END>>>` | Script extract |
| `# ОТЧЁТ ПРОВЕРКИ` / `vp.check.v1` | Check dialects |

Kinds: `text | xlsx_writeback | check_txt | check_json | voiceover`

## Excel priority pack

Pinned rows on sheet «план» before budget truncate: **15, 45, 46, 48, 49, 50, 64**.  
Banner: truncated ≠ missing binary xlsx.

## Writeback

Plan sheets without `@row=` and without label fallback → **reject** (no short-row densify).

## Harness

- Verify artifacts / frames / R48 / node_runs / logs
- Soft `POST …/steps/{code}/run` only; **never** `reset_step`
- **Never** run `audio` / `music`
- CLI: `scripts/night_harness.py`; API: `GET/POST /api/projects/{id}/harness/*`
