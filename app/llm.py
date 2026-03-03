from __future__ import annotations

import httpx

from app.config import SETTINGS


class LLMError(RuntimeError):
    """Raised when upstream LLM calls fail."""


def _provider_config() -> tuple[str, str, str]:
    provider = SETTINGS.llm_provider
    if provider == "openai":
        if not SETTINGS.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        return (
            "https://api.openai.com/v1/chat/completions",
            SETTINGS.openai_api_key,
            SETTINGS.openai_model,
        )

    if provider == "openrouter":
        if not SETTINGS.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        return (
            "https://openrouter.ai/api/v1/chat/completions",
            SETTINGS.openrouter_api_key,
            SETTINGS.openrouter_model,
        )

    raise LLMError(f"Unsupported LLM_PROVIDER: {provider}")


async def chat_completion(system: str, user: str) -> str:
    url, api_key, model = _provider_config()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if SETTINGS.llm_provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/picoclaw/picoclaw"
        headers["X-Title"] = "PicoClaw"

    timeout = httpx.Timeout(SETTINGS.llm_timeout_seconds)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMError("LLM request timed out") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM HTTP error: {exc}") from exc

    if response.status_code != 200:
        body_preview = response.text[:400]
        raise LLMError(f"LLM non-200 response ({response.status_code}): {body_preview}")

    try:
        data = response.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("LLM response missing choices")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMError("LLM response missing message object")

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM response missing content")

        return content.strip()
    except (ValueError, TypeError) as exc:
        raise LLMError("LLM response JSON parse failed") from exc
