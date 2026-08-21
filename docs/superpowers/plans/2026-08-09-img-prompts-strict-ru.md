# img_prompts_trash_polka_strict_ru Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Новый активный вариант `img_pr` со строгим RU-форматом сцены, жёсткими фоном/планом и лимитом 4000 символов; старый watercolor сохранить.

**Architecture:** Новый `.md` в `prompts/05_image_prompts/`; global active → новый вариант; лёгкий footer в `img_pr_batches.py`; STYLE+Negative по-прежнему в `img_pr_style.py`.

**Tech Stack:** markdown master-prompt, Python batches footer, JSON active_variants.

## Global Constraints

- Сцена на русском; STYLE/Negative не в JSON (пайплайн).
- Фон из `shot01_bg` / plan из `scene_feature`+`shot01_description` — священны.
- Тело `промт_картинки` ≤ **4000** символов (до STYLE wrap).
- 1 референс = 1 герой; no clones; no garbage text.
- Ветка push: `main` (ORCHESTRATOR_GIT_BRANCH=main).

---

### Task 1: Master prompt file

**Files:**
- Create: `prompts/05_image_prompts/img_prompts_trash_polka_strict_ru.md`
- Keep: `prompts/05_image_prompts/img_prompts_trash_polka_watercolor.md`

- [ ] Скопировать контракт входа/STYLE §5 из watercolor
- [ ] RU-метки блоков + Эмоция; фоны/планы/4000/anti-clone/1 hero/text rules
- [ ] Чеклист: без STYLE в JSON; Negative через пайплайн

### Task 2: Activate + batch footer

**Files:**
- Modify: `data/library/current/prompts/active_variants.json`
- Modify: `app/services/img_pr_batches.py` (`_BATCH_FOOTER` / follow-up)
- Modify: `prompts/05_image_prompts/.file_meta.json` (если принято трогать)

- [ ] `"img_pr": "img_prompts_trash_polka_strict_ru"`
- [ ] Footer: RU, фон/план, ≤4000, 1 герой, anti-clone, no text garbage

### Task 3: Verify + commit

- [ ] `pytest` связанных тестов img_pr / style / batches
- [ ] Commit + push `main`
