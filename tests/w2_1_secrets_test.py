"""W2-1 / A16: секреты не сохраняются ни в тексте, ни в плане, ни в истории, ни в логах."""
from __future__ import annotations

import io
import json

import pytest
from loguru import logger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.monitor import log_sink
from app.services import observation
from app.services.action_layer import store
from app.services.redact import install_log_redaction, redact, redact_text

# Значения собираются из частей, чтобы тест сам не был «утечкой» для сканеров.
SECRETS = {
    "telegram": "123456789" + ":" + "AAEhBOweik6ad9r" + "_QXBkHjCgyZRoBvKcT5s",
    "supabase_jwt": "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "." + "eyJyb2xlIjoic2VydmljZV9yb2xlIn0" + "." + "abcDEF123_-xyz",
    "supabase_sb": "sb_" + "secret_" + "AbCdEfGh1234567890XyZ",
    "aiza": "AIza" + "SyA1234567890abcdefghijklmnopqrstuv",
    "bearer": "Bearer " + "abc123.DEF-456_ghi",
    "sk": "sk-" + "ABCDEFGH12345678",
    "vk": "vk-" + "AbCdEf0123456789xyz",
    "ghp": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "xi_api_key": "xi-api-key: " + "9f8e7d6c5b4a39281716",
    "token_eq": "token=" + "zzTopSecret99",
    "signature_eq": "signature=" + "0123abcd4567ef89",
    "amz": "X-Amz-Signature=" + "deadbeefcafe1234",
}


def _core(name: str, value: str) -> str:
    """Часть значения, которая не должна пережить redact."""
    if name == "bearer":
        return value.split()[1]
    if name in {"token_eq", "signature_eq", "amz"}:
        return value.split("=", 1)[1]
    if name == "xi_api_key":
        return value.split(": ", 1)[1]
    return value


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_redact_text_each_format(name):
    value = SECRETS[name]
    out = redact_text(f"ошибка при вызове {value} дальше текст")
    assert _core(name, value) not in out
    assert "дальше текст" in out


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_redact_nested_structures_and_observation_alias(name):
    core = _core(name, SECRETS[name])
    body = {"msg": [{"t": f"x {SECRETS[name]} y"}], "n": 5}
    assert core not in json.dumps(redact(body))
    assert core not in json.dumps(observation.redact(body))


def test_telegram_token_inside_bot_url():
    url = "https://api.telegram.org/bot" + SECRETS["telegram"] + "/sendMessage"
    assert SECRETS["telegram"] not in redact_text(url)


def test_credential_keys_and_plain_text_preserved():
    out = redact({"xi-api-key": "k1", "X-Amz-Signature": "s", "bot_token": "b", "max_tokens": 100,
                  "note": "see vk-test and vk-1, model gpt-5"})
    assert out["xi-api-key"] == out["X-Amz-Signature"] == out["bot_token"] == "[redacted]"
    assert out["max_tokens"] == 100
    assert out["note"] == "see vk-test and vk-1, model gpt-5"


# --- слой действий -----------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'al.db'}")
    store.Base.metadata.create_all(eng, tables=store._TABLES)
    with eng.begin() as c:
        for ddl in store._TRIGGERS:
            c.exec_driver_sql(ddl)
    yield eng
    eng.dispose()


def _dump_tables(eng) -> str:
    out = []
    with eng.connect() as c:
        for t in ("action_layer_plans", "action_layer_steps", "action_layer_history", "action_layer_macros"):
            out.append(json.dumps([list(map(str, r)) for r in c.exec_driver_sql(f"SELECT * FROM {t}")],
                                  ensure_ascii=False))
    return "\n".join(out)


def test_plan_steps_history_macros_never_store_secrets(db):
    cores = [_core(n, v) for n, v in SECRETS.items()]
    blob = " ; ".join(SECRETS.values())
    with Session(db) as s:
        s.add(store.ActionPlan(id="p1", plan_hash="h", request_text=f"сделай {blob}",
                               plan_json={"steps": [{"params": {"prompt": blob, "api_key": "KEYVALUE1"}}]},
                               error=blob, stop_reason=blob))
        s.add(store.ActionPlanStep(plan_id="p1", idx=0, action_id="a", idempotency_key="k1",
                                   params={"x": blob}, progress_note=blob, result={"r": blob}, error=blob))
        store.append_history(s, event="plan_created", plan_id="p1", request_text=blob,
                             params={"p": blob, "token": "KEYVALUE2"}, error=blob)
        s.add(store.ActionMacro(id="m1", owner="o", title_ru="t", description_ru=blob,
                                steps=[{"p": blob}], params_schema={"d": blob}))
        s.commit()
    dump = _dump_tables(db)
    for c in cores + ["KEYVALUE1", "KEYVALUE2"]:
        assert c not in dump, c
    assert "сделай" in dump


