"""Start the local read-only MCP server over stdin/stdout."""

from __future__ import annotations

import argparse
from pathlib import Path

from mailklient.config import default_database_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only mail MCP server over stdio (existing local cache only)."
    )
    parser.add_argument(
        "--database", type=Path,
        help="Absolute path to an existing cache; defaults to MAILKLIENT_DATABASE_PATH "
        "or the desktop's data directory.",
    )
    args = parser.parse_args(argv)
    try:
        database_path = (
            args.database.expanduser() if args.database else default_database_path()
        )
        if not database_path.is_absolute():
            raise ValueError("Mail cache configuration must use an absolute path.")
    except ValueError as error:
        parser.error(str(error))

    try:
        from mailklient.mcp_server.server import create_server
    except ModuleNotFoundError as error:
        if error.name not in {"mcp", "pydantic"}:
            raise
        parser.error("Install MCP support first: python -m pip install -e '.[mcp]'")

    from mailklient.services.mail_service import MailService

    create_server(MailService(database_path)).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
