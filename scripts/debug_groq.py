#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from urllib import error, request

from groq import Groq


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def raw_http_probe(api_key: str, model: str, user_agent: str) -> tuple[int | None, str]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0,
    }
    req = request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": user_agent,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    ctx = ssl._create_unverified_context()
    try:
        with request.urlopen(req, timeout=30, context=ctx) as resp:
            body = resp.read().decode("utf-8", "ignore")
            return resp.status, body[:350]
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        return exc.code, body[:350]
    except Exception as exc:
        return None, str(exc)


def sdk_probe(api_key: str, model: str, user_agent: str) -> tuple[bool, str]:
    client = Groq(api_key=api_key)
    try:
        chat = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0,
            extra_headers={"User-Agent": user_agent},
        )
        text = chat.choices[0].message.content or ""
        return True, text[:120]
    except Exception as exc:
        return False, str(exc)


def main() -> None:
    load_env()

    api_key = os.getenv("GROQ_API_KEY", "")
    model = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    user_agent = os.getenv(
        "GROQ_USER_AGENT",
        (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )

    if not api_key:
        print("GROQ_API_KEY missing")
        return

    print(f"model={model}")
    print(f"user_agent={user_agent}")

    status, detail = raw_http_probe(api_key, model, user_agent)
    print(f"raw_http_status={status}")
    print(f"raw_http_detail={detail}")

    ok, detail2 = sdk_probe(api_key, model, user_agent)
    print(f"sdk_ok={ok}")
    print(f"sdk_detail={detail2}")


if __name__ == "__main__":
    main()
