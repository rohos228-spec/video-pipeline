"""Каталог моделей vibecode: только дорогой канал ×3, resolve с ноды."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.vibecode_catalog import (
    DEFAULT_IMAGE_MODEL_ID,
    DEFAULT_TEXT_MODEL_ID,
    PRICE_MARKUP,
    apply_markup,
    default_model_id_for_node_type,
    find_model,
    grouped_catalog,
    load_snapshot,
    resolve_image_generator_id,
    resolve_node_choice,
)


def test_snapshot_has_screenshot_tabs() -> None:
    ids = {m["id"] for m in load_snapshot()}
    assert "claude-sonnet-5" in ids
    assert "gpt-5.6-sol" in ids
    assert "gpt-5.6-luna" in ids
    assert "gemini-3.7-flash" in ids
    assert "deepseek-v4-pro" in ids
    assert "grok-4-5" in ids
    assert "gpt-image-2-vip" in ids
    assert "gpt-image-2" not in ids
    assert "nano-banana-2" in ids
    assert len(ids) >= 15


def test_prices_use_expensive_markup() -> None:
    raw = next(m for m in load_snapshot() if m["id"] == "gpt-5.6-sol")
    priced = apply_markup(raw["pricing"])
    assert priced["markup"] == PRICE_MARKUP
    assert priced["input_usd_per_m"] == round(raw["pricing"]["input_usd_per_m"] * 3, 6)
    assert priced["output_usd_per_m"] == round(raw["pricing"]["output_usd_per_m"] * 3, 6)
    luna = next(m for m in load_snapshot() if m["id"] == "gpt-5.6-luna")
    expensive = apply_markup(luna["pricing"])
    assert expensive["input_usd_per_m"] == round(0.026244 * 3, 6)


def test_legacy_cheap_channel_arg_is_ignored() -> None:
    raw = next(m for m in load_snapshot() if m["id"] == "claude-sonnet-5")["pricing"]
    priced = apply_markup(raw, channel="cheap")
    assert priced["markup"] == PRICE_MARKUP
    assert priced["input_usd_per_m"] == round(raw["input_usd_per_m"] * 3, 6)


def test_image_price_marked_up() -> None:
    raw = next(m for m in load_snapshot() if m["id"] == "gpt-image-2-vip")
    priced = apply_markup(raw["pricing"])
    assert priced["usd_per_image"] == round(raw["pricing"]["usd_per_image"] * 3, 6)


def test_grouped_vendors_match_ui_tabs() -> None:
    cat = grouped_catalog()
    assert cat["channel"] == "stable"
    assert cat["markup"] == PRICE_MARKUP
    ids = [v["id"] for v in cat["vendors"]]
    assert "deepseek" in ids
    assert "gemini" in ids
    assert "anthropic" in ids
    assert "xai" in ids
    counts = {v["id"]: v["count"] for v in cat["vendors"]}
    assert counts["anthropic"] == 5
    assert counts["gemini"] == 3
    assert counts["deepseek"] == 2
    assert counts["xai"] == 2
    assert counts["images"] == 7
    assert counts["video"] == 11
    assert counts["openai"] == 4
    sonnet = find_model("claude-sonnet-5")
    assert sonnet is not None
    assert sonnet["kind"] == "text"
    assert "input_usd_per_m" in sonnet["pricing"]
    banana = find_model("nano-banana-2")
    assert banana is not None
    assert banana["kind"] == "image"
    assert banana["image_generator"] == "nano_banana_2"
    assert banana["resolution"] == "1K/2K/4K"


def test_default_model_by_node_type() -> None:
    assert default_model_id_for_node_type("plan") == DEFAULT_TEXT_MODEL_ID
    assert default_model_id_for_node_type("images") == DEFAULT_IMAGE_MODEL_ID
    assert default_model_id_for_node_type("videos") == "veo-3-1-lite"
    gpt2 = find_model("gpt-image-2")
    assert gpt2 is not None
    assert gpt2["id"] == "gpt-image-2-vip"
    assert gpt2["label"] == "GPT Image 2"
    assert gpt2["image_generator"] == "gpt_image_2_vip"
    assert default_model_id_for_node_type("hero") == DEFAULT_IMAGE_MODEL_ID


class _P:
    def __init__(self, meta, image_generator="gpt_image_2"):
        self.meta = meta
        self.image_generator = image_generator


def test_resolve_text_model_from_canvas_node() -> None:
    meta = {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [
                {
                    "id": "n_plan_1",
                    "type": "plan",
                    "data": {"modelId": "claude-sonnet-5", "modelChannel": "cheap"},
                }
            ],
            "edges": [],
        }
    }
    choice = resolve_node_choice(meta, node_key="n_plan_1")
    assert choice is not None
    assert choice["id"] == "claude-sonnet-5"
    assert choice["kind"] == "text"
    assert choice["channel"] == "stable"
    assert choice["pricing"]["input_usd_per_m"] == round(0.196829 * 3, 6)


def test_resolve_image_generator_from_images_node() -> None:
    meta = {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [
                {
                    "id": "n_images_1",
                    "type": "images",
                    "data": {"modelId": "nano-banana-2", "modelChannel": "cheap"},
                }
            ],
            "edges": [],
        }
    }
    p = _P(meta, image_generator="gpt_image_2")
    assert resolve_image_generator_id(p, node_type="images") == "nano_banana_2"


def test_resolve_node_media_settings_from_images_node() -> None:
    from app.services.vibecode_catalog import resolve_node_media_settings

    meta = {
        "canvas_graph": {
            "workflow_id": 1,
            "nodes": [
                {
                    "id": "n_images_1",
                    "type": "images",
                    "data": {
                        "modelId": "gpt-image-2-vip",
                        "imageResolution": "4K",
                        "imageQuality": "high",
                        "aspectRatio": "9:16",
                    },
                }
            ],
            "edges": [],
        }
    }

    class _Proj:
        def __init__(self) -> None:
            self.meta = meta
            self.image_generator = "nano_banana"
            self.image_resolution = "1k"
            self.image_quality = "low"
            self.aspect_ratio = "16_9"

    media = resolve_node_media_settings(_Proj(), node_type="images")
    assert media["image_generator_id"] == "gpt_image_2_vip"
    assert media["resolution_id"] == "4k"
    assert media["resolution_slug"] == "4K"
    assert media["aspect_slug"] == "9:16"
    assert media["quality_slug"] == "Высокое"


def test_frontend_picker_wired() -> None:
    node = Path("web/src/components/canvas/pipeline-node.tsx").read_text(encoding="utf-8")
    assert "NodeModelPicker" in node
    picker = Path("web/src/components/canvas/node-model-picker.tsx").read_text(
        encoding="utf-8"
    )
    assert "Дешёвый канал" not in picker
    assert "Стабильный канал" not in picker
    assert "catalog_channels" not in picker
    assert "вход" in picker
    assert "выход" in picker
    # Вкладки вендоров: не сбрасывать setVendor из‑за нового catalog каждый рендер
    assert "[open, selectedId]" in picker
    assert "[open, selectedId, catalog]" not in picker
    assert "Соотношение" in picker
    assert "MiniMenu" in picker
    media_opts = Path("web/src/lib/node-media-options.ts").read_text(encoding="utf-8")
    assert "mediaOptionsForModel" in media_opts
    assert "мало" in media_opts
    assert "максимум" in media_opts
    assert "1:1" in media_opts and "21:9" in media_opts and "5:4" in media_opts
    assert "veo-3-1-lite" in media_opts and "kling-2-6" in media_opts
    settings = Path("web/src/components/inspector/project-settings.tsx").read_text(
        encoding="utf-8"
    )
    assert "GenerationModelsPanel" not in settings
    merge = Path("web/src/lib/canvas-node-merge.ts").read_text(encoding="utf-8")
    assert "modelId: n.data.modelId ?? old.data.modelId" in merge
    assert "aspectRatio" in merge
    serialize = Path("web/src/lib/workflow-node-serialize.ts").read_text(encoding="utf-8")
    assert "data.modelId" in serialize
    assert "imageResolution" in serialize
    catalog_ts = Path("web/src/lib/node-model-catalog.ts").read_text(encoding="utf-8")
    assert "PRICE_MARKUP = 3" in catalog_ts
    assert "PRICE_MARKUP_CHEAP" not in catalog_ts
    snap = Path("web/src/lib/vibecode-models-snapshot.ts").read_text(encoding="utf-8")
    assert "GPT Image 2 SLOW" not in snap
    assert "GPT Image 2 FAST" not in snap
    assert '"gpt-image-2-vip"' in snap
    streams = Path("web/src/components/inspector/streams-panel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Картинки + Видео" in streams
    assert "Текстовые модели" in streams
    assert "Проверка GPT · проект" not in streams
    assert "Outsee (img + video)" not in streams
    assert "PRICE_MARKUP_CHEAP" not in catalog_ts
    pipeline = Path("app/orchestrator/pipeline.py").read_text(encoding="utf-8")
    assert "bind_project_llm" in pipeline


def test_node_override_routes_chat_to_vibecode(monkeypatch, tmp_path: Path) -> None:
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.llm_override import NodeLlmOverride, use_override
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    ov = NodeLlmOverride(
        model_id="claude-sonnet-5",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="Claude Sonnet 5",
    )
    with use_override(ov):
        assert gpt_api.is_responses_mode() is False
        assert gpt_api._chat_url("claude-sonnet-5") == (
            "https://vibecode.moe/v1/chat/completions"
        )
        assert gpt_api._headers()["Authorization"] == "Bearer vk-test"
    assert gpt_api.is_responses_mode() is True


def test_node_override_vibecode_bypasses_vps_relay(
    monkeypatch, tmp_path: Path
) -> None:
    """Node vibecode must not use GPT VPS (stale Caddy = kie 401 envelope)."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.llm_override import NodeLlmOverride, use_override
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://gpt.example.com")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("GPT_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("GPT_PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    assert s.vps_relay_base_url == "https://gpt.example.com"
    assert gpt_api._gpt_proxy_url() is None
    assert gpt_api._chat_url("gpt-5-6-sol") == (
        "https://gpt.example.com/codex/v1/responses"
    )

    ov = NodeLlmOverride(
        model_id="claude-sonnet-5",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="Claude Sonnet 5",
    )
    with use_override(ov):
        assert gpt_api._chat_url("claude-sonnet-5") == (
            "https://vibecode.moe/v1/chat/completions"
        )
        assert gpt_api._headers()["Authorization"] == "Bearer vk-test"
        assert "gpt.example.com" not in gpt_api._chat_url("claude-sonnet-5")


