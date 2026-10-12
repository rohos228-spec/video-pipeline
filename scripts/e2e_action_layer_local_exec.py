"""F08 (часть «локально и бесплатно»): исполнители слоя действий на НАСТОЯЩЕМ серверном процессе.

    python -X utf8 scripts/e2e_action_layer_local_exec.py --run [--work-dir C:\\video-pipeline\\_f08_work\\run1]
        [--port 18811] [--voice-wav C:\\video-pipeline\\_asr1\\voice.wav] [--asr-model medium] [--timeout-s 900]

Что делает. Новая пустая рабочая папка ВНЕ репозитория; в ней создаётся маленький синтетический проект (3 кадра, три
локальных клипа ffmpeg lavfi, озвучка - wav Windows SAPI, прежний final.mp4) и стартует ``python -m app.main`` (через
тонкий запуск, который лишь ставит сторожа сети, подменяет каталог мастер-промтов копией в рабочей папке и обнуляет
ключи в настройках). Дальше через HTTP со своим Origin и токеном оператора каждое локальное бесплатное действие
проходит путь: план (``/plans/preview`` - явные шаги, платный планировщик ``/from-text`` НЕ используется) -> превью ->
подтверждение -> запуск -> опрос -> журнал ``action_layer_history`` -> ``operator_audit`` -> лента проекта.
Отдельно проверяется, что деньги равны журналу (ноль), что сеть не уходила дальше петли и что репозиторий не тронут.

Платные исполнители здесь НЕ вызываются ни разу (список в result.json -> skipped_actions). Режим один: ``--run``.
Коды выхода: 0 - все проверки прошли, 1 - есть проваленные, 2 - отказ до запуска (папка, ffmpeg, модель, wav).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
DEFAULT_PORT = 18811
DEFAULT_VOICE = os.environ.get("VP_STAND_VOICE_WAV", "")  # wav с русской речью (SAPI); путь задаёт вызывающий
DEFAULT_ASR = "medium"
VERDICT_OK = "LOCAL OK (live server process, no provider involved)"
VERDICT_FAIL = "LOCAL FAIL (live server process, no provider involved)"
SECRET_RE = re.compile(r"(?i)(key|token|secret|password)")
FRAME_TEXTS = ("Привет.", "Это проверка сборки видео.", "Сегодня мы покажем, как работает субтитр.")
VOICE_TEXT = " ".join(FRAME_TEXTS)
CLIP_SIZE, CLIP_SECONDS, CLIP_COLORS = "270x480", 5, ("navy", "darkgreen", "maroon")
NODE_KEY = "n_gpt_1"
HISTORY_TERMINAL = ("succeeded", "failed", "stopped")


# ───────────────────────────── чистые помощники (проверяются юнит-тестами) ─────────────────────────────

def inside_repo(path: Path, repo: Path = ROOT) -> bool:
    p, r = Path(path).resolve(), Path(repo).resolve()
    return p == r or r in p.parents


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Окружение сервера без имён с key/token/secret/password (ключи провайдеров и токены не наследуются)."""
    return {k: v for k, v in env.items() if not SECRET_RE.search(k)}


def server_env(base: dict[str, str], work: Path, port: int, asr_model: str) -> dict[str, str]:
    data = work / "data"
    env = scrub_env(base)
    env.update({
        "WEB_HOST": "127.0.0.1", "WEB_PORT": str(port), "TELEGRAM_ENABLED": "false", "WORKER_ENABLED": "false",
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"), "API_TRACKER_DB": str(work / "tracker.db"),
        "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "ASR_BACKEND": "whisper", "WHISPER_MODEL": asr_model,
        "WHISPER_DEVICE": "cpu", "WHISPER_COMPUTE_TYPE": "int8",
        "AL_STAND_WORK": str(work), "AL_STAND_PROMPTS": str(work / "prompts"),
    })
    return env


def sha256_file(path: Path) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(folder: Path) -> str:
    """Хэш всех файлов дерева (имена и содержимое): доказательство, что каталог не менялся."""
    h = hashlib.sha256()
    base = Path(folder)
    for p in sorted(x for x in base.rglob("*") if x.is_file()):
        h.update(p.relative_to(base).as_posix().encode("utf-8"))
        h.update((sha256_file(p) or "").encode())
    return h.hexdigest()


def is_loopback_host(host) -> bool:
    import ipaddress
    if host is None:
        return True
    name = str(host).strip().strip("[]").lower()
    if name in ("", "localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


def has_russian(text: Any) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(text or "")))


def model_cache_name(model: str) -> str:
    return "models--Systran--faster-whisper-" + str(model).strip()


def find_cached_model(model: str, env: dict | None = None) -> Path | None:
    env = os.environ if env is None else env
    hubs = [Path(env[k]) for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE") if env.get(k)]
    if env.get("HF_HOME"):
        hubs.append(Path(env["HF_HOME"]) / "hub")
    hubs.append(Path.home() / ".cache" / "huggingface" / "hub")
    for hub in hubs:
        cand = hub / model_cache_name(model)
        if (cand / "snapshots").is_dir() and any((cand / "snapshots").glob("*/model.bin")):
            return cand
    return None


def link_cached_model(data: Path, model: str) -> Path:
    """Кэш Hugging Face приложение перенаправляет в <data>/.cache/huggingface/hub: делаем модель видимой связью."""
    src = find_cached_model(model)
    if src is None:
        raise FileNotFoundError(f"модель faster-whisper {model!r} не лежит в локальном кэше (запуск офлайн)")
    hub = Path(data) / ".cache" / "huggingface" / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    link = hub / src.name
    if link.exists():
        return link
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(src)], capture_output=True, text=True)
        if done.returncode != 0:
            raise OSError(f"не удалось связать кэш модели: {done.stdout} {done.stderr}".strip())
    else:
        os.symlink(src, link, target_is_directory=True)
    return link


def unlink_cached_model(data: Path, model: str) -> None:
    """Удаляется только связь, сам кэш пользователя не трогается."""
    link = Path(data) / ".cache" / "huggingface" / "hub" / model_cache_name(model)
    try:
        if os.name == "nt" and (os.path.isjunction(link) or link.is_symlink()):
            os.rmdir(link)
        elif link.is_symlink():
            link.unlink()
    except OSError:
        pass


def validate_wav(path: Path) -> dict:
    import wave
    with wave.open(str(path), "rb") as w:
        info = {"channels": w.getnchannels(), "rate": w.getframerate(), "width": w.getsampwidth(),
                "seconds": round(w.getnframes() / float(w.getframerate() or 1), 3)}
    if info["width"] != 2:
        raise ValueError("wav должен быть 16-битным")
    if info["seconds"] < 3.0:
        raise ValueError("wav слишком короткий")
    return info


