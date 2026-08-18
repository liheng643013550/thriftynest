"""Minimal LLM client for the ThriftyNest content pipeline.

Default provider: DeepSeek (very cheap). Also ships an OpenAI mapping so you
can swap models by editing config.yaml — no code changes needed.

API key resolution order:
    1. explicit api_key argument
    2. LLM_API_KEY env var
    3. DEEPSEEK_API_KEY env var (used by the GitHub Actions workflow)
    4. OPENAI_API_KEY env var
"""
import json
import os
import urllib.error
import urllib.request

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}

DEFAULT_SYSTEM = (
    "You are an expert SEO copywriter for ThriftyNest, a budget home & kitchen "
    "blog. You write practical, honest, easy-to-read US English that real "
    "people find genuinely useful. No fluff, no hype, no marketing speak."
)


class LLMError(Exception):
    """Raised when the LLM call fails for any reason."""


def complete(prompt, *, provider="deepseek", model=None, temperature=0.8,
             max_tokens=4096, system=DEFAULT_SYSTEM, api_key=None):
    api_key = (
        api_key
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise LLMError(
            "No API key found. Set the DEEPSEEK_API_KEY environment variable "
            "(see SETUP.md)."
        )

    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise LLMError("Unknown LLM provider: %r" % provider)

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": model or cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise LLMError("HTTP %s: %s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise LLMError("Network error: %s" % exc.reason) from exc

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError("Unexpected API response: %s" % json.dumps(data)[:500])
