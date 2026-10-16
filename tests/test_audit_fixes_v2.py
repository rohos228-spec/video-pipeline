"""Unit tests for Architecture Audit v2.1 fixes.

1. Scene span cursor tracking in db_apply (repeating start_words across scenes).
2. Clean text_llm_catalog providers (kie + vibecode only, tokenrouter excluded).
3. Status registry unification (gen_queue, node_registry, project_state).
4. SFX linear transitions and fallbacks in auto_advance.
5. SFX mixing support in variant2 montage and sfx_mix voice_gain.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import asyncio

import pytest

from app.models import ProjectStatus
from app.services.db_apply import _scene_span_in_text, expand_scene_registry_onto_frames
from app.services import step_registry
from app.services import gen_queue
from app.services import project_state
from app.orchestrator import node_registry
from app.orchestrator import auto_advance
from app.services import text_llm_catalog
from app.services import sfx_mix


def test_scene_span_cursor_with_repeating_words():
    """Проверяет, что при одинаковых start_words в разных сценах курсор находит второе вхождение."""
    full = (
        "В темной комнате сидел детектив. Часы тикали. Дверь открылась. "
        "В темной комнате сидел свидетель. Он молчал. Дверь закрылась."
    )
    # Поиск первой сцены
    s1 = _scene_span_in_text(full, "В темной комнате", "Дверь открылась.", start_offset=0)
    assert s1 is not None
    assert s1[0] == 0
    assert full[s1[0]:s1[1]] == "В темной комнате сидел детектив. Часы тикали. Дверь открылась."

    # Поиск второй сцены с курсором от конца первой сцены
    s2 = _scene_span_in_text(full, "В темной комнате", "Дверь закрылась.", start_offset=s1[1])
    assert s2 is not None
    assert s2[0] > s1[0]
    assert full[s2[0]:s2[1]] == "В темной комнате сидел свидетель. Он молчал. Дверь закрылась."


def test_expand_scene_registry_onto_frames_sequential():
    """Проверяет привязку кадров к сценам при повторяющихся start_words."""
    f1 = SimpleNamespace(attrs={}, voiceover_text="В темной комнате сидел детектив.")
    f2 = SimpleNamespace(attrs={}, voiceover_text="Часы тикали. Дверь открылась.")
    f3 = SimpleNamespace(attrs={}, voiceover_text="В темной комнате сидел свидетель.")
    f4 = SimpleNamespace(attrs={}, voiceover_text="Он молчал. Дверь закрылась.")

    frames = [f1, f2, f3, f4]
    registry = [
        {
            "id_scene": "sc_01",
            "start_words": "В темной комнате",
            "end_words": "Дверь открылась.",
            "место": "комната 1",
        },
        {
            "id_scene": "sc_02",
            "start_words": "В темной комнате",
            "end_words": "Дверь закрылась.",
            "место": "комната 2",
        },
    ]

    applied = expand_scene_registry_onto_frames(frames, registry)
    assert applied == 4
    assert f1.attrs["shot01_id_scene"] == "sc_01"
    assert f2.attrs["shot01_id_scene"] == "sc_01"
    assert f3.attrs["shot01_id_scene"] == "sc_02"
    assert f4.attrs["shot01_id_scene"] == "sc_02"
    assert f1.attrs["place"] == "комната 1"
    assert f3.attrs["place"] == "комната 2"


def test_text_llm_catalog_providers(tmp_path):
    """Проверяет, что поддерживаются только 'kie' и 'vibecode', а 'tokenrouter' полностью исключен."""
    assert text_llm_catalog._PROVIDERS == frozenset({"kie", "vibecode"})
    assert text_llm_catalog.catalog_item("kimi-k3-tokenrouter") is None

    cfg = SimpleNamespace(data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown text LLM provider"):
        text_llm_catalog.write_choice(provider="tokenrouter", cfg=cfg)

    res_vibe = text_llm_catalog.write_choice(provider="vibecode", model_id="gpt-5.6-sol-vibecode", cfg=cfg)
    assert res_vibe["provider"] == "vibecode"
    assert text_llm_catalog.resolve_active_provider(cfg) == "vibecode"

    res_kie = text_llm_catalog.write_choice(provider="kie", model_id="gpt-kie", cfg=cfg)
    assert res_kie["provider"] == "kie"
    assert text_llm_catalog.resolve_active_provider(cfg) == "kie"


def test_status_unification():
    """Проверяет синхронизацию активных статусов по всем реестрам."""
    all_running = step_registry.running_statuses()
    all_running_list = step_registry.running_statuses_list()

    # gen_queue
    assert gen_queue.GEN_QUEUE_BUSY_STATUSES == all_running_list
    assert ProjectStatus.scene_designing in gen_queue.GEN_QUEUE_BUSY_STATUSES
    assert ProjectStatus.scene_assembling in gen_queue.GEN_QUEUE_BUSY_STATUSES

    # node_registry
    assert "sfx_plan" in node_registry.LINEAR_NODE_TYPES
    assert "sfx_gen" in node_registry.LINEAR_NODE_TYPES
    idx_music = node_registry.LINEAR_NODE_TYPES.index("music")
    idx_sfx_plan = node_registry.LINEAR_NODE_TYPES.index("sfx_plan")
    idx_sfx_gen = node_registry.LINEAR_NODE_TYPES.index("sfx_gen")
    idx_assemble = node_registry.LINEAR_NODE_TYPES.index("assemble")
    assert idx_music < idx_sfx_plan < idx_sfx_gen < idx_assemble

    # project_state
    for st in all_running:
        assert project_state.is_running_status(st) is True
    assert project_state.is_running_status(ProjectStatus.plan_ready) is False


def test_auto_advance_sfx_sets_and_progression():
    """Проверяет наличие SFX в множествах линейного продвижения."""
    assert ProjectStatus.music_ready in auto_advance._LINEAR_MEDIA_READY
    assert ProjectStatus.sfx_plan_ready in auto_advance._LINEAR_MEDIA_READY
    assert ProjectStatus.sfx_ready in auto_advance._LINEAR_MEDIA_READY

    assert ProjectStatus.sfx_planning in auto_advance._LINEAR_MEDIA_RUNNING
    assert ProjectStatus.generating_sfx in auto_advance._LINEAR_MEDIA_RUNNING

    prog = auto_advance.expected_status_progression(None)
    assert ProjectStatus.sfx_planning in prog
    assert ProjectStatus.generating_sfx in prog
    assert prog.index(ProjectStatus.generating_music) < prog.index(ProjectStatus.sfx_planning)
    assert prog.index(ProjectStatus.sfx_planning) < prog.index(ProjectStatus.generating_sfx)
    assert prog.index(ProjectStatus.generating_sfx) < prog.index(ProjectStatus.assembling)


def test_sfx_mix_voice_gain(tmp_path):
    """Проверяет учет voice_gain в фильтре audio mux."""
    sfx_file = tmp_path / "click.mp3"
    sfx_file.write_bytes(b"dummy")
    sfx_input = sfx_mix.SfxInput(path=sfx_file, t_start=1.5, gain=0.6, kind="click")

    args, fc = sfx_mix.build_mux_audio_args(
        bgm_path=None,
        bgm_gain=0.0,
        output_duration=10.0,
        tail=0.0,
        sfx=[sfx_input],
        voice_gain=1.25,
    )
    assert fc is not None
    assert "volume=1.2500" in fc
    assert "adelay=1500|1500" in fc
    assert "amix=inputs=2" in fc


def test_multi_image_formats_recovery(tmp_path):
    """Проверяет корректное распознавание и восстановление .jpg, .webp и .png."""
    from app.services import artifact_recovery, reset_step

    project_dir = tmp_path / "proj"
    scenes_dir = project_dir / "scenes"
    old_scenes = project_dir / "old" / "scenes" / "20260910_120000"
    old_scenes.mkdir(parents=True)

    # Создаем кадры в разных форматах в бэкапе
    f1 = old_scenes / "frame_001_abc12345.webp"
    f2 = old_scenes / "frame_002_def67890.jpg"
    f3 = old_scenes / "frame_003_ghi11223.png"
    f1.write_bytes(b"webp data" * 20)
    f2.write_bytes(b"jpeg data" * 20)
    f3.write_bytes(b"png data" * 20)

    proj = SimpleNamespace(id=42, data_dir=project_dir)

    # Восстановление из бэкапа
    stats = artifact_recovery.restore_scene_images_from_old(proj)
    assert stats["restored"] == 3
    assert (scenes_dir / "frame_001_abc12345.webp").exists()
    assert (scenes_dir / "frame_002_def67890.jpg").exists()
    assert (scenes_dir / "frame_003_ghi11223.png").exists()

    # Бэкап перед wipe
    backed_up = reset_step._backup_scenes_before_wipe(proj, scenes_dir)
    assert backed_up == 3


@pytest.mark.asyncio
async def test_kling_download_retry(monkeypatch, tmp_path):
    """Проверяет 3 попытки скачивания видео Kling с экспоненциальной задержкой."""
    from app.bots import kie_kling
    import httpx
    import asyncio

    out_file = tmp_path / "kling_test.mp4"
    calls = 0

    class DummyResponse:
        def __init__(self, status_code, content):
            self.status_code = status_code
            self.content = content

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            nonlocal calls
            calls += 1
            if calls < 2:
                # Первый вызов падает с ошибкой соединения
                raise httpx.ConnectError("Connection reset by peer")
            # Второй вызов успешен
            return DummyResponse(200, b"kling-video-data" * 100)

    async def noop_sleep(s):
        pass

    monkeypatch.setattr(kie_kling.httpx, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(kie_kling.asyncio, "sleep", noop_sleep)

    res = await kie_kling._download("https://cdn.kling.example/video.mp4", out_file)
    assert res == out_file
    assert calls == 2
    assert out_file.read_bytes() == b"kling-video-data" * 100


@pytest.mark.asyncio
async def test_kling_download_fails_after_3_attempts(monkeypatch, tmp_path):
    """Проверяет выброс KieKlingError после 3 неудачных попыток скачивания."""
    from app.bots import kie_kling
    import httpx

    out_file = tmp_path / "kling_fail.mp4"
    calls = 0

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            nonlocal calls
            calls += 1
            raise httpx.ConnectTimeout("Timed out")

    async def noop_sleep(s):
        pass

    monkeypatch.setattr(kie_kling.httpx, "AsyncClient", FailingAsyncClient)
    monkeypatch.setattr(kie_kling.asyncio, "sleep", noop_sleep)

    with pytest.raises(kie_kling.KieKlingError, match="failed after 3 attempts"):
        await kie_kling._download("https://cdn.kling.example/video.mp4", out_file)
    assert calls == 3


@pytest.mark.asyncio
async def test_ffmpeg_timeouts(monkeypatch):
    """Проверяет выброс TimeoutError и убийство процесса при зависании FFmpeg."""
    from app.services import assembly, frame_audio
    from app.services.montage import variant2

    class HangingProc:
        def __init__(self):
            self.killed = False
            self.returncode = None

        async def communicate(self):
            await asyncio.sleep(10.0)
            return b"", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            return 0

    hanging_proc = HangingProc()

    async def mock_create_subprocess_exec(*args, **kwargs):
        return hanging_proc

    # 1. assembly._run
    monkeypatch.setattr(assembly.asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr(assembly, "probe_duration", lambda p: 1.0)
    # Wrap with a small timeout or test the internal wait_for
    # assembly._run has hardcoded 300.0, so we can monkeypatch asyncio.wait_for to raise asyncio.TimeoutError
    real_wait_for = asyncio.wait_for

    async def fast_timeout_wait_for(fut, timeout):
        fut.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(assembly.asyncio, "wait_for", fast_timeout_wait_for)
    hanging_proc.killed = False
    with pytest.raises(TimeoutError, match="ffmpeg timed out"):
        await assembly._run(["ffmpeg", "-i", "dummy.mp4"])
    assert hanging_proc.killed is True

    # 2. variant2._run
    monkeypatch.setattr(variant2.asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr(variant2.asyncio, "wait_for", fast_timeout_wait_for)
    hanging_proc.killed = False
    with pytest.raises(TimeoutError, match="ffmpeg timed out"):
        await variant2._run(["ffmpeg", "-i", "dummy.mp4"])
    assert hanging_proc.killed is True

    # 3. frame_audio._run_ffmpeg
    monkeypatch.setattr(frame_audio.asyncio, "create_subprocess_exec", mock_create_subprocess_exec)
    monkeypatch.setattr(frame_audio.asyncio, "wait_for", fast_timeout_wait_for)
    hanging_proc.killed = False
    with pytest.raises(TimeoutError, match="ffmpeg timed out"):
        await frame_audio._run_ffmpeg(["ffmpeg", "-i", "dummy.mp3"])
    assert hanging_proc.killed is True


@pytest.mark.asyncio
async def test_enhance_prompt_endpoint_with_api_gpt_client(monkeypatch):
    """Проверяет корректность очистки и возврата ответа ApiGptClient (без ошибки 're')."""
    from app.web.routers import outsee_create
    from app.web.routers.outsee_create import EnhancePromptRequest, _clean_llm_prompt_response

    # Проверка работы очистителя
    raw = "Here is the enhanced prompt: \"A glorious cyberpunk street with neon lights\"\nNote: hope you like it!"
    cleaned = _clean_llm_prompt_response(raw)
    assert cleaned == "A glorious cyberpunk street with neon lights"

    class DummyGptClient:
        async def ask_fresh(self, prompt, timeout=30):
            return "```\nHere is the enhanced prompt: A stunning cosmic nebula with radiant stars\n```"

    monkeypatch.setattr("app.services.gpt_client.gpt_text_via_api", lambda: True)
    monkeypatch.setattr("app.services.gpt_client.ApiGptClient", DummyGptClient)

    req = EnhancePromptRequest(prompt="space nebula", style="fantasy")
    res = await outsee_create.enhance_prompt_endpoint(req)
    assert res["ok"] is True
    assert res["provider"] == "llm"
    assert res["enhanced_prompt"] == "A stunning cosmic nebula with radiant stars"




