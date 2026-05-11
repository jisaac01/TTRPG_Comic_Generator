"""LLM client factory.

Routing rules:
  - Model names starting with "gemini-" → Google Gemini via its OpenAI-compatible endpoint.
    Requires GEMINI_API_KEY to be set in the environment.
  - All other model names → Ollama.
    OLLAMA_BASE_URL defaults to http://localhost:11434/v1.
    OLLAMA_API_KEY defaults to "ollama" (Ollama ignores the key).
"""

from __future__ import annotations

import os
from pathlib import Path


_ENV_LOADED = False


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_local_env_once() -> None:
    """Load .env from current working directory once, without overriding existing env vars."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    env_path = Path(".env")
    if not env_path.exists():
        _ENV_LOADED = True
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_matching_quotes(value.strip())
        if not key:
            continue
        os.environ.setdefault(key, value)

    _ENV_LOADED = True


def _is_gemini(model: str) -> bool:
    return model.startswith("gemini-")


def build_openai_client(model: str):
    """Return a raw openai.OpenAI client configured for the given model."""
    from openai import OpenAI

    _load_local_env_once()

    if _is_gemini(model):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable is not set. "
                "Set it before using a Gemini model."
            )
        return OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key,
        )

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    return OpenAI(base_url=base_url, api_key=api_key)


def build_instructor_client(model: str):
    """Return an instructor-patched client configured for the given model."""
    import instructor

    openai_client = build_openai_client(model)
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)
