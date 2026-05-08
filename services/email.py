"""
Email service using Resend.

Gracefully no-ops when RESEND_API_KEY is not configured (logs the email body
instead) so dev/test environments don't need credentials.
"""
from __future__ import annotations

import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


def _is_configured() -> bool:
    return bool(settings.RESEND_API_KEY and settings.FROM_EMAIL)


def _from_address() -> str:
    """Formata o remetente com o nome da app: 'Agronomy <noreply@gaek.com.br>'."""
    email = settings.FROM_EMAIL
    name = (settings.APP_NAME or "").strip()
    if name and "<" not in email:
        return f"{name} <{email}>"
    return email


def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """
    Send a transactional email. Returns True if delivered, False on failure.
    Never raises — failures are logged so auth flows don't 500.
    """
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
    """Returns (subject, html, text) for password reset."""
    subject = "Redefinição de senha - Agronomy"
    text = (
        f"Olá, {full_name}.\n\n"
        f"Recebemos uma solicitação para redefinir sua senha.\n"
        f"Clique no link abaixo para criar uma nova:\n\n"
        f"{reset_url}\n\n"
        f"Se você não fez essa solicitação, ignore este e-mail.\n"
        f"O link expira em 30 minutos."
    )
    html = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1 style="color:#131E29; font-size:20px;">Redefinir senha</h1>
  <p>Olá, <strong>{full_name}</strong>.</p>
  <p>Recebemos uma solicitação para redefinir sua senha na Agronomy.</p>
  <p style="margin:28px 0;">
    <a href="{reset_url}"
       style="background:#EC6608; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:600; display:inline-block;">
      Criar nova senha
    </a>
  </p>
  <p style="color:#666; font-size:13px;">Se você não fez essa solicitação, ignore este e-mail.</p>
  <p style="color:#999; font-size:12px;">O link expira em 30 minutos.</p>
</div>"""
    return subject, html, text


def access_request_decision_email(full_name: str, approved: bool, login_url: str, reason: str | None = None) -> tuple[str, str, str]:
    """Email enviado quando admin aprova ou rejeita pedido de acesso."""
    if approved:
        subject = "Seu acesso à Agronomy foi aprovado"
        text = (
            f"Olá, {full_name}.\n\n"
            f"Sua solicitação de acesso foi aprovada. "
            f"Acesse {login_url} para definir sua senha e entrar na plataforma."
        )
        html = f"""\
<div style="font-family: sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1 style="color:#EC6608;">Acesso aprovado!</h1>
  <p>Olá, <strong>{full_name}</strong>.</p>
  <p>Sua solicitação de acesso à Agronomy foi aprovada.</p>
  <p style="margin:28px 0;">
    <a href="{login_url}" style="background:#EC6608; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:600;">
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
        html = f"""\
<div style="font-family: sans-serif; max-width:560px; margin:0 auto; padding:24px; color:#131E29;">
  <h1>Solicitação não aprovada</h1>
  <p>Olá, <strong>{full_name}</strong>.</p>
  <p>Infelizmente sua solicitação de acesso à Agronomy não foi aprovada no momento.</p>
  {f'<p style="background:#f5f5f5; padding:12px; border-radius:6px;"><strong>Motivo:</strong> {reason}</p>' if reason else ''}
  <p style="color:#666;">Se tiver dúvidas, entre em contato com o administrador.</p>
</div>"""
    return subject, html, text
