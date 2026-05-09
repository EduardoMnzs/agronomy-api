"""Email transacional via Resend. No-op se RESEND_API_KEY não estiver configurado."""
from __future__ import annotations

import html
import logging
from typing import Any
from urllib.parse import urlparse

from core.config import settings

logger = logging.getLogger(__name__)


def _safe_url(url: str) -> str:
    # Bloqueia javascript:, data:, file:, etc.
    try:
        parsed = urlparse(url)
    except ValueError:
        return "#"
    if parsed.scheme not in ("http", "https"):
        return "#"
    return html.escape(url, quote=True)


def _e(text: str | None) -> str:
    return html.escape(text or "", quote=True)


def _is_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.FROM_EMAIL)


def _from_address() -> str:
    email = settings.FROM_EMAIL
    name = (settings.APP_NAME or "").strip()
    if name and "<" not in email:
        return f"{name} <{email}>"
    return email


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Nunca levanta — falhas são logadas para que fluxos de auth não retornem 500."""
    if not _is_configured():
        logger.warning(
            "RESEND not configured, logging email instead.\nTO: %s\nSUBJECT: %s\n%s",
            to, subject, text or html,
        )
        return False

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        params: dict[str, Any] = {
            "from": _from_address(),
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if text:
            params["text"] = text
        resend.Emails.send(params)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send email to %s", to)
        return False


def password_reset_email(full_name: str, reset_url: str) -> tuple[str, str, str]:
    subject = "Redefinição de senha - Agronomy"
    text = (
        f"Olá, {full_name}.\n\n"
        f"Recebemos uma solicitação para redefinir sua senha.\n"
        f"Clique no link abaixo para criar uma nova:\n\n"
        f"{reset_url}\n\n"
        f"Se você não fez essa solicitação, ignore este e-mail.\n"
        f"O link expira em 30 minutos."
    )
    safe_name = _e(full_name)
    safe_url = _safe_url(reset_url)
    body = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1 style="color:#131E29; font-size:20px;">Redefinir senha</h1>
  <p>Olá, <strong>{safe_name}</strong>.</p>
  <p>Recebemos uma solicitação para redefinir sua senha na Agronomy.</p>
  <p style="margin:28px 0;">
    <a href="{safe_url}"
       style="background:#EC6608; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:600; display:inline-block;">
      Criar nova senha
    </a>
  </p>
  <p style="color:#666; font-size:13px;">Se você não fez essa solicitação, ignore este e-mail.</p>
  <p style="color:#999; font-size:12px;">O link expira em 30 minutos.</p>
</div>"""
    return subject, body, text


def access_request_decision_email(full_name: str, approved: bool, login_url: str, reason: str | None = None) -> tuple[str, str, str]:
    safe_name = _e(full_name)
    safe_url = _safe_url(login_url)

    if approved:
        subject = "Seu acesso à Agronomy foi aprovado"
        text = (
            f"Olá, {full_name}.\n\n"
            f"Sua solicitação de acesso foi aprovada. "
            f"Acesse {login_url} para definir sua senha e entrar na plataforma."
        )
        body = f"""\
<div style="font-family: sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1 style="color:#EC6608;">Acesso aprovado!</h1>
  <p>Olá, <strong>{safe_name}</strong>.</p>
  <p>Sua solicitação de acesso à Agronomy foi aprovada.</p>
  <p style="margin:28px 0;">
    <a href="{safe_url}" style="background:#EC6608; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:600;">
      Acessar plataforma
    </a>
  </p>
  <p style="color:#666;">Use o e-mail com o qual você solicitou o acesso e a senha temporária fornecida pelo administrador.</p>
</div>"""
    else:
        reason_txt = f"\n\nMotivo: {reason}" if reason else ""
        subject = "Sua solicitação de acesso à Agronomy"
        text = (
            f"Olá, {full_name}.\n\n"
            f"Infelizmente sua solicitação de acesso não foi aprovada no momento.{reason_txt}\n\n"
            f"Se tiver dúvidas, entre em contato com o administrador."
        )
        reason_block = (
            f'<p style="background:#f5f5f5; padding:12px; border-radius:6px;">'
            f'<strong>Motivo:</strong> {_e(reason)}</p>'
            if reason else ''
        )
        body = f"""\
<div style="font-family: sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1>Solicitação não aprovada</h1>
  <p>Olá, <strong>{safe_name}</strong>.</p>
  <p>Infelizmente sua solicitação de acesso à Agronomy não foi aprovada no momento.</p>
  {reason_block}
  <p style="color:#666;">Se tiver dúvidas, entre em contato com o administrador.</p>
</div>"""
    return subject, body, text
