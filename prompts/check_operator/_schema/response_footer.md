---
ФОРМАТ ОТВЕТА (строго): один JSON-объект, без markdown и без текста вокруг.
Схема vp.check.v1:
{
  "schema": "vp.check.v1",
  "verdict": "pass" | "fail",
  "summary": "кратко почему",
  "checks": [{"id": "имя", "ok": true|false, "note": "…"}],
  "forward": {"mode": "inherit" | "explicit", "paths": ["относительный/путь"]},
  "fix": {"target": "source" | "xlsx" | "prompt" | "none", "instructions": "…", "rewrite_file": null}
}
verdict=pass — пустить по стрелке «Ок»; fail — по «Не ок».
forward.mode=inherit — дальше те же файлы, что пришли на проверку;
explicit — только paths (относительно корня проекта).
