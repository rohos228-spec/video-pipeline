"""Реестр правил группы нод «Каркас → площадка → шоты + QC» и карта полей.

Одно место, где у каждого правила есть ID, название, объяснение простыми
словами, что оно делает и где живёт (промт или функция кода). Дневник
кадра (``node_trace``) пишет ID правила в каждую правку, отчёт и
``docs/NODE_GROUP_RULES.md`` строятся отсюда. ``tests/test_node_rules.py``
сверяет реестр с кодом: новое правило без записи здесь роняет тест.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["чинит", "предупреждает", "останавливает", "решает"]

PROMPT_DIR = "templates/node_groups/script_frames_qc"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    plain: str
    action: Action
    nodes: tuple[str, ...]
    where: tuple[str, ...]


RULES: tuple[Rule, ...] = (
    Rule(
        "R-UUID",
        "Номер кадра вместо uuid",
        "GPT иногда пишет номер кадра или uuid с опечаткой. Программа "
        "подставляет правильный uuid; ops с чужими uuid выбрасывает.",
        "чинит",
        ("script", "action", "shots", "qc"),
        (
            "app.services.db_apply:remap_frame_number_uuids",
            "app.services.db_apply:repair_near_miss_frame_uuids",
        ),
    ),
    Rule(
        "R-BITS-REPAIR",
        "Биты: объект и якорь",
        "Если в бите слоган вместо объекта или якорь не из закадра — "
        "программа берёт объект и кусок закадра сама.",
        "чинит",
        ("script",),
        ("app.services.apply_ops_batches:repair_bits_ops",),
    ),
    Rule(
        "R-BITS-SHAPE",
        "Биты — список изменений",
        "Биты должны быть списком по числу изменений в закадре, не одной "
        "строкой. Если нет — предупреждение, данные пишутся как есть.",
        "предупреждает",
        ("script",),
        (
            "app.services.apply_ops_batches:bits_ops_reason",
            f"prompt:{PROMPT_DIR}/script_writer_ru.md",
        ),
    ),
    Rule(
        "R-CHECK-SCRIPT",
        "Проверка сценария",
        "Только старые канвасы, где осталась нода проверки после каркаса. "
        "«Не ок» — каркас запускается заново, «Ок» — дальше к площадке. "
        "В новую группу проверка не входит.",
        "останавливает",
        ("check_script",),
        ("app.services.node_groups:_script_frames_qc_group",),
    ),
    Rule(
        "R-ACTION-FORMAT",
        "Формат действия сцены",
        "Главное действие пишется как «1. место — шаг → шаг». Если GPT "
        "забыл номер или скобки — программа дописывает.",
        "чинит",
        ("action",),
        (
            "app.services.apply_ops_batches:auto_repair_action_chain_ops",
            "app.services.apply_ops_batches:repair_action_ops",
        ),
    ),
    Rule(
        "R-ACTION-PLAN",
        "Площадка ячейки",
        "Площадку (зоны, двери, проходы, люди по сторонам света) программа "
        "приводит к одному виду; чего нет — выведет позже на кадрах.",
        "чинит",
        ("action",),
        ("app.services.apply_ops_batches:normalize_action_plan_ops",),
    ),
    Rule(
        "R-ACTION-CHAIN",
        "Действие — цепочка, не слоган",
        "Главное действие — нумерованная цепочка шагов. Слоган вместо "
        "цепочки — предупреждение, пишется как есть.",
        "предупреждает",
        ("action",),
        (
            "app.services.apply_ops_batches:action_chain_ops_reason",
            f"prompt:{PROMPT_DIR}/main_action_from_bits_ru.md",
        ),
    ),
    Rule(
        "R-SHOT-PARENT",
        "Главный кадр места",
        "В одном месте первый кадр главный, остальные ссылаются на него "
        "(parent_id). GPT часто оставляет пусто — программа ставит.",
        "чинит",
        ("shots", "qc"),
        ("app.services.apply_ops_batches:repair_same_place_shot_parents",),
    ),
    Rule(
        "R-SHOT-GRAMMAR",
        "Грамматика кадров",
        "Закадр: обычный кадр 26–80 симв.; перебивка 10–30 или короткий "
        "эмо/реакция (можно пустой). Не вали весь VO в один кадр. "
        "План, линза, ракурс, движение — из таблицы; недостающие шаги "
        "и вход в новое место дописываются из главного действия.",
        "чинит",
        ("shots", "qc"),
        (
            "app.services.scene_shot_grammar:apply_grammar_to_ops",
            f"prompt:{PROMPT_DIR}/scenes_to_frames_ru.md",
        ),
    ),
    Rule(
        "R-SHOT-VO-FILL",
        "Пустой закадр кадра",
        "Перебивка может быть без закадра. Хвост текста ячейки код "
        "кладёт на покрывающий кадр (не копирует во все пустые).",
        "чинит",
        ("shots", "qc"),
        ("app.services.apply_ops_batches:repair_shot_vo_ops",),
    ),
    Rule(
        "R-SHOT-COVERAGE",
        "Кадры иллюстрируют закадр",
        "Кадр — картина к смыслу своего куска закадра, без повторов. "
        "Нарушение — предупреждение, пишется как есть.",
        "предупреждает",
        ("shots", "qc"),
        (
            "app.services.apply_ops_batches:shots_coverage_ops_reason",
            f"prompt:{PROMPT_DIR}/shots_qc_ru.md",
        ),
    ),
    Rule(
        "R-SCENE-PLAN",
        "Площадка на кадрах",
        "По плану площадки программа расставляет зоны, камеру и людей в "
        "каждом кадре и записывает, что вывела сама.",
        "чинит",
        ("shots", "qc"),
        ("app.services.apply_ops_batches:apply_scene_plan_ops",),
    ),
    Rule(
        "R-DOOR-STATE",
        "Закрытая дверь",
        "Дверь, которая по плану ещё закрыта, в тексте не может быть "
        "«открытой».",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_doors",),
    ),
    Rule(
        "R-DOOR-THRESHOLD",
        "Проход через дверь",
        "Сквозь закрытую дверь не пройти: программа вставляет кадр "
        "«открывает дверь» или открывает её в прошлом кадре.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_doors",),
    ),
    Rule(
        "R-ALREADY-INSIDE",
        "Нельзя «уже внутри»",
        "Герой не может сразу оказаться внутри — показывается вход через "
        "дверь.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_entry_shown",),
    ),
    Rule(
        "R-WALK-PROGRESS",
        "Кто шёл — продвинулся",
        "Если человек шёл, в следующем кадре он дальше по пути, а не на "
        "старом месте.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_path",),
    ),
    Rule(
        "R-PLAN-STEP",
        "Смена крупности",
        "Канон: ДАЛЬНИЙ|ОБЩИЙ|СРЕДНИЙ|СРЕДНЕ-КРУПНЫЙ|КРУПНЫЙ|ДЕТАЛЬ. "
        "Не два одинаковых подряд; запрет ОБЩИЙ↔СРЕДНИЙ и СРЕДНИЙ↔СРЕДНЕ-КРУПНЫЙ; "
        "не прыжок через 3+ ступени.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.vary_shot_sizes",),
    ),
    Rule(
        "R-30DEG",
        "Правило 30°",
        "При смене плана или том же объекте камера поворачивается минимум "
        "на 30°: другой ракурс или другая сторона.",
        "чинит",
        ("shots", "qc"),
        (
            "app.services.scene_plan:_Sim.vary_repeated_camera",
            "app.services.scene_plan:next_named_angle_30",
        ),
    ),
    Rule(
        "R-SCREEN-DIRECTION",
        "Направление на экране",
        "Кто бежал влево по экрану, бежит влево и в следующем кадре.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_screen_direction",),
    ),
    Rule(
        "R-AXIS-180",
        "Ось 180°",
        "Двое держат свои стороны экрана: камера не перескакивает линию "
        "между ними.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.check_axis",),
    ),
    Rule(
        "R-LAYOUT",
        "Раскладка кадра",
        "Программа пишет раскладку: где что стоит, кто где, откуда камера "
        "и чем кадр отличается от прошлого. По ней пишутся промты картинок.",
        "чинит",
        ("shots", "qc"),
        ("app.services.scene_plan:_Sim.compile_layouts",),
    ),
    Rule(
        "R-FREEZE-PRECISE",
        "СТАРТ/КОНЕЦ у смены состояния / необратимости",
        "Две картинки (старт/конец) — если меняется предмет/дверь, действие "
        "необратимо, или GPT дал разные старт·конец. Непрерывный процесс "
        "(идёт, говорит…) — одна картинка. Список глаголов — запасной сигнал.",
        "решает",
        ("shots", "qc"),
        (
            "app.services.scene_plan:freeze_decision",
            f"prompt:{PROMPT_DIR}/scenes_to_frames_ru.md",
        ),
    ),
    Rule(
        "R-FREEZE-END-STILL",
        "Конечная картинка и видео",
        "После группы: у кадра СТАРТ/КОНЕЦ картинка конца ставится в "
        "очередь и идёт в видео последним кадром; второго клипа нет.",
        "решает",
        ("после группы",),
        (
            "app.services.freeze_stills:seed_end_still_prompt",
            "app.services.freeze_stills:video_end_still",
        ),
    ),
    Rule(
        "R-ANALYTICS-COLLAPSE",
        "Аналитика: разный закадр — разный смысл",
        "Вне этой группы: если у разного закадра одинаковые смысл или "
        "место — пачка останавливается.",
        "останавливает",
        ("другие ноды",),
        ("app.services.apply_ops_batches:analytics_ops_collapsed_reason",),
    ),
    Rule(
        "R-PROMPTS-SHAPE",
        "Промты картинок",
        "Вне этой группы: нода промтов обязана вернуть промт_картинки, а "
        "не кадры. Иначе пачка останавливается.",
        "останавливает",
        ("другие ноды",),
        ("app.services.apply_ops_batches:prompts_ops_reason",),
    ),
)

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}

# Проход кода после ответа GPT → правило (см. run_code_passes).
PASS_RULES: dict[str, str] = {
    "uuid": "R-UUID",
    "bits_repair": "R-BITS-REPAIR",
    "bits_check": "R-BITS-SHAPE",
    "analytics_check": "R-ANALYTICS-COLLAPSE",
    "action_format": "R-ACTION-FORMAT",
    "action_plan": "R-ACTION-PLAN",
    "action_repair": "R-ACTION-FORMAT",
    "action_check": "R-ACTION-CHAIN",
    "shot_parent": "R-SHOT-PARENT",
    "shot_chain": "R-SHOT-GRAMMAR",
    "shot_grammar": "R-SHOT-GRAMMAR",
    "shot_parent_again": "R-SHOT-PARENT",
    "shot_vo_fill": "R-SHOT-VO-FILL",
    "scene_plan": "R-SCENE-PLAN",
    "shots_check": "R-SHOT-COVERAGE",
    "prompts_check": "R-PROMPTS-SHAPE",
}

# «вид» проблемы площадки (scene_plan._issue) → правило.
PLAN_ISSUE_RULES: dict[str, str] = {
    "дверь": "R-DOOR-STATE",
    "порог": "R-DOOR-THRESHOLD",
    "уже_внутри": "R-ALREADY-INSIDE",
    "на_месте": "R-WALK-PROGRESS",
    "план": "R-PLAN-STEP",
    "ракурс": "R-30DEG",
    "направление": "R-SCREEN-DIRECTION",
    "ось_180": "R-AXIS-180",
}

# Поле, которое поменял проход площадки → правило (если нет заметки площадки).
PLAN_FIELD_RULES: dict[str, str] = {
    "раскладка": "R-LAYOUT",
    "точность": "R-FREEZE-PRECISE",
    "старт": "R-FREEZE-PRECISE",
    "конец": "R-FREEZE-PRECISE",
    "ракурс": "R-30DEG",
    "камера": "R-30DEG",
    "план": "R-PLAN-STEP",
    "линза_мм": "R-PLAN-STEP",
}


@dataclass(frozen=True)
class NodeFlow:
    key: str
    label: str
    prompt: str
    reads: tuple[str, ...]
    gpt_writes: tuple[str, ...]
    code_rules: tuple[str, ...]
    code_writes: tuple[str, ...] = ()
    on_fail: str = ""


NODE_FLOW: tuple[NodeFlow, ...] = (
    NodeFlow(
        "script",
        "Каркас",
        "prompts/scene_design/scene_skeleton_agent.md",
        ("закадр",),
        ("сцены: закадр, место, персонажи, предметы, речь, сцена", "база_персонажей", "предметы"),
        ("R-UUID", "R-BITS-REPAIR", "R-BITS-SHAPE"),
        ("биты",),
    ),
    NodeFlow(
        "action",
        "Площадка",
        "prompts/scene_design/action.md",
        ("закадр",),
        ("площадка: зоны, предметы, проходы, люди", "меняет"),
        ("R-UUID", "R-ACTION-FORMAT", "R-ACTION-PLAN", "R-ACTION-CHAIN"),
        ("главное_действие", "площадка"),
    ),
    NodeFlow(
        "shots",
        "Шоты",
        "scenes_to_frames_ru.md",
        ("главное_действие", "площадка", "закадр"),
        ("кадры: действие, объект, закадр, меняет, люди, камера, точность",),
        (
            "R-UUID",
            "R-SHOT-PARENT",
            "R-SHOT-GRAMMAR",
            "R-SHOT-VO-FILL",
            "R-SCENE-PLAN",
            "R-DOOR-STATE",
            "R-DOOR-THRESHOLD",
            "R-ALREADY-INSIDE",
            "R-WALK-PROGRESS",
            "R-PLAN-STEP",
            "R-30DEG",
            "R-SCREEN-DIRECTION",
            "R-AXIS-180",
            "R-FREEZE-PRECISE",
            "R-LAYOUT",
            "R-SHOT-COVERAGE",
        ),
        (
            "кадры: parent_id, закадр, план, линза_мм, ракурс, движение",
            "кадры: раскладка, точность, старт, конец",
            "площадка: выведено_кодом, исправлено_кодом",
        ),
    ),
    NodeFlow(
        "qc",
        "GPT: QC кадров",
        "shots_qc_ru.md",
        ("кадры", "площадка", "закадр"),
        ("кадры (только нарушители)",),
        (
            "R-UUID",
            "R-SHOT-PARENT",
            "R-SHOT-GRAMMAR",
            "R-SHOT-VO-FILL",
            "R-SCENE-PLAN",
            "R-30DEG",
            "R-FREEZE-PRECISE",
            "R-LAYOUT",
            "R-SHOT-COVERAGE",
        ),
        (
            "кадры: те же поля, что после «Шоты»",
        ),
    ),
    NodeFlow(
        "report",
        "Отчёт: кадры",
        "",
        ("всё выше", "дневник кадров"),
        (),
        (),
        ("shots-report.html",),
    ),
)

# Кто по очереди пишет одно поле кадра. Последний — побеждает.
FIELD_WRITERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ракурс", ("GPT «Шоты»", "R-SHOT-GRAMMAR", "R-30DEG", "GPT «QC»", "R-SHOT-GRAMMAR", "R-30DEG")),
    ("план", ("GPT «Шоты»", "R-SHOT-GRAMMAR", "R-PLAN-STEP", "GPT «QC»", "R-SHOT-GRAMMAR", "R-PLAN-STEP")),
    ("закадр кадра", ("GPT «Шоты»", "R-SHOT-GRAMMAR", "R-SHOT-VO-FILL", "GPT «QC»", "R-SHOT-GRAMMAR")),
    ("parent_id", ("GPT «Шоты»", "R-SHOT-PARENT", "GPT «QC»", "R-SHOT-PARENT")),
    ("точность / старт / конец", ("GPT «Шоты»", "R-FREEZE-PRECISE", "GPT «QC»", "R-FREEZE-PRECISE")),
    ("раскладка", ("R-LAYOUT (после каждой GPT-ноды кадров)",)),
    ("кадры (сколько)", ("GPT «Шоты»", "R-SHOT-GRAMMAR", "R-DOOR-THRESHOLD", "GPT «QC»")),
    ("главное_действие", ("main_action_from_bits_ru (вне цепочки)", "R-ACTION-FORMAT")),
    ("площадка", ("GPT «Площадка»", "R-ACTION-PLAN", "R-SCENE-PLAN")),
)


def node_local_key(node_key: str) -> str:
    """``n_excel_gpt_fw_shots`` → ``shots``."""
    nk = str(node_key or "")
    for flow in NODE_FLOW:
        if nk.endswith(f"_fw_{flow.key}") or nk == flow.key:
            return flow.key
    return nk


def node_label(node_key: str) -> str:
    local = node_local_key(node_key)
    for flow in NODE_FLOW:
        if flow.key == local:
            return flow.label
    return str(node_key or "")


def rule_title(rule_id: str) -> str:
    rule = RULES_BY_ID.get(rule_id)
    return rule.title if rule else rule_id


def flow_mermaid() -> str:
    lines = ["flowchart LR"]
    for flow in NODE_FLOW:
        lines.append(f'    {flow.key}["{flow.label}"]')
    order = [f.key for f in NODE_FLOW]
    for a, b in zip(order, order[1:], strict=False):
        edge = "|Ок|" if a == "check_script" else ""
        lines.append(f"    {a} -->{edge} {b}")
    for flow in NODE_FLOW:
        if flow.on_fail:
            lines.append(f"    {flow.key} -.->|Не ок| {flow.on_fail}")
    return "\n".join(lines)


def rules_markdown() -> str:
    """``docs/NODE_GROUP_RULES.md`` — генерится ``scripts/node_rules_doc.py``."""
    out = [
        "# Правила группы нод «Каркас → площадка → шоты + QC»",
        "",
        "Файл собран из `app/services/node_rules.py` командой "
        "`python3 scripts/node_rules_doc.py`. Руками не править.",
        "",
        "## Схема группы",
        "",
        "```mermaid",
        flow_mermaid(),
        "```",
        "",
        "## Что читает и пишет каждая нода",
        "",
        "| Нода | Читает | Пишет GPT | Потом программа (правила) | Пишет программа |",
        "|---|---|---|---|---|",
    ]
    for flow in NODE_FLOW:
        prompt = f"<br>промт `{flow.prompt}`" if flow.prompt else ""
        fail = f"<br>«Не ок» → {node_label(flow.on_fail)}" if flow.on_fail else ""
        out.append(
            f"| {flow.label}{prompt}{fail} | {', '.join(flow.reads) or '—'} | "
            f"{'; '.join(flow.gpt_writes) or '—'} | "
            f"{', '.join(flow.code_rules) or '—'} | "
            f"{'; '.join(flow.code_writes) or '—'} |"
        )
    out += [
        "",
        "## Кто по очереди пишет одно поле",
        "",
        "Последний в строке побеждает. Отсюда видно, где GPT и программа "
        "перетирают друг друга.",
        "",
        "| Поле | Кто пишет по порядку |",
        "|---|---|",
    ]
    for field, writers in FIELD_WRITERS:
        out.append(f"| {field} | {' → '.join(writers)} |")
    out += [
        "",
        "## Правила",
        "",
        "| ID | Правило | Простыми словами | Что делает | Ноды | Где живёт |",
        "|---|---|---|---|---|---|",
    ]
    for r in RULES:
        where = "<br>".join(f"`{w}`" for w in r.where)
        out.append(
            f"| {r.id} | {r.title} | {r.plain} | {r.action} | "
            f"{', '.join(r.nodes)} | {where} |"
        )
    out += [
        "",
        "## Дневник кадра",
        "",
        "Каждый вызов GPT в этой группе сохраняется в "
        "`data/videos/<проект>/node_trace/<нода>/<время>/`: вход, ответ GPT, "
        "ops до и после правок программы, `diary.jsonl` — что, где и по "
        "какому правилу поменяла программа. Отчёт группы показывает это "
        "по каждому кадру. Повтор без GPT: "
        "`python3 scripts/replay_node.py <папка прогона>`.",
        "",
    ]
    return "\n".join(out)
