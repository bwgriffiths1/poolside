"""Outbound email via Resend — briefing-ready notices and the weekly digest.

Deliberately optional: with RESEND_API_KEY / POOLSIDE_EMAIL_FROM unset every
send is a logged no-op, so dev environments and tests never talk to the
network and never fail on missing config. Sends are best-effort — callers
must never let an email failure break the action that triggered it.

Templates are self-contained HTML with inline styles in the app's Editorial
palette (cream / ink / terracotta, serif headings) — email clients strip
stylesheets, so everything rides on the elements.
"""
from __future__ import annotations

import html
import logging
import os
from typing import Any

import requests

log = logging.getLogger("poolside.mailer")

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = 10


def _base_url() -> str:
    return (os.environ.get("POOLSIDE_BASE_URL")
            or "https://poolside.bwgriffiths.com").rstrip("/")


def mail_enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY")
                and os.environ.get("POOLSIDE_EMAIL_FROM"))


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send one email. Returns True on acceptance; False (logged) otherwise."""
    if not mail_enabled():
        log.info("mail disabled (no RESEND_API_KEY/POOLSIDE_EMAIL_FROM); "
                 "would send %r to %s", subject, to)
        return False
    try:
        resp = requests.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "from": os.environ["POOLSIDE_EMAIL_FROM"],
                "to": [to],
                "subject": subject,
                "html": html_body,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 300:
            log.warning("resend rejected email to %s: %s %s",
                        to, resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as e:
        log.warning("email send to %s failed: %s", to, e)
        return False


# ── Templates ──────────────────────────────────────────────────────────────

def _esc(v: Any) -> str:
    return html.escape(str(v or ""), quote=True)


def _shell(title_html: str, body_html: str, footer_html: str | None = None) -> str:
    """The shared Editorial wrapper: cream page, elevated card, footer.

    footer_html overrides the default settings-page footer — invite/reset
    recipients aren't (yet) users with a settings page, so that copy would
    be wrong for them.
    """
    if footer_html is None:
        footer_html = (
            "You're receiving this because email notifications are enabled in your\n"
            f'      <a href="{_base_url()}/#/settings" style="color:#c4633a;">Poolside settings</a>.'
        )
    return f"""\
<div style="margin:0;padding:32px 16px;background:#f6f4ef;font-family:Georgia,'Iowan Old Style',serif;color:#1a1815;">
  <div style="max-width:560px;margin:0 auto;">
    <div style="font-family:Helvetica,Arial,sans-serif;font-size:14px;font-weight:700;letter-spacing:-0.01em;margin-bottom:14px;">
      Poolside<span style="color:#c4633a;">.</span>
    </div>
    <div style="background:#fbf9f4;border:1px solid #e3ddd1;border-radius:10px;padding:28px;">
      {title_html}
      {body_html}
    </div>
    <div style="font-family:Helvetica,Arial,sans-serif;font-size:11px;color:#8a847a;margin-top:14px;line-height:1.5;">
      {footer_html}
    </div>
  </div>
</div>"""


def _meeting_line(m: dict) -> str:
    date = _esc(m.get("meeting_date"))
    committee = _esc(m.get("type_short") or m.get("committee") or "")
    title = _esc(m.get("title") or m.get("type_name") or "Meeting")
    return (
        f'<tr><td style="padding:6px 10px 6px 0;font-family:Menlo,monospace;'
        f'font-size:12px;color:#8a847a;white-space:nowrap;vertical-align:top;">{date}</td>'
        f'<td style="padding:6px 8px;vertical-align:top;">'
        f'<span style="font-family:Helvetica,Arial,sans-serif;font-size:11px;'
        f'color:#c4633a;border:1px solid #e5b89e;border-radius:99px;'
        f'padding:1px 7px;">{committee}</span></td>'
        f'<td style="padding:6px 0;font-size:14px;line-height:1.45;">{title}</td></tr>'
    )


def briefing_approved_email(payload: dict) -> tuple[str, str]:
    """(subject, html) for a watcher's briefing-ready notice."""
    committee = payload.get("committee") or ""
    date = payload.get("meeting_date") or ""
    meeting_id = payload.get("meeting_id")
    subject = f"Briefing ready: {committee} {date}".strip()

    title_html = (
        f'<h1 style="font-size:24px;font-weight:400;margin:0 0 6px;'
        f'letter-spacing:-0.015em;">Briefing ready</h1>'
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;'
        f'color:#4a4640;margin:0 0 18px;">A meeting you watch has an approved briefing.</p>'
    )
    body_html = (
        f'<p style="font-size:16px;margin:0 0 4px;">'
        f'<strong>{_esc(payload.get("title") or committee)}</strong></p>'
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;'
        f'color:#8a847a;margin:0 0 20px;">{_esc(committee)} · {_esc(date)}'
        + (f' · approved by {_esc(payload["approved_by"])}'
           if payload.get("approved_by") else "")
        + "</p>"
        f'<a href="{_base_url()}/#/briefing/{meeting_id}" '
        f'style="display:inline-block;font-family:Helvetica,Arial,sans-serif;'
        f'font-size:13px;background:#c4633a;color:#fbf9f4;text-decoration:none;'
        f'padding:9px 16px;border-radius:6px;">Read the briefing</a>'
    )
    return subject, _shell(title_html, body_html)


