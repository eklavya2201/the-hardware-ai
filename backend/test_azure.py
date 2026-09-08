"""One-off Azure auth probe. Run from `backend/`:
    .venv/Scripts/python test_azure.py
Prints what works so we can pin the right config in agent.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

KEY = os.environ.get("OPENAI_API_KEY", "")
URL = os.environ.get("OPENAI_BASE_URL", "")
MODEL = os.environ.get("OPENAI_MODEL", "")

print(f"KEY: {len(KEY)} chars, prefix {KEY[:6]!r}")
print(f"URL: {URL!r}")
print(f"MODEL (deployment): {MODEL!r}")
print()

if not (KEY and URL and MODEL):
    raise SystemExit("Missing env. Set OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL in .env.")


def attempt(name, build):
    print(f"--- {name} ---")
    try:
        client = build()
        out = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Say 'hello' in one word."}],
            max_tokens=10,
        )
        print(f"OK: {out.choices[0].message.content!r}")
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
    print()


# 1. Bare OpenAI client, Bearer auth (matches the screenshot exactly).
def _bearer_only():
    from openai import OpenAI
    return OpenAI(base_url=URL, api_key=KEY)


# 2. OpenAI client + extra api-key header (covers either Azure auth scheme).
def _both_headers():
    from openai import OpenAI
    return OpenAI(base_url=URL, api_key=KEY, default_headers={"api-key": KEY})


# 3. AzureOpenAI client (legacy path; needs api-version).
def _azure_client():
    from openai import AzureOpenAI
    endpoint = URL.split("/openai/v1")[0].split("/openai")[0].rstrip("/")
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=KEY,
        api_version=os.environ.get("OPENAI_API_VERSION", "2024-10-21"),
    )


attempt("1. OpenAI(base_url=v1) + Bearer only", _bearer_only)
attempt("2. OpenAI(base_url=v1) + Bearer + api-key", _both_headers)
attempt("3. AzureOpenAI(legacy path)", _azure_client)
