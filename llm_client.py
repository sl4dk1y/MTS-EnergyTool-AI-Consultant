"""Клиент OpenAI-совместимого API на стандартной библиотеке Python."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMConfigurationError(RuntimeError):
    """Отсутствует обязательная конфигурация API."""


class LLMAPIError(RuntimeError):
    """Безопасная для логирования ошибка API без тела ответа или заголовков."""


HTTPTransport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]


def _first_env(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name, "").strip()
        if value and value != name:
            return value
    return ""


def _urlopen_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        # Не читаем и не печатаем тело ошибки: прокси может вернуть чувствительные данные.
        return exc.code, b""


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 90.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        transport: Optional[HTTPTransport] = None,
        sleeper: Callable[[float], None] = time.sleep,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise LLMConfigurationError("Нужны адрес API, ключ и идентификатор модели из окружения")
        if timeout <= 0 or max_attempts < 1 or backoff_seconds < 0:
            raise ValueError("Некорректные параметры таймаута или повторов")
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._transport = transport or _urlopen_transport
        self._sleep = sleeper
        self._temperature = temperature
        self._max_tokens = max_tokens

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        **kwargs: Any,
    ) -> "LLMClient":
        source = os.environ if env is None else env
        base_url = _first_env(source, "LLM_BASE_URL", "OPENAI_BASE_URL")
        api_key = _first_env(source, "LLM_API_KEY", "OPENAI_API_KEY")
        model = _first_env(source, "LLM_MODEL", "OPENAI_MODEL", "GEN_MODEL")
        missing = [
            label
            for label, value in (("адрес API", base_url), ("ключ API", api_key), ("модель", model))
            if not value
        ]
        if missing:
            raise LLMConfigurationError("В окружении отсутствует: " + ", ".join(missing))
        if "temperature" not in kwargs:
            raw_temperature = _first_env(source, "TEMPERATURE")
            kwargs["temperature"] = float(raw_temperature) if raw_temperature else 0.0
        if "max_tokens" not in kwargs:
            raw_max_tokens = _first_env(source, "MAX_TOKENS")
            kwargs["max_tokens"] = int(raw_max_tokens) if raw_max_tokens else 2048
        return cls(base_url=base_url, api_key=api_key, model=model, **kwargs)

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        if not messages:
            raise ValueError("Нужен хотя бы один элемент messages")
        payload: dict[str, Any] = {"model": self._model, "messages": list(messages)}
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self._api_key,
            "Content-Type": "application/json",
        }

        last_reason = "неизвестная ошибка"
        for attempt in range(self._max_attempts):
            try:
                status, response_body = self._transport(
                    self._endpoint, headers, body, self._timeout
                )
            except (TimeoutError, URLError, OSError) as exc:
                last_reason = "таймаут" if isinstance(exc, TimeoutError) else "сетевая ошибка"
                if attempt + 1 < self._max_attempts:
                    self._sleep(self._backoff_seconds * (2**attempt))
                    continue
                raise LLMAPIError("API недоступен после ограниченных повторов: " + last_reason) from None

            if status == 429 or 500 <= status <= 599:
                last_reason = f"временный HTTP {status}"
                if attempt + 1 < self._max_attempts:
                    self._sleep(self._backoff_seconds * (2**attempt))
                    continue
                raise LLMAPIError("API не ответил после ограниченных повторов: " + last_reason)
            if status < 200 or status >= 300:
                raise LLMAPIError(f"API вернул HTTP {status}")
            try:
                data = json.loads(response_body.decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise LLMAPIError("API вернул ответ неожиданной структуры") from None
            if not isinstance(content, str) or not content.strip():
                raise LLMAPIError("API вернул пустой ответ")
            return content.strip()
        raise LLMAPIError("API недоступен после ограниченных повторов: " + last_reason)
