"""TLS trust for mail servers and explicitly configured local bridges."""

from __future__ import annotations

import ssl


def create_mail_ssl_context(
    host: str, local_certificate: str | None = None
) -> ssl.SSLContext:
    """Keep local certificate trust separate from ordinary mail connections."""
    if local_certificate is not None and host != "127.0.0.1":
        raise ValueError("Local bridge certificates require host 127.0.0.1.")
    return ssl.create_default_context(cafile=local_certificate)
