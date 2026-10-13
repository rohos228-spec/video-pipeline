"""Русская фраза для строки «действие оператора» в ленте: по методу и шаблону маршрута.

Лента должна говорить, ЧТО сделал человек («Оператор поставил на паузу: проект»), а не «изменил данные».
Фраза собирается из двух словарей: последнее слово пути (действие) и ближайшее слово-ресурс. Если на маршруте
слова нет в словарях, функция вернёт None, а тест ``tests/test_operator_phrases.py`` уронит сборку: новый
маршрут нельзя добавить без фразы.
"""
from __future__ import annotations

import re

# Последний сегмент пути - действие.
ACTIONS: dict[str, str] = {
    "activate": "включил", "apply": "применил", "apply-ops": "применил операции", "approve": "подтвердил",
    "ask": "задал вопрос", "ask-stream": "задал вопрос", "analyze-style": "проанализировал стиль",
    "audio-align": "выровнял озвучку по кадрам", "build-agent": "собрал агента", "bulk-enqueue": "поставил в очередь пачкой",
    "cancel": "отменил", "categorize-styles": "разложил стили по категориям", "clear": "очистил",
    "close": "закрыл", "commit-uncertainty": "зафиксировал неопределённый исход", "compile": "собрал",
    "compose": "составил", "confirm-delete-projects": "подтвердил удаление проектов",
    "confirm-git-push": "подтвердил отправку в git", "confirm-manual": "подтвердил вручную",
    "confirm-remove": "подтвердил удаление", "continue": "продолжил", "control": "дал управляющее решение",
    "create": "создал", "decision": "принял решение", "delete-image": "удалил картинку",
    "delete-ref": "удалил референс", "delete-video": "удалил видео", "disable-auto-mode": "выключил автоматический режим",
    "duplicate": "сделал копию", "enable": "включил", "enhance-prompt": "улучшил промт", "enqueue": "поставил в очередь",
    "ensure-run": "подготовил запуск", "estimate": "посчитал оценку стоимости", "export-xlsx": "выгрузил таблицу",
    "finish": "завершил", "from-macro": "создал план из макроса", "from-selection": "создал план из выделения",
    "from-text": "создал план из текста", "from-workflow": "создал план из workflow", "generate": "сгенерировал",
    "insert": "вставил", "launch": "запустил", "link-ref": "привязал референс", "load": "загрузил", "login": "вошёл в Studio",
    "logout": "вышел из Studio", "mark-failed": "пометил неудачным", "mark-montage-ready": "пометил готовым к монтажу",
    "merge": "склеил", "montage": "запустил монтаж", "move-image": "переместил картинку", "normalize": "нормализовал",
    "parse-topics": "разобрал темы", "pause": "поставил на паузу", "pin": "закрепил", "preview": "посмотрел предпросмотр",
    "pull-to-main": "подтянул изменения в main", "rebuild": "пересобрал", "recover-outsee": "запустил восстановление Outsee",
    "register": "зарегистрировал", "reject": "отклонил", "release": "снял", "release-unknown": "снял блокировку неизвестного исхода",
    "reload": "перечитал", "remap-keys": "переназначил ключи", "remount-video": "пересобрал видео", "rename": "переименовал",
    "replace": "заменил", "reset": "сбросил", "restore": "восстановил", "restore-original-voiceover": "вернул исходную озвучку",
    "resume": "возобновил", "resume-poll": "возобновил опрос задачи", "retry": "повторил", "review": "рассмотрел",
    "run": "запустил", "save": "сохранил", "save-and-activate": "сохранил и включил", "save-template": "сохранил шаблон",
    "save-to-project": "сохранил в проект", "save-voiceover": "сохранил озвучку", "scene-action-generate": "сгенерировал действие сцены",
    "scene-generate-with-images": "сгенерировал сцену с картинками", "scene-improve": "улучшил сцену",
    "scene-variants": "запросил варианты сцены", "split": "разделил", "start": "запустил", "stop": "остановил",
    "swap-shots": "поменял шоты местами", "swap-slots": "поменял слоты местами", "sync": "синхронизировал",
    "to-outputs": "отправил в выходы", "toggle": "переключил", "upload": "загрузил файл", "upload-image": "загрузил картинку",
    "upload-music": "загрузил музыку", "upload-video": "загрузил видео", "upload-voice": "загрузил озвучку",
    "validate": "проверил", "kill-switch": "переключил рубильник", "verify": "проверил", "child": "создал дочерний проект",
}

