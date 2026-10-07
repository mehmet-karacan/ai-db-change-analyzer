"""Render a validated mail-view/2.0 through the approved V5 templates."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jsonschema import Draft202012Validator, FormatChecker

from .taxonomy.catalog import change_catalog, supported_types


_TEMPLATES = Path(__file__).parent / "templates" / "v5"
_LOGO = Path(__file__).parent / "assets" / "innova-logo-beyaz.png"
_TOKENS = json.loads((Path(__file__).parent / "taxonomy" / "design-tokens.json").read_text(encoding="utf-8"))
_OBJECT_CATALOG = json.loads((Path(__file__).parent / "taxonomy" / "object-catalog.json").read_text(encoding="utf-8"))
_TYPES = {item["id"]: item for item in _OBJECT_CATALOG["types"]}
_PROFILES = (("full", 60, 24, 24), ("compact", 24, 8, 10), ("summary", 16, 2, 4), ("minimal", 0, 0, 0))


class V5RenderError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class V5Rendered:
    html: bytes
    text: bytes
    mime: bytes
    profile: str
    view_sha256: str
    template_sha256: str
    index_object_ids: tuple[str, ...]
    detail_object_ids: tuple[str, ...]
    ai_objects_displayed: int


def validate_mail_view(view: dict[str, Any]) -> None:
    schema = json.loads((Path(__file__).parent / "schemas" / "v5" / "mail-view-model.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(view), key=lambda e: list(map(str, e.path)))
    if errors:
        where = "/".join(map(str, errors[0].path)) or "$"
        raise V5RenderError(f"MAIL_VIEW_SCHEMA_INVALID:{where}:{errors[0].validator}")
    registry = {entry["evidence_id"]: entry for entry in view["evidence_registry"]}
    if len(registry) != len(view["evidence_registry"]):
        raise V5RenderError("MAIL_VIEW_DUPLICATE_EVIDENCE")
    if len({item["object_id"] for item in view["objects"]}) != len(view["objects"]):
        raise V5RenderError("MAIL_VIEW_DUPLICATE_OBJECT")
    catalog = change_catalog()
    for obj in view["objects"]:
        for evidence_id in obj["evidence_ids"]:
            if evidence_id not in registry:
                raise V5RenderError("MAIL_VIEW_UNKNOWN_EVIDENCE")
            owner = registry[evidence_id]["object_id"]
            if owner is not None and owner != obj["object_id"]:
                raise V5RenderError("MAIL_VIEW_EVIDENCE_OWNER_MISMATCH")
        for fact in obj["facts"]:
            family = catalog[fact["taxonomy_id"]]
            if obj["identity"]["object_type"] not in family.object_types:
                raise V5RenderError("MAIL_VIEW_TAXONOMY_TYPE_MISMATCH")
            context_presence = fact["context_only"] and fact["change_action"] == obj["operation"] and obj["operation"] in {"added", "removed"}
            if fact["change_action"] not in family.allowed_actions and not context_presence:
                raise V5RenderError("MAIL_VIEW_TAXONOMY_ACTION_MISMATCH")
            for side in ("before", "after"):
                if not set(fact[side]["evidence_ids"]) <= set(obj["evidence_ids"]):
                    raise V5RenderError("MAIL_VIEW_FACT_EVIDENCE_MISMATCH")
                expected = "base" if side == "before" else "target"
                if any(registry[evidence_id]["side"] not in {expected, "both"} for evidence_id in fact[side]["evidence_ids"]):
                    raise V5RenderError("MAIL_VIEW_FACT_SIDE_MISMATCH")


def _checked_url(url: str, hosts: set[str], prefixes: tuple[str, ...]) -> str:
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise V5RenderError("REPORT_URL_REJECTED") from exc
    if (
        parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username is not None or parsed.password is not None
        or port is not None or parsed.query or parsed.fragment or not parsed.path.startswith("/")
        or "%" in parsed.path or "\\" in parsed.path or ".." in parsed.path
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in url)
        or not any(parsed.path.startswith(p) for p in prefixes)
    ):
        raise V5RenderError("REPORT_URL_REJECTED")
    return url


def _date(value: str | None, timezone: str) -> str:
    if not value:
        return "Kaydedilmedi"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M:%S")


def _duration(milliseconds: int | None) -> str:
    return "Kaydedilmedi" if milliseconds is None else (f"{milliseconds / 1000:g} saniye").replace(".", ",")


def _group_integer_literal(value: str) -> str:
    """Add visual groups without parsing or changing an Oracle source literal."""
    sign = value[:1] if value[:1] in {"+", "-"} else ""
    digits = value[len(sign):]
    first = len(digits) % 3 or 3
    return sign + ".".join((digits[:first], *(digits[index:index + 3] for index in range(first, len(digits), 3))))


def _value(fact: dict[str, Any], side: str) -> str:
    field = fact[side]
    labels = {
        "absent_in_snapshot": "Yok", "not_in_source": "Kaynakta belirtilmedi",
        "unparsed": "Ayrıştırılamadı", "redacted": "Gizlendi",
        "unknown": "Doğrulanamadı", "not_applicable": "Uygulanamaz: nesne yok",
    }
    if field["state"] != "present":
        return labels[field["state"]]
    value = field["value"]
    if fact["taxonomy_id"] == "sequence.sequence_property.start_with" and re.fullmatch(r"[+-]?[0-9]+", value):
        return _group_integer_literal(value)
    return value


def _change_label(fact: dict[str, Any]) -> str:
    family = change_catalog()[fact["taxonomy_id"]]
    action = fact["change_action"]
    if fact["context_only"]:
        return "Yeni tanımda yer alıyor" if action == "added" else "Önceki tanımda yer alıyordu"
    if fact["taxonomy_id"] == "common.object.presence":
        return "Nesne eklendi" if action == "added" else "Tanım snapshot'tan çıkarıldı"
    if fact["taxonomy_id"] == "table.column.length":
        return "Tanımdaki uzunluk değişti"
    if fact["taxonomy_id"] == "common.source.text":
        return "Kaynak metni değişti"
    if fact["taxonomy_id"].startswith("sequence."):
        return "Değer değişti"
    return family.label_tr + {"added": " eklendi", "modified": " değişti", "removed": " çıkarıldı", "unchanged": " aynı", "unknown": " doğrulanamadı"}[action]


def _primary(obj: dict[str, Any]) -> bool:
    return (
        obj["operation"] in {"added", "modified", "removed"}
        and obj["pattern"] not in {"format_only", "source_order_only", "relocation"}
        and obj["identity"]["identity_confidence"] == "known"
    )


def _route(obj: dict[str, Any]) -> str:
    identity = obj["identity"]
    if (
        identity["object_type"] not in supported_types() or identity["identity_confidence"] != "known"
        or obj["verification"] in {"unresolved", "redacted"} or obj["operation"] in {"unknown", "relocated"}
        or obj["pattern"] in {"format_only", "source_order_only", "relocation"}
    ):
        return "objects/generic.html.j2"
    return _TYPES[identity["object_type"]]["renderer"]


def _context(view: dict[str, Any], profile: tuple[str, int, int, int], report_url: str) -> dict[str, Any]:
    _, max_index, max_detail, max_facts = profile
    ordered = view["objects"]
    operations = Counter(obj["operation"] for obj in ordered if _primary(obj))
    counts = {key: operations.get(key, 0) for key in ("added", "modified", "removed")}
    counts["primary_total"] = sum(counts.values())
    transformed = []
    for number, obj in enumerate(ordered, 1):
        identity = obj["identity"]
        action = _TOKENS["actions"].get(obj["operation"], {"label": "Doğrulanamadı", "symbol": "?", "fg": "#5F5954", "bg": "#F3EEE7"})
        facts = [fact for fact in obj["facts"] if fact["change_action"] != "unchanged" or fact["context_only"]]
        comments = [{"label": {"summary": "AI Yorumu", "interpretation": "AI Yorumu", "uncertainty": "AI Belirsizlik Notu", "recommended_check": "AI Kontrol Önerisi"}[comment["kind"]], "text": comment["text_tr"]} for comment in obj["ai_comments"]]
        limited = obj["verification"] != "verified"
        method = "Deterministik kaynak karşılaştırması + AI destekli yorum." if comments else "Kaynak farkı doğrulandı; ayrıntılı ayrıştırma sınırlı, AI yorumu gösterilmedi." if limited else "Deterministik kaynak karşılaştırması; AI yorumu kullanılmadı."
        transformed.append({
            "number": f"{number:02d}", "anchor": "nesne-" + obj["object_id"],
            "type": identity["object_type"], "type_label": _TYPES.get(identity["object_type"], {}).get("label_tr", "Desteklenmeyen / çözümlenemeyen tür"),
            "display_name": ((identity["schema_name"] + ".") if identity["schema_name"] else "") + identity["name"],
            "action": action, "summary": obj["deterministic_summary_tr"], "limited": limited, "renderer": _route(obj),
            "rows": [{"field": fact["subject_name"], "subject": fact["subject_kind"], "change": _change_label(fact), "before": _value(fact, "before"), "after": _value(fact, "after")} for fact in facts[:max_facts]],
            "omitted_facts": max(0, len(facts) - max_facts), "comments": comments,
            "checks": [check["text_tr"] for check in obj["recommended_checks"]], "notes": obj["limitations_tr"], "method": method,
        })
    priority = sorted(range(len(transformed)), key=lambda index: (not transformed[index]["limited"], index))
    index_slots, detail_slots = set(priority[:max_index]), set(priority[:max_detail])
    for index, item in enumerate(transformed):
        item["has_detail"] = index in detail_slots
    indexed = [item for index, item in enumerate(transformed) if index in index_slots]
    detailed = [item for index, item in enumerate(transformed) if index in detail_slots]
    coverage = view["coverage"]
    limited_count = sum(obj["verification"] != "verified" for obj in ordered)
    coverage_display = f"{len({obj['identity']['object_type'] for obj in ordered})} nesne türü. {limited_count} nesnenin ayrıntılı analizi sınırlı. Canlı veritabanı kontrol edilmedi."
    if not coverage["object_count_complete"]:
        coverage_display = f"Nesne toplamı kesin değil. En az {counts['primary_total']} bilinen fark; {coverage['unknown_artifacts']} çözümlenemeyen kaynak dosyası. " + coverage_display
    elif any(not _primary(obj) for obj in ordered):
        coverage_display += f" {sum(not _primary(obj) for obj in ordered)} teknik/belirsiz kayıt ana toplama dahil değil."
    if not ordered and not view["artifact_notices"] and view["changed_files"] == 0:
        headline, summary = "Net nesne değişikliği bulunmadı", "Başlangıç ve hedef Git snapshot'ları arasında net kaynak farkı yok; aralıktaki commit geçişleri kapsam notunda kayıtlıdır."
    else:
        headline, summary = "Oracle nesnesinde kaynak değişikliği", "Farklar aynı düzende, önceki ve yeni değerleriyle gösterilir. AI yorumları kaynak bulgularından ayrı tutulur."
    if not coverage["object_count_complete"]:
        headline = "Bilinen nesnede kaynak değişikliği; kapsam sınırlı"
    elif ordered and counts["primary_total"] == 0:
        headline = "Ana nesne farkı yok; teknik kaynak kayıtları var"
    analysis, source = view["analysis"], view["source"]
    timezone, ai = analysis["display_timezone"], analysis["ai"]
    ledger = []
    if not ai["models"]:
        ledger.append({"label": "Model kullanımı", "value": "AI çağrısı yapılmadı" if ai["status"] == "not_used" else "Model bilgisi kaydedilmedi"})
    for model in ai["models"]:
        if len(ai["models"]) > 1:
            ledger.append({"label": "İlgili AI birimleri", "value": ", ".join(model["unit_ids"]) or "Kaydedilmedi"})
        ledger.extend((
            {"label": "Seçilen model", "value": model["configured_model"]},
            {"label": "Sağlayıcının bildirdiği model", "value": ", ".join(model["returned_models"]) or "Bildirilmedi"},
            {"label": "Model sürümü doğrulaması", "value": model["resolved_model_version"] if model["version_verification"] == "verified_record" else "Doğrulanmadı" if model["resolved_model_version"] else "Bildirilmedi"},
        ))
    ledger.extend((
        {"label": "Analiz başlangıcı", "value": _date(analysis["started_at"], timezone)},
        {"label": "Analiz bitişi", "value": _date(analysis["completed_at"], timezone)},
        {"label": "Toplam analiz süresi", "value": _duration(analysis["elapsed_ms"])},
    ))
    if ai["status"] != "not_used":
        ledger.extend((
            {"label": "AI aşaması başlangıcı", "value": _date(ai["phase_started_at"], timezone)},
            {"label": "AI aşaması bitişi", "value": _date(ai["phase_completed_at"], timezone)},
            {"label": "AI aşaması geçen süresi", "value": _duration(ai["phase_elapsed_ms"])},
        ))
    ledger.extend((
        {"label": "Saat dilimi", "value": timezone},
        {"label": "Kaynak farkı belirlenen", "value": f"{counts['primary_total']} nesne"},
        {"label": "Ayrıntılı ayrıştırma", "value": f"{len(ordered)-limited_count} nesne tamamlandı, {limited_count} nesne sınırlı"},
        {"label": "AI yorumu kabul edilen", "value": f"{sum(bool(obj['ai_comments']) for obj in ordered)} / {len(ordered)} nesne"},
        {"label": "Bu e-postada AI yorumu gösterilen", "value": f"{sum(bool(obj['comments']) for obj in detailed)} nesne"},
    ))
    comparison = [{"label": label, "value": value} for label, value in (
        ("Repository", source["repository"]), ("Dal", source["branch"]), ("Önceki snapshot", source["base_label"]),
        ("Yeni snapshot", source["target_label"]), ("Karşılaştırma görünümü", source["comparison_mode"]), ("Rapor kimliği", view["report_id"]),
    )]
    return {
        "counts": counts, "headline": headline, "summary_tr": summary,
        "preheader": f"{counts['primary_total']} nesne kaynak farkı. {summary}",
        "report_date": _date(analysis["completed_at"] or source["observed_at"], timezone).split(" ")[0],
        "synthetic": view["synthetic"], "coverage_display": coverage_display,
        "index_objects": indexed, "detail_objects": detailed,
        "omitted_index": len(ordered) - len(indexed), "omitted_details": len(ordered) - len(detailed),
        "ledger_rows": ledger, "comparison_rows": comparison,
        "artifact_notices": view["artifact_notices"], "coverage_notes": coverage["limitations_tr"],
        "critical_notes": [obj["display_name"] + ": " + obj["summary"] for index, obj in enumerate(transformed) if obj["limited"] and index not in detail_slots][:4],
        "report_url": report_url,
    }


def _template_digest() -> str:
    files: dict[str, str] = {}
    for path in sorted(_TEMPLATES.rglob("*.j2")):
        files[path.relative_to(_TEMPLATES.parent).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    tokens = Path(__file__).parent / "taxonomy" / "design-tokens.json"
    files["design-tokens.json"] = hashlib.sha256(tokens.read_bytes()).hexdigest()
    canonical = json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def render_v5_view(
    view: dict[str, Any], *, sender: str, recipients: list[str], message_id: str,
    date: datetime, report_url: str = "", allowed_link_hosts: set[str] | None = None,
    allowed_path_prefixes: tuple[str, ...] = ("/reports/",), html_limit: int = 90_000,
    mime_limit: int = 150_000,
) -> V5Rendered:
    validate_mail_view(view)
    url = _checked_url(report_url, allowed_link_hosts or set(), allowed_path_prefixes)
    if date.tzinfo is None or not sender or not recipients:
        raise V5RenderError("MAIL_HEADERS_INVALID")
    html_env = Environment(loader=FileSystemLoader(_TEMPLATES), undefined=StrictUndefined, autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True))
    text_env = Environment(loader=FileSystemLoader(_TEMPLATES), undefined=StrictUndefined, autoescape=False)
    view_json = json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for profile in _PROFILES:
        context = _context(view, profile, url)
        html = html_env.get_template("report_email.html.j2").render(**context).encode("utf-8")
        text = text_env.get_template("report_email.txt.j2").render(**context).encode("utf-8")
        if len(html) > html_limit:
            continue
        mail = EmailMessage(policy=SMTP)
        mail["Subject"] = f"[AI DB Analyzer] {context['counts']['primary_total']} nesne kaynak farkı"
        mail["From"] = sender
        mail["To"] = ", ".join(recipients)
        mail["Date"] = format_datetime(date)
        mail["Message-ID"] = message_id
        mail.set_content(text.decode("utf-8"))
        mail.add_alternative(html.decode("utf-8"), subtype="html")
        html_part = mail.get_body(preferencelist=("html",))
        if html_part is not None and _LOGO.is_file():
            html_part.add_related(_LOGO.read_bytes(), maintype="image", subtype="png", cid="<innova-logo>")
        mail.set_boundary("v5-" + hashlib.sha256(html).hexdigest()[:32])
        mime = mail.as_bytes()
        if len(mime) <= mime_limit:
            return V5Rendered(
                html, text, mime, profile[0], hashlib.sha256(view_json).hexdigest(), _template_digest(),
                tuple(obj["anchor"].removeprefix("nesne-") for obj in context["index_objects"]),
                tuple(obj["anchor"].removeprefix("nesne-") for obj in context["detail_objects"]),
                sum(bool(obj["comments"]) for obj in context["detail_objects"]),
            )
    raise V5RenderError("MAIL_SIZE_LIMIT")


def build_render_manifest(view: dict[str, Any], rendered: V5Rendered, *, source_report_sha256: str | None, generation: int = 0) -> dict[str, Any]:
    manifest = {
        "schema_version": "render-manifest/1.0", "report_id": view["report_id"],
        "synthetic": view["synthetic"], "render_generation": generation,
        "source_report_sha256": source_report_sha256,
        "mail_view_sha256": rendered.view_sha256,
        "template_pack_sha256": rendered.template_sha256,
        "template_version": view["template_version"], "taxonomy_version": view["taxonomy_version"],
        "profile": rendered.profile, "html_sha256": hashlib.sha256(rendered.html).hexdigest(),
        "text_sha256": hashlib.sha256(rendered.text).hexdigest(),
        "mime_sha256": hashlib.sha256(rendered.mime).hexdigest(),
        "html_bytes": len(rendered.html), "mime_bytes": len(rendered.mime),
        "index_object_ids": list(rendered.index_object_ids),
        "detail_object_ids": list(rendered.detail_object_ids),
        "omitted_index_objects": len(view["objects"]) - len(rendered.index_object_ids),
        "omitted_detail_objects": len(view["objects"]) - len(rendered.detail_object_ids),
        "ai_objects_displayed": rendered.ai_objects_displayed,
    }
    schema = json.loads((Path(__file__).parent / "schemas" / "v5" / "render-manifest.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    if errors:
        where = "/".join(map(str, errors[0].path)) or "$"
        raise V5RenderError(f"RENDER_MANIFEST_INVALID:{where}:{errors[0].validator}")
    return manifest
