from __future__ import annotations

import json
import os
import re
import ssl
from pathlib import Path
from urllib import request, error


def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def pick_key(env: dict[str, str]) -> str:
    return env.get("SUPABASE_KEY") or env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_ANON_KEY") or ""


def probe(url: str, headers: dict[str, str] | None = None) -> tuple[int | None, str]:
    ctx = ssl._create_unverified_context()
    req = request.Request(url, headers=headers or {})
    try:
        with request.urlopen(req, timeout=20, context=ctx) as resp:
            body = resp.read().decode("utf-8", "ignore")
            return resp.status, body[:200]
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        return exc.code, body[:200]
    except Exception as exc:
        return None, str(exc)


def main() -> None:
    env = load_env()
    url = env.get("SUPABASE_URL", "").rstrip("/")
    key = pick_key(env)
    groq = env.get("GROQ_API_KEY", "")

    print("== ENV VALIDATION ==")
    url_ok = bool(re.match(r"^https://[a-z0-9-]+\.supabase\.co$", url))
    print(json.dumps({
        "SUPABASE_URL_VALID": url_ok,
        "SUPABASE_KEY_PRESENT": bool(key),
        "GROQ_KEY_PRESENT": bool(groq),
        "SUPABASE_KEY_TYPE": (
            "service" if key.startswith("sb_secret_") else "publishable" if key.startswith("sb_publishable_") else "jwt_or_unknown"
        ),
    }, indent=2))

    if not url_ok or not key:
        print("\nFix SUPABASE_URL/SUPABASE_KEY before connectivity checks.")
        return

    print("\n== CONNECTIVITY CHECKS ==")
    status1, detail1 = probe(
        f"{url}/auth/v1/settings",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    print(f"supabase_auth_settings: status={status1}")
    if status1 is None or status1 >= 400:
        print(f"  detail={detail1}")

    status2, detail2 = probe(
        f"{url}/rest/v1/",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    print(f"supabase_rest_root: status={status2}")
    if status2 is None or status2 >= 400:
        print("  detail=" + detail2)
        print("  note=401 here is expected with publishable key; use service key for admin schema access.")

    if groq:
        status3, detail3 = probe(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {groq}"},
        )
        print(f"groq_models: status={status3}")
        if status3 is None or status3 >= 400:
            print(f"  detail={detail3}")
    else:
        print("groq_models: skipped (no GROQ_API_KEY)")


if __name__ == "__main__":
    main()
