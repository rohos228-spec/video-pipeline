"""Одноразовый сервер для визуальной проверки панелей «Журнал» и «Публикации» в Studio.

Поднимает настоящее веб-приложение (app.web.api.create_app) на ОДНОРАЗОВЫХ данных:
* DATA_DIR и SQLITE_PATH только внутри рабочей папки вне репозитория (по умолчанию
  %LOCALAPPDATA%\\video-pipeline-temp\\uicheck), порт 18999 (UICHECK_PORT);
* ключи провайдеров НЕ задаются, из окружения процесса убираются все переменные с KEY/TOKEN/SECRET/PASSWORD в имени;
* Telegram выключен, воркер выключен, автопубликация выключена, сеть не нужна, платных вызовов нет;
* файл .env не читается этим скриптом (в репозитории его нет; если появится, настройки приложения его подхватят,
  поэтому скрипт отказывается стартовать при наличии .env рядом с репозиторием).

Сид: проект 1 (собранный, с финальным видео и площадками в разных состояниях), проект 2 (пустой).
В журнал проекта 1 пишутся строки через публичные функции модулей: operator_audit, spend_guard (spend_ledger),
notification_log, publications_store, плюс события и инцидент наблюдения.

Запуск (из корня репозитория, python из .venv):
    .venv\\Scripts\\python.exe scripts\\ui_check_journal_publications_server.py
Когда сервер готов, он печатает строку ``READY <порт> <рабочая папка>``.
"""
import asyncio, os, shutil, sys, time, uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("UICHECK_PORT", "18999"))
WORK = Path(os.environ.get("UICHECK_WORK") or (Path(os.environ.get("LOCALAPPDATA") or Path.home())
                                              / "video-pipeline-temp" / "uicheck")).resolve()
MARKER = WORK / ".uicheck_marker"


def refuse(msg: str):
    raise SystemExit("ОТКАЗ ui_check: " + msg)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


if _inside(WORK, ROOT) or _inside(ROOT, WORK) or WORK.name != "uicheck":
    refuse(f"рабочая папка {WORK} должна называться uicheck и лежать вне репозитория {ROOT}")
if (ROOT / ".env").exists() or (ROOT / ".env.env").exists():
    refuse("рядом с репозиторием есть .env: приложение подхватило бы боевые ключи. Проверка не запускается.")

# Чистый старт: удаляем только то, что создано этим скриптом внутри uicheck.
if WORK.exists():
    if not MARKER.exists() and any(WORK.iterdir()):
        refuse(f"{WORK} не пуста и создана не этим скриптом (нет маркера).")
    for child in WORK.iterdir():
        if child == MARKER:
            continue
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
WORK.mkdir(parents=True, exist_ok=True)
MARKER.write_text("created by scripts/ui_check_journal_publications_server.py\n", encoding="utf-8")
(WORK / "data").mkdir()

