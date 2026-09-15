"""The desktop uses the same bounded queries as a future read adapter."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from mailklient.domain.errors import EmailNotFound, MailStoreUnavailable
from mailklient.services import MailService, MailStore
from mailklient.ui.main_window import MainWindow


def test_gui_search_and_details_use_shared_service_without_truncating(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "mail.sqlite3")
    account = store.add_account("Test", "me@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    for index in range(205):
        last = store.add_message(account.id, inbox.id, body_text=f"body-{index}-end")
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    service = MailService(store.database_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("UI must use the injected service for mail reads")

    monkeypatch.setattr("mailklient.ui.main_window.MailService", forbidden)
    monkeypatch.setattr(store, "list_messages", forbidden)
    monkeypatch.setattr(store, "list_mailbox_messages", forbidden)
    monkeypatch.setattr(store, "list_unified_inbox_messages", forbidden)
    window = MainWindow(store, preferences=settings, mail_service=service)
    assert window._mail_service is service
    searches, details = [], []
    original_search = window._mail_service.search_emails
    original_read = window._mail_service.get_email

    def search(*args, **kwargs):
        searches.append(kwargs)
        return original_search(*args, **kwargs)

    def read(identifier):
        details.append(identifier)
        return original_read(identifier)

    monkeypatch.setattr(window._mail_service, "search_emails", search)
    monkeypatch.setattr(window._mail_service, "get_email", read)
    try:
        window._apply_message_filters()
        assert [call["offset"] for call in searches] == [0, 200]
        assert window.message_list.count() == 205
        window.message_search_edit.setText("body-204-end")
        assert window.message_list.count() == 1
        window.message_list.setCurrentRow(0)
        assert details == [last.id]
        assert store.get_message(last.id).is_read is False

        def missing(*args, **kwargs):
            raise EmailNotFound("Email is not in the local cache.")

        monkeypatch.setattr(service, "get_email", missing)
        window._show_selected_message(window.message_list.currentItem(), None)
        assert window.sync_status_label.text() == "Email is not in the local cache."
        assert not window.mark_read_button.isEnabled()

        def unavailable(*args, **kwargs):
            raise MailStoreUnavailable("Cache unavailable.")

        monkeypatch.setattr(window._mail_service, "search_emails", unavailable)
        window.message_search_edit.setText("changed")
        assert window.message_list.count() == 0
        assert window.sync_status_label.text() == "Cache unavailable."

        def fail_second_page(*args, **kwargs):
            if kwargs["offset"]:
                raise MailStoreUnavailable("Second page unavailable.")
            return original_search(*args, **kwargs)

        monkeypatch.setattr(service, "search_emails", fail_second_page)
        window.message_search_edit.clear()
        assert window.message_list.count() == 0
        assert window._current_messages == []
        assert window.sync_status_label.text() == "Second page unavailable."
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
