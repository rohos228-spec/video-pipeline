import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from app.services import prompt_library as pl, frame_style
from app.web.routers import prompt_files as routes


@pytest.fixture
def style_library(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_style, 'styles_dir', lambda: tmp_path / 'styles')
    return tmp_path / 'styles'


def test_style_library_is_exact_runtime_folder(style_library):
    pl.write_prompt('frame_style', 'style_custom', 'My custom style')
    assert frame_style.load_style_text('style_custom') == 'My custom style'
    assert pl.list_prompts('frame_style') == ['style_custom']
    assert pl.read_prompt('frame_style', 'style_custom') == 'My custom style'


@pytest.mark.asyncio
async def test_upload_style_without_prefix_is_selectable_at_runtime(style_library, monkeypatch):
    monkeypatch.setattr(routes.lib, 'create_or_update_item', AsyncMock())
    from app.services import prompts
    monkeypatch.setattr(prompts, 'sync_step_prompt_to_db', AsyncMock())
    session = SimpleNamespace(commit=AsyncMock())
    upload = UploadFile(filename='my_look.md', file=io.BytesIO(b'Custom ink rendering'))
    info = await routes.upload_prompt_file('frame_style', upload, name=None, session=session)
    assert info.name == 'style_my_look'
    assert frame_style.load_style_text(info.name) == 'Custom ink rendering'
    names = await routes.list_prompt_files('frame_style', group_id=None)
    assert [row.name for row in names] == ['style_my_look']
