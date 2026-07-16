---
name: fix-chatgpt-attach
description: Fix ChatGPT file attach / drag-drop / attachment preview counting in app/bots/chatgpt.py. Use when uploads fail, previews are missing, or attachment guard tests fail.
---

# Fix ChatGPT attach

## Hard constraints

- Count attachments with **inline** `page.evaluate` in `_count_attachment_previews` / `_attachments_upload_state`.
- **Forbidden inside evaluate JS strings:**
  - `div.group/attachment`
  - `vpComposer*`
  - `_COMPOSER_ATTACHMENT_DOM_JS`
  - `_composer_page_eval_js`
- Escaped `div.group\\/attachment` is OK only in Playwright locators (`FILE_PREVIEW_SELECTORS`), never in evaluate.

## Workflow

1. Read `app/bots/chatgpt.py` around attach helpers and `FILE_PREVIEW_SELECTORS`.
2. Read `tests/test_chatgpt_attachment_guard.py` and keep it green.
3. Make the smallest fix that preserves attach reliability.
4. Run:
   ```bash
   python3 -m pytest tests/test_chatgpt_attachment_guard.py -q
   ```
5. Tell the user to **restart the backend** after `chatgpt.py` changes (`STUDIO.cmd` stop/start or equivalent).
6. Success markers in logs:
   - `ChatGPT: drag-drop batch — [...]`
   - `все файлы видны`

## Never

- Do not “simplify” selectors by putting slash-group CSS into evaluate
- Do not invent new composer DOM helpers that break `querySelectorAll`
---