from __future__ import annotations

import hashlib
import json
import base64
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jsonschema import Draft202012Validator, FormatChecker

from .v5_rendering import _PROFILES, _context, validate_mail_view


_TEMPLATES = Path(__file__).parent / "templates" / "innova_v1"
_LOGO = Path(__file__).parent / "assets" / "innova-logo-approved.png"


class InnovaRenderError(ValueError):
    pass


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