def test_node_override_empty_vibecode_key_does_not_steal_kie_key(
    monkeypatch, tmp_path: Path
) -> None:
    """Без VIBECODE_API_KEY нода не должна слать kie-ключ на /v1/chat/completions."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.gpt_api import GptApiError
    from app.services.llm_override import NodeLlmOverride, use_override
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("VIBECODE_API_KEY", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    object.__setattr__(s, "vibecode_api_key", "")
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    ov = NodeLlmOverride(
        model_id="gpt-5.6-sol",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="GPT 5.6 Sol",
    )
    with use_override(ov):
        with pytest.raises(GptApiError, match="VIBECODE_API_KEY") as ei:
            gpt_api._headers()
        assert "kie-key" not in str(ei.value)


@pytest.mark.asyncio
async def test_node_gpt55_uses_completions_stream_when_header_is_kie(
    monkeypatch, tmp_path: Path
) -> None:
    """Шапка kie, на ноде GPT 5.5 — stream chat/completions, не Responses и не non-stream."""
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod
    from app.services.gpt_api import GptChatResult
    from app.services.llm_override import NodeLlmOverride, use_override
    from app.settings import Settings

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    called: dict[str, str] = {}

    async def fake_stream(**kwargs):
        called["path"] = "completions_stream"
        called["model"] = str(kwargs.get("use_model") or "")
        called["url"] = str(kwargs.get("url") or "")
        body = kwargs.get("body") or {}
        called["has_messages"] = "messages" in body
        return GptChatResult(
            text="ok-from-stream",
            model=called["model"],
            finish_reason="stop",
            usage={},
            raw={},
        )

    async def fake_responses(**kwargs):
        raise AssertionError("node GPT 5.5 must not call kie Responses API")

    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("node GPT 5.5 must not use non-stream POST")

    monkeypatch.setattr(gpt_api, "_chat_completions_stream", fake_stream)
    monkeypatch.setattr(gpt_api, "_chat_responses_stream", fake_responses)
    monkeypatch.setattr(gpt_api, "_async_client", lambda **kwargs: _BoomClient())

    ov = NodeLlmOverride(
        model_id="gpt-5.5",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="GPT 5.5",
    )
    with use_override(ov):
        result = await gpt_api.chat(
            prompt="ping", timeout=5, max_retries=0, auto_pack=False
        )
    assert called.get("path") == "completions_stream"
    assert called.get("model") == "gpt-5.5"
    assert "/v1/chat/completions" in (called.get("url") or "")
    assert called.get("has_messages") is True
    assert result.text == "ok-from-stream"


@pytest.mark.asyncio
async def test_catalog_api_returns_marked_up_prices() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.web.api import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/text-llm/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["channel"] == "stable"
    assert body["markup"] == 3.0
    assert "catalog_channels" not in body
    vendors = {v["id"]: v for v in body["vendors"]}
    assert vendors["anthropic"]["count"] == 5
    luna = next(m for m in body["models"] if m["id"] == "gpt-5.6-luna")
    assert luna["pricing"]["input_usd_per_m"] == round(0.026244 * 3, 6)