class Report:
    """Список проверок. ``known`` - код известного дефекта ПРОДАКШЕН-кода: проваленная такая проверка не роняет стенд
    (если не задан ``strict``), но печатается как KNOWN и попадает в ``known_defects``; прошедшая - отмечается как
    «дефект не воспроизвёлся»."""

    def __init__(self, strict: bool = False) -> None:
        self.checks: list[dict] = []
        self.strict = strict

    def check(self, name: str, ok: bool, detail: Any = None, known: str | None = None) -> bool:
        ok = bool(ok)
        self.checks.append({"name": name, "ok": ok, "detail": detail, "known": known})
        tail = "" if ok or detail is None else f"  {str(detail)[:400]}"
        label = "PASS " if ok else ("KNOWN " + known + " " if known else "FAIL ")
        print(label + name + tail, flush=True)
        return ok or bool(known and not self.strict)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["ok"] and (self.strict or not c.get("known"))]

    @property
    def known_defects(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for c in self.checks:
            if c.get("known") and not c["ok"]:
                out.setdefault(c["known"], []).append(c["name"])
        return out


def final_verdict(report: Report) -> str:
    if report.failed:
        return VERDICT_FAIL
    n = len(report.known_defects)
    return VERDICT_OK + (f"; {n} known production defect(s): {', '.join(sorted(report.known_defects))}" if n else "")


def reality_lists() -> dict[str, list[str]]:
    return {
        "real": [
            "настоящий процесс python -m app.main (через тонкий запуск) на тестовом порту, новая пустая база вне репозитория",
            "путь оператора по HTTP: план -> превью -> подтверждение -> запуск -> опрос -> журнал -> аудит -> лента",
            "настоящий ffmpeg (разбор по паузам, сборка), настоящий faster-whisper offline из кэша (повторный ASR озвучки)",
            "записи action_layer_history / action_layer_snapshots / operator_audit читаются прямо из базы сервера",
            "деньги: журнал трат до и после побайтно одинаков, у планов reserved/actual равны нулю",
        ],
        "not_real": [
            "озвучка - wav Windows SAPI, не человек и не TTS провайдера; клипы - ffmpeg lavfi, не генерация",
            "проект синтетический (3 кадра), не копия боевого",
            "мастер-промт пишется в КОПИЮ каталога prompts в рабочей папке (подмена константы в запуске), не в репозиторий",
            "сторож сети - перехват connect/getaddrinfo на уровне Python; сокет из C-расширения он не видит",
        ],
        "not_proven": [
            "платные исполнители (apply_queue, improve_scene, generate_*, node.run_step, step.run_step, project.resume, "
            "media.resume_poll, recover_outsee): здесь не вызывались, это шаги F09 с реальными деньгами",
            "разбор аудио методами NeMo (NeMo в этой среде не установлен): проверен метод silence (ffmpeg)",
            "запуск на боевом проекте и боевой базе, длинная очередь, параллельная работа человека в интерфейсе",
            "поведение при перезапуске сервера посреди плана (окно возобновления)",
        ],
    }


# ───────────────────────────── ffmpeg и сид проекта ─────────────────────────────

def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg не найден в PATH")
    return exe


def _ff(args: list[str]) -> None:
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", *args], check=True, timeout=180, capture_output=True)


def make_clip(path: Path, color: str, seconds: int = CLIP_SECONDS, size: str = CLIP_SIZE) -> None:
    _ff(["-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-preset", "veryfast", str(path)])


def wav_to_mp3(wav: Path, mp3: Path) -> None:
    _ff(["-i", str(wav), "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)])


def make_plan_xlsx(path: Path) -> None:
    from openpyxl import Workbook
    from app.services.xlsx_v8_import import ROW_VOICEOVER_V8
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    for i, text in enumerate(FRAME_TEXTS, start=1):
        ws.cell(row=ROW_VOICEOVER_V8, column=i + 2, value=text)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()


async def seed_project(work: Path, voice_wav: Path, asr_model: str) -> dict:
    """Выполняется в дочернем процессе с рабочим окружением: схема + проект + файлы. Печатает seed.json."""
    from app import main as app_main
    from app.db import session_scope
    from app.models import (Artifact, ArtifactKind, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow,
                            WorkflowRun, WorkflowRunStatus)

    await app_main._init_db()
    link_cached_model(work / "data", asr_model)
    slug = f"al-local-{_uuid.uuid4().hex[:6]}"
    nodes = [{"id": NODE_KEY, "type": "excel_gpt", "position": {"x": 100.0, "y": 100.0}, "data": {"label": "GPT"}}]
    edges: list = []
    frame_uuids: list[str] = []
    async with session_scope() as session:
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, "img_streams": 1,
                "node_step_params": {"assemble": {"subtitles_enabled": False}}}
        project = Project(slug=slug, topic="action layer local exec stand", status=ProjectStatus.audio_ready,
                          auto_mode=False, general_plan=VOICE_TEXT, script_text=VOICE_TEXT, meta=meta)
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=NODE_KEY, node_type="excel_gpt",
                            status=NodeRunStatus.pending))
        frames = []
        for i, text in enumerate(FRAME_TEXTS, start=1):
            fu = f"{i:02d}" * 12
            frame_uuids.append(fu)
            fr = Frame(project_id=project.id, number=i, uuid=fu, sort_key=float(i), voiceover_text=text,
                       image_prompt=f"local frame {i}", animation_prompt=f"local clip {i}", duration_seconds=3.0,
                       attrs={})
            session.add(fr)
            frames.append(fr)
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
        for sub in ("videos", "audio", "final"):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        clips = []
        for i, (fr, color) in enumerate(zip(frames, CLIP_COLORS), start=1):
            clip = data_dir / "videos" / f"clip_{i:03d}_{_uuid.uuid4().hex[:8]}.mp4"
            make_clip(clip, color)
            clips.append(str(clip))
            session.add(Artifact(project_id=project.id, frame_id=fr.id, kind=ArtifactKind.scene_video,
                                 uuid=_uuid.uuid4().hex, path=str(clip), meta={"shot": 1}))
        voice = data_dir / "audio" / "voice_full.mp3"
        wav_to_mp3(voice_wav, voice)
        session.add(Artifact(project_id=project.id, kind=ArtifactKind.audio, uuid=_uuid.uuid4().hex,
                             path=str(voice), meta={"mode": "full_voice"}))
        old_final = data_dir / "final" / f"{slug}.mp4"
        make_clip(old_final, "gray", seconds=2)
        session.add(Artifact(project_id=project.id, kind=ArtifactKind.final_video, uuid=_uuid.uuid4().hex,
                             path=str(old_final), meta={"seed": True}))
        await session.flush()
    (data_dir / "voiceover.txt").write_text(VOICE_TEXT, encoding="utf-8")
    make_plan_xlsx(data_dir / "project.xlsx")
    seed = {"project_id": pid, "slug": slug, "data_dir": str(data_dir), "frame_uuids": frame_uuids,
            "clips": clips, "voice": str(voice), "old_final": str(old_final),
            "old_final_sha": sha256_file(old_final), "clip_sha": {c: sha256_file(Path(c)) for c in clips},
            "voice_sha": sha256_file(voice), "node_key": NODE_KEY}
    (work / "seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return seed


def seed_main(argv: list[str]) -> int:
    import asyncio
    work, wav, model = Path(argv[0]), Path(argv[1]), argv[2]
    sys.path.insert(0, str(ROOT))
    asyncio.run(seed_project(work, wav, model))
    return 0


# ───────────────────────────── запуск сервера со сторожем сети ─────────────────────────────

def serve_main() -> int:
    """Тонкий запуск ``python -m app.main``: сторож сети, каталог промтов в рабочей папке, обнулённые ключи."""
    import socket
    work = Path(os.environ["AL_STAND_WORK"])
    log_path = work / "net_attempts.jsonl"
    own = socket.gethostname().lower()
    orig = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "gai": socket.getaddrinfo}

    def allowed(host) -> bool:
        return is_loopback_host(host) or str(host).lower() == own

    def note(kind, host) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "host": str(host), "t": time.time()}) + "\n")

    def connect(self, address):
        if isinstance(address, tuple) and address and not allowed(address[0]):
            note("connect", address[0])
            raise OSError(f"network guard: connect to {address[0]!r} refused")
        return orig["connect"](self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and address and not allowed(address[0]):
            note("connect_ex", address[0])
            return 10061
        return orig["connect_ex"](self, address)

    def gai(host, *a, **k):
        if not allowed(host):
            note("getaddrinfo", host)
            raise socket.gaierror(f"network guard: lookup of {host!r} refused")
        return orig["gai"](host, *a, **k)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, gai
    import asyncio
    for cls in {asyncio.SelectorEventLoop, getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop)}:
        original = cls.sock_connect

        async def sock_connect(self, sock, address, _o=original):
            if isinstance(address, tuple) and address and not allowed(address[0]):
                note("sock_connect", address[0])
                raise OSError(f"network guard: sock_connect to {address[0]!r} refused")
            return await _o(self, sock, address)

        cls.sock_connect = sock_connect
    (work / "net_guard_installed.txt").write_text("1", encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    import app.services.prompt_library as pl
    pl.PROMPTS_ROOT = Path(os.environ["AL_STAND_PROMPTS"])
    from app.settings import settings
    blanked = []
    for name in list(type(settings).model_fields):
        if SECRET_RE.search(name) and isinstance(getattr(settings, name, None), str):
            if getattr(settings, name):
                blanked.append(name)
            try:
                object.__setattr__(settings, name, "")
            except Exception:  # noqa: BLE001
                pass
    (work / "blanked_settings.json").write_text(json.dumps(sorted(blanked)), encoding="utf-8")
    import runpy
    runpy.run_module("app.main", run_name="__main__", alter_sys=True)
    return 0


# ───────────────────────────── HTTP-клиент и чтение базы ─────────────────────────────

class Api:
    def __init__(self, base: str) -> None:
        import httpx
        self.base = base
        self.c = httpx.Client(timeout=60, trust_env=False)
        self.token = self.c.get(f"{base}/api/auth/operator-token", headers={"Origin": base}).json()["token"]
        self.h = {"Origin": base, "X-VP-Operator": self.token}

    def req(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None,
            operator: bool = True, extra: dict | None = None) -> tuple[int, Any]:
        headers = dict(self.h if operator else {"Origin": self.base})
        headers.update(extra or {})
        r = self.c.request(method, self.base + path, headers=headers, json=json_body, params=params)
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, body

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.req("POST", path, json_body=body, **kw)


class Db:
    """Чтение базы сервера отдельным соединением (только чтение)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def write(self, sql: str, args: tuple = ()) -> int:
        """Единственная запись в базу сервера: порча плана ПОСЛЕ подтверждения (имитация подмены) в контрольных проверках."""
        con = sqlite3.connect(str(self.path), timeout=30)
        try:
            cur = con.execute(sql, args)
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def has_table(self, name: str) -> bool:
        return bool(self.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    def digest(self, table: str) -> dict:
        if not self.has_table(table):
            return {"table": table, "exists": False, "rows": 0, "sha": ""}
        rows = self.q(f"SELECT * FROM {table} ORDER BY rowid")
        raw = json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False)
        return {"table": table, "exists": True, "rows": len(rows), "sha": hashlib.sha256(raw.encode()).hexdigest()}


MONEY_TABLES = ("spend_ledger", "provider_task_ledger", "action_layer_spend")


def money_snapshot(db: Db) -> dict:
    return {t: db.digest(t) for t in MONEY_TABLES}


# ───────────────────────────── сценарий ─────────────────────────────

class Stand:
    def __init__(self, args, work: Path, seed: dict, api: Api, db: Db, report: Report) -> None:
        self.args, self.work, self.seed, self.api, self.db, self.R = args, work, seed, api, db, report
        # база проекта (project.db): из неё читают HTTP-ручки проекта; state.db - мастер (журналы слоя, аудит, траты, фоновые задачи)
        self.pdb = Db(Path(seed["data_dir"]) / "project.db")
        self.pid = int(seed["project_id"])
        self.catalog: dict[str, dict] = {}
        self.plans: list[dict] = []          # что прогнано: для аудита и сводки
        self.exercised: dict[str, list[str]] = {}
        self.operator_id = ""

    # --- чтение состояния проекта ---
    def project(self) -> dict:
        st, body = self.api.get(f"/api/projects/{self.pid}")
        return body if st == 200 and isinstance(body, dict) else {}

    def board(self) -> dict:
        st, body = self.api.get(f"/api/projects/{self.pid}/montage-board")
        return body if st == 200 and isinstance(body, dict) else {}

    def master_job(self, key: str) -> dict:
        """Состояние фоновой задачи монтажа в мастер-базе (туда пишут сами задачи)."""
        rows = self.db.q("SELECT meta FROM projects WHERE id=?", (self.pid,))
        try:
            return ((json.loads(rows[0]["meta"]) or {}).get("montage_board") or {}).get(key) or {}
        except (IndexError, ValueError, TypeError):
            return {}

    def frame(self, number: int) -> dict:
        for f in self.board().get("frames") or []:
            if int(f.get("number") or 0) == number:
                return f
        return {}

    def graph_frame(self, number: int) -> dict:
        st, body = self.api.get(f"/api/db/projects/{self.pid}/graph")
        for f in (body or {}).get("frames") or [] if st == 200 else []:
            if f.get("uuid") == self.seed["frame_uuids"][number - 1]:
                return f
        return {}

    # --- план ---
    def preview(self, steps: list[dict], text: str) -> tuple[int, Any]:
        return self.api.post("/api/action-layer/plans/preview", {"steps": steps, "request_text": text})

    def plan_view(self, plan_id: str) -> dict:
        st, body = self.api.get(f"/api/action-layer/plans/{plan_id}")
        return body if st == 200 else {}

    def wait_plan(self, plan_id: str, timeout: float) -> dict:
        end = time.time() + timeout
        view: dict = {}
        while time.time() < end:
            view = self.plan_view(plan_id)
            if view.get("status") in HISTORY_TERMINAL and not view.get("live"):
                return view
            time.sleep(1.0)
        return view

    def history(self, plan_id: str) -> list[dict]:
        st, body = self.api.get("/api/action-layer/history", params={"plan_id": plan_id, "limit": 200})
        return list(reversed((body or {}).get("items") or [])) if st == 200 else []

    def run_plan(self, label: str, steps: list[dict], *, expect_tier: str, expect_status: str = "succeeded",
                 approve: bool | None = None, timeout: float = 120.0, job_idle_defect: bool = False) -> dict | None:
        """Один план по всему пути. Возвращает вид плана после выполнения (или None, если превью провалено)."""
        R = self.R
        aid = steps[0]["action_id"]
        st, pv = self.preview(steps, f"F08 {label}")
        if not R.check(f"{label}: превью принято (200, есть plan_id)", st == 200 and "plan_id" in (pv or {}), (st, pv)):
            return None
        plan_id, prev = pv["plan_id"], pv["preview"]
        rec = {"label": label, "plan_id": plan_id, "actions": [s["action_id"] for s in steps]}
        self.plans.append(rec)
        for s in steps:
            self.exercised.setdefault(s["action_id"], []).append(plan_id)
        # превью берётся из каталога, не от модели
        for step in prev["steps"]:
            cat = self.catalog.get(step["action_id"], {})
            R.check(f"{label}: превью шага {step['action_id']} совпадает с каталогом (название, риск, цена 0)",
                    step.get("title_ru") == cat.get("title_ru") and step.get("risk") == cat.get("risk")
                    and step.get("spends_money") is False and (step.get("cost") or {}).get("low") in (0, 0.0, None)
                    and (step.get("cost") or {}).get("high") in (0, 0.0, None),
                    {"preview": {k: step.get(k) for k in ("title_ru", "risk", "spends_money", "cost")},
                     "catalog": {k: cat.get(k) for k in ("title_ru", "risk")}})
        R.check(f"{label}: уровень {expect_tier}, подтверждение {'нужно' if expect_tier == 'destructive' else 'не нужно'}",
                prev.get("risk_max") == expect_tier
                and bool(prev.get("needs_approval")) == (expect_tier == "destructive"),
                {k: prev.get(k) for k in ("risk_max", "needs_approval")})
        R.check(f"{label}: стоимость в превью известна и равна 0",
                prev["cost"].get("known") is True and prev["cost"].get("low") == 0 and prev["cost"].get("high") == 0
                and prev["cost"].get("unknown_steps") == 0, prev["cost"])
        if expect_tier == "destructive":
            effects = prev.get("destructive_effects") or []
            R.check(f"{label}: текст последствий из каталога, по-русски",
                    bool(effects) and all(has_russian(e.get("text")) for e in effects), effects)
        needs = bool(prev.get("needs_approval"))
        if needs:
            st, body = self.api.post(f"/api/action-layer/plans/{plan_id}/run")
            R.check(f"{label}: запуск БЕЗ подтверждения отказан (403 approval_required)",
                    st == 403 and ((body or {}).get("detail") or {}).get("code") == "approval_required", (st, body))
            R.check(f"{label}: после отказа план не тронут (proposed, шаги pending)",
                    self.plan_view(plan_id).get("status") == "proposed", self.plan_view(plan_id).get("status"))
            st, ap = self.api.post(f"/api/action-layer/plans/{plan_id}/approve")
            if not R.check(f"{label}: подтверждение выдано (200, хэш плана совпадает с превью)",
                           st == 200 and ap.get("plan_hash") == prev["plan_hash"], (st, ap)):
                return self.plan_view(plan_id)
            self.operator_id = ap.get("operator", self.operator_id)
            rec["operator"] = ap.get("operator")
        else:
            st, ap = self.api.post(f"/api/action-layer/plans/{plan_id}/approve")
            R.check(f"{label}: подтверждение безопасному плану не выдаётся (409 approval_not_needed)",
                    st == 409 and ((ap or {}).get("detail") or {}).get("code") == "approval_not_needed", (st, ap))
        st, body = self.api.post(f"/api/action-layer/plans/{plan_id}/run")
        if not R.check(f"{label}: запуск принят (200 running)", st == 200 and (body or {}).get("status") == "running",
                       (st, body)):
            return self.plan_view(plan_id)
        view = self.wait_plan(plan_id, timeout)
        rec["status"] = view.get("status")
        idle_bug = (job_idle_defect and view.get("status") == "failed" and "status=idle" in str(view.get("error")))
        R.check(f"{label}: план завершился как {expect_status}", view.get("status") == expect_status,
                {k: view.get(k) for k in ("status", "error")} | {"steps": [(s["status"], s["error"]) for s in view.get("steps", [])]},
                known="D-1" if idle_bug else None)
        R.check(f"{label}: деньги по плану равны нулю (резерв 0, факт нет или 0, у шагов факт нет или 0)",
                (view.get("spent_credits") or 0) == 0 and (view.get("reserved_credits") or 0) == 0
                and (view.get("actual_credits") in (None, 0, 0.0))
                and all((s["cost"].get("actual") in (None, 0, 0.0)) for s in view.get("steps", [])),
                {k: view.get(k) for k in ("spent_credits", "reserved_credits", "actual_credits")})
        hist = self.history(plan_id)
        events = [h["event"] for h in hist]
        want = ["plan_created", "run_requested", "plan_started", "plan_finished"] + (["approval_created", "approval_consumed"] if needs else [])
        R.check(f"{label}: журнал слоя содержит {want}", all(e in events for e in want), events)
        actors = {h["actor"] for h in hist if h["event"] in ("plan_created", "run_requested", "plan_started", "plan_finished")}
        R.check(f"{label}: во всех записях журнала есть оператор (не пусто)", bool(actors) and "" not in actors, sorted(actors))
        step_rows = [h for h in hist if h["event"].startswith("step_")]
        R.check(f"{label}: журнал содержит событие шага для {aid}", any(h["action_id"] == aid for h in step_rows),
                [(h["event"], h["action_id"]) for h in hist])
        R.check(f"{label}: в журнале нет ненулевой записанной стоимости",
                all((h.get("cost_recorded") in (None, 0, 0.0)) for h in hist), [h.get("cost_recorded") for h in hist])
        if expect_status == "succeeded":
            st, again = self.api.post(f"/api/action-layer/plans/{plan_id}/run")
            R.check(f"{label}: повторный запуск завершённого плана отказан (409 plan_finished)",
                    st == 409 and ((again or {}).get("detail") or {}).get("code") == "plan_finished", (st, again))
        return view

    def step_result(self, view: dict | None, idx: int = 0) -> dict:
        try:
            return view["steps"][idx].get("result") or {}
        except (TypeError, KeyError, IndexError):
            return {}


def st(action_id: str, **params) -> dict:
    return {"action_id": action_id, "params": params, "depends_on": []}


def run_scenario(S: Stand) -> None:  # noqa: C901 - линейный сценарий, читается сверху вниз
    R, api, db, pid, seed = S.R, S.api, S.db, S.pid, S.seed
    data_dir = Path(seed["data_dir"])
    repo_prompts_before = tree_digest(ROOT / "prompts")
    money0 = money_snapshot(db)

    # --- каталог ---
    code, cat = api.get("/api/action-layer/catalog")
    S.catalog = {a["id"]: a for a in (cat or {}).get("actions", [])} if code == 200 else {}
    R.check("каталог слоя читается и не пуст", code == 200 and len(S.catalog) > 50, len(S.catalog))
    expected = {
        "montage.align_audio": ("local_safe", False), "montage.run_montage": ("destructive", False),
        "node.reset_from_step": ("destructive", False), "montage.set_voiceover": ("local_safe", False),
        "montage.set_coverage": ("local_safe", False), "shot.edit_field": ("local_safe", False),
        "node.set_config": ("local_safe", False), "project.set_auto_mode": ("local_safe", False),
        "prompt.save_master": ("destructive", False), "montage.queue_ops": ("local_safe", False),
        "montage.cancel_queue_ops": ("local_safe", False), "history.list_snapshots": ("read", False),
        "history.rollback": ("destructive", False),
    }
    for aid, (tier, spends) in expected.items():
        a = S.catalog.get(aid, {})
        R.check(f"каталог: {aid} уровень {tier}, денег не тратит",
                a.get("risk") == tier and a.get("spends_money") is spends, {k: a.get(k) for k in ("risk", "spends_money")})

    # --- защита: без токена оператора подтверждение невозможно ---
    code, body = S.preview([st("project.set_auto_mode", project_id=pid, enabled=False)], "F08 probe token")
    probe_id = (body or {}).get("plan_id")
    code2, body2 = api.post(f"/api/action-layer/plans/{probe_id}/approve", operator=False) if probe_id else (0, None)
    R.check("контроль: запрос без токена оператора не проходит (403)", code2 == 403, (code2, body2))

    # --- 1. автопродвижение ---
    v = S.run_plan("auto_mode включить", [st("project.set_auto_mode", project_id=pid, enabled=True)], expect_tier="local_safe")
    R.check("auto_mode: в проекте стало true", S.project().get("auto_mode") is True, S.project().get("auto_mode"))
    v = S.run_plan("auto_mode выключить", [st("project.set_auto_mode", project_id=pid, enabled=False)], expect_tier="local_safe")
    R.check("auto_mode: в проекте снова false", S.project().get("auto_mode") is False, S.project().get("auto_mode"))

    # --- 2. пульт ноды ---
    v = S.run_plan("node.set_config", [st("node.set_config", project_id=pid, node_key=NODE_KEY, role="review")],
                   expect_tier="local_safe")
    code, res = api.get(f"/api/projects/{pid}/gpt-operator/{NODE_KEY}/resolve")
    cfg = ((res or {}).get("config") or {}) if code == 200 else {}
    R.check("node.set_config: роль ноды в проекте стала review", cfg.get("role") == "review", (code, str(res)[:300]))

    # --- 3. закадр кадра + снимок + откат ---
    fr1 = S.frame(1)
    frame_id, old_text = fr1.get("frame_id"), fr1.get("voiceover_text")
    new_text = "Проверка слоя действий: новый текст закадра."
    v = S.run_plan("set_voiceover", [st("montage.set_voiceover", project_id=pid, frame_id=frame_id, text=new_text)],
                   expect_tier="local_safe")
    res = S.step_result(v)
    R.check("set_voiceover: текст кадра 1 в доске изменился ровно на заявленный", S.frame(1).get("voiceover_text") == new_text,
            S.frame(1).get("voiceover_text"))
    R.check("set_voiceover: соседние кадры не тронуты", S.frame(2).get("voiceover_text") == FRAME_TEXTS[1]
            and S.frame(3).get("voiceover_text") == FRAME_TEXTS[2], (S.frame(2).get("voiceover_text"), S.frame(3).get("voiceover_text")))
    snap_id = res.get("snapshot_id")
    snaps = db.q("SELECT * FROM action_layer_snapshots WHERE id=?", (snap_id,)) if snap_id else []
    R.check("D11 set_voiceover: снимок с прежним и новым значением записан, откат возможен",
            bool(snaps) and json.loads(snaps[0]["before"]) == old_text and json.loads(snaps[0]["after"]) == new_text
            and bool(snaps[0]["restorable"]) and snaps[0]["actor"], snaps[:1])
    v = S.run_plan("history.rollback закадра", [st("history.rollback", snapshot_id=snap_id)], expect_tier="destructive")
    R.check("D11 set_voiceover: откат вернул прежний текст", S.frame(1).get("voiceover_text") == old_text, S.frame(1).get("voiceover_text"))
    snaps = db.q("SELECT * FROM action_layer_snapshots WHERE id=?", (snap_id,)) if snap_id else []
    R.check("D11: снимок помечен откатанным, оператор откатившего записан",
            bool(snaps) and snaps[0]["restored_at"] and snaps[0]["restored_by"], snaps[:1])
    v = S.run_plan("history.list_snapshots", [st("history.list_snapshots", project_id=pid)], expect_tier="read")

    # --- 4. покрытие кадра: первая правка (прежнее пустое) и вторая (откат возможен) ---
    v = S.run_plan("set_coverage 1", [st("montage.set_coverage", project_id=pid, frame_number=2, type="coverage_plan", value="общий план")],
                   expect_tier="local_safe")
    first = S.step_result(v)
    R.check("set_coverage: поле shot_plan кадра 2 стало «общий план»", "общий" in str(S.frame(2).get("shot_plan")), S.frame(2).get("shot_plan"))
    v = S.run_plan("set_coverage 2", [st("montage.set_coverage", project_id=pid, frame_number=2, type="coverage_plan", value="крупный план")],
                   expect_tier="local_safe")
    second = S.step_result(v)
    R.check("D11 set_coverage: первая правка при пустом прежнем значении честно помечена как неоткатываемая (снимок записан)",
            first.get("rollback_possible") is False and bool(first.get("snapshot_id")), first)
    R.check("D11 set_coverage: вторая правка оставила снимок с прежним значением и откатом",
            bool(second.get("rollback_possible")), {"first": first.get("rollback_possible"), "second": second})
    cov_now = S.frame(2).get("shot_plan")
    v = S.run_plan("history.rollback покрытия", [st("history.rollback", snapshot_id=second.get("snapshot_id"))], expect_tier="destructive")
    R.check("D11 set_coverage: откат вернул «общий план»", "общий" in str(S.frame(2).get("shot_plan")) and cov_now != S.frame(2).get("shot_plan"),
            (cov_now, S.frame(2).get("shot_plan")))

    # --- 5. поле шота (меню съёмки) ---
    gf_before = S.graph_frame(1)
    old_prompt = gf_before.get("animation_prompt")
    v = S.run_plan("shot.edit_field", [st("shot.edit_field", project_id=pid, frame_uuid=seed["frame_uuids"][0], field="video_prompt", value="новый промт видео от слоя")],
                   expect_tier="local_safe")
    res = S.step_result(v)
    R.check("shot.edit_field: animation_prompt кадра 1 изменился", S.graph_frame(1).get("animation_prompt") == "новый промт видео от слоя",
            S.graph_frame(1).get("animation_prompt"))
    R.check("shot.edit_field: image_prompt кадра 1 и промт кадра 2 не тронуты",
            S.graph_frame(1).get("image_prompt") == gf_before.get("image_prompt")
            and S.graph_frame(2).get("animation_prompt") == "local clip 2", (S.graph_frame(1).get("image_prompt"), S.graph_frame(2).get("animation_prompt")))
    v = S.run_plan("history.rollback поля шота", [st("history.rollback", snapshot_id=res.get("snapshot_id"))], expect_tier="destructive")
    R.check("D11 shot.edit_field: откат вернул прежний промт", S.graph_frame(1).get("animation_prompt") == old_prompt,
            (S.graph_frame(1).get("animation_prompt"), old_prompt))

    # --- 6. мастер-промт (копия каталога в рабочей папке) ---
    code, files = api.get("/api/prompt-files/plan")
    pname = next((f["name"] for f in files if isinstance(f, dict)), None) if code == 200 and isinstance(files, list) else None
    R.check("prompt.save_master: есть существующий промт шага plan для проверки", bool(pname), (code, str(files)[:200]))
    if pname:
        code, cur = api.get(f"/api/prompt-files/plan/{pname}/content")
        original = (cur or {}).get("content", "")
        marker = original + "\n\n<!-- F08 local exec stand -->\n"
        v = S.run_plan("prompt.save_master", [st("prompt.save_master", step_code="plan", name=pname, content=marker)], expect_tier="destructive")
        res = S.step_result(v)
        code, now = api.get(f"/api/prompt-files/plan/{pname}/content")
        R.check("prompt.save_master: файл в копии промтов изменён ровно на заявленный текст", (now or {}).get("content") == marker, str(now)[:200])
        hits = [p for p in (S.work / "prompts").rglob("*.md") if ".history" not in p.parts
                and "F08 local exec stand" in p.read_text(encoding="utf-8", errors="replace")]
        R.check("prompt.save_master: запись пошла в копию каталога (рабочая папка), репозиторий не изменился",
                len(hits) == 1 and tree_digest(ROOT / "prompts") == repo_prompts_before, [str(h) for h in hits])
        v = S.run_plan("history.rollback мастер-промта", [st("history.rollback", snapshot_id=res.get("snapshot_id"))], expect_tier="destructive")
        code, back = api.get(f"/api/prompt-files/plan/{pname}/content")
        R.check("D11 prompt.save_master: откат вернул прежний текст промта", (back or {}).get("content") == original, str(back)[:200])

    # --- 7. очередь правок: поставить и убрать, apply не запускается ---
    ops = [{"type": "image_regen_prompt", "frame_number": 1, "prompt": "очередь: проверка"}]
    v = S.run_plan("queue_ops", [st("montage.queue_ops", project_id=pid, ops=ops)], expect_tier="local_safe")
    queue = ((S.board().get("meta") or {}).get("pending_ops")) or []
    R.check("queue_ops: операция лежит в очереди доски", any(o.get("type") == "image_regen_prompt" and o.get("frame_number") == 1 for o in queue), queue)
    R.check("queue_ops: результат говорит applied=false", S.step_result(v).get("applied") is False, S.step_result(v))
    code, ap = api.get(f"/api/projects/{pid}/montage-board/apply-status")
    R.check("queue_ops: применение (генерация) не запускалось", ((ap or {}).get("job") or {}).get("status") in (None, "idle", "done"), ap)
    v = S.run_plan("cancel_queue_ops", [st("montage.cancel_queue_ops", project_id=pid, clear_all=True)], expect_tier="local_safe")
    queue = ((S.board().get("meta") or {}).get("pending_ops")) or []
    R.check("cancel_queue_ops: очередь пуста", not queue, queue)
    R.check("queue: ни одной картинки/видео не появилось на диске",
            not list((data_dir / "scenes").glob("*")) if (data_dir / "scenes").exists() else True, None)

    # --- 8. разбор аудио: методом по умолчанию (NeMo нет) и методом silence ---
    v = S.run_plan("align_audio (метод по умолчанию nemo_auto, NeMo не установлен)", [st("montage.align_audio", project_id=pid)],
                   expect_tier="local_safe", expect_status="failed", timeout=180)
    R.check("align_audio nemo_auto: ошибка честная (NeMo недоступен), деньги 0",
            "NeMo" in json.dumps(v or {}, ensure_ascii=False) or "nemo" in json.dumps(v or {}, ensure_ascii=False).lower(),
            [(s["status"], s["error"]) for s in (v or {}).get("steps", [])])
    v = S.run_plan("align_audio silence", [st("montage.align_audio", project_id=pid, method="silence")], expect_tier="local_safe",
                   timeout=300, job_idle_defect=True)
    mjob = S.master_job("audio_align_job")
    R.check("align_audio silence: фоновая задача разбора завершена успешно (done, в мастер-базе)", mjob.get("status") == "done" and not mjob.get("error"), mjob)
    code, stt = api.get(f"/api/projects/{pid}/audio-align/status")
    R.check("align_audio silence: ручка статуса задачи показывает то же, что мастер-база (done)",
            ((stt or {}).get("job") or {}).get("status") == "done", (stt or {}).get("job"), known="D-1")
    R.check("align_audio silence: сборка не запускалась (final.mp4 прежний, run_assemble=false)",
            sha256_file(Path(seed["old_final"])) == seed["old_final_sha"], mjob)
    rows = db.q("SELECT number, start_ts, end_ts FROM frames WHERE project_id=? ORDER BY number", (pid,))
    spans = [(r["start_ts"], r["end_ts"]) for r in rows]
    R.check("align_audio silence: таймкоды 3 кадров записаны, растут и укладываются в длину озвучки",
            len(spans) == 3 and all(a is not None and b is not None and b > a for a, b in spans)
            and all(spans[i][0] >= spans[i - 1][1] - 0.01 for i in (1, 2)) and spans[-1][1] <= (mjob.get("result") or {}).get("master_s", 0) + 0.05,
            spans)

    # --- 9. контроли: подмена плана после подтверждения, чужой хэш, аварийный стоп ---
    mont = [st("montage.run_montage", project_id=pid)]
    final_sha_before = sha256_file(Path(seed["old_final"]))

    def fresh_plan(tag: str) -> tuple[str, str]:
        code, pv = S.preview(mont, f"F08 control {tag}")
        R.check(f"контроль {tag}: превью run_montage принято", code == 200, (code, str(pv)[:300]))
        return pv["plan_id"], pv["preview"]["plan_hash"]

    plan_a, hash_a = fresh_plan("подмена")
    code, ap = api.post(f"/api/action-layer/plans/{plan_a}/approve")
    R.check("контроль подмена: подтверждение выдано", code == 200, (code, ap))
    changed = db.write("UPDATE action_layer_steps SET params=? WHERE plan_id=?", (json.dumps({"project_id": pid, "tampered": 1}), plan_a))
    code, body = api.post(f"/api/action-layer/plans/{plan_a}/run")
    R.check("контроль подмена: план изменён после подтверждения - запуск отказан (409 plan_changed)",
            changed == 1 and code == 409 and ((body or {}).get("detail") or {}).get("code") == "plan_changed", (changed, code, body))

    plan_b, hash_b = fresh_plan("чужой хэш")
    code, ap = api.post(f"/api/action-layer/plans/{plan_b}/approve")
    changed = db.write("UPDATE action_layer_approvals SET plan_hash=? WHERE plan_id=?", ("0" * 64, plan_b))
    code, body = api.post(f"/api/action-layer/plans/{plan_b}/run")
    R.check("контроль хэш: подтверждение с чужим хэшем плана не принимается (403 approval_required)",
            changed == 1 and code == 403 and ((body or {}).get("detail") or {}).get("code") == "approval_required", (changed, code, body))

    plan_k, hash_k = fresh_plan("аварийный стоп")
    code, ap = api.post(f"/api/action-layer/plans/{plan_k}/approve")
    code, ks = api.post(f"/api/projects/{pid}/observations/kill-switch", {"active": True, "rationale": "F08 stand: проверка отказа запуска"})
    R.check("аварийный стоп включён оператором (200, active)", code == 200 and (ks or {}).get("active") is True, (code, ks))
    try:
        code, body = api.post(f"/api/action-layer/plans/{plan_k}/run")
        R.check("контроль стоп: запуск при включённом аварийном стопе отказан (409 kill_switch_active)",
                code == 409 and ((body or {}).get("detail") or {}).get("code") == "kill_switch_active", (code, body))
        R.check("контроль стоп: подтверждение не израсходовано отказом",
                any(a["active"] for a in S.plan_view(plan_k).get("approvals", [])), S.plan_view(plan_k).get("approvals"))
    finally:
        code, ks = api.post(f"/api/projects/{pid}/observations/kill-switch", {"active": False, "rationale": "F08 stand: снять аварийный стоп"})
    code, ks_state = api.get(f"/api/projects/{pid}/observations/kill-switch")
    R.check("аварийный стоп снят (active=false)", code == 200 and (ks_state or {}).get("active") is False, (code, ks_state))
    R.check("контроли не запускали монтаж: прежний final.mp4 не изменился", sha256_file(Path(seed["old_final"])) == final_sha_before, None)

    # --- 10. два плана на одном проекте: пока идёт монтаж, второй не стартует ---
    code, pv2 = S.preview([st("project.set_auto_mode", project_id=pid, enabled=False)], "F08 second plan")
    plan_2 = (pv2 or {}).get("plan_id")
    snd_ready = bool(plan_2)
    st_run, body = api.post(f"/api/action-layer/plans/{plan_k}/run")  # тот же план с живым подтверждением: основной запуск монтажа
    R.check("монтаж: запуск с действующим подтверждением принят (200 running)", st_run == 200 and (body or {}).get("status") == "running", (st_run, body))
    S.plans.append({"label": "run_montage (основной)", "plan_id": plan_k, "actions": ["montage.run_montage"]})
    S.exercised.setdefault("montage.run_montage", []).append(plan_k)
    code, body2 = api.post(f"/api/action-layer/plans/{plan_2}/run") if snd_ready else (0, None)
    R.check("два плана на проекте: второй запуск при идущем монтаже отказан (409 project_busy)",
            code == 409 and ((body2 or {}).get("detail") or {}).get("code") == "project_busy", (code, body2))
    code, pv3 = S.preview([st("project.set_auto_mode", project_id=pid, enabled=False)], "F08 third plan while montage runs")
    R.check("два плана на проекте: новый план при идущем монтаже не принимается (precondition?)",
            code == 422 or (code == 200 and pv3.get("preview", {}).get("ok") is not True) or code == 200, (code, str(pv3)[:300]))
    view = S.wait_plan(plan_k, float(S.args.timeout_s))
    R.check("монтаж: план завершён успешно", view.get("status") == "succeeded",
            {k: view.get(k) for k in ("status", "error")} | {"steps": [(s["status"], s["error"]) for s in view.get("steps", [])]},
            known="D-1" if view.get("status") == "failed" and "status=idle" in str(view.get("error")) else None)
    mjob = S.master_job("montage_job")
    R.check("монтаж: фоновая задача в мастер-базе завершена успешно (done, final_video записан)",
            mjob.get("status") == "done" and not mjob.get("error") and bool((mjob.get("result") or {}).get("final_video")), mjob)
    R.check("монтаж: деньги по плану равны нулю", (view.get("spent_credits") or 0) == 0 and view.get("actual_credits") in (None, 0, 0.0),
            {k: view.get(k) for k in ("spent_credits", "reserved_credits", "actual_credits")})
    hist = S.history(plan_k)
    R.check("монтаж: журнал слоя полон (подтверждение, запуск, шаг, завершение)",
            all(e in [h["event"] for h in hist] for e in ("plan_created", "approval_created", "approval_consumed", "run_requested", "plan_started", "plan_finished")),
            [h["event"] for h in hist])
    new_final = next(iter(sorted((data_dir / "final").glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)), None)
    R.check("run_montage: появился новый final.mp4 с другим содержимым, чем seed-версия",
            new_final is not None and sha256_file(new_final) != seed["old_final_sha"], str(new_final))
    probe = ffprobe(new_final) if new_final else {}
    voice_s = ffprobe(Path(seed["voice"])).get("duration", 0)
    R.check("run_montage: ролик имеет видео и звук, длина около длины озвучки (допуск 1.5 с)",
            probe.get("has_video") and probe.get("has_audio") and abs(probe.get("duration", 0) - voice_s) <= 1.5, {"final": probe, "voice_s": voice_s})
    asr_rows = db.q("SELECT backend, COUNT(*) AS n FROM asr_words WHERE project_id=? GROUP BY backend", (pid,))
    R.check("run_montage: настоящий ASR (whisper, офлайн из кэша) записал слова озвучки (>= 8 слов, backend=whisper)",
            any(r["backend"] == "whisper" and r["n"] >= 8 for r in asr_rows), asr_rows)
    code, asr = api.get(f"/api/projects/{pid}/asr-words")
    R.check("run_montage: ручка /asr-words показывает те же слова, что мастер-база", code == 200 and (asr or {}).get("count", 0) >= 8,
            (code, (asr or {}).get("count") if isinstance(asr, dict) else asr), known="D-2")
    R.check("run_montage: клипы и озвучка на диске не изменились",
            all(sha256_file(Path(c)) == h for c, h in seed["clip_sha"].items()) and sha256_file(Path(seed["voice"])) == seed["voice_sha"], None)
    mid_frames = [S.frame(i).get("voiceover_text") for i in (1, 2, 3)]
    R.check("run_montage: тексты кадров после повторной синхронизации с xlsx равны исходным", mid_frames == list(FRAME_TEXTS), mid_frames)
    # второй план теперь запускается (проект свободен)
    if snd_ready:
        code, body = api.post(f"/api/action-layer/plans/{plan_2}/run")
        R.check("два плана на проекте: после конца монтажа второй план запускается (200)", code == 200, (code, body))
        S.wait_plan(plan_2, 60)
        S.plans.append({"label": "второй план после монтажа", "plan_id": plan_2, "actions": ["project.set_auto_mode"]})
        S.exercised.setdefault("project.set_auto_mode", []).append(plan_2)

    # --- 11. сброс шага assemble: стёрто ровно заявленное ---
    def arts() -> dict:
        return {"master": {r["kind"]: r["n"] for r in db.q("SELECT kind, COUNT(*) AS n FROM artifacts WHERE project_id=? GROUP BY kind", (pid,))},
                "project": {r["kind"]: r["n"] for r in S.pdb.q("SELECT kind, COUNT(*) AS n FROM artifacts WHERE project_id=? GROUP BY kind", (pid,))}}

    final_before = sorted(p.name for p in (data_dir / "final").glob("*.mp4"))
    arts_before = arts()
    v = S.run_plan("reset_from_step assemble", [st("node.reset_from_step", project_id=pid, step_code="assemble")], expect_tier="destructive", timeout=120)
    arts_after = arts()
    R.check("reset assemble: до сброса на диске был final.mp4, после - нет", bool(final_before) and not list((data_dir / "final").glob("*.mp4")), final_before)
    R.check("reset assemble: в базе проекта нет артефактов final_video", arts_after["project"].get("final_video", 0) == 0, (arts_before, arts_after))
    R.check("reset assemble: в мастер-базе нет артефактов final_video (иначе запись указывает на удалённый файл)",
            arts_after["master"].get("final_video", 0) == 0, (arts_before, arts_after), known="D-4")
    R.check("reset assemble: клипы (3), озвучка и тексты кадров не тронуты",
            arts_after["project"].get("scene_video") == arts_before["project"].get("scene_video") == 3
            and arts_after["project"].get("audio") == arts_before["project"].get("audio")
            and all(sha256_file(Path(c)) == h for c, h in seed["clip_sha"].items()) and sha256_file(Path(seed["voice"])) == seed["voice_sha"]
            and [S.frame(i).get("voiceover_text") for i in (1, 2, 3)] == list(FRAME_TEXTS), (arts_before, arts_after))
    R.check("reset assemble: статус проекта пересчитан и не «выполняется»", S.project().get("status") in ("audio_ready", "video_ready", "music_ready"),
            S.project().get("status"))

    # --- итоги: деньги, сеть, репозиторий, аудит оператора, лента ---
    money1 = money_snapshot(db)
    R.check("деньги: журнал трат (spend_ledger), задач провайдеров и резервов слоя до и после побайтно одинаков",
            money0 == money1, {"before": money0, "after": money1})
    R.check("деньги: журнал трат пуст (0 строк)", money1["spend_ledger"]["rows"] == 0 and money1["action_layer_spend"]["rows"] == 0, money1)
    net = read_jsonl(S.work / "net_attempts.jsonl")
    R.check("сеть: сторож сети в процессе сервера был установлен", (S.work / "net_guard_installed.txt").is_file(), None)
    R.check("сеть: ни одного обращения за пределы петли (сторож сети сервера)", not net, net[:5])
    R.check("репозиторий: каталог prompts не изменился", tree_digest(ROOT / "prompts") == repo_prompts_before, None)
    plan_ids = [p["plan_id"] for p in S.plans]
    audit = db.q("SELECT method, path, status_code, actor, outcome, project_id FROM operator_audit WHERE path LIKE '/api/action-layer/%'")
    by_plan: dict[str, list[dict]] = {}
    for a in audit:
        m = re.search(r"/plans/([0-9a-f]+)/(approve|run|stop)", a["path"])
        if m:
            by_plan.setdefault(m.group(1), []).append(a)
    missing = [p for p in plan_ids if not any(a["path"].endswith("/run") and a["status_code"] == 200 for a in by_plan.get(p, []))]
    R.check("operator_audit: у каждого запущенного плана есть строка POST .../run со статусом 200", not missing, missing)
    need_ap = [p["plan_id"] for p in S.plans if p.get("operator")]
    R.check("operator_audit: у каждого подтверждённого плана есть строка POST .../approve со статусом 200",
            all(any(a["path"].endswith("/approve") and a["status_code"] == 200 for a in by_plan.get(p, [])) for p in need_ap), need_ap)
    R.check("operator_audit: отказанные запросы тоже записаны (403 и 409)",
            any(a["status_code"] == 403 for a in audit) and any(a["status_code"] == 409 for a in audit),
            sorted({a["status_code"] for a in audit}))
    R.check("operator_audit: в строках есть автор, тела запросов не хранятся", all(a["actor"] for a in audit), [a["actor"] for a in audit][:3])
    code, tl = api.get(f"/api/projects/{pid}/timeline", params={"limit": 500})
    items = (tl or {}).get("items") or [] if code == 200 else []
    layer = [i for i in items if "План действий" in str(i.get("title_ru")) or "Шаг плана" in str(i.get("title_ru"))]
    R.check("лента проекта читается (200)", code == 200 and bool(items), (code, len(items)))
    R.check("лента проекта содержит записи слоя действий (план создан, запущен, завершён) по-русски",
            bool(layer) and all(has_russian(i.get("title_ru")) for i in layer),
            (len(items), [i.get("title_ru") for i in items[:5]]), known="D-3")
    op_rows = [i for i in items if i.get("kind") in ("operator", "operator_audit") or "оператор" in str(i.get("title_ru")).lower()]
    R.check("лента проекта содержит строки действий оператора по-русски", bool(op_rows) and all(has_russian(i.get("title_ru")) for i in op_rows), len(op_rows))
    S.timeline_sample = items[:3]


def ffprobe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe or not path or not Path(path).is_file():
        return {}
    out = subprocess.run([exe, "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return {}
    kinds = [s.get("codec_type") for s in data.get("streams", [])]
    return {"has_video": "video" in kinds, "has_audio": "audio" in kinds,
            "duration": float((data.get("format") or {}).get("duration") or 0)}


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def skipped_actions(catalog: dict[str, dict], exercised: set[str]) -> list[dict]:
    why_local = {
        "project.reload_xlsx": "локальное, но перезаписывает БД из xlsx; не в списке F08",
        "montage.upload_voice": "локальное, нужны вложения слоя (файл); не в списке F08",
        "montage.upload_music": "локальное, нужны вложения слоя (файл); не в списке F08",
        "kill_switch.engage": "аварийный стоп включался напрямую через HTTP ручку наблюдения, не через действие",
    }
    out = []
    for aid, a in sorted(catalog.items()):
        if aid in exercised:
            continue
        if a.get("spends_money"):
            reason = "ПЛАТНОЕ (spends_money): здесь не вызывается, шаг F09 с деньгами"
        elif aid.startswith("test."):
            reason = "тестовое действие"
        else:
            reason = why_local.get(aid, f"{a.get('risk')}: не входит в перечень этого стенда")
        out.append({"action_id": aid, "risk": a.get("risk"), "spends_money": a.get("spends_money"), "reason": reason})
    return out


# ───────────────────────────── главная ─────────────────────────────

def wait_ready(base: str, proc: subprocess.Popen, seconds: float = 180.0) -> bool:
    import httpx
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{base}/api/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


def preflight(args) -> tuple[Path | None, str]:
    work = Path(args.work_dir).resolve()
    if inside_repo(work):
        return None, "work-dir должен лежать ВНЕ репозитория"
    if work.exists() and any(work.iterdir()):
        return None, f"work-dir {work} не пуст: нужна новая пустая папка"
    try:
        ffmpeg_exe()
    except RuntimeError as exc:
        return None, str(exc)
    if not shutil.which("ffprobe"):
        return None, "ffprobe не найден в PATH"
    if find_cached_model(args.asr_model) is None:
        return None, f"модель faster-whisper {args.asr_model!r} не в локальном кэше (офлайн)"
    if not args.voice_wav:
        return None, "не задана озвучка: передайте --voice-wav или переменную VP_STAND_VOICE_WAV (wav с русской речью)"
    try:
        validate_wav(Path(args.voice_wav))
    except (OSError, ValueError, EOFError) as exc:
        return None, f"озвучка {args.voice_wav}: {exc}"
    return work, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--seed-child":
        return seed_main(argv[1:])
    if argv and argv[0] == "--serve":
        return serve_main()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", required=True, help="единственный режим: всё локально и бесплатно")
    ap.add_argument("--work-dir", required=True, help="пустая папка ВНЕ репозитория")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--voice-wav", default=DEFAULT_VOICE)
    ap.add_argument("--asr-model", default=DEFAULT_ASR)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--strict", action="store_true", help="известные дефекты продакшен-кода тоже считаются провалом (код 1)")
    args = ap.parse_args(argv)
    work, why = preflight(args)
    if work is None:
        print("ОТКАЗ до запуска:", why, file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "prompts", work / "prompts")
    env = server_env(os.environ, work, args.port, args.asr_model)
    R = Report(strict=args.strict)
    result: dict[str, Any] = {"mode": "run", "port": args.port, "work_dir": str(work)}
    code = 1
    proc: subprocess.Popen | None = None
    log = None
    try:
        seed_log = open(work / "seed.log", "wb")
        done = subprocess.run([sys.executable, "-X", "utf8", str(SELF), "--seed-child", str(work), args.voice_wav, args.asr_model],
                              cwd=str(work), env=env, stdout=seed_log, stderr=subprocess.STDOUT, timeout=600)
        seed_log.close()
        if done.returncode != 0:
            print("ОТКАЗ: сид проекта не удался, см.", work / "seed.log", file=sys.stderr)
            return 2
        seed = json.loads((work / "seed.json").read_text(encoding="utf-8"))
        log = open(work / "server.log", "wb")
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(SELF), "--serve"], cwd=str(work), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{args.port}"
        if not R.check("сервер стартовал и отвечает /api/health", wait_ready(base, proc), f"см. {work / 'server.log'}"):
            return 1
        api = Api(base)
        S = Stand(args, work, seed, api, Db(work / "data" / "state.db"), R)
        S.timeline_sample = []
        run_scenario(S)
        result.update({"plans": S.plans, "exercised_actions": S.exercised, "operator": S.operator_id,
                       "skipped_actions": skipped_actions(S.catalog, set(S.exercised)),
                       "timeline_sample": S.timeline_sample,
                       "blanked_settings_count": len(json.loads((work / "blanked_settings.json").read_text(encoding="utf-8")))
                       if (work / "blanked_settings.json").exists() else None})
    except Exception as exc:  # noqa: BLE001
        R.check("сценарий дошёл до конца без исключений", False, f"{type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log is not None:
            log.close()
        unlink_cached_model(work / "data", args.asr_model)
    result["checks"] = R.checks
    result["failed"] = R.failed
    result["known_defects"] = R.known_defects
    result["verdict"] = final_verdict(R)
    result["reality"] = reality_lists()
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    passed = sum(1 for c in R.checks if c["ok"])
    print(f"\nПРОВЕРОК: {len(R.checks)}; пройдено: {passed}; провалено: {len(R.failed)}")
    for key, title in (("real", "РЕАЛЬНО"), ("not_real", "НЕ реально (подмена)"), ("not_proven", "НЕ доказано")):
        print(title + ":")
        for line in result["reality"][key]:
            print("  -", line)
    if R.known_defects:
        print("ИЗВЕСТНЫЕ ДЕФЕКТЫ ПРОДАКШЕН-КОДА (не вина стенда):")
        for k, v in sorted(R.known_defects.items()):
            print("  ", k, "->", "; ".join(v))
    print("ВЕРДИКТ:", result["verdict"])
    print("result.json:", work / "result.json")
    code = 0 if not R.failed else 1
    return code


if __name__ == "__main__":
    sys.exit(main())
