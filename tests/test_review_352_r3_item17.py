"""Ревью #352 п.17: ссылка сброса пароля не берётся из Origin."""

from app.web.routers.auth import reset_link_base


def test_reset_base_ignores_client_origin(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "fleet_public_url", "https://studio.example")
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "web_port", 8765)
    assert reset_link_base() == "https://studio.example"
    monkeypatch.setattr(settings, "fleet_public_url", "")
    assert reset_link_base() == "http://127.0.0.1:8765"
    assert "evil.example" not in reset_link_base()
