"""Unit tests verifying outsee ref image hosting fallback works when Yandex is unconfigured."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.bots.outsee_http import ensure_public_image_url


@pytest.mark.asyncio
async def test_ensure_public_image_url_uses_fallback_when_yandex_not_configured(monkeypatch) -> None:
    """Ensure ensure_public_image_url falls back to litterbox/catbox without raising unconfigured error."""
    from app.settings import settings
    monkeypatch.setattr(settings, "yandex_storage_bucket", "")
    monkeypatch.setattr(settings, "yandex_storage_access_key", "")
    monkeypatch.setattr(settings, "yandex_storage_secret_key", "")

    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    with patch("app.bots.outsee_http._host_via_litterbox", new_callable=AsyncMock) as mock_litterbox:
        mock_litterbox.return_value = "https://litterbox.catbox.moe/files/test_frame.png"
        
        hosted = await ensure_public_image_url(data_url)
        assert hosted == "https://litterbox.catbox.moe/files/test_frame.png"
        assert mock_litterbox.called