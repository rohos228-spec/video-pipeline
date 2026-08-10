"""Yandex Object Storage SigV4 / config helpers."""

from __future__ import annotations

from app.bots import yandex_storage as ys


def test_yandex_not_configured_without_env(monkeypatch) -> None:
    monkeypatch.setattr(ys.settings, "yandex_storage_bucket", "")
    monkeypatch.setattr(ys.settings, "yandex_storage_access_key", "")
    monkeypatch.setattr(ys.settings, "yandex_storage_secret_key", "")
    assert ys.yandex_storage_configured() is False


def test_build_put_headers_stable(monkeypatch) -> None:
    monkeypatch.setattr(ys.settings, "yandex_storage_bucket", "test2videogen")
    monkeypatch.setattr(ys.settings, "yandex_storage_access_key", "AKIAEXAMPLE")
    monkeypatch.setattr(ys.settings, "yandex_storage_secret_key", "secretsecretsecret")
    monkeypatch.setattr(ys.settings, "yandex_storage_endpoint", "https://storage.yandexcloud.net")
    monkeypatch.setattr(ys.settings, "yandex_storage_region", "ru-central1")

    headers = ys.build_put_headers(
        method="PUT",
        object_key="vp-frames/x.jpg",
        payload=b"hello",
        content_type="image/jpeg",
        amz_date="20260810T120000Z",
    )
    assert headers["Host"] == "storage.yandexcloud.net"
    assert headers["x-amz-date"] == "20260810T120000Z"
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert "SignedHeaders=" in headers["Authorization"]
    assert "Signature=" in headers["Authorization"]
    assert ys.public_object_url("vp-frames/x.jpg") == (
        "https://storage.yandexcloud.net/test2videogen/vp-frames/x.jpg"
    )
