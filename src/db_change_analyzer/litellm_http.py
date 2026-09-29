from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .config import ModelConfig


class ModelTransportError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, retry_after: int | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str
    returned_model: str | None
    finish_reason: str
    usage: dict[str, int] | None


def response_format(mode: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    if mode == "json_schema":
        name = "db_change_mail_commentary" if schema.get("properties", {}).get("schema_version", {}).get("const") == "mail-commentary/1.1" else "db_change_unit"
        return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def synthetic_probe_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0", "unit_id": "synthetic-smoke", "object_identity": None,
        "artifact_paths": ["synthetic/smoke.sql"], "related_object_keys": [], "view_tags": ["net"],
        "source_pair": {"old_revision": None, "new_revision": "0" * 40},
        "deterministic_facts": [],
        "evidence_registry": [{"kind": "source", "evidence_id": "synthetic-ev", "snippet": "CREATE TABLE SYNTHETIC_CHECK (ID NUMBER);"}],
        "dependency_edges": [], "coverage_manifest": {"synthetic": True},
        "allowed_claim_kinds": ["interpretation"],
    }


class LiteLLMClient:
    def __init__(self, config: ModelConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        base = config.base_url if config.base_url.endswith("/") else config.base_url + "/"
        self.url = urljoin(base, config.chat_path)
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.hostname != config.allowed_host:
            raise ModelTransportError("ENDPOINT_HOST_INVALID")
        context = ssl.create_default_context(cafile=config.ca_file or None)
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.pool_timeout_seconds,
        )
        arguments: dict[str, Any] = {
            "verify": context,
            "timeout": timeout,
            "trust_env": False,
            "follow_redirects": False,
            "transport": transport,
        }
        if config.proxy_url and transport is None:
            arguments["proxy"] = config.proxy_url
        self._client = httpx.Client(**arguments)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LiteLLMClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def complete(
        self,
        *,
        api_key: str,
        system_message: str,
        user_payload: dict[str, Any],
        response_schema: dict[str, Any],
        output_tokens: int,
    ) -> ModelReply:
        if not self.config.route_verified or not self.config.capabilities_verified:
            raise ModelTransportError("MODEL_CAPABILITY_UNVERIFIED")
        return self._complete_request(
            api_key=api_key, system_message=system_message, user_payload=user_payload,
            response_schema=response_schema, output_tokens=output_tokens,
        )

    def probe_synthetic(self, *, api_key: str, response_schema: dict[str, Any], output_tokens: int,
                        user_payload: dict[str, Any] | None = None, system_message: str | None = None) -> ModelReply:
        """Probe an unverified route with fixed synthetic content only."""
        return self._complete_request(
            api_key=api_key,
            system_message=system_message or "Return only the requested JSON for this synthetic smoke input.",
            user_payload=user_payload if user_payload is not None else synthetic_probe_payload(),
            response_schema=response_schema,
            output_tokens=output_tokens,
        )

    def _complete_request(
        self, *, api_key: str, system_message: str, user_payload: dict[str, Any],
        response_schema: dict[str, Any], output_tokens: int,
    ) -> ModelReply:
        if self.config.output_mode != "json_schema":
            system_message += "\nReturn exactly one JSON object matching this schema; do not add fields: " + json.dumps(
                response_schema, ensure_ascii=False, separators=(",", ":")
            )
        body: dict[str, Any] = {
            "model": self.config.id,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": json.dumps(user_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))},
            ],
            "stream": False,
            self.config.output_limit_parameter: output_tokens,
        }
        format_value = response_format(self.config.output_mode, response_schema)
        if format_value is not None:
            body["response_format"] = format_value
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            with self._client.stream("POST", self.url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    self._raise_status(response)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.config.max_response_bytes:
                        raise ModelTransportError("MODEL_RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
        except ModelTransportError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ModelTransportError("MODEL_TRANSPORT", retryable=True) from exc
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelTransportError("MODEL_ENVELOPE_INVALID") from exc
        return self._validate_envelope(payload)


    def _raise_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in {401, 403}:
            raise ModelTransportError("MODEL_AUTH")
        if status == 429:
            retry_after: int | None = None
            value = response.headers.get("Retry-After")
            if value and value.isdecimal():
                retry_after = min(int(value), self.config.retry_after_cap_seconds)
            raise ModelTransportError("MODEL_RATE_LIMIT", retryable=True, retry_after=retry_after)
        if status in {500, 502, 503, 504}:
            raise ModelTransportError("MODEL_SERVER", retryable=True)
        if status == 404:
            raise ModelTransportError("MODEL_ROUTE")
        if status == 400:
            # Never infer or downgrade capabilities from a generic 400 response.
            raise ModelTransportError("MODEL_REQUEST_REJECTED")
        raise ModelTransportError("MODEL_HTTP_STATUS")

    @staticmethod
    def _validate_envelope(payload: Any) -> ModelReply:
        if not isinstance(payload, dict):
            raise ModelTransportError("MODEL_ENVELOPE_INVALID")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelTransportError("MODEL_CHOICES_INVALID")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ModelTransportError("MODEL_CONTENT_INVALID")
        if message.get("refusal") or message.get("tool_calls"):
            raise ModelTransportError("MODEL_REFUSAL_OR_TOOL_CALL")
        finish = choice.get("finish_reason")
        if finish != "stop":
            raise ModelTransportError("MODEL_FINISH_REASON")
        usage = payload.get("usage")
        validated_usage: dict[str, int] | None = None
        if usage is not None:
            if not isinstance(usage, dict) or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in usage.values()):
                raise ModelTransportError("MODEL_USAGE_INVALID")
            validated_usage = usage
        returned_model = payload.get("model")
        if returned_model is not None and not isinstance(returned_model, str):
            raise ModelTransportError("MODEL_RETURNED_ID_INVALID")
        return ModelReply(message["content"], returned_model, finish, validated_usage)
