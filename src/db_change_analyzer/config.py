from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ConfigError(ValueError):
    pass


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RepositoryConfig(ConfigModel):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    url: str
    branch: str
    allowed_host: str

    @model_validator(mode="after")
    def validate_https(self) -> "RepositoryConfig":
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host or parsed.username or parsed.password:
            raise ValueError("repository URL must be credential-free HTTPS on allowed_host")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", self.branch) or ".." in self.branch or self.branch.startswith("-"):
            raise ValueError("invalid branch name")
        return self


class ScopeRoot(ConfigModel):
    path: str
    default_schema: str

    @field_validator("path")
    @classmethod
    def safe_root(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("scope root must be a relative contained path")
        return path.as_posix().rstrip("/")


class EncodingOverride(ConfigModel):
    path: str
    encoding: str


class ScopeConfig(ConfigModel):
    name: str
    explicit_excludes: list[str]
    roots: Annotated[list[ScopeRoot], Field(min_length=1)]
    encoding_overrides: list[EncodingOverride]

    @model_validator(mode="after")
    def no_overlapping_roots(self) -> "ScopeConfig":
        roots = sorted(root.path for root in self.roots)
        for left, right in zip(roots, roots[1:]):
            if right == left or right.startswith(left + "/"):
                raise ValueError("scope roots overlap")
        return self


class StateConfig(ConfigModel):
    root: str
    lock_wait_seconds: Annotated[int, Field(ge=0, le=300)]
    busy_timeout_ms: Annotated[int, Field(ge=1, le=60_000)]


class GitConfig(ConfigModel):
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)]
    max_new_commits: Annotated[int, Field(ge=1, le=100_000)]
    ca_file: str


class ParserConfig(ConfigModel):
    default_encoding: str
    grammar_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_version: Literal["4.13.2"]
    max_file_bytes: Annotated[int, Field(ge=1)]
    max_parse_unit_bytes: Annotated[int, Field(ge=1)]
    worker_timeout_seconds: Annotated[int, Field(ge=1)]
    worker_memory_mib: Annotated[int, Field(ge=64)]


class AnalysisConfig(ConfigModel):
    max_job_seconds: Annotated[int, Field(ge=1)]
    shutdown_reserve_seconds: Annotated[int, Field(ge=0)]
    max_new_units_per_invocation: Annotated[int, Field(ge=1)]
    max_http_attempts_per_invocation: Annotated[int, Field(ge=1)]
    max_http_attempts_per_unit_per_invocation: Annotated[int, Field(ge=1)]
    max_repairs_per_unit_generation: Annotated[int, Field(ge=0, le=1)]
    max_chunks_per_object_transition: Annotated[int, Field(ge=1)]
    max_overflow_split_depth: Annotated[int, Field(ge=0)]
    max_request_utf8_bytes: Annotated[int, Field(ge=1024)]
    hunk_context_lines: Annotated[int, Field(ge=0)]
    dependency_depth: Annotated[int, Field(ge=0)]
    max_dependency_depth: Annotated[int, Field(ge=0)]
    max_neighbors_per_side: Annotated[int, Field(ge=0)]
    max_synonym_hops: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=1)]
    safety_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_limits(self) -> "AnalysisConfig":
        if self.dependency_depth > self.max_dependency_depth:
            raise ValueError("dependency_depth exceeds max_dependency_depth")
        if self.shutdown_reserve_seconds >= self.max_job_seconds:
            raise ValueError("shutdown reserve consumes the whole job budget")
        return self


class ModelConfig(ConfigModel):
    base_url: str
    chat_path: str
    allowed_host: str
    id: str
    route_verified: bool
    capabilities_verified: bool
    capability_record: str
    verified_context_window_tokens: Annotated[int, Field(ge=0)]
    output_mode: Literal["json_schema", "json_object", "prompt_json"]
    output_limit_parameter: str
    connect_timeout_seconds: Annotated[int, Field(gt=0)]
    read_timeout_seconds: Annotated[int, Field(gt=0)]
    write_timeout_seconds: Annotated[int, Field(gt=0)]
    pool_timeout_seconds: Annotated[int, Field(gt=0)]
    max_response_bytes: Annotated[int, Field(gt=0, le=16 * 1024 * 1024)]
    retry_after_cap_seconds: Annotated[int, Field(ge=0)]
    backoff_cap_seconds: Annotated[int, Field(ge=0)]
    ca_file: str
    proxy_url: str

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ModelConfig":
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host or parsed.username or parsed.password:
            raise ValueError("model base_url must be credential-free HTTPS on allowed_host")
        path = PurePosixPath(self.chat_path)
        if self.chat_path.startswith("/") or path.is_absolute() or ".." in path.parts or any(x in self.chat_path for x in "?#"):
            raise ValueError("chat_path must preserve the configured base prefix")
        if self.route_verified != self.capabilities_verified:
            raise ValueError("route and capability verification must advance together")
        if self.capabilities_verified and (not self.capability_record or self.verified_context_window_tokens <= 0):
            raise ValueError("verified model requires a capability record and context window")
        if self.proxy_url:
            proxy = urlsplit(self.proxy_url)
            if proxy.scheme != "https" or proxy.username or proxy.password:
                raise ValueError("proxy_url must be credential-free HTTPS")
        return self


