"""Шаги загрузки файлов с вложениями: путь подставляет сервер, модель его не видит и не выдумывает.

Если к запросу приложены файлы (``attachments.AttachmentRef``), шаг загрузки от модели очищается от
source_path / data_base64 / filename, подбирается подходящее вложение (вид и формат подходят действию,
размер в пределах лимита, вложение не занято другим шагом этого плана), и в шаг ставится
``source_path`` вида ``attachments/<проект>/<uuid>_<имя>`` (внутри DATA_DIR). Нет подходящего -
понятное уточнение на русском. Без вложений действует прежнее уточнение «прикрепите файл»
(``planner_rules.upload_clarification``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.action_layer import planner_rules as rules
from app.services.action_layer.attachments import KINDS, AttachmentRef, max_bytes_for

MIB = 1024 * 1024

# Какие виды вложений годятся действию, и какая политика загрузки (context.UPLOAD_POLICIES) его проверяет.
ACTION_KINDS: dict[str, tuple[str, ...]] = {
    "montage.upload_image": ("image",),
    "montage.upload_ref": ("image",),
    "montage.upload_video": ("video",),
    "montage.upload_voice": ("audio",),
    "montage.upload_music": ("audio",),
    "node.upload_file": ("excel", "json", "text"),
    "node.upload_check_agent": ("text",),
}
ACTION_POLICY: dict[str, str] = {
    "montage.upload_image": "scene_image", "montage.upload_ref": "ref_image",
    "montage.upload_video": "scene_video", "montage.upload_voice": "voice",
    "montage.upload_music": "music", "node.upload_file": "node_file",
    "node.upload_check_agent": "check_agent",
}
_WANT_RU = {
    "montage.upload_image": "картинка PNG", "montage.upload_ref": "картинка (png, jpg, webp)",
    "montage.upload_video": "видео MP4", "montage.upload_voice": "аудио (mp3, wav, m4a, ogg)",
    "montage.upload_music": "аудио (mp3, wav, m4a, ogg)", "node.upload_file": "таблица xlsx, txt, md или json",
    "node.upload_check_agent": "текстовый файл txt или md",
}
_ASK_PARAM = {
    "frame_number": "В какой кадр загрузить файл «{name}»? Назовите номер кадра.",
    "node_key": "В какую ноду загрузить файл «{name}»? Назовите ключ ноды (например n_excel_gpt_fw_script).",
    "name": "Как назвать реф для файла «{name}»?",
}


@dataclass
class UploadFill:
    steps: list[dict]
    used: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)
    attachment: dict | None = None


def _kind_ru(kind: str) -> str:
    return KINDS[kind].title_ru if kind in KINDS else kind


def misfit(action_id: str, att: AttachmentRef) -> str:
    """Почему вложение не годится действию (по-русски), пустая строка - годится."""
    want = _WANT_RU.get(action_id, "файл")
    if att.kind not in ACTION_KINDS.get(action_id, ()):
        return f"это {_kind_ru(att.kind)}, а нужен файл: {want}"
    from app.services.action_layer.context import UPLOAD_POLICIES
    policy = UPLOAD_POLICIES[ACTION_POLICY[action_id]]
    if att.ext not in policy.extensions:
        return f"формат {att.ext} не принимается, нужен файл: {want}"
    cap = min(policy.max_bytes, max_bytes_for(att.kind))
    if att.size > cap:
        return f"файл больше лимита {cap // MIB} МБ для этого действия"
    return ""


def _wrong(action_id: str, title: str, atts: list[AttachmentRef], used: set[str]) -> str:
    free = [a for a in atts if a.id not in used]
    if not free:
        return (f"Для действия «{title}» нужен ещё один файл: прикреплённые уже использованы "
                "другими шагами. Прикрепите файл и повторите запрос.")
    first = free[0]
    return (f"Файл «{first.name}» не подходит для действия «{title}»: {misfit(action_id, first)}. "
            "Прикрепите другой файл.")


def pick(action_id: str, title: str, atts: list[AttachmentRef], used: set[str],
         request_text: str) -> tuple[AttachmentRef | None, str, list[str]]:
    """(вложение, вопрос, варианты). Вопрос задаётся, когда подходящего нет или их несколько."""
    fit = [a for a in atts if a.id not in used and not misfit(action_id, a)]
    if len(fit) == 1:
        return fit[0], "", []
    if not fit:
        return None, _wrong(action_id, title, atts, used), []
    low = (request_text or "").lower()
    named = [a for a in fit if a.name.lower() in low]
    if len(named) == 1:
        return named[0], "", []
    names = [a.name for a in fit][:rules.MAX_OPTIONS]
    return None, f"Какой из прикреплённых файлов загрузить для действия «{title}»?", names


def request_for(action_id: str, params: dict) -> dict:
    clean = {k: v for k, v in params.items() if k not in rules.FILE_PARAMS and k != "project_id"}
    return {"action_id": action_id, "params": clean, "formats": rules._FORMATS.get(action_id, "файл")}


def fill_steps(steps: list[dict], atts: list[AttachmentRef], titles: dict[str, str],
               request_text: str = "") -> UploadFill:
    """Подставляет source_path во все шаги загрузки плана; первая проблема - уточнение вместо плана."""
    used: set[str] = set()
    out: list[dict] = []
    for st in steps:
        aid = st["action_id"]
        if aid not in rules.UPLOAD_ACTIONS:
            out.append(st)
            continue
        title = titles.get(aid, aid)
        params = {k: v for k, v in st["params"].items() if k not in rules.FILE_PARAMS}
        att, question, options = pick(aid, title, atts, used, request_text)
        if att is None:
            return UploadFill([], list(used), question, options, request_for(aid, params))
        used.add(att.id)
        out.append({**st, "params": {**params, "source_path": att.rel_path}})
    return UploadFill(out, sorted(used))


def ask_missing(action_id: str, params: dict, att: AttachmentRef, required: list[str]) -> str:
    """Вопрос о первом обязательном параметре, которого нет (кадр, нода, имя рефа); пусто - всё есть."""
    for key in required:
        if key in rules.FILE_PARAMS or key == "project_id" or key in params:
            continue
        tpl = _ASK_PARAM.get(key)
        if tpl:
            return tpl.format(name=att.name)
    return ""
