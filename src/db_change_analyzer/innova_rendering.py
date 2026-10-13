from __future__ import annotations

import hashlib
import json
import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jsonschema import Draft202012Validator, FormatChecker

from .v5_rendering import _PROFILES, _context, _date, validate_mail_view


_TEMPLATES = Path(__file__).parent / "templates" / "innova_v1"
_LOGO = Path(__file__).parent / "assets" / "innova-logo-approved.png"


class InnovaRenderError(ValueError):
    pass


def _format_analysis_duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "Kaydedilmedi"

    total_seconds = (milliseconds + 500) // 1000
    hours, remaining_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining_seconds, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} saat")
    if minutes:
        parts.append(f"{minutes} dakika")
    if seconds:
        parts.append(f"{seconds} saniye")
    if not parts:
        parts.append("0 saniye")
    return " ".join(parts)


def _display_model_name(configured_model: str) -> str:
    model_id = configured_model.rsplit("/", 1)[-1]
    aliases = {
        "Qwen3.8-27B-FP8": "Qwen3.8 27B-IT",
    }
    return aliases.get(model_id, model_id)


def _sequence_start_direction(obj: dict[str, Any]) -> str | None:
    if obj["identity"].get("object_type") != "SEQUENCE" or obj.get("operation") != "modified":
        return None
    facts = [fact for fact in obj["facts"] if fact["change_action"] != "unchanged" or fact["context_only"]]
    if len(facts) != 1 or facts[0]["taxonomy_id"] != "sequence.sequence_property.start_with":
        return None
    before, after = facts[0]["before"], facts[0]["after"]
    if before.get("state") != "present" or after.get("state") != "present":
        return None
    try:
        before_value, after_value = int(before["value"]), int(after["value"])
    except (TypeError, ValueError):
        return None
    if after_value > before_value:
        return "İleri"
    if after_value < before_value:
        return "Geri"
    return None


_OBJECT_PRIORITY = {
    "TABLE": 10,
    "VIEW": 20,
    "PACKAGE_BODY": 30,
    "PROCEDURE": 40,
    "FUNCTION": 40,
    "TRIGGER": 40,
    "PACKAGE_SPEC": 50,
    "TYPE": 50,
    "TYPE_BODY": 50,
    "INDEX": 60,
    "SEQUENCE": 70,
}

_INNOVA_OBJECT_LABELS = {
    "TABLE": "Tablo",
    "VIEW": "Görünüm",
    "PACKAGE_BODY": "Paket gövdesi",
    "PROCEDURE": "Prosedür",
    "FUNCTION": "Fonksiyon",
    "TRIGGER": "Tetikleyici",
    "PACKAGE_SPEC": "Paket tanımı",
    "TYPE": "Tür tanımı",
    "TYPE_BODY": "Tür gövdesi",
    "INDEX": "İndeks",
    "SEQUENCE": "Sıra üreticisi",
}


def _diff_row_sort_key(row: dict[str, str]) -> tuple[int, int]:
    object_type = row.get("tur") or "Diğer"
    priority = _OBJECT_PRIORITY.get(object_type, 100)
    if object_type != "SEQUENCE":
        return priority, 0
    direction_priority = {
        "Geri gitti": 0,
        "İleri gitti": 1,
    }
    return priority, direction_priority.get(row.get("degisiklik", ""), 2)


def _build_professional_summary(
    *,
    object_count: int,
    object_type_counts: Counter[str],
    risk: dict[str, Any],
) -> str:
    type_summary = ", ".join(
        f"{_INNOVA_OBJECT_LABELS.get(object_type, 'Diğer')} ({count})"
        for object_type, count in sorted(object_type_counts.items())
    ) or "Değişiklik nesnesi bulunmuyor"
    if object_count:
        return (
            f"Bu karşılaştırmada {object_count} veritabanı nesnesinde değişiklik tespit edildi. "
            f"Değişikliklerin kapsamı: {type_summary}. "
            f"Genel risk seviyesi {risk['label']} olarak değerlendirildi; {risk['confidence_label'].lower()}. "
            "Bu değerlendirme yalnızca kaynak farklarına dayanır; canlı veritabanı, uygulama kullanımı "
            "ve dağıtım durumu kontrol edilmedi."
        )
    return "Bu karşılaştırmada veritabanı nesnelerinde doğrulanmış bir değişiklik tespit edilmedi. Canlı veritabanı kontrol edilmedi."


