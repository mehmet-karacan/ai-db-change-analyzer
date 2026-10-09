from __future__ import annotations

import hashlib
import inspect
import json
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

import httpx
from jsonschema import Draft202012Validator

from .config import ModelConfig


def _safe_log(event: str, **fields: Any) -> None:
    """Write troubleshooting metadata without request or response contents."""
    payload = {"event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=sys.stderr, flush=True)


def _safe_exception_detail(exc: BaseException, secret: str) -> str:
    detail = " ".join(str(exc).split())[:240]
    if secret:
        detail = detail.replace(secret, "<redacted>")
    return detail


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


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolAssistantReply:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    returned_model: str | None
    finish_reason: str
    usage: dict[str, int] | None


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    final_content: str
    messages: tuple[dict[str, Any], ...]
    turns: int
    tool_call_count: int
    returned_model: str | None


def _strict_object(value: str) -> dict[str, Any]:
    duplicate = False

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                duplicate = True
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelTransportError("TOOL_ARGUMENTS_INVALID") from exc
    if duplicate:
        raise ModelTransportError("TOOL_ARGUMENTS_DUPLICATE_KEY")
    if not isinstance(parsed, dict):
        raise ModelTransportError("TOOL_ARGUMENTS_INVALID")
    return parsed


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _validated_usage(value: Any) -> dict[str, int]:
    """Keep portable token counters and ignore provider-specific detail blocks."""
    if not isinstance(value, dict):
        raise ModelTransportError("MODEL_USAGE_INVALID")
    counters: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            counters[key] = item
            continue
        if isinstance(key, str) and key.endswith("_details") and isinstance(item, dict):
            if all(detail is None or (isinstance(detail, int) and not isinstance(detail, bool) and detail >= 0)
                   for detail in item.values()):
                continue
        raise ModelTransportError("MODEL_USAGE_INVALID")
    return counters


def capability_record_digest(record: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def load_capability_record(path: str | Path, *, expected_route: str, expected_model: str,
                           required_features: Sequence[str] = ()) -> dict[str, Any]:
    candidate = Path(path).resolve(strict=False)
    if candidate.is_symlink() or not candidate.is_file():
        raise ModelTransportError("CAPABILITY_RECORD_MISSING")
    try:
        record = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelTransportError("CAPABILITY_RECORD_INVALID") from exc
    if not isinstance(record, dict) or record.get("schema_version") != "capability/1.0":
        raise ModelTransportError("CAPABILITY_RECORD_INVALID")
    if record.get("record_sha256") != capability_record_digest(record):
        raise ModelTransportError("CAPABILITY_RECORD_DIGEST_MISMATCH")
    if record.get("route") != expected_route or record.get("configured_model") != expected_model:
        raise ModelTransportError("CAPABILITY_RECORD_ROUTE_MISMATCH")
    features = record.get("features")
    if not isinstance(features, dict) or any(features.get(name) is not True for name in required_features):
        raise ModelTransportError("CAPABILITY_FEATURE_UNVERIFIED")
    return record


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
        self._endpoint_host = parsed.hostname or ""
        self._endpoint_path = parsed.path or "/"
        self._ca_file_name = Path(config.ca_file).name if config.ca_file else "system-default"
        self._ca_file_present = bool(config.ca_file) and Path(config.ca_file).is_file()
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

    def complete_with_tools(
        self, *, api_key: str, system_message: str, user_payload: dict[str, Any],
        response_schema: dict[str, Any], output_tokens: int, tools: Sequence[dict[str, Any]],
        handlers: Mapping[str, Callable[..., Any]], max_turns: int = 8,
        initial_messages: Sequence[dict[str, Any]] = (),
    ) -> ToolLoopResult:
        """Run a bounded stateful assistant/tool/final conversation."""
        if not self.config.route_verified or not self.config.capabilities_verified:
            raise ModelTransportError("MODEL_CAPABILITY_UNVERIFIED")
        load_capability_record(
            self.config.capability_record, expected_route=self.url,
            expected_model=self.config.id, required_features=("tools",),
        )
        return self._tool_loop(
            api_key=api_key, system_message=system_message, user_payload=user_payload,
            response_schema=response_schema, output_tokens=output_tokens, tools=tools,
            handlers=handlers, max_turns=max_turns, initial_messages=initial_messages,
        )

    def probe_tools_synthetic(self, *, api_key: str, response_schema: dict[str, Any], output_tokens: int) -> ToolLoopResult:
        """Probe tool continuation with fixed synthetic data only."""
        sentinel = "synthetic-tool-sentinel-7f3a"
        probe_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"sentinel": {"type": "string", "const": sentinel}},
            "required": ["sentinel"],
        }
        tool = {"type": "function", "function": {"name": "synthetic_lookup", "description": "fixed synthetic probe", "parameters": {"type": "object", "properties": {"nonce": {"type": "string"}}, "required": ["nonce"], "additionalProperties": False}}}
        result = self._tool_loop(
            api_key=api_key, system_message="Use synthetic_lookup exactly once, then return JSON containing its sentinel.",
            user_payload={"synthetic": True, "nonce": "probe-nonce-1", "sentinel": sentinel},
            response_schema=probe_schema, output_tokens=output_tokens, tools=[tool],
            handlers={"synthetic_lookup": lambda args: {"sentinel": sentinel, "nonce": args.get("nonce")}},
            max_turns=4, tool_choice="required",
        )
        sentinel_present = sentinel in result.final_content
        _safe_log(
            "model_tool_probe",
            model=self.config.id,
            turns=result.turns,
            tool_call_count=result.tool_call_count,
            sentinel_present=sentinel_present,
            final_content_length=len(result.final_content),
        )
        if result.tool_call_count != 1 or not sentinel_present:
            raise ModelTransportError("SYNTHETIC_TOOL_SENTINEL_MISSING")
        return result

    def _tool_loop(
        self, *, api_key: str, system_message: str, user_payload: dict[str, Any],
        response_schema: dict[str, Any], output_tokens: int, tools: Sequence[dict[str, Any]],
        handlers: Mapping[str, Callable[..., Any]], max_turns: int,
        tool_choice: str = "auto",
        initial_messages: Sequence[dict[str, Any]] = (),
    ) -> ToolLoopResult:
        if max_turns < 1 or max_turns > 64 or not tools or tool_choice not in {"auto", "required"}:
            raise ModelTransportError("TOOL_LOOP_LIMIT_INVALID")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": json.dumps(user_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))},
        ]
        messages.extend(dict(item) for item in initial_messages)
        history_ids: set[str] = set()
        for item in initial_messages:
            if item.get("role") == "assistant":
                for call in item.get("tool_calls", []):
                    if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
                        raise ModelTransportError("TOOL_CALL_ID_INVALID")
                    history_ids.add(call["id"])
            elif item.get("role") == "tool":
                if not isinstance(item.get("tool_call_id"), str) or item["tool_call_id"] not in history_ids:
                    raise ModelTransportError("TOOL_CALL_ID_UNKNOWN")
        known_ids: set[str] = set()
        returned_model: str | None = None
        call_count = 0
        for turn in range(1, max_turns + 1):
            assistant = self._tool_request(
                api_key=api_key, messages=messages, tools=tools,
                response_schema=response_schema, output_tokens=output_tokens, tool_choice=tool_choice,
            )
            returned_model = assistant.returned_model or returned_model
            assistant_message: dict[str, Any] = {"role": "assistant", "content": assistant.content}
            if assistant.tool_calls:
                assistant_message["tool_calls"] = [
                    {"id": item.call_id, "type": "function", "function": {"name": item.name, "arguments": json.dumps(item.arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))}}
                    for item in assistant.tool_calls
                ]
            messages.append(assistant_message)
            if assistant.tool_calls:
                for call in assistant.tool_calls:
                    if call.call_id in known_ids:
                        raise ModelTransportError("TOOL_CALL_ID_DUPLICATE")
                    known_ids.add(call.call_id)
                    handler = handlers.get(call.name)
                    if handler is None:
                        raise ModelTransportError("TOOL_UNKNOWN")
                    try:
                        accepts_call_id = False
                        try:
                            inspect.signature(handler).bind(call.arguments, call.call_id)
                            accepts_call_id = True
                        except (TypeError, ValueError):
                            pass
                        result = handler(call.arguments, call.call_id) if accepts_call_id else handler(call.arguments)
                    except Exception as exc:
                        raise ModelTransportError("TOOL_EXECUTION_FAILED") from exc
                    if result is None:
                        raise ModelTransportError("TOOL_RESULT_MISSING")
                    try:
                        encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                    except (TypeError, ValueError) as exc:
                        raise ModelTransportError("TOOL_RESULT_INVALID") from exc
                    messages.append({"role": "tool", "tool_call_id": call.call_id, "content": encoded})
                    call_count += 1
                continue
            if assistant.content is None:
                raise ModelTransportError("MODEL_FINAL_CONTENT_MISSING")
            final_object = _strict_object(assistant.content)
            if list(Draft202012Validator(response_schema).iter_errors(final_object)):
                raise ModelTransportError("MODEL_FINAL_SCHEMA_INVALID")
            return ToolLoopResult(assistant.content, tuple(messages), turn, call_count, returned_model)
        raise ModelTransportError("TOOL_TURN_LIMIT")

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
        if self.config.id.startswith("Qwen/"):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return self._validate_envelope(self._send_json(api_key, body))

    def _tool_request(self, *, api_key: str, messages: list[dict[str, Any]], tools: Sequence[dict[str, Any]],
                      response_schema: dict[str, Any], output_tokens: int, tool_choice: str) -> ToolAssistantReply:
        body: dict[str, Any] = {
            "model": self.config.id, "messages": messages, "tools": list(tools), "tool_choice": tool_choice,
            "stream": False, self.config.output_limit_parameter: output_tokens,
        }
        format_value = response_format(self.config.output_mode, response_schema)
        if format_value is not None:
            body["response_format"] = format_value
        if self.config.id.startswith("Qwen/"):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return self._validate_tool_envelope(self._send_json(api_key, body))

    def _send_json(self, api_key: str, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        request_kind = "tools" if "tools" in body else "json_schema" if "response_format" in body else "chat"
        started = time.perf_counter()
        _safe_log(
            "model_http_request",
            endpoint_host=self._endpoint_host,
            endpoint_path=self._endpoint_path,
            model=self.config.id,
            request_kind=request_kind,
            message_count=len(body.get("messages", [])),
            tls_verify=True,
            ca_file=self._ca_file_name,
            ca_file_present=self._ca_file_present,
            trust_env=False,
            follow_redirects=False,
        )
        try:
            with self._client.stream("POST", self.url, headers=headers, json=body) as response:
                content_type = response.headers.get("content-type", "")
                _safe_log(
                    "model_http_response",
                    endpoint_host=self._endpoint_host,
                    endpoint_path=self._endpoint_path,
                    model=self.config.id,
                    request_kind=request_kind,
                    status=response.status_code,
                    content_type=content_type[:120],
                    content_length=response.headers.get("content-length", ""),
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
                if response.status_code != 200:
                    self._raise_status(response)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.config.max_response_bytes:
                        raise ModelTransportError("MODEL_RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
                _safe_log(
                    "model_http_body_read",
                    endpoint_host=self._endpoint_host,
                    endpoint_path=self._endpoint_path,
                    model=self.config.id,
                    request_kind=request_kind,
                    response_bytes=size,
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                )
        except ModelTransportError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            error_type = type(exc).__name__.upper()
            cause_type = type(exc.__cause__).__name__.upper() if exc.__cause__ is not None else "NOCAUSE"
            _safe_log(
                "model_http_error",
                endpoint_host=self._endpoint_host,
                endpoint_path=self._endpoint_path,
                model=self.config.id,
                request_kind=request_kind,
                exception_type=error_type,
                cause_type=cause_type,
                detail=_safe_exception_detail(exc, api_key),
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )
            raise ModelTransportError(f"MODEL_TRANSPORT_{error_type}_{cause_type}", retryable=True) from exc
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelTransportError("MODEL_ENVELOPE_INVALID") from exc
        return payload

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
            validated_usage = _validated_usage(usage)
        returned_model = payload.get("model")
        if returned_model is not None and not isinstance(returned_model, str):
            raise ModelTransportError("MODEL_RETURNED_ID_INVALID")
        return ModelReply(message["content"], returned_model, finish, validated_usage)

    @staticmethod
    def _validate_tool_envelope(payload: Any) -> ToolAssistantReply:
        if not isinstance(payload, dict):
            raise ModelTransportError("MODEL_ENVELOPE_INVALID")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ModelTransportError("MODEL_CHOICES_INVALID")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelTransportError("MODEL_MESSAGE_INVALID")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelTransportError("MODEL_CONTENT_INVALID")
        if message.get("refusal"):
            raise ModelTransportError("MODEL_REFUSAL")
        raw_calls = message.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ModelTransportError("TOOL_CALLS_INVALID")
        calls: list[ToolCall] = []
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
                raise ModelTransportError("TOOL_CALL_ID_INVALID")
            function = raw.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"] or not isinstance(function.get("arguments"), str):
                raise ModelTransportError("TOOL_CALL_INVALID")
            calls.append(ToolCall(raw["id"], function["name"], _strict_object(function["arguments"])))
        if content is None and not calls:
            raise ModelTransportError("MODEL_FINAL_CONTENT_MISSING")
        finish = choice.get("finish_reason")
        if finish not in {"stop", "tool_calls"}:
            raise ModelTransportError("MODEL_FINISH_REASON")
        usage = payload.get("usage")
        validated_usage: dict[str, int] | None = None
        if usage is not None:
            validated_usage = _validated_usage(usage)
        returned_model = payload.get("model")
        if returned_model is not None and not isinstance(returned_model, str):
            raise ModelTransportError("MODEL_RETURNED_ID_INVALID")
        return ToolAssistantReply(content, tuple(calls), returned_model, finish, validated_usage)
