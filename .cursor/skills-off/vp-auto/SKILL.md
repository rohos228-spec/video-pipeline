---
name: vp-auto
description: Полный автономный цикл разработки video-pipeline на Superpowers — от задачи до проверенной ветки в worktree без вопросов человеку.
disable-model-invocation: true
---

# /vp-auto <задача>

Автопилот включён (см. `.cursor/rules/00-autonomy.mdc` §2). Выполни задачу целиком.

1. **Контекст.** Прочитай `docs/AGENT_MAP.md` и источники затронутой зоны. Проверь
   `git status` основного checkout — чужие незакоммиченные правки не трогай.
2. **Изоляция — сразу.** `superpowers:using-git-worktrees`: `.worktrees/<тема>`, ветка
   `vp/<тема>`. Без установки зависимостей; python — `.venv` основного checkout.
   Все файлы дальше (спека, план, код) — только внутри worktree.
3. **Спека.** `superpowers:brainstorming` в режиме автопилота: вопросы решай сам,
   допущения — в раздел «Допущения (автопилот)». Сохрани в
   `docs/superpowers/specs/YYYY-MM-DD-<тема>-design.md` внутри worktree и закоммить.
   Самопроверка по чек-листу скилла вместо одобрения человеком.
4. **Базовая линия.** Прогони тесты зоны, запиши упавшие в спеку («падали до меня»).
5. **План.** `superpowers:writing-plans` → `docs/superpowers/plans/YYYY-MM-DD-<тема>.md`
   в worktree. Мелкие задачи, у каждой — тест и команда проверки. Коммит.
6. **Исполнение.** `superpowers:subagent-driven-development`:
   - исполнитель — модель по §7 правила (механика `composer-2.5`);
   - после ревьюера Superpowers на задачи в `app/`, `prompts/`, `web/` добавь
     субагента `vp-contract-reviewer`;
   - журнал `.superpowers/sdd/<план>/progress.md` веди строго — по нему работает `/vp-resume`.
   - независимые задачи без общих файлов можно параллелить
     (`superpowers:dispatching-parallel-agents`), каждую в своём worktree.
7. **Проверка.** Полный прогон тестов, сравнение с базовой линией, затем субагент
   `vp-verifier`. «НЕ ГОТОВО» → исправляй (до 5 раундов), потом снова `vp-verifier`.
8. **Завершение.** `superpowers:finishing-a-development-branch` → «Keep the branch».
   Никаких push/merge.
9. **Отчёт** (§8 правила) + команды для человека, например:

```
cd <основной checkout>
git log --oneline <база>..vp/<тема>
git merge --no-ff vp/<тема>      # в ветку ПК, когда проверишь руками
git push origin <ветка ПК>
```

и список ручных проверок (Studio, UI, живые шаги).