def _build_impact_commentary(impact: dict[str, Any]) -> str:
    status = impact["status_label"]
    candidate_count = impact["candidate_count"]
    if status == "Repository içi statik kanıt bulundu":
        return (
            f"Kaynak kodu taramasında {candidate_count} statik bağımlılık adayı bulundu. "
            "Bunlar olası ilişkilerdir; çalışma zamanı kullanımı veya canlı etki kanıtı değildir. "
            "İlgili uygulama akışları ayrıca doğrulanmalıdır."
        )
    if status == "Repository içi statik bağımlılık kanıtı bulunmadı":
        return (
            "Kaynak kodu taramasında bu değişikliklerle ilişkili doğrulanmış bir uygulama bağımlılığı bulunmadı. "
            "Bu sonuç, nesnelerin kullanılmadığını kanıtlamaz; yalnızca mevcut incelemede kanıt bulunmadığını gösterir."
        )
    return (
        "Uygulama bağımlılıkları için yeterli inceleme verisi bulunmadığından etki hakkında kesin yorum yapılamıyor. "
        "İlgili kullanım ve bağımlılıkların ayrıca kontrol edilmesi gerekir."
    )


def _new_analysis_context(view: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Adapt the validated mail view to the current corporate email template."""
    source = view["source"]
    analysis = view["analysis"]
    ai = analysis["ai"]
    models = ai.get("models", [])
    model_name = ", ".join(
        dict.fromkeys(_display_model_name(item["configured_model"]) for item in models)
    ) if models else "AI çağrısı yapılmadı"
    completed_at = analysis.get("completed_at") or source.get("observed_at")
    timezone = analysis["display_timezone"]
    objects = view["objects"]
    operation_labels = {
        "added": "Eklendi", "modified": "Güncellendi", "removed": "Silindi",
        "relocated": "Taşındı", "unknown": "Doğrulanamadı",
    }
    # The complete object inventory remains in report.json and the archive.
    # The email uses the renderer's selected detail window so a large commit
    # still produces a deliverable message within the configured MIME limit.
    displayed_objects = context.get("detail_objects") or context.get("index_objects") or objects
    displayed_keys = {
        item.get("anchor", "").removeprefix("nesne-")
        for item in displayed_objects
    }
    diff_rows: list[dict[str, str]] = []
    sequence_counts = {"İleri": 0, "Geri": 0}
    for obj in objects:
        identity = obj["identity"]
        facts = [fact for fact in obj["facts"] if fact["change_action"] != "unchanged" or fact["context_only"]]
        sequence_direction = _sequence_start_direction(obj)
        if sequence_direction:
            sequence_counts[sequence_direction] += 1
            continue
        if obj["object_id"] not in displayed_keys:
            continue
        comments = [comment["text_tr"] for comment in obj.get("ai_comments", [])]
        description = comments[0] if comments else obj.get("deterministic_summary_tr", "Kaynak farkı doğrulandı.")
        if obj.get("verification") != "verified":
            description += " Ayrıntılı ayrıştırma sınırlı."
        diff_rows.append({
            "tur": identity.get("object_type") or "Diğer",
            "nesne": ((identity.get("schema_name") + ".") if identity.get("schema_name") else "") + identity["name"],
            "degisiklik": operation_labels.get(obj["operation"], "Doğrulanamadı"),
            "aciklama": description,
        })
    sequence_rows = [
        {
            "tur": "SEQUENCE",
            "nesne": f"{count} sequence",
            "degisiklik": f"{direction} gitti",
            "aciklama": f"{count} sequence tek satırda toplandı.",
        }
        for direction, count in sequence_counts.items()
        if count
    ]
    diff_rows = sorted(sequence_rows + diff_rows, key=_diff_row_sort_key)
    risk = context["risk_summary"]
    impact = context["impact_summary"]
    limited_count = sum(obj.get("verification") != "verified" for obj in objects)
    object_count = len(objects)
    object_type_counts = Counter(obj["identity"].get("object_type", "Diğer") for obj in objects)
    verified = f"{object_count} nesne değişikliği tespit edildi."
    possible = _build_impact_commentary(impact)
    assessment = (
        f"Genel risk seviyesi {risk['label']} olarak değerlendirildi; {risk['confidence_label'].lower()}. "
        "Sınıflandırma kaynak farkları ve statik kurallara dayanıyor; canlı veritabanı kontrol edilmedi."
    )
    if limited_count:
        assessment += f" {limited_count} nesnenin ayrıntılı ayrıştırması sınırlı."
    assessment += " Dağıtım öncesinde ilgili nesnelerin uygulama kullanımı ve bağımlılıkları doğrulanmalıdır."
    summary = _build_professional_summary(
        object_count=object_count,
        object_type_counts=object_type_counts,
        risk=risk,
    )
    return {
        "report_title": "Veritabanı Değişiklik Analizi",
        "system_context": f"{source['repository']} · Git snapshot karşılaştırması",
        "ornek": bool(view.get("synthetic")),
        "model_name": model_name,
        "analysis_duration": _format_analysis_duration(analysis.get("elapsed_ms")),
        "total_changes": str(object_count),
        "risk_level": risk["label"],
        "risk_color": risk["fg"],
        "analysis_datetime": _date(completed_at, timezone),
        "summary": summary,
        "verified": verified,
        "possible": possible,
        "assessment": assessment,
        "diff_rows": diff_rows,
        "old_snapshot": source["base_label"],
        "new_snapshot": source["target_label"],
        "analysis_id": view["report_id"],
        "scope_name": f"{source['repository']} / {source['branch']}",
        "scope_detail": f"{view['changed_files']} dosya · {object_count} çözümlenen nesne",
        "scope_kinds": f"Karşılaştırma: {source['comparison_mode']} · Canlı veritabanı kullanılmadı",
        "bottom_notice": "Bu rapor Git snapshot kaynaklarıyla hazırlanmıştır; canlı veritabanı doğrulaması içermez.",
    }


@dataclass(frozen=True, slots=True)
class InnovaRendered:
    html: bytes
    email_html: bytes
    text: bytes
    mime: bytes
    profile: str
    view_sha256: str
    template_sha256: str
    index_object_ids: tuple[str, ...]
    detail_object_ids: tuple[str, ...]
    ai_objects_displayed: int


def _template_digest() -> str:
    files = {path.relative_to(_TEMPLATES.parent).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(_TEMPLATES.rglob("*.j2"))}
    files["approved-logo.png"] = hashlib.sha256(_LOGO.read_bytes()).hexdigest()
    value = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def render_innova_view(view: dict[str, Any], *, sender: str, recipients: list[str], message_id: str,
                       date: datetime, html_limit: int = 150_000, mime_limit: int = 180_000) -> InnovaRendered:
    validate_mail_view(view)
    if not _LOGO.is_file() or _LOGO.is_symlink():
        raise InnovaRenderError("APPROVED_LOGO_MISSING")
    if date.tzinfo is None or not sender or not recipients:
        raise InnovaRenderError("MAIL_HEADERS_INVALID")
    required_templates = (_TEMPLATES / "report_email.html.j2", _TEMPLATES / "report_email.txt.j2")
    if any(not path.is_file() or path.is_symlink() for path in required_templates):
        raise InnovaRenderError("APPROVED_TEMPLATE_MISSING")
    html_env = Environment(loader=FileSystemLoader(_TEMPLATES), undefined=StrictUndefined,
                           autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True))
    text_env = Environment(loader=FileSystemLoader(_TEMPLATES), undefined=StrictUndefined, autoescape=False)
    view_json = json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    logo_data_uri = "data:image/png;base64," + base64.b64encode(_LOGO.read_bytes()).decode("ascii")
    for profile in _PROFILES:
        context = _context(view, profile, "")
        context["source"] = view["source"]
        context["logo_src"] = logo_data_uri
        context["r"] = _new_analysis_context(view, context)
        html = html_env.get_template("report_email.html.j2").render(**context).encode("utf-8")
        email_context = dict(context)
        email_context["logo_src"] = "cid:innova-logo"
        email_html = html_env.get_template("report_email.html.j2").render(**email_context).encode("utf-8")
        text = text_env.get_template("report_email.txt.j2").render(**context).encode("utf-8")
        if len(html) > html_limit:
            continue
        mail = EmailMessage(policy=SMTP)
        mail["Subject"] = f"[İnnova DB] {context['counts']['primary_total']} nesne kaynak farkı"
        mail["From"], mail["To"] = sender, ", ".join(recipients)
        mail["Date"], mail["Message-ID"] = format_datetime(date), message_id
        mail.set_content(text.decode("utf-8"))
        mail.add_alternative(email_html.decode("utf-8"), subtype="html")
        html_part = mail.get_body(preferencelist=("html",))
        if html_part is None:
            raise InnovaRenderError("HTML_PART_MISSING")
        html_part.add_related(_LOGO.read_bytes(), maintype="image", subtype="png", cid="<innova-logo>")
        mail.set_boundary("innova-" + hashlib.sha256(email_html).hexdigest()[:32])
        mime = mail.as_bytes()
        if len(mime) <= mime_limit:
            return InnovaRendered(html, email_html, text, mime, profile[0], hashlib.sha256(view_json).hexdigest(),
                                  _template_digest(),
                                  tuple(item["anchor"].removeprefix("nesne-") for item in context["index_objects"]),
                                  tuple(item["anchor"].removeprefix("nesne-") for item in context["detail_objects"]),
                                  sum(bool(item["comments"]) for item in context["detail_objects"]))
    raise InnovaRenderError("MAIL_SIZE_LIMIT")


def build_innova_render_manifest(view: dict[str, Any], rendered: InnovaRendered, *, source_report_sha256: str | None,
                                 generation: int = 0) -> dict[str, Any]:
    manifest = {
        "schema_version": "render-manifest/2.0", "report_id": view["report_id"],
        "synthetic": view["synthetic"], "render_generation": generation,
        "source_report_sha256": source_report_sha256, "mail_view_sha256": rendered.view_sha256,
        "template_pack_sha256": rendered.template_sha256, "template_version": "innova-db-report/1.0",
        "taxonomy_version": view["taxonomy_version"], "profile": rendered.profile,
        "html_sha256": hashlib.sha256(rendered.html).hexdigest(), "text_sha256": hashlib.sha256(rendered.text).hexdigest(),
        "email_html_sha256": hashlib.sha256(rendered.email_html).hexdigest(),
        "mime_sha256": hashlib.sha256(rendered.mime).hexdigest(), "html_bytes": len(rendered.html),
        "email_html_bytes": len(rendered.email_html), "mime_bytes": len(rendered.mime),
        "index_object_ids": list(rendered.index_object_ids), "detail_object_ids": list(rendered.detail_object_ids),
        "omitted_index_objects": len(view["objects"]) - len(rendered.index_object_ids),
        "omitted_detail_objects": len(view["objects"]) - len(rendered.detail_object_ids),
        "ai_objects_displayed": rendered.ai_objects_displayed,
    }
    schema = json.loads((Path(__file__).parent / "schemas" / "v5" / "render-manifest.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    if errors:
        where = "/".join(map(str, errors[0].path)) or "$"
        raise InnovaRenderError(f"RENDER_MANIFEST_INVALID:{where}:{errors[0].validator}")
    return manifest

