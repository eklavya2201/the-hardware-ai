"""Bring-your-own-model (Prompt to Product, 2026-09).

Two ways to use Synqit: on the subscription, the server's own key/model
(`OPENAI_*` env — unchanged behaviour); or with the user's own key for
OpenAI, Azure, Google Gemini, Anthropic, or any OpenAI-compatible endpoint.
Every provider is reached through the OpenAI client with a `base_url`, so
the agent loop, the critic, firmware and the build guide need no
provider-specific code — just a resolved `LlmRuntime` handed down from the
endpoint that knows which user is calling.

The key is stored encrypted (Fernet, key derived from SESSION_SECRET) and is
never returned to the browser — only a "set" flag and the last four chars.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

Provider = Literal["synqit", "openai", "azure", "google", "anthropic", "custom"]

# Where each provider's OpenAI-compatible endpoint lives. Azure and custom
# endpoints are user-supplied.
PRESET_BASE_URL: dict[str, str | None] = {
    # Explicit on purpose: with base_url=None the SDK reads OPENAI_BASE_URL from
    # the server env, which on prod points at Azure — a user's OpenAI key would
    # be sent to the wrong host (seen in the first live test, 2026-09-06).
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "anthropic": "https://api.anthropic.com/v1/",
}

# Suggested models per provider — a datalist in the UI, not a whitelist: the
# user may type any model id their key can reach.
SUGGESTED_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.4", "gpt-5.1"],
    "azure": ["gpt-6-astra", "gpt-5.1", "gpt-5.4"],
    "google": ["gemini-3.8-pro", "gemini-3.8-flash", "gemini-2.5-pro"],
    "anthropic": ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5"],
    "custom": [],
}


class LlmSettingsIn(BaseModel):
    provider: Provider = "synqit"
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    api_version: str | None = Field(default=None, max_length=40)   # Azure only
    api_key: str | None = Field(default=None, max_length=500)       # write-only; None = keep the stored one


class LlmSettingsOut(BaseModel):
    provider: Provider = "synqit"
    model: str | None = None
    base_url: str | None = None
    api_version: str | None = None
    key_set: bool = False
    key_hint: str | None = None          # last 4 characters, never the key
    server_model: str | None = None      # what the subscription path uses
    suggested_models: dict[str, list[str]] = Field(default_factory=lambda: dict(SUGGESTED_MODELS))


@dataclass(frozen=True)
class LlmRuntime:
    """What a service needs to make a call: resolved from the user's settings
    or from the server env. `provider` lets a service drop features an
    endpoint lacks (Anthropic's compatibility layer ignores response_format)."""
    provider: str
    api_key: str | None
    base_url: str | None
    model: str
    api_version: str | None = None

    @property
    def supports_json_mode(self) -> bool:
        return self.provider not in ("anthropic",)


# --- encryption ---------------------------------------------------------------


def _fernet() -> Fernet:
    secret = os.environ.get("SESSION_SECRET", "dev-only-do-not-use-in-prod")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_key(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # SESSION_SECRET rotated: the stored key is unreadable. Fall back to the
        # server path rather than failing every call; the user re-enters it.
        return None


# --- resolution -----------------------------------------------------------------


def server_runtime() -> LlmRuntime:
    """The subscription path: the server's own env, exactly as before."""
    return LlmRuntime(
        provider="synqit",
        api_key=os.environ.get("OPENAI_API_KEY") or None,
        base_url=(os.environ.get("OPENAI_BASE_URL") or "").strip() or None,
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        api_version=os.environ.get("OPENAI_API_VERSION") or None,
    )


def runtime_for_user(user) -> LlmRuntime:
    """Resolve a user's stored settings into a runtime. Any gap — no key, no
    model, provider 'synqit' — means the server path."""
    provider = (getattr(user, "llm_provider", None) or "synqit") if user is not None else "synqit"
    if provider == "synqit":
        base = server_runtime()
        model = (getattr(user, "llm_model", None) or "").strip() if user is not None else ""
        if model and model in allowed_server_models():
            return LlmRuntime(base.provider, base.api_key, base.base_url, model, base.api_version)
        return base
    key = decrypt_key(getattr(user, "llm_api_key_enc", None))
    model = (getattr(user, "llm_model", None) or "").strip()
    if not key or not model:
        return server_runtime()
    base_url = (getattr(user, "llm_base_url", None) or "").strip() or PRESET_BASE_URL.get(provider)
    return LlmRuntime(
        provider=provider,
        api_key=key,
        base_url=base_url,
        model=model,
        api_version=(getattr(user, "llm_api_version", None) or "").strip() or None,
    )


def allowed_server_models() -> list[str]:
    """Models a subscription user may pick from (env SYNQIT_MODELS, comma
    separated). Defaults to just the server's OPENAI_MODEL."""
    raw = os.environ.get("SYNQIT_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    default = os.environ.get("OPENAI_MODEL", "gpt-4o")
    if default not in models:
        models.insert(0, default)
    return models


def settings_out(user) -> LlmSettingsOut:
    enc = getattr(user, "llm_api_key_enc", None)
    key = decrypt_key(enc)
    return LlmSettingsOut(
        provider=getattr(user, "llm_provider", None) or "synqit",
        model=getattr(user, "llm_model", None),
        base_url=getattr(user, "llm_base_url", None),
        api_version=getattr(user, "llm_api_version", None),
        key_set=bool(key),
        key_hint=key[-4:] if key and len(key) >= 8 else None,
        server_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        suggested_models={**SUGGESTED_MODELS, "synqit": allowed_server_models()},
    )


# --- client factory -----------------------------------------------------------------


def make_client(rt: LlmRuntime):
    """One OpenAI-SDK client for any provider. Azure keeps its own client class
    (deployment-name URLs + `api-key` header); everything else is `base_url`."""
    from openai import AzureOpenAI, OpenAI

    base_url = rt.base_url
    is_azure = rt.provider == "azure" or bool(
        base_url
        and (
            ".openai.azure.com" in base_url
            or ".services.ai.azure.com" in base_url
            or ".cognitiveservices.azure.com" in base_url
        )
    )
    if is_azure and base_url and "/openai/v1" in base_url:
        cleaned = base_url.rstrip("/")
        for suffix in ("/responses", "/chat/completions", "/completions", "/embeddings"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        return OpenAI(base_url=cleaned, api_key=rt.api_key,
                      default_headers={"api-key": rt.api_key} if rt.api_key else None)
    if is_azure:
        endpoint = (base_url or "").rstrip("/")
        if endpoint.endswith("/openai"):
            endpoint = endpoint[: -len("/openai")]
        return AzureOpenAI(azure_endpoint=endpoint, api_key=rt.api_key,
                           api_version=rt.api_version or "2024-10-21")
    if base_url:
        return OpenAI(base_url=base_url, api_key=rt.api_key)
    return OpenAI(api_key=rt.api_key)


def test_runtime(rt: LlmRuntime) -> tuple[bool, str]:
    """One tiny completion to prove the key, endpoint and model line up."""
    if not rt.api_key:
        return False, "No API key."
    try:
        client = make_client(rt)
        resp = client.chat.completions.create(
            model=rt.model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            # A reasoning model spends its budget thinking before it writes, so a
            # tight cap comes back with an empty message even though the call
            # worked (gpt-5.1 answered "(empty)" at 8 tokens, 2026-09-06).
            max_completion_tokens=256,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return True, f"{rt.provider} · {rt.model} responded (no text — reasoning model), the key and model work."
        return True, f"{rt.provider} · {rt.model} answered: {text[:40]}"
    except Exception as exc:  # noqa: BLE001 — the message IS the diagnosis
        msg = str(exc)
        return False, msg[:300]
