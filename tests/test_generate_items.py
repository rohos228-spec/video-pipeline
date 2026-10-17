import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from app.models import Artifact, ArtifactKind, Entity, Project, ProjectStatus
from app.orchestrator.steps.generate_items import (
    _need_browser_session,
    _existing_item_indices,
    _resolve_item_descriptions,
    run,
)


def test_need_browser_session_kie_and_outsee():
    # KIE models should never need CDP browser
    assert _need_browser_session('flux-2-pro') is False
    assert _need_browser_session('seedream-5-pro') is False

    # Outsee with API key configured
    with patch('app.orchestrator.steps.generate_items.outsee_api_configured', return_value=True):
        assert _need_browser_session('nano-banana-2') is False

    # Outsee without API key configured (needs browser)
    with patch('app.orchestrator.steps.generate_items.outsee_api_configured', return_value=False), \
         patch('app.orchestrator.steps.generate_items.outsee_api_enabled_for_image', return_value=False), \
         patch('app.orchestrator.steps.generate_items.image_provider_for', return_value='outsee'):
        assert _need_browser_session('unknown-outsee-model') is True


@pytest.mark.asyncio
async def test_resolve_item_descriptions_from_project():
    session = AsyncMock()
    project = Project(id=1, item_descriptions=['  Меч Императора  ', 'Болтер'])
    descs = await _resolve_item_descriptions(session, project)
    assert descs == ['Меч Императора', 'Болтер']


@pytest.mark.asyncio
async def test_resolve_item_descriptions_from_entity():
    session = AsyncMock()
    project = Project(id=1, item_descriptions=[])
    
    e1 = Entity(id=10, project_id=1, type='prop', name='Реликварий', attrs={'description': 'Древний золотой ларец'})
    e2 = Entity(id=11, project_id=1, type='item', name='Штандарт', attrs={})
    
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [e1, e2]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result
    
    descs = await _resolve_item_descriptions(session, project)
    assert len(descs) == 2
    assert 'Реликварий: Древний золотой ларец' in descs
    assert 'Штандарт' in descs
    assert project.item_descriptions == descs


@pytest.mark.asyncio
async def test_existing_item_indices_artifact_and_disk(tmp_path: Path):
    session = AsyncMock()
    project = Project(id=1, slug="test-slug")
    
    # Mock DB artifacts
    a1 = Artifact(id=1, project_id=1, kind=ArtifactKind.item_reference, meta={'item_index': 1})
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [a1]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result
    
    # Mock disk files
    items_dir = tmp_path / 'items'
    items_dir.mkdir()
    disk_file = items_dir / 'predmet2_abcdef12.png'
    disk_file.write_bytes(b'x' * 2000)
    
    with patch('app.orchestrator.steps.generate_items._project_data_dir', return_value=tmp_path):
        indices = await _existing_item_indices(session, project)
    assert 1 in indices
    assert 2 in indices


@pytest.mark.asyncio
async def test_run_empty_descriptions_instant_ready():
    session = AsyncMock()
    project = Project(id=1, status=ProjectStatus.generating_items, item_descriptions=[])
    bot = AsyncMock()
    
    # DB entity search returns empty
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result
    
    await run(session, project, bot)
    assert project.status == ProjectStatus.items_ready
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_run_with_item_generates_and_syncs_master(tmp_path: Path):
    session = AsyncMock()
    project = Project(id=42, status=ProjectStatus.generating_items, slug="warhammer", item_descriptions=["Силовой меч"])
    bot = AsyncMock()
    
    # Existing artifacts: empty
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result
    
    mock_gen_result = MagicMock()
    mock_gen_result.file_path = tmp_path / "items" / "predmet1_12345678.png"
    
    mock_master_sess = AsyncMock()
    mock_master_ctx = AsyncMock()
    mock_master_ctx.__aenter__.return_value = mock_master_sess
    
    with patch("app.orchestrator.steps.generate_items._project_data_dir", return_value=tmp_path), \
         patch("app.orchestrator.steps.generate_items.generate_image_with_retries", new_callable=AsyncMock) as mock_gen, \
         patch("app.orchestrator.steps.generate_items.SessionLocal", return_value=mock_master_ctx):
        mock_gen.return_value = mock_gen_result
        await run(session, project, bot)
        
        assert project.status == ProjectStatus.items_ready
        mock_gen.assert_awaited_once()
        _, kwargs = mock_gen.call_args
        assert kwargs["project_id"] == 42
        assert kwargs["aspect_ratio"] == "16:9"
        
        # Artifact added to session
        assert session.add.called
        added_art = session.add.call_args[0][0]
        assert added_art.kind == ArtifactKind.item_reference
        assert added_art.meta["item_index"] == 1
        
        # Artifact synced to master_sess
        assert mock_master_sess.add.called
        assert mock_master_sess.commit.called
