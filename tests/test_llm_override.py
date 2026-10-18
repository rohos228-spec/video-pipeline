"""Модель с канвас-ноды, не дефолт каталога."""

from __future__ import annotations

from app.models import ProjectStatus
from app.services.llm_override import bind_project_llm, current_text_model_id
from app.services.vibecode_catalog import resolve_node_choice


class _P:
    def __init__(self, meta, *, pid: int = 61):
        self.id = pid
        self.meta = meta


_GRAPH = {
    "canvas_graph": {
        "workflow_id": 1,
        "nodes": [
            {
                "id": "n_excel_gpt_sd_cd_skeleton",
                "type": "excel_gpt",
                "data": {
                    "sd_agent": "skeleton",
                    "modelId": "gpt-5.5",
                    "modelChannel": "cheap",
                },
            },
            {
                "id": "n_excel_gpt_sd_cd_camera",
                "type": "excel_gpt",
                "data": {
                    "sd_agent": "camera",
                    "modelId": "gpt-5.6-sol",
                    "modelChannel": "cheap",
                },
            },
        ],
        "edges": [],
    }
}


def test_resolve_sd_agent_excel_gpt_node_by_type() -> None:
    """Ноды веера — type=excel_gpt, не sd_agent. Ищем по маркеру."""
    choice = resolve_node_choice(_GRAPH, node_type="sd_agent")
    assert choice is not None
    assert choice["id"] == "gpt-5.5"


def test_bind_only_agent_uses_that_nodes_model() -> None:
    meta = {
        **_GRAPH,
        "scene_design": {"only_agent": "camera"},
    }
    with bind_project_llm(_P(meta), ProjectStatus.scene_designing):
        assert current_text_model_id() == "gpt-5.6-sol"


def test_bind_skeleton_click_uses_gpt_55_not_default_56() -> None:
    meta = {
        **_GRAPH,
        "scene_design": {"only_agent": "skeleton"},
    }
    with bind_project_llm(_P(meta), ProjectStatus.scene_designing):
        assert current_text_model_id() == "gpt-5.5"


def test_bind_generation_llm_always_vibecode_even_if_header_is_kie() -> None:
    from app.services.llm_override import bind_generation_llm, current_override

    with bind_generation_llm(None, node_type="image_prompts"):
        ov = current_override()
        assert ov is not None
        assert ov.kind == "text"
        assert ov.provider == "vibecode"
        assert ov.model_id == "gpt-5.6-sol"


def test_bind_generation_llm_uses_image_prompts_node_model() -> None:
    from app.services.llm_override import bind_generation_llm, current_override

    meta = {
        "canvas_graph": {
            "nodes": [
                {
                    "id": "n_img_pr",
                    "type": "image_prompts",
                    "data": {"modelId": "gpt-5.5"},
                }
            ]
        }
    }
    with bind_generation_llm(_P(meta), node_type="image_prompts"):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "vibecode"
        assert ov.model_id == "gpt-5.5"


def test_bind_generation_llm_routes_chat_to_vibecode_not_kie(
    monkeypatch, tmp_path
) -> None:
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.llm_override import bind_generation_llm
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://gpt.example.com")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("GPT_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    assert gpt_api._chat_url("gpt-5-6-sol").startswith("https://gpt.example.com")
    with bind_generation_llm(None, node_type="image_prompts"):
        assert gpt_api.is_responses_mode() is False
        assert gpt_api._chat_url("gpt-5.6-sol") == (
            "https://gpt.example.com/v1/chat/completions"
        )
        headers = gpt_api._headers()
        assert headers["Authorization"] == "Bearer vk-test"
        assert headers["X-VP-Relay-Token"] == "relay-secret"


_SFQC_GRAPH = {
    "canvas_graph": {
        "workflow_id": 1,
        "nodes": [
            {
                "id": "n_excel_gpt_fw_script",
                "type": "excel_gpt",
                "data": {
                    "groupId": "script_frames_qc",
                    "modelId": "gpt-5.6-sol",
                    "modelChannel": "stable",
                },
            },
            {
                "id": "n_excel_gpt_fw_shots",
                "type": "excel_gpt",
                "data": {
                    "groupId": "script_frames_qc",
                    "modelId": "gpt-5.6-sol",
                    "modelChannel": "stable",
                },
            },
            {
                "id": "n_excel_gpt_fw_report",
                "type": "excel_gpt",
                "data": {
                    "groupId": "script_frames_qc",
                    "modelId": "gpt-5.6-sol",
                },
            },
            {
                "id": "n_excel_gpt_1",
                "type": "excel_gpt",
                "data": {"modelId": "gpt-5.6-sol"},
            },
        ],
        "edges": [],
    }
}


def _pay52_key(monkeypatch, value: str) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "pay52_gpt_api_key", value)


def test_bind_script_frames_qc_uses_vibecode_high(monkeypatch) -> None:
    """Каркас: основной маршрут 52pay gpt-5.6-sol, мышление high."""
    from app.services.llm_override import current_override

    _pay52_key(monkeypatch, "pay52-dummy")
    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
    }
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "52pay"
        assert ov.model_id == "gpt-5.6-sol"
        assert ov.reasoning_effort == "high"
        assert current_text_model_id() == "gpt-5.6-sol"


def test_bind_script_frames_qc_forces_vibecode(monkeypatch) -> None:
    """Без ключа 52pay каркас уходит на vibecode: это только фолбэк."""
    from app.services.llm_override import current_override

    _pay52_key(monkeypatch, "")
    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
    }
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "vibecode"
        assert ov.model_id == "gpt-5.6-sol"
        assert ov.reasoning_effort == "high"


