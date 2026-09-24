"""Standalone policy copied into the pinned Desktop adapter (stdlib only).

The adapter is always managed. It cannot silently fall back to another provider
when launched without the starter's ephemeral relay environment.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit


def session():
    base = os.environ.get("MYHERMES_DESKTOP_RELAY_URL", "")
    token = os.environ.get("AUXILIARY_MYHERMES_API_KEY", "")
    home = os.environ.get("MYHERMES_DESKTOP_HOME", "")
    url = urlsplit(base)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.path != "/v1"
        or url.query
        or url.fragment
        or url.username
        or url.password
        or not token
        or not home
        or os.environ.get("HERMES_HOME") != home
        or not os.environ.get("HERMES_MANAGED_DIR")
    ):
        raise ValueError("Start this Desktop with myhermes desktop.")
    return base, token, home


def inventory():
    session()
    return {
        "provider": "myhermes",
        "model": "economy",
        "providers": [
            {
                "slug": "myhermes",
                "name": "MyHermes",
                "models": ["economy"],
                "total_models": 1,
                "is_current": True,
                "is_user_defined": True,
                "authenticated": True,
                "configured": True,
            }
        ],
    }


def resolve(*, requested=None, explicit_api_key=None, explicit_base_url=None, target_model=None):
    base, token, _ = session()
    if (
        (requested or "myhermes").strip().lower() not in ("myhermes", "auto")
        or (target_model or "economy") != "economy"
        or explicit_base_url not in (None, "", base)
        or explicit_api_key not in (None, "", token)
    ):
        raise ValueError("MyHermes Desktop allows only MyHermes / economy.")
    return {
        "provider": "myhermes",
        "requested_provider": "myhermes",
        "model": "economy",
        "api_mode": "chat_completions",
        "base_url": base,
        "api_key": token,
        "source": "myhermes",
    }


def assignment(scope, provider, model, task, base_url, api_key):
    # Do not persist the session relay URL/key into owner config.
    from fastapi import HTTPException

    try:
        resolve(requested=provider, target_model=model, explicit_base_url=base_url, explicit_api_key=api_key)
    except ValueError:
        raise HTTPException(403, "MyHermes manages the inference provider and model.") from None
    return {"ok": True, "scope": scope, "provider": "myhermes", "model": "economy", "tasks": [task]}


def auxiliary(provider, model, async_mode, base_url, api_key, api_mode, raw_codex):
    from openai import AsyncOpenAI, OpenAI

    runtime = resolve(requested=provider, target_model=model, explicit_base_url=base_url, explicit_api_key=api_key)
    if raw_codex or api_mode not in (None, "", "chat_completions"):
        raise ValueError("MyHermes Desktop uses chat completions.")
    client = AsyncOpenAI if async_mode else OpenAI
    return client(base_url=runtime["base_url"], api_key=runtime["api_key"], max_retries=0), "economy"


def auxiliary_route(provider, model, base_url, api_key):
    runtime = resolve(requested=provider, target_model=model, explicit_base_url=base_url, explicit_api_key=api_key)
    return "myhermes", "economy", runtime["base_url"], runtime["api_key"], "chat_completions"


def guard_profile(name):
    session()
    if name not in (None, "", "default", "current"):
        raise ValueError("MyHermes Desktop uses the enrolled home only.")


def profile_home(name):
    guard_profile(name)
    return Path(session()[2])


def guard_home(path):
    _, _, home = session()
    if path is not None and Path(path).resolve() != Path(home).resolve():
        raise ValueError("MyHermes Desktop uses the enrolled home only.")
