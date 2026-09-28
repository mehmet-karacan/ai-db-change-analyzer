from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jsonschema import Draft202012Validator, FormatChecker


class ReportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RenderedReport:
    canonical_json: bytes
    sha256: str
    html: bytes
    text: bytes


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    subject: str
    mime_bytes: bytes
    sha256: str
    object_cards: int


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_report(report: dict[str, Any], schema_path: Path | None = None) -> None:
    schema = json.loads((schema_path or (_root() / "schemas" / "report.schema.json")).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report), key=lambda error: list(error.path))
    if errors:
        location = "/".join(str(item) for item in errors[0].path) or "$"
        raise ReportError(f"REPORT_SCHEMA_INVALID:{location}:{errors[0].validator}")
    run = report["run"]
    times = [datetime.fromisoformat(run[name].replace("Z", "+00:00")) for name in ("planned_at", "analysis_started_at", "analysis_completed_at")]
    if not times[0] <= times[1] <= times[2]:
        raise ReportError("REPORT_TIME_ORDER_INVALID")
    counts = report["counts"]
    if counts["analyzed_objects"] + counts["limited_objects"] + counts["unresolved_objects"] != counts["history_known_objects"]:
        raise ReportError("REPORT_OBJECT_COUNTS_INVALID")
    if report["quality"] == "blocked" and report["automatic_commit_eligible"]:
        raise ReportError("REPORT_BLOCKED_COMMIT_INVALID")
    evidence_ids = [item["evidence_id"] for item in report["evidence_registry"]]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ReportError("REPORT_EVIDENCE_DUPLICATE")


def _cards(report: dict[str, Any]) -> list[dict[str, str]]:
    rank = {"blocked": 0, "unresolved": 0, "limited": 1, "analyzed": 2}
    cards: list[dict[str, str]] = []
    for item in report["objects"]:
        if "sequence_observed_value" in item["categories"]:
            continue
        assessments = item.get("assessments", [])
        responses = [assessment.get("response", {}) for assessment in assessments]
        summary = " | ".join(response.get("summary_tr", "") for response in responses if response.get("summary_tr"))
        cards.append({
            "name": item["identity"]["name"], "schema": item["identity"]["schema_name"],
            "object_type": item["identity"]["object_type"],
            "summary": summary or "Kaynak tanımında fark gözlendi; ayrıntı tam rapordadır.",
            "status": item["status"],
        })
    return sorted(cards, key=lambda card: (rank.get(card["status"], 3), card["schema"], card["name"]))


def _sequence_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in report["objects"]:
        if "sequence_observed_value" not in item["categories"]:
            continue
        for fact in item["facts"]:
            if fact["category"] != "sequence_observed_value" or fact["property"] != "START WITH":
                continue
            before, after = fact["before"], fact["after"]
            if before is None or after is None:
                continue
            try:
                difference = int(after) - int(before)
            except (TypeError, ValueError):
                continue
            rows.append({
                "name": item["identity"]["name"], "schema": item["identity"]["schema_name"],
                "before": before, "after": after,
                "delta": f"{difference:+d}",
            })
    return sorted(rows, key=lambda row: (row["schema"], row["name"]))


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True),
        undefined=StrictUndefined,
    )


def render_report(report: dict[str, Any], *, report_url: str = "", allowed_link_hosts: set[str] | None = None, max_object_details: int = 12) -> RenderedReport:
    validate_report(report)
    if report_url:
        parsed = urlsplit(report_url)
        if parsed.scheme != "https" or parsed.hostname not in (allowed_link_hosts or set()) or parsed.username or parsed.password:
            raise ReportError("REPORT_URL_NOT_ALLOWED")
    canonical = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cards = _cards(report)
    sequence_rows = _sequence_rows(report)
    visible_sequence_rows = sequence_rows[:max_object_details]
    visible_cards = cards[:max(0, max_object_details - len(visible_sequence_rows))]
    quality_display = {
        "complete": ("Analiz tamamlandı", "#E5F5EB", "#17603A"),
        "limited": ("Sınırlı analiz", "#FFF3D9", "#855300"),
        "blocked": ("İnceleme gerekli", "#FDE8E8", "#9B2C2C"),
    }[report["quality"]]
    sequence_only = bool(report["objects"]) and len(sequence_rows) == len(report["objects"])
    context = {
        "summary_tr": report["summary_tr"],
        "quality": report["quality"],
        "quality_label": quality_display[0],
        "quality_background": quality_display[1],
        "quality_foreground": quality_display[2],
        "headline": "Sequence başlangıç değerleri" if sequence_only else "Veritabanı kaynak değişim özeti",
        "sequence_only": sequence_only,
        "counts": report["counts"],
        "run": report["run"],
        "short_base": (report["run"]["base_sha"] or "ROOT")[:12],
        "short_target": report["run"]["target_sha"][:12],
        "sequence_rows": visible_sequence_rows,
        "object_cards": visible_cards,
        "omitted_objects": max(0, len(cards) + len(sequence_rows) - len(visible_cards) - len(visible_sequence_rows)),
        "limitations": report["limitations"] or ["Kaynak kapsami disindaki tuketiciler bilinmez."],
        "report_url": report_url,
    }
    environment = _environment()
    html = environment.get_template("report_email.html.j2").render(**context).encode("utf-8")
    text = environment.get_template("report_email.txt.j2").render(**context).encode("utf-8")
    return RenderedReport(canonical, hashlib.sha256(canonical).hexdigest(), html, text)


def build_message(
    report: dict[str, Any],
    rendered: RenderedReport,
    *,
    sender: str,
    recipients: list[str],
    message_id: str,
    date: datetime,
    job_short_name: str,
    current_build_number: str | None,
    max_bytes: int,
) -> RenderedMessage:
    if any("\r" in value or "\n" in value for value in (sender, message_id, job_short_name, report["run"]["repository_id"], *recipients)):
        raise ReportError("MAIL_HEADER_INJECTION")
    origin = report["run"].get("originating_analyzer_build")
    build = origin["build_number"] if origin else ("MANUAL" if report["run"]["mode"] == "MANUAL" else report["run"]["run_id"][:8])
    descriptor = (
        f"{len(_sequence_rows(report))} sequence başlangıç değeri"
        if report["objects"] and len(_sequence_rows(report)) == len(report["objects"])
        else f"{report['counts']['net_known_objects']} nesne değişikliği"
    )
    subject = f"[AI DB Analyzer][{report['run']['repository_id']}] {descriptor} · {job_short_name} #{build}"
    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = format_datetime(date)
    message["Message-ID"] = message_id
    message.set_content(rendered.text.decode("utf-8"))
    message.add_alternative(rendered.html.decode("utf-8"), subtype="html")
    boundary_seed = hashlib.sha256((rendered.sha256 + message_id).encode("utf-8")).hexdigest()[:32]
    message.set_boundary(f"dbanalyzer-{boundary_seed}")
    payload = message.as_bytes()
    if len(payload) > max_bytes:
        raise ReportError("MAIL_SIZE_LIMIT")
    return RenderedMessage(subject, payload, hashlib.sha256(payload).hexdigest(), min(12, len(report["objects"])))
