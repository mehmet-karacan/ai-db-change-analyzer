from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from typing import Callable

from .config import SmtpConfig


class SmtpDeliveryError(RuntimeError):
    def __init__(self, code: str, *, unknown: bool = False, permanent: bool = False) -> None:
        self.code = code
        self.unknown = unknown
        self.permanent = permanent
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SmtpResult:
    accepted: tuple[str, ...]
    refused: dict[str, int]


class SmtpTransport:
    def __init__(self, config: SmtpConfig, *, factory: Callable[..., smtplib.SMTP] = smtplib.SMTP) -> None:
        self.config = config
        self.factory = factory

    def send(
        self,
        mime_bytes: bytes,
        recipients: list[str],
        *,
        username: str | None,
        password: str | None,
        on_data_started: Callable[[], None],
    ) -> SmtpResult:
        smtp: smtplib.SMTP | None = None
        data_started = False
        try:
            smtp = self.factory(self.config.host, self.config.port, timeout=self.config.timeout_seconds)
            code, _ = smtp.ehlo()
            if code != 250:
                raise SmtpDeliveryError("SMTP_EHLO")
            context = ssl.create_default_context(cafile=self.config.ca_file or None)
            code, _ = smtp.starttls(context=context)
            if code != 220:
                raise SmtpDeliveryError("SMTP_STARTTLS")
            code, _ = smtp.ehlo()
            if code != 250:
                raise SmtpDeliveryError("SMTP_TLS_EHLO")
            if self.config.auth_mode == "login":
                if not username or not password:
                    raise SmtpDeliveryError("SMTP_CREDENTIAL_MISSING", permanent=True)
                smtp.login(username, password)
            code, _ = smtp.mail(self.config.sender)
            if code != 250:
                raise SmtpDeliveryError("SMTP_SENDER_REJECTED", permanent=code >= 500)
            accepted: list[str] = []
            refused: dict[str, int] = {}
            for recipient in recipients:
                code, _ = smtp.rcpt(recipient)
                if code in {250, 251}:
                    accepted.append(recipient)
                else:
                    refused[recipient] = code
            if not accepted:
                raise SmtpDeliveryError("SMTP_NO_RECIPIENT_ACCEPTED", permanent=bool(refused) and all(code >= 500 for code in refused.values()))
            on_data_started()
            data_started = True
            code, _ = smtp.data(mime_bytes)
            if code != 250:
                raise SmtpDeliveryError("SMTP_DATA_REJECTED", unknown=False, permanent=code >= 500)
            return SmtpResult(tuple(accepted), refused)
        except SmtpDeliveryError:
            raise
        except (TimeoutError, OSError, smtplib.SMTPException) as exc:
            raise SmtpDeliveryError("SMTP_DATA_UNKNOWN" if data_started else "SMTP_TRANSPORT", unknown=data_started) from exc
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except (OSError, smtplib.SMTPException):
                    pass
