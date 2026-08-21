"""Тесты retry-логики commit_with_retry при блокировке SQLite (database is locked)."""

import pytest
from unittest.mock import AsyncMock
from sqlalchemy.exc import OperationalError
from app.db import commit_with_retry


@pytest.mark.asyncio
async def test_commit_with_retry_succeeds_after_transient_locked():
    """Проверяет, что commit_with_retry выдерживает временную блокировку базы и коммитит."""
    mock_session = AsyncMock()
    call_count = 0

    async def fake_commit():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise OperationalError("commit", {}, Exception("database is locked"))
        return None

    mock_session.commit.side_effect = fake_commit

    await commit_with_retry(mock_session, max_retries=5, base_delay=0.01)
    assert call_count == 3


@pytest.mark.asyncio
async def test_commit_with_retry_raises_after_exceeding_max_retries():
    """Проверяет, что при постоянной блокировке commit_with_retry вызывает rollback и рейзит ошибку."""
    mock_session = AsyncMock()
    mock_session.commit.side_effect = OperationalError("commit", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        await commit_with_retry(mock_session, max_retries=3, base_delay=0.01)

    assert mock_session.rollback.called