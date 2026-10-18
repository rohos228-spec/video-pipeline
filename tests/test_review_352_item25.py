"""№25: Authorization curl-generate не попадает в argv процесса."""

from __future__ import annotations

from app.bots.outsee_http import curl_post_argv, write_curl_header_file


def test_curl_post_argv_keeps_authorization_in_header_file(tmp_path) -> None:
    secret = "Bearer unit-test-token"
    header = write_curl_header_file(
        {
            "Authorization": secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    try:
        body = tmp_path / "body.json"
        body.write_text("{}", encoding="utf-8")
        argv = curl_post_argv(
            curl="curl",
            url="https://example.test/generate",
            header_file=header,
            body_file=body,
            connect_timeout=5,
            max_time=30,
        )
        joined = "\n".join(argv)
        assert secret not in joined
        assert "Authorization:" not in joined
        assert "Bearer" not in joined
        assert f"@{header}" in argv
        mode = header.stat().st_mode & 0o777
        assert mode == 0o600
        stored = header.read_text(encoding="utf-8")
        assert secret in stored
        assert "Content-Type: application/json" in stored
    finally:
        header.unlink(missing_ok=True)