# Ближайший к концу сегмент-ресурс - «что именно».
RESOURCES: dict[str, str] = {
    "action-layer": "слой действий", "actions": "действия", "agent-overrides": "переопределения агентов",
    "animation-prompts": "промты анимации", "assets": "ресурсы проекта", "attachments": "вложения", "auth": "вход",
    "auto": "автоматический режим", "block-activity": "активность блока", "blocks": "блоки", "bug-reports": "отчёты об ошибках",
    "canvas": "холст", "canvas-edges": "связь на холсте", "cell": "ячейка", "change-control": "контроль изменений",
    "chat": "чат оркестратора", "check-agent": "агент проверки", "configs": "конфигурации", "coverage": "покрытие кадров",
    "custom-styles": "свои стили", "db": "база данных", "decisions": "решения надзора", "default": "значение по умолчанию",
    "edges": "связи", "entities": "сущности", "excel-gpt": "нода Excel-GPT", "excel-hero": "герой из таблицы",
    "files": "файлы", "fleet": "флот", "folders": "папки", "frames": "кадры", "gen-assistant": "ассистент генерации",
    "gen-queue": "очередь генерации", "generation-config-presets": "пресеты генерации", "gpt-operator": "нода GPT-оператор",
    "gpt-text": "текст GPT", "gpt-verdict": "вердикт GPT", "gpt-workspace": "рабочее место GPT", "groups": "группы нод",
    "harness": "проверочный стенд", "hero": "герой", "history": "история", "hitl": "ручное подтверждение", "images": "картинки",
    "items": "предметы", "jobs": "фоновые задачи", "kie-create": "создание через Kie", "kill-switch": "рубильник",
    "knowledge": "база знаний", "library": "библиотека", "local": "локальные настройки", "macros": "макросы",
    "mass-lanes": "массовые линии", "media-recovery": "восстановление медиа", "meta-agent": "мета-агент",
    "montage-board": "монтажная доска", "node-groups": "группы нод", "nodes": "ноды", "observations": "наблюдение",
    "orchestrator": "оркестратор", "outsee": "Outsee", "outsee-create": "создание через Outsee", "parents": "родительские проекты",
    "plans": "планы слоя действий", "powershell": "консоль PowerShell", "presets": "пресеты", "profiles": "профили",
    "projects": "проект", "prompt-bundles": "наборы промтов", "prompt-config": "настройки промтов",
    "prompt-files": "файлы промтов", "prompt-studio": "студия промтов", "prompts": "промты", "proposals": "предложения изменений",
    "publications": "публикации", "queue": "очередь правок", "refs": "референсы", "reserves": "незакрытый резерв расхода",
    "runs": "запуски",
    "runtime-streams": "потоки выполнения", "scenes": "сцены", "sessions": "сессии", "settings": "настройки",
    "sheet-cell": "ячейка таблицы", "shot-field": "поле шота", "shot-menu": "меню съёмки",
    "sidebar-layout": "раскладка боковой панели", "step-presets": "пресеты шагов", "step-template": "шаблон шага",
    "steps": "шаги", "storage": "хранилище", "stream": "поток", "style-cover": "обложка стиля", "templates": "шаблоны",
    "text-llm": "текстовая модель", "texts": "тексты", "verdict-templates": "шаблоны вердиктов", "videos": "видео",
    "voiceover": "озвучка", "workflows": "workflow", "xlsx": "таблица xlsx",
}

_METHOD_VERB = {"POST": "создал или запустил", "PUT": "заменил", "PATCH": "изменил", "DELETE": "удалил"}
_METHOD_VERB_RES = {"POST": "добавил", "PUT": "заменил", "PATCH": "изменил", "DELETE": "удалил"}


def _words(template: str) -> list[str]:
    parts = [p for p in re.split(r"/+", template or "") if p and not p.startswith("{") and p != "api"]
    return parts


def operator_phrase(method: str, template: str) -> str | None:
    """Фраза по методу и шаблону маршрута; None, если в пути нет слов из словарей."""
    method = (method or "").upper()
    words = _words(template)
    if not words:
        return None
    last = words[-1]
    res = next((w for w in reversed(words[:-1]) if w in RESOURCES), None)
    if last in ACTIONS:
        verb = ACTIONS[last]
        if last == "child":
            return f"Оператор {verb}"
        return f"Оператор {verb}" + (f": {RESOURCES[res]}" if res and res != "projects" else ": проект" if res == "projects" else "")
    if last in RESOURCES:
        verb = _METHOD_VERB_RES.get(method)
        return f"Оператор {verb}: {RESOURCES[last]}" if verb else None
    return None