for name in list(os.environ):
    up = name.upper()
    if any(w in up for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")) or up.startswith(("KIE_", "GPT_", "OPENAI_")):
        os.environ.pop(name, None)
os.environ.update(
    DATA_DIR=str(WORK / "data"), SQLITE_PATH=str(WORK / "data" / "state.db"), TELEGRAM_ENABLED="false",
    WEB_PORT=str(PORT), WEB_HOST="127.0.0.1", WORKER_ENABLED="false", SOCIAL_PUBLISH_ENABLED="false")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import uvicorn

from app.main import _init_db
from app.web.api import create_app


async def seed() -> dict:
    from app.db import SessionLocal
    from app.models import Artifact, ArtifactKind, Project, ProjectStatus
    from app.services import notification_log, operator_audit, publications_store as pstore, spend_guard
    from app.services.observation import ObservationEvent
    from app.services.action_layer.spec import ensure_builtin_actions
    import eval_seed

    ensure_builtin_actions()
    info = await eval_seed.seed_realistic(1, prompts_dir=WORK / "prompts")
    now = datetime.utcnow()

    final = WORK / "data" / "final_uicheck.mp4"
    final.write_bytes(b"uicheck-final-video-bytes" * 64)
    async with SessionLocal() as s:
        p = await s.get(Project, 1)
        p.topic = "Проверка журнала и публикаций"
        p.status = ProjectStatus.assembled
        s.add(Artifact(project_id=1, kind=ArtifactKind.final_video, uuid=str(uuid.uuid4()), path=str(final), meta={}))
        s.add(Project(id=2, topic="Пустой проект", slug="uicheck-empty"))
        await s.commit()
        art = (await s.execute(__import__("sqlalchemy").select(Artifact).where(Artifact.project_id == 1,
              Artifact.kind == ArtifactKind.final_video))).scalars().first()
        sha = pstore.sha256_of_file(final)
        art_uuid = art.uuid

        # публикации: подтверждена / ждёт проверки / сбой до клика / пропущена / не начиналась (нет строки)
        await pstore.record_unclaimed(s, project_id=1, platform="tiktok", sha256=sha, artifact_uuid=art_uuid,
                                      status="confirmed", clicked=True, post_url="https://www.tiktok.com/@uicheck/video/1")
        await pstore.record_unclaimed(s, project_id=1, platform="yt_shorts", sha256=sha, artifact_uuid=art_uuid,
                                      status="unconfirmed", clicked=True, error_code="confirm_timeout")
        await pstore.record_unclaimed(s, project_id=1, platform="ig_reels", sha256=sha, artifact_uuid=art_uuid,
                                      status="failed", clicked=False, error_code="not_logged_in")
        await pstore.mark_skipped(s, project_id=1, platform="vk_clips", sha256=sha, artifact_uuid=art_uuid,
                                  reason="publish_disabled")

        # деньги: резерв, уточнение, факт, событие; плюс пачка мелких трат, чтобы страниц стало больше одной
        await spend_guard.reserve(s, project_id=1, run_id="run-uicheck-1", estimate_usd=0.40, source="kie", provider="kie",
                                  note="картинка кадра 3", request_id="req-spend-1", ref="task-1")
        await spend_guard.settle(s, project_id=1, run_id="run-uicheck-1", reserved_usd=0.40, actual_usd=0.35,
                                 source="kie", provider="kie", basis="reported", request_id="req-spend-1", ref="task-1")
        await spend_guard.record_actual(s, project_id=1, usd=1.2, provider="gpt", source="native_call",
                                        run_id="run-uicheck-1", note="сценарий", request_id="req-spend-2")
        await spend_guard.record_event(s, project_id=1, provider="kie", source="kie", run_id="run-uicheck-1",
                                       note="write_failed: запись в учёт не удалась", request_id="req-spend-3")
        for i in range(60):
            await spend_guard.record_actual(s, project_id=1, usd=0.01 + i / 1000, provider="kie", source="native_call",
                                            run_id=f"run-bulk-{i // 10}", note=f"мелкая трата {i}", request_id=f"req-bulk-{i}")
        await s.commit()

        # события и инцидент наблюдения (чтобы проверить русские подписи вместо сырых видов)
        kinds = ["node_start", "node_end", "node_error", "retry_scheduled", "zz_unknown_kind_for_ui"]
        for i, k in enumerate(kinds):
            s.add(ObservationEvent(event_id=str(uuid.uuid4()), execution_id="exec-uicheck-1", project_id=1,
                                   workflow_run_id=1, node_key="script", db_scope="master", kind=k,
                                   payload={"note": f"проверка {k}"}, created_at=now - timedelta(minutes=30 - i)))
        await s.commit()

    # журнал оператора: 80 строк (200/403/409/500), так что вместе с остальным выходит больше 100 записей
    for i in range(80):
        code = 500 if i == 3 else 403 if i % 17 == 5 else 409 if i % 11 == 7 else 200
        operator_audit.insert_row(dict(
            ts=now - timedelta(minutes=i * 7 + 1), request_id=f"req-op-{i}", method="POST",
            path=f"/api/projects/1/publications/yt_shorts/confirm-manual", route_template="/projects/{project_id}/publications/{platform}/confirm-manual",
            project_id=1, actor="web_operator:abc123" if i % 2 else "local_web_operator", status_code=code,
            duration_ms=12 + i, body_sha256=None, outcome="ok" if code < 400 else "rejected"))
    for i in range(6):
        notification_log.log_notification(project_id=1, run_id="run-uicheck-1", kind="publish_result",
                                          text=f"Публикация на площадке {i}: нужна проверка человека", ok=i != 2,
                                          error=None if i != 2 else "Telegram выключен")
    return {"project_id": 1, "sha256": sha}


async def main():
    await _init_db()
    info = await seed()
    cfg = uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.2)
    print("READY", PORT, WORK, info["sha256"][:12], flush=True)
    await task

asyncio.run(main())