_ROLE_BLURB = {
    "admin": "full access, including user management and system settings",
    "editor": "you can edit and approve briefings, run summaries, and manage content",
    "viewer": "read-only access to briefings, meetings, and dockets",
}


def invite_email(payload: dict) -> tuple[str, str]:
    """(subject, html) for a new-user invite. payload: {name, email, role,
    accept_url, expires_days?, invited_by?}."""
    name = payload.get("name") or ""
    role = payload.get("role") or "viewer"
    invited_by = payload.get("invited_by") or ""
    expires_days = payload.get("expires_days")
    subject = "You're invited to Poolside"

    intro = (f"{_esc(invited_by)} has invited you to Poolside"
             if invited_by else "You've been invited to Poolside")
    expiry_note = (
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:12px;'
        f'color:#8a847a;margin:16px 0 0;">This invitation expires in '
        f'{int(expires_days)} days.</p>'
        if expires_days else ""
    )
    title_html = (
        f'<h1 style="font-size:24px;font-weight:400;margin:0 0 6px;'
        f'letter-spacing:-0.015em;">Hi {_esc(name)},</h1>'
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;'
        f'color:#4a4640;margin:0 0 18px;">{intro} — ISO-NE meeting '
        f'intelligence: briefings, dockets, and committee materials.</p>'
    )
    body_html = (
        f'<p style="font-size:15px;margin:0 0 20px;">Your account will be an '
        f'<strong>{_esc(role)}</strong> ({_esc(_ROLE_BLURB.get(role, ""))}).</p>'
        f'<a href="{_esc(payload.get("accept_url"))}" '
        f'style="display:inline-block;font-family:Helvetica,Arial,sans-serif;'
        f'font-size:13px;background:#c4633a;color:#fbf9f4;text-decoration:none;'
        f'padding:9px 16px;border-radius:6px;">Accept invitation</a>'
        + expiry_note
    )
    footer = (f"This invitation was sent to {_esc(payload.get('email'))}. "
              "If you weren't expecting it, you can safely ignore this email.")
    return subject, _shell(title_html, body_html, footer_html=footer)


def password_reset_email(payload: dict) -> tuple[str, str]:
    """(subject, html) for a password reset. payload: {name, email,
    accept_url, expires_days?}."""
    name = payload.get("name") or ""
    expires_days = payload.get("expires_days")
    subject = "Reset your Poolside password"

    expiry_note = (
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:12px;'
        f'color:#8a847a;margin:16px 0 0;">This link expires in '
        f'{int(expires_days)} days.</p>'
        if expires_days else ""
    )
    title_html = (
        f'<h1 style="font-size:24px;font-weight:400;margin:0 0 6px;'
        f'letter-spacing:-0.015em;">Hi {_esc(name)},</h1>'
        f'<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;'
        f'color:#4a4640;margin:0 0 18px;">A password reset was generated for '
        f'your Poolside account.</p>'
    )
    body_html = (
        f'<a href="{_esc(payload.get("accept_url"))}" '
        f'style="display:inline-block;font-family:Helvetica,Arial,sans-serif;'
        f'font-size:13px;background:#c4633a;color:#fbf9f4;text-decoration:none;'
        f'padding:9px 16px;border-radius:6px;">Set a new password</a>'
        + expiry_note
    )
    footer = (f"This reset link was sent to {_esc(payload.get('email'))}. "
              "If you didn't ask for it, you can safely ignore this email — "
              "your current password still works.")
    return subject, _shell(title_html, body_html, footer_html=footer)


def weekly_digest_email(upcoming: list[dict],
                        recent_briefings: list[dict]) -> tuple[str, str]:
    """(subject, html) for the Monday week-ahead digest."""
    subject = "Poolside — your week ahead"

    sections = []
    if upcoming:
        rows = "".join(_meeting_line(m) for m in upcoming)
        sections.append(
            '<h2 style="font-size:17px;font-weight:400;margin:0 0 8px;">'
            "This week's meetings</h2>"
            f'<table style="border-collapse:collapse;width:100%;margin:0 0 22px;">{rows}</table>'
        )
    if recent_briefings:
        rows = ""
        for b in recent_briefings:
            line = _meeting_line(b)
            # Swap the bare title cell for a link into the reader.
            url = f"{_base_url()}/#/briefing/{b.get('id') or b.get('meeting_id')}"
            title = _esc(b.get("title") or b.get("type_name") or "Briefing")
            line = line.replace(
                f'<td style="padding:6px 0;font-size:14px;line-height:1.45;">{title}</td>',
                f'<td style="padding:6px 0;font-size:14px;line-height:1.45;">'
                f'<a href="{url}" style="color:#1a1815;">{title}</a></td>',
            )
            rows += line
        sections.append(
            '<h2 style="font-size:17px;font-weight:400;margin:0 0 8px;">'
            "New briefings from last week</h2>"
            f'<table style="border-collapse:collapse;width:100%;margin:0 0 8px;">{rows}</table>'
        )

    title_html = (
        '<h1 style="font-size:24px;font-weight:400;margin:0 0 6px;'
        'letter-spacing:-0.015em;">Your week ahead</h1>'
        '<p style="font-family:Helvetica,Arial,sans-serif;font-size:13px;'
        'color:#4a4640;margin:0 0 20px;">Upcoming committee meetings and '
        "what got briefed last week.</p>"
    )
    return subject, _shell(title_html, "".join(sections))