class SmtpConfig(ConfigModel):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)]
    security: Literal["starttls_required"]
    auth_mode: Literal["login", "none"]
    authorized_no_auth_relay: bool
    sender: str
    recipients: Annotated[list[str], Field(min_length=1)]
    message_id_domain: str
    timeout_seconds: Annotated[int, Field(gt=0)]
    max_automatic_attempts: Annotated[int, Field(ge=1)]
    retry_min_interval_seconds: Annotated[int, Field(ge=0)]
    ca_file: str

    @model_validator(mode="after")
    def safe_mail(self) -> "SmtpConfig":
        values = [self.sender, self.message_id_domain, *self.recipients]
        if any(any(ord(ch) < 32 or ord(ch) == 127 for ch in value) for value in values):
            raise ValueError("mail fields contain control characters")
        placeholder = re.compile(r"^<[A-Z0-9_]+>$")
        address = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$")
        if not (address.fullmatch(self.sender) or placeholder.fullmatch(self.sender)) or any(not address.fullmatch(item) for item in self.recipients):
            raise ValueError("SMTPUTF8 envelope addresses are not supported")
        if not (re.fullmatch(r"[A-Za-z0-9.-]+", self.message_id_domain) or placeholder.fullmatch(self.message_id_domain)):
            raise ValueError("invalid Message-ID domain")
        if self.auth_mode == "none" and not self.authorized_no_auth_relay:
            raise ValueError("auth_mode=none requires explicit relay authorization")
        if self.auth_mode == "login" and self.authorized_no_auth_relay:
            raise ValueError("relay authorization flag conflicts with login")
        return self


class ReportsConfig(ConfigModel):
    emit_dir: str
    mail_max_bytes: Annotated[int, Field(gt=0)]
    mail_max_object_details: Annotated[int, Field(ge=0)]
    commit_url_template: str
    artifact_url_template: str
    allowed_link_hosts: list[str]
    include_raw_sources_in_mail: Literal[False]
    error_emails: bool


class SecurityConfig(ConfigModel):
    extra_secret_patterns: list[str]

    @field_validator("extra_secret_patterns")
    @classmethod
    def compilable_patterns(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


class RetentionConfig(ConfigModel):
    completed_report_days: Annotated[int, Field(ge=1)]
    log_days: Annotated[int, Field(ge=1)]
    cache_days: Annotated[int, Field(ge=1)]
    receipt_days: Annotated[int, Field(ge=1)]
    backup_days: Annotated[int, Field(ge=1)]
    disk_warning_percent: Annotated[int, Field(ge=1, le=99)]
    disk_stop_percent: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def disk_threshold_order(self) -> "RetentionConfig":
        if self.disk_warning_percent >= self.disk_stop_percent:
            raise ValueError("disk warning must be below disk stop threshold")
        return self


class AppConfig(ConfigModel):
    schema_version: Literal[1]
    repository: RepositoryConfig
    scope: ScopeConfig
    state: StateConfig
    git: GitConfig
    parser: ParserConfig
    analysis: AnalysisConfig
    model: ModelConfig
    smtp: SmtpConfig
    reports: ReportsConfig
    security: SecurityConfig
    retention: RetentionConfig

    def canonical_public_dict(self) -> dict:
        return self.model_dump(mode="json")

    @property
    def config_digest(self) -> str:
        payload = json.dumps(self.canonical_public_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def scope_hash(self) -> str:
        payload = {
            "repository_id": self.repository.id,
            "canonical_remote_url": self.repository.url,
            "branch": self.repository.branch,
            "roots": sorted((root.path, root.default_schema) for root in self.scope.roots),
            "explicit_excludes": sorted(self.scope.explicit_excludes),
            "encoding_overrides": sorted((item.path, item.encoding) for item in self.scope.encoding_overrides),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


ALLOWED_ENV = {
    "LITELLM_API_KEY",
    "DB_ANALYZER_GIT_USERNAME",
    "DB_ANALYZER_GIT_PASSWORD",
    "DB_ANALYZER_SMTP_USERNAME",
    "DB_ANALYZER_SMTP_PASSWORD",
    "ANALYZER_CONFIG",
    "BUILD_NUMBER",
    "BUILD_URL",
    "JOB_NAME",
    "NODE_NAME",
    "ANALYZER_WHEELHOUSE",
}


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve(strict=True)
    if config_path.is_symlink() or not config_path.is_file():
        raise ConfigError("config must be a regular non-symlink file")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(data, strict=True)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError("configuration validation failed") from exc


def read_secret(name: str) -> SecretStr:
    if name not in ALLOWED_ENV:
        raise ConfigError("environment key is not allowlisted")
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"required environment variable is not set: {name}")
    return SecretStr(value)