def test_plan_update_is_redacted_too(db):
    secret = _core("sk", SECRETS["sk"])
    with Session(db) as s:
        s.add(store.ActionPlan(id="p2", plan_hash="h"))
        s.commit()
        p = s.get(store.ActionPlan, "p2")
        p.error = f"упало: {secret}"
        s.commit()
    assert secret not in _dump_tables(db)


def test_history_columns_new_db_and_append_history_correlation(db):
    cols = {c["name"] for c in inspect(db).get_columns("action_layer_history")}
    assert {"project_id", "execution_id", "request_id"} <= cols
    with Session(db) as s:
        store.append_history(s, event="e", project_id=7, execution_id="ex-1", request_id="rq-1")
        s.commit()
        row = s.query(store.ActionHistory).one()
        assert (row.project_id, row.execution_id, row.request_id) == (7, "ex-1", "rq-1")


def test_ensure_columns_adds_to_old_table_idempotently(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE action_layer_history (id INTEGER PRIMARY KEY, event TEXT)")
        c.exec_driver_sql("INSERT INTO action_layer_history (event) VALUES ('old')")
        store._ensure_history_columns(c)
        store._ensure_history_columns(c)  # повтор безопасен
        names = [r[1] for r in c.exec_driver_sql("PRAGMA table_info(action_layer_history)")]
        assert {"project_id", "execution_id", "request_id"} <= set(names)
        assert c.execute(text("SELECT count(*) FROM action_layer_history")).scalar() == 1
    eng.dispose()


# --- loguru и файлы монитора ---------------------------------------------------

@pytest.fixture()
def clean_loguru():
    yield
    logger.configure(patcher=lambda r: None)


def test_global_loguru_filter_hides_secrets_in_every_sink(clean_loguru, tmp_path):
    install_log_redaction()
    buf = io.StringIO()
    path = tmp_path / "x.log"
    ids = [logger.add(buf, format="{message}", diagnose=False), logger.add(str(path), format="{message}", diagnose=False),
           logger.add(str(tmp_path / "s.jsonl"), serialize=True, diagnose=False)]
    try:
        for v in SECRETS.values():
            logger.info("ключ {}", v)
        try:
            raise RuntimeError("boom " + SECRETS["sk"])
        except RuntimeError:
            logger.opt(exception=True).error("падение")
    finally:
        for i in ids:
            logger.remove(i)
    text_all = (buf.getvalue() + path.read_text(encoding="utf-8")
                + (tmp_path / "s.jsonl").read_text(encoding="utf-8"))
    for n, v in SECRETS.items():
        assert _core(n, v) not in text_all, n


def test_log_sink_init_installs_redaction_and_files_are_clean(clean_loguru, tmp_path):
    mon = tmp_path / "monitor"
    log_sink.init(mon)
    try:
        for n, v in SECRETS.items():
            logger.warning("утечка {}", v)
            log_sink.emit_event("test_event", detail={"raw": v, "password": "PWVALUE1"})
        leaked = SECRETS["telegram"]
        try:
            raise RuntimeError("упало " + leaked)
        except RuntimeError:
            logger.opt(exception=True).error("с исключением")
        logger.complete()
    finally:
        log_sink.close()
        log_sink.remove_sinks()
    files = [p for p in mon.rglob("*") if p.is_file()]
    assert files
    blob = "".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    for n, v in SECRETS.items():
        assert _core(n, v) not in blob, n
    assert "PWVALUE1" not in blob
    assert "test_event" in blob


def test_json_format_redacts_message():
    out = log_sink._json_format({"record": {"message": "k " + SECRETS["sk"]}})
    assert SECRETS["sk"] not in out
