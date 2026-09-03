"""Small SMTP client wrapper."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable

from mailklient.mail.config import SmtpSettings
from mailklient.security import build_xoauth2_payload

SmtpConnectionFactory = Callable[..., smtplib.SMTP]
SmtpSslConnectionFactory = Callable[..., smtplib.SMTP_SSL]


class SmtpClient:
    """SMTP operations used by the application services."""

    def __init__(
        self,
        settings: SmtpSettings,
        connection_factory: SmtpConnectionFactory = smtplib.SMTP,
        ssl_connection_factory: SmtpSslConnectionFactory = smtplib.SMTP_SSL,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory
        self._ssl_connection_factory = ssl_connection_factory

    def test_connection(self) -> bool:
        """Connect, login, quit and report success."""
        context = ssl.create_default_context()

        if self._settings.security == "ssl":
            connection = self._ssl_connection_factory(
                self._settings.host,
                self._settings.port,
                context=context,
            )
        elif self._settings.security == "starttls":
            connection = self._connection_factory(
                self._settings.host,
                self._settings.port,
            )
            connection.starttls(context=context)
        else:
            raise ValueError(f"Unsupported SMTP security mode: {self._settings.security}")

        try:
            if self._settings.auth_method == "oauth2":
                connection.auth(
                    "XOAUTH2",
                    lambda _challenge=None: build_xoauth2_payload(
                        self._settings.username,
                        self._settings.password,
                    ),
                    initial_response_ok=True,
                )
            else:
                connection.login(self._settings.username, self._settings.password)
            return True
        finally:
            connection.quit()
