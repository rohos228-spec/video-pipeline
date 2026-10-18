**VERDICT: OK** (только по тому, что есть в PR-инкременте; в рабочей директории файлов нет, поэтому `shot_dropped.py`, `no_deletions.py` и `activity_flags.py` я не открывал)

**(a) Обход единого предиката**
- `shot_activity.py`: флаги `активен` и скрывающие метки читаются только через `flag_is_false`/`flag_is_true` (`_activity_choice`, `_source_activity`). `_flag(op) is not True` и `_flag(op) is not active` сравнивают уже готовый результат (True/False/None), это не обход. `_marked_qc` проверяет на истинность `qc`/`render`, но это не флаги активности или удаления.
- `activity_flags.py:70`: проверка `is False` стоит внутри самого предиката, это нормально.
- Подтверждённых дефектов нет.

**(b) marked_shot_removed**
- Код задан в `shot_dropped.py:93`, функция подключена в `preservation.py:152`, есть тест `tests/validators/test_remove_inactive_shot_is_error.py:23,31`. Условие срабатывания в `shot_dropped.py:43–93` я не видел, поэтому подтвердить его не могу.

**Непроверено, нужно посмотреть до мержа:**
1. `no_deletions.py:70`: `raw.get("available") is False` проверяется в обход предиката. Если `available` означает мягкое удаление, то значения `0`, `"false"`, `"нет"` не распознаются. Тогда исход зависит от того, что делает код рядом: либо удаление пропускается, либо срабатывает ложная тревога. Нужен контекст вокруг строки 70.
2. В grep искались только `is False`/`is not False`. Проверки на истинность (`if not x.get("активен")`, `if x.get("deleted")`, `bool(...)`) он не ловит. Нужен отдельный поиск: `get\("(активен|удалён|active|deleted)"\)` по `app/orchestrator/validators`.
3. Нужно убедиться, что `check_marked_shot_removed` использует `ops` и не выдаёт error при явном delete-op помеченного шота (по решению Heil это не блокер), и что тест не ожидает error в таком сценарии.