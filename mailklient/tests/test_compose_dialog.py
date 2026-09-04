from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from mailklient.domain import Account
from mailklient.services import ComposeDraft
from mailklient.ui import compose_dialog
from mailklient.ui.compose_dialog import ComposeDialog


def _get_qapplication() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_compose_dialog_returns_draft_data() -> None:
    _get_qapplication()
    accounts = [
        Account(id=1, display_name="Privat", email_address="privat@example.com"),
        Account(id=2, display_name="Arbeid", email_address="arbeid@example.com"),
    ]
    dialog = ComposeDialog(
        accounts,
        ComposeDraft(
            account_id=2,
            recipients="sender@example.com",
            subject="Re: Hei",
            body_text="Svartekst",
            in_reply_to="<original@example.com>",
        ),
    )

    draft = dialog.draft()

    assert draft.account_id == 2
    assert draft.recipients == "sender@example.com"
    assert draft.cc == ""
    assert draft.bcc == ""
    assert draft.subject == "Re: Hei"
    assert draft.body_text == "Svartekst"
    assert "Svartekst" in draft.body_html
    assert draft.attachment_paths == ()
    assert draft.in_reply_to == "<original@example.com>"

    dialog.close()


def test_compose_dialog_returns_cc_bcc_and_attachments(
    tmp_path,
    monkeypatch,
) -> None:
    _get_qapplication()
    attachment_path = tmp_path / "rapport.txt"
    attachment_path.write_text("rapport", encoding="utf-8")
    accounts = [
        Account(id=1, display_name="Privat", email_address="privat@example.com"),
    ]
    dialog = ComposeDialog(
        accounts,
        ComposeDraft(
            account_id=1,
            recipients="friend@example.com",
            cc="copy@example.com",
            bcc="hidden@example.com",
            attachment_paths=(str(attachment_path),),
        ),
    )

    monkeypatch.setattr(
        compose_dialog.QFileDialog,
        "getOpenFileNames",
        lambda *_args, **_kwargs: ([str(attachment_path)], ""),
    )
    dialog._add_attachments()

    draft = dialog.draft()

    assert draft.cc == "copy@example.com"
    assert draft.bcc == "hidden@example.com"
    assert draft.attachment_paths == (str(attachment_path),)

    dialog.close()
