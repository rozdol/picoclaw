from __future__ import annotations

import json
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
            "https://api.openai.com/v1",
            SETTINGS.openai_api_key,
            SETTINGS.openai_model,
        )

    if provider == "openrouter":
        if not SETTINGS.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")
        return (
            "https://openrouter.ai/api/v1",
            SETTINGS.openrouter_api_key,
            SETTINGS.openrouter_model,
        )

    raise LLMError(f"Unsupported LLM_PROVIDER: {provider}")


def _is_likely_text_completion_model(model: str) -> bool:
    model_lower = model.lower()
    return any(
        marker in model_lower
        for marker in (
            "codex",
            "instruct",
            "text-",
            "davinci",
            "curie",
            "babbage",
            "ada",
        )
    )


def _build_payload(
    endpoint_kind: str, model: str, system: str, user: str, include_temperature: bool = True
) -> dict:
    if endpoint_kind == "chat":
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if include_temperature:
            payload["temperature"] = 0.2
        return payload

    payload = {
        "model": model,
        "prompt": f"System:\n{system}\n\nUser:\n{user}\n\nAssistant:\n",
    }
    if include_temperature:
        payload["temperature"] = 0.2
    return payload


def _extract_error_obj(response_text: str) -> dict | None:
    try:
        data = json.loads(response_text)
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None
    error = data.get("error")
    return error if isinstance(error, dict) else None


def _extract_error_text(response_text: str) -> str:
    error = _extract_error_obj(response_text)
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message.lower()
    return response_text.lower()


def _suggests_completion_endpoint(response_text: str) -> bool:
    text = _extract_error_text(response_text)
    return (
        "not a chat model" in text
        or "not supported in the v1/chat/completions endpoint" in text
        or "did you mean to use v1/completions" in text
    )


def _suggests_chat_endpoint(response_text: str) -> bool:
    text = _extract_error_text(response_text)
    return (
        "not supported in the v1/completions endpoint" in text
        or "did you mean to use v1/chat/completions" in text
    )


def _is_unsupported_temperature(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False

    error = _extract_error_obj(response.text)
    if not isinstance(error, dict):
        return False

    param = error.get("param")
    if isinstance(param, str) and param.lower() == "temperature":
        return True

    code = error.get("code")
    message = error.get("message")
    message_lower = message.lower() if isinstance(message, str) else ""
    return (
        isinstance(code, str)
        and code.lower() == "unsupported_value"
        and "temperature" in message_lower
        and "default (1)" in message_lower
    )


def _parse_response_content(endpoint_kind: str, data: dict) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LLM response missing choices")

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMError("LLM response has invalid choice item")

    if endpoint_kind == "chat":
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_chunks: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text")
                        if isinstance(text, str):
                            text_chunks.append(text)
                merged = "".join(text_chunks).strip()
                if merged:
                    return merged
        raise LLMError("LLM response missing content")

    content = first.get("text")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM response missing completion text")
    return content.strip()


async def chat_completion(system: str, user: str) -> str:
    base_url, api_key, model = _provider_config()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if SETTINGS.llm_provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/picoclaw/picoclaw"
        headers["X-Title"] = "PicoClaw"

    timeout = httpx.Timeout(SETTINGS.llm_timeout_seconds)

    endpoint_order = ["chat", "completions"]
    if _is_likely_text_completion_model(model):
        endpoint_order = ["completions", "chat"]

    response: httpx.Response | None = None
    endpoint_used = endpoint_order[0]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, endpoint_kind in enumerate(endpoint_order):
                endpoint_used = endpoint_kind
                path = "/chat/completions" if endpoint_kind == "chat" else "/completions"
                payload = _build_payload(endpoint_kind, model, system, user, include_temperature=True)
                response = await client.post(f"{base_url}{path}", json=payload, headers=headers)
                if _is_unsupported_temperature(response):
                    payload = _build_payload(endpoint_kind, model, system, user, include_temperature=False)
                    response = await client.post(f"{base_url}{path}", json=payload, headers=headers)
                if response.status_code == 200:
                    break

                if index == len(endpoint_order) - 1:
                    break

                if endpoint_kind == "chat" and _suggests_completion_endpoint(response.text):
                    continue
                if endpoint_kind == "completions" and _suggests_chat_endpoint(response.text):
                    continue
                break
    except httpx.TimeoutException as exc:
        raise LLMError("LLM request timed out") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM HTTP error: {exc}") from exc

    if response is None:
        raise LLMError("LLM request failed before receiving a response")

    if response.status_code != 200:
        body_preview = response.text[:400]
        raise LLMError(f"LLM non-200 response ({response.status_code}): {body_preview}")

    try:
        data = response.json()
        if not isinstance(data, dict):
            raise LLMError("LLM response JSON root is not an object")
        return _parse_response_content(endpoint_used, data)
    except (ValueError, TypeError) as exc:
        raise LLMError("LLM response JSON parse failed") from exc
