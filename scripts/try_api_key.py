"""One-off: test ElevenLabs API key from argv or env."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "try-api-key-result.txt"


def read_env(name: str) -> str:
    p = ROOT / ".env"
    if not p.is_file():
        return ""
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def try_key(label: str, key: str, proxy: str | None) -> dict:
    import requests

    proxies = {"http": proxy, "https": proxy} if proxy else None
    h = {"xi-api-key": key}
    result: dict = {"label": label, "key_hint": f"{key[:8]}…" if len(key) > 10 else "?"}
    for path in ("/user", "/voices"):
        try:
            r = requests.get(
                f"https://api.elevenlabs.io/v1{path}",
                headers=h,
                proxies=proxies,
                timeout=(20, 45),
            )
            result[f"{path}_status"] = r.status_code
            if r.status_code >= 400:
                result[f"{path}_body"] = r.text[:400]
                continue
            d = r.json()
            if path == "/user":
                sub = d.get("subscription") or {}
                result["user_id"] = d.get("user_id")
                result["tier"] = sub.get("tier")
                result["status"] = sub.get("status")
                result["ivc"] = sub.get("can_use_instant_voice_cloning")
                result["pvc"] = sub.get("can_use_professional_voice_cloning")
                result["api_key_preview"] = d.get("xi_api_key_preview")
            else:
                result["voice_count"] = len(d.get("voices") or [])
        except Exception as exc:
            result[f"{path}_error"] = str(exc)
    return result


def main() -> int:
    raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not raw:
        print("usage: try_api_key.py <key>")
        return 1
    proxy = read_env("ELEVENLABS_PROXY_URL") or None
    variants: list[tuple[str, str]] = []
    if raw.startswith("sk_"):
        variants.append(("as_given", raw))
    else:
        variants.append(("raw", raw))
        variants.append(("sk_prefix", f"sk_{raw}"))

    lines = [f"proxy={proxy or 'none'}"]
    ok = False
    for label, key in variants:
        r = try_key(label, key, proxy)
        lines.append(json.dumps(r, ensure_ascii=False, indent=2))
        if r.get("/user_status") == 200 and r.get("ivc") is True:
            ok = True
        if r.get("/voices_status") == 200 and r.get("/user_status") == 200:
            ok = True

    text = "\n\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
