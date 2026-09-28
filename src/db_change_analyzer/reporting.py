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
        raise ReportError("REPORT_SCHEMA_INVALID:" + errors[0].validator)
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
    risk = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    cards: list[dict[str, str]] = []
    for item in report["objects"]:
        assessments = item.get("assessments", [])
        responses = [assessment.get("response", {}) for assessment in assessments]
        summary = " | ".join(response.get("summary_tr", "") for response in responses if response.get("summary_tr"))
        finding_risks = [finding["risk"] for response in responses for finding in response.get("findings", [])]
        highest = min(finding_risks, key=lambda value: risk[value]) if finding_risks else "unknown"
        cards.append({"object_key": item["identity"]["object_key"], "summary": summary or "Yerel fark kaydi mevcut.", "risk": highest, "status": item["status"]})
    return sorted(cards, key=lambda card: (rank.get(card["status"], 3), risk[card["risk"]], card["object_key"]))


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
    context = {
        "summary_tr": report["summary_tr"],
        "quality": report["quality"],
        "counts": report["counts"],
        "run": report["run"],
        "object_cards": cards[:max_object_details],
        "omitted_objects": max(0, len(cards) - max_object_details),
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
    if any("\r" in value or "\n" in value for value in (sender, message_id, job_short_name, *recipients)):
        raise ReportError("MAIL_HEADER_INJECTION")
    origin = report["run"].get("originating_analyzer_build")
    build = origin["build_number"] if origin else ("MANUAL" if report["run"]["mode"] == "MANUAL" else report["run"]["run_id"][:8])
    quality = report["quality"].upper()
    subject = f"[AI DB Analyzer][{report['run']['repository_id']}][{quality}] Analyzer {job_short_name} #{build} · {report['report_id'][:8]}"
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
