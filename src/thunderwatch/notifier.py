"""Plain-text email composition and SMTP transport."""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

SMTPFactory = Callable[..., Any]


def compose_email(  # noqa: PLR0913, PLR0917
    kind: str,
    location: str,
    hostname: str,
    version: str,
    changes: dict[str, dict[str, Any]] | None = None,
    current: dict[str, str] | None = None,
    now: datetime | None = None,
) -> EmailMessage:
    changes = changes or {}
    current = current or {}
    now = now or datetime.now().astimezone()
    message = EmailMessage()
    if kind == "test":
        subject = f"ThunderWatch test from {location}"
    elif kind == "started":
        value = next(iter(current.values()), "address pending")
        subject = f"ThunderWatch: monitoring started for {location} ({value})"
    else:
        family_names = {"ipv4": "IPv4", "ipv6": "IPv6"}
        details = " and ".join(
            f"{family_names.get(family, family.upper())} changed to {change['new']}"
            for family, change in changes.items()
        )
        subject = f"ThunderWatch: {location} {details}"
    message["Subject"] = subject
    lines: list[str] = []
    if kind == "change":
        for family, change in changes.items():
            providers = ", ".join(change.get("providers", []))
            detail = f"{family.upper()}: {change.get('old') or 'not previously reported'}"
            lines.append(f"{detail} -> {change['new']} (confirmed by {providers})")
        lines.extend(
            [
                f"Detected local: {now.astimezone().isoformat()}",
                f"Detected UTC: {now.astimezone(UTC).isoformat()}",
            ]
        )
    else:
        lines.append(
            "Current addresses: "
            + (", ".join(f"{key}: {value}" for key, value in current.items()) or "not available")
        )
    lines.extend(
        [
            f"Host: {hostname}",
            f"ThunderWatch version: {version}",
            "You may need to update allowlists.",
        ]
    )
    message.set_content("\n".join(lines))
    return message


def send_email(  # noqa: PLR0913, PLR0917
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    sender: str,
    recipient: str,
    message: EmailMessage,
    smtp_factory: SMTPFactory | None = None,
    ssl_factory: SMTPFactory | None = None,
) -> tuple[bool, str]:
    implicit_tls = security == "ssl" or (security == "auto" and port == 465)
    try:
        factory = (
            (ssl_factory or smtplib.SMTP_SSL) if implicit_tls else (smtp_factory or smtplib.SMTP)
        )
        with factory(host, port, timeout=30) as smtp:
            if not implicit_tls:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            smtp.login(username, password)
            refused = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                return False, "SMTP recipient was refused"
        return True, "Email accepted"
    except Exception as exc:  # SMTP implementations use many exception types.
        text = str(exc).replace(password, "[redacted]") if password else str(exc)
        return False, text or type(exc).__name__