def test_bind_script_frames_qc_shots_uses_vibecode_high(monkeypatch) -> None:
    """Кадры: основной маршрут 52pay gpt-5.6-sol, мышление high."""
    from app.services.llm_override import current_override

    _pay52_key(monkeypatch, "pay52-dummy")
    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_shots",
    }
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "52pay"
        assert ov.model_id == "gpt-5.6-sol"
        assert ov.reasoning_effort == "high"
        assert current_text_model_id() == "gpt-5.6-sol"


def test_bind_script_frames_qc_report_stays_off_kie() -> None:
    from app.services.llm_override import current_override

    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_report",
    }
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "vibecode"
        assert ov.provider != "kie"


def test_bind_plain_excel_gpt_stays_vibecode() -> None:
    from app.services.llm_override import current_override

    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_1",
    }
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "vibecode"
        assert ov.model_id == "gpt-5.6-sol"


def _direct_settings(monkeypatch, tmp_path):
    """kie в шапке, relay выключен: группа всё равно идёт на vibecode.moe напрямую."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_RELAY_TOKEN", "")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("GPT_REASONING_EFFORT", "low")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    return gpt_api


def _route_script_frames_qc(monkeypatch, tmp_path, header: str, *, pay52: str) -> None:
    from app.services.llm_override import bind_project_llm, current_override
    from app.services.pay52_routes import resolve_text

    gpt_api = _direct_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(gpt_api.settings, "pay52_gpt_api_key", pay52)
    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
    }

    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None

        assert ov.model_id == "gpt-5.6-sol"
        assert ov.reasoning_effort == "high"

        assert gpt_api._node_reasoning_effort() == "high"
        if pay52:
            assert ov.provider == "52pay"
            route = resolve_text("gpt-5.6-sol")
            assert route is not None
            assert route.api_model == "gpt-5.6-sol"
            assert "52pay.com" in route.url
            assert pay52 not in route.url
        else:
            assert ov.provider == "vibecode"
            assert resolve_text("gpt-5.6-sol") is None
            assert gpt_api.is_responses_mode() is False
            assert gpt_api._chat_url("gpt-5.6-sol") == (
                "https://vibecode.moe/v1/chat/completions"
            )
    assert gpt_api._node_reasoning_effort() is None


def test_script_frames_qc_routes_chat_to_vibecode_direct_while_header_is_kie(
    monkeypatch, tmp_path
) -> None:
    """Шапка kie не перебивает основной маршрут 52pay."""
    _route_script_frames_qc(monkeypatch, tmp_path, "kie", pay52="pay52-dummy")


def test_script_frames_qc_routes_chat_to_vibecode_while_header_is_kie(
    monkeypatch, tmp_path
) -> None:
    _route_script_frames_qc(monkeypatch, tmp_path, "kie", pay52="pay52-dummy")


def test_script_frames_qc_routes_chat_to_vibecode_while_header_is_vibecode(
    monkeypatch, tmp_path
) -> None:
    _route_script_frames_qc(monkeypatch, tmp_path, "vibecode", pay52="pay52-dummy")


def test_shots_routes_chat_to_vibecode_direct_while_header_is_kie(
    monkeypatch, tmp_path
) -> None:
    from app.services.llm_override import (
        bind_project_llm,
        current_override,
        current_text_model_id,
    )
    from app.services.pay52_routes import resolve_text

    gpt_api = _direct_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(gpt_api.settings, "pay52_gpt_api_key", "pay52-dummy")
    meta = {
        **_SFQC_GRAPH,
        "active_excel_gpt_node_key": "n_excel_gpt_fw_shots",
    }
    monkeypatch.setattr(gpt_api.settings, "pay52_gpt_api_key", "pay52-test")
    with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
        ov = current_override()
        assert ov is not None
        assert ov.provider == "52pay"
        assert ov.model_id == "gpt-5.6-sol"
        assert gpt_api._node_vibecode_override() is False
        route = resolve_text("gpt-5.6-sol")
        assert route is not None and "52pay.com" in route.url
        assert "pay52-dummy" not in route.url
        assert current_text_model_id() == "gpt-5.6-sol"
        assert gpt_api._node_reasoning_effort() == "high"
    assert gpt_api._node_reasoning_effort() is None


def test_script_frames_qc_fallback_logs_warning_without_printing_key(monkeypatch) -> None:
    """Фолбэк на vibecode пишется warning с причиной и без значения ключа."""
    from loguru import logger

    from app.services.llm_override import bind_project_llm, current_override
    from app.settings import settings

    secret = "pay52-should-not-leak"
    vibe = "vk-should-not-leak"
    monkeypatch.setattr(settings, "pay52_gpt_api_key", "")
    monkeypatch.setattr(settings, "vibecode_api_key", vibe)
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        meta = {
            **_SFQC_GRAPH,
            "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
        }
        with bind_project_llm(_P(meta), ProjectStatus.enriching_1):
            ov = current_override()
            assert ov is not None
            assert ov.provider == "vibecode"
            assert ov.reasoning_effort == "high"
    finally:
        logger.remove(sink)
    text = "".join(lines)
    assert "fallback vibecode" in text
    assert "ключ 52pay для gpt-5.6-sol пуст" in text
    assert secret not in text
    assert vibe not in text


