import logging
from typing import Iterable, Optional

import resend

from app.config import settings


logger = logging.getLogger("imageopz.email")


class EmailService:
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.resend_api_key) and bool(settings.resend_from_email)

    @staticmethod
    def send_html(
        *,
        to: Iterable[str],
        subject: str,
        html: str,
        from_email: Optional[str] = None,
    ) -> dict:
        """
        Send an email using Resend Python SDK.

        Quickstart reference: https://resend.com/docs/send-with-python
        """
        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is not configured")

        resend.api_key = settings.resend_api_key

        params: resend.Emails.SendParams = {
            "from": from_email or settings.resend_from_email,
            "to": list(to),
            "subject": subject,
            "html": html,
        }

        try:
            result = resend.Emails.send(params)
            if isinstance(result, dict) and result.get("error"):
                logger.warning("Resend send returned error: %r", result.get("error"))
            return result if isinstance(result, dict) else {"result": result}
        except Exception:
            logger.exception("Failed to send email via Resend")
            raise

