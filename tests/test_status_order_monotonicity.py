"""Тесты математической монотонности и строгости рангов статусов в _STATUS_ORDER."""

import pytest
from app.models import ProjectStatus
from app.telegram.menu import _STATUS_ORDER, status_order


def test_status_order_pipeline_progression():
    """Проверяет строгое монотонное возрастание рангов вдоль пайплайна."""
    pipeline_chain = [
        ProjectStatus.planning,
        ProjectStatus.plan_ready,
        ProjectStatus.scripting,
        ProjectStatus.script_ready,
        ProjectStatus.splitting,
        ProjectStatus.frames_ready,
        ProjectStatus.scene_designing,
        ProjectStatus.scene_agents_ready,
        ProjectStatus.scene_assembling,
        ProjectStatus.scene_design_ready,
        ProjectStatus.generating_hero,
        ProjectStatus.hero_ready,
        ProjectStatus.generating_items,
        ProjectStatus.items_ready,
        ProjectStatus.enriching_1,
        ProjectStatus.enrich_1_ready,
        ProjectStatus.generating_image_prompts,
        ProjectStatus.image_prompts_ready,
        ProjectStatus.generating_images,
        ProjectStatus.images_ready,
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.animation_prompts_ready,
        ProjectStatus.generating_videos,
        ProjectStatus.videos_ready,
        ProjectStatus.generating_audio,
        ProjectStatus.audio_ready,
        ProjectStatus.generating_music,
        ProjectStatus.music_ready,
        ProjectStatus.sfx_planning,
        ProjectStatus.sfx_plan_ready,
        ProjectStatus.generating_sfx,
        ProjectStatus.sfx_ready,
        ProjectStatus.assembling,
        ProjectStatus.assembled,
        ProjectStatus.publishing,
        ProjectStatus.published,
    ]
    
    for i in range(len(pipeline_chain) - 1):
        curr_st = pipeline_chain[i]
        next_st = pipeline_chain[i + 1]
        assert status_order(curr_st) < status_order(next_st), (
            f"Expected rank({curr_st}) < rank({next_st}), but got "
            f"{status_order(curr_st)} >= {status_order(next_st)}"
        )


def test_sfx_and_assembled_have_distinct_ranks():
    """Проверяет, что SFX и Assembled больше не коллидируют на ранге 38."""
    assert status_order(ProjectStatus.music_ready) == 36
    assert status_order(ProjectStatus.sfx_planning) == 37
    assert status_order(ProjectStatus.sfx_plan_ready) == 38
    assert status_order(ProjectStatus.generating_sfx) == 39
    assert status_order(ProjectStatus.sfx_ready) == 40
    assert status_order(ProjectStatus.assembling) == 41
    assert status_order(ProjectStatus.assembled) == 42