"""Yandex Object Storage (S3-совместимый) — надёжный хост для реф-кадров Outsee/Kling.

Env:
  YANDEX_STORAGE_BUCKET
  YANDEX_STORAGE_ACCESS_KEY
  YANDEX_STORAGE_SECRET_KEY
  YANDEX_STORAGE_ENDPOINT=https://storage.yandexcloud.net
  YANDEX_STORAGE_REGION=ru-central1
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from loguru import logger

from app.settings import settings


def yandex_storage_configured() -> bool:
    return bool(
        (getattr(settings, "yandex_storage_bucket", None) or "").strip()
        and (getattr(settings, "yandex_storage_access_key", None) or "").strip()
        and (getattr(settings, "yandex_storage_secret_key", None) or "").strip()
    )


def _endpoint() -> str:
    return (
        getattr(settings, "yandex_storage_endpoint", None) or "https://storage.yandexcloud.net"
    ).strip().rstrip("/")


def _region() -> str:
    return (getattr(settings, "yandex_storage_region", None) or "ru-central1").strip() or "ru-central1"


def _bucket() -> str:
    return (settings.yandex_storage_bucket or "").strip()


def _access_key() -> str:
    return (settings.yandex_storage_access_key or "").strip()


def _secret_key() -> str:
    return (settings.yandex_storage_secret_key or "").strip()


def public_object_url(object_key: str) -> str:
    """Публичный URL объекта (бакет должен иметь чтение объектов «для всех»)."""
    key = object_key.lstrip("/")
    return f"{_endpoint()}/{_bucket()}/{key}"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _uri_encode(path: str, *, encode_slash: bool = False) -> str:
    safe = "~"
    if not encode_slash:
        safe += "/"
    return quote(path, safe=safe)


def build_put_headers(
    *,
    method: str,
    object_key: str,
    payload: bytes,
    content_type: str,
    amz_date: str | None = None,
) -> dict[str, str]:
    """AWS SigV4 headers для PutObject (path-style)."""
    now = datetime.now(timezone.utc)
    amz = amz_date or now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz[:8]
    region = _region()
    service = "s3"
    host = _endpoint().removeprefix("https://").removeprefix("http://")
    bucket = _bucket()
    key = object_key.lstrip("/")
    canonical_uri = f"/{bucket}/{_uri_encode(key)}"
    payload_hash = hashlib.sha256(payload).hexdigest()
    content_type = content_type or "application/octet-stream"

    headers_to_sign = {
        "content-type": content_type,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz,
    }
    signed_headers = ";".join(sorted(headers_to_sign))
    canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted(headers_to_sign))
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            "",  # query
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(_secret_key(), datestamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={_access_key()}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": auth,
        "Content-Type": content_type,
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz,
    }


async def upload_public_bytes(
    client: httpx.AsyncClient,
    payload: bytes,
    mime: str,
    filename: str,
) -> str:
    """Залить байты в бакет → публичный HTTPS URL."""
    if not yandex_storage_configured():
        raise RuntimeError("yandex storage: не настроен (bucket/access/secret)")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bin"):
        ext = "jpg"
    object_key = f"vp-frames/{datetime.now(timezone.utc).strftime('%Y%m%d')}/{uuid4().hex}.{ext}"
    url = public_object_url(object_key)
    headers = build_put_headers(
        method="PUT",
        object_key=object_key,
        payload=payload,
        content_type=mime or "application/octet-stream",
    )
    r = await client.put(url, content=payload, headers=headers)
    if r.status_code >= 400:
        body = (r.text or "")[:200]
        raise RuntimeError(f"yandex storage HTTP {r.status_code}: {body or '(empty)'}")
    logger.info(
        "yandex_storage: uploaded {} bytes → {}",
        len(payload),
        url[:160],
    )
    return url


def config_summary() -> dict[str, Any]:
    return {
        "configured": yandex_storage_configured(),
        "bucket": _bucket() or None,
        "endpoint": _endpoint(),
        "region": _region(),
        "access_key_set": bool(_access_key()),
        "secret_key_set": bool(_secret_key()),
    }
