import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .config import Settings, settings


class NotificationService:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def status(self) -> dict[str, bool]:
        return {
            "webhook_enabled": self.config.webhook_enabled,
            "email_enabled": self.config.email_enabled,
        }

    async def send(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        tasks = [self._send_webhook(url, payload) for url in self.config.webhook_urls]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    errors.append(str(result))
        if self.config.email_enabled:
            try:
                await asyncio.to_thread(self._send_email, payload)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    async def _send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

    def _send_email(self, payload: dict[str, Any]) -> None:
        message = EmailMessage()
        state = str(payload.get("state") or "notice").upper()
        instance_name = str(payload.get("instance", {}).get("name") or "Adobe2API")
        message["Subject"] = f"[{state}] {instance_name}: {payload.get('rule_name', 'Manager notice')}"
        message["From"] = self.config.smtp_from
        message["To"] = ", ".join(self.config.smtp_to)
        message.set_content(
            "\n".join(
                [
                    f"Instance: {instance_name}",
                    f"Location: {payload.get('instance', {}).get('location', '')}",
                    f"State: {payload.get('state', '')}",
                    f"Severity: {payload.get('severity', '')}",
                    f"Message: {payload.get('message', '')}",
                    f"Time: {payload.get('timestamp', '')}",
                ]
            )
        )
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=10) as smtp:
            if self.config.smtp_starttls:
                smtp.starttls()
            if self.config.smtp_username:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(message)

    async def send_test(self) -> list[str]:
        return await self.send(
            {
                "event": "test",
                "state": "test",
                "severity": "info",
                "rule_id": "notification_test",
                "rule_name": "Notification test",
                "message": "Adobe2API Manager notification channel is working.",
                "timestamp": __import__("time").time(),
                "instance": {"id": None, "name": "Manager", "location": ""},
            }
        )


notification_service = NotificationService()
