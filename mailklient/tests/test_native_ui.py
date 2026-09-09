"""The compact reader retains its space, actions and native palette."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QListWidgetItem,
    QStyle,
    QStyleOptionSlider,
)

from mailklient.domain import Attachment
from mailklient.services import MailStore
from mailklient.ui.main_window import MainWindow
from mailklient.ui.presentation import ElidedLabel


@pytest.fixture
def reader(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = MailStore(tmp_path / "mail.sqlite3")
    account = store.add_account("Test", "test@example.com")
    inbox = store.add_folder(account.id, "INBOX")
    message = store.add_message(
        account.id, inbox.id, subject="Testmelding", body_text="Hei!"
    )
    store.replace_message_attachments(
        message.id,
        [
            Attachment(0, message.id, "note.txt", "text/plain", 5, content=b"Hello"),
            Attachment(0, message.id, "second.txt", "text/plain", 6, content=b"Second"),
        ],
    )
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    settings.setValue("sync/automatic", False)
    window = MainWindow(store, preferences=settings)
    window.message_list.setCurrentRow(0)
    window.show()
    app.processEvents()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_attachment_dialog_does_not_take_space_from_message(reader):
    assert reader.attachment_bar.isVisible()
    assert reader.attachment_bar.height() == 42
    assert reader.attachments_button.text() == "2 attachments"
    assert not reader.attachment_dialog.isVisible()
    assert reader.attachment_list.currentRow() == -1
    assert "note.txt" not in reader.message_view.rendered_html()
    height = reader.message_view.height()

    QTest.mouseClick(reader.attachments_button, Qt.MouseButton.LeftButton)
    assert reader.attachment_dialog.isVisible()
    assert "Hello" in reader.attachment_preview.toPlainText()
    assert reader.open_attachment_button.isEnabled()
    assert reader.save_attachment_button.isEnabled()
    reader.attachment_list.setCurrentRow(1)
    assert "Second" in reader.attachment_preview.toPlainText()
    assert reader.message_view.height() == height

    QTest.keyClick(reader.attachment_dialog, Qt.Key.Key_Escape)
    assert not reader.attachment_dialog.isVisible()
    assert reader.message_view.height() == height
    reader.attachments_button.click()
    assert "Second" in reader.attachment_preview.toPlainText()
    reader.message_list.clearSelection()
    reader.message_list.setCurrentRow(-1)
    assert not reader.attachment_dialog.isVisible()
    assert not reader.attachment_bar.isVisible()


def test_closed_preview_does_not_start_a_queued_download(reader):
    attachments = reader._mail_store.list_attachments(
        next(iter(reader._messages_by_id))
    )
    uncached = Attachment(
        attachments[0].id, attachments[0].message_id, "remote.txt", "text/plain", 5
    )
    reader._show_attachments([uncached])
    calls = []
    reader._attachment_service = SimpleNamespace(
        content=lambda value: calls.append(value)
    )
    assert not calls
    reader._sync_in_progress = True
    try:
        reader.attachments_button.click()
        assert reader._pending_attachment_preview == uncached.id
        reader.attachment_dialog.reject()
        assert reader._pending_attachment_preview is None
    finally:
        reader._sync_in_progress = False
    reader._update_attachment_actions()
    QApplication.processEvents()
    assert not calls


def test_tools_have_icons_names_and_native_autoraise(reader):
    for button in [
        reader.reply_button,
        reader.forward_button,
        reader.archive_button,
        reader.trash_button,
        reader.move_button,
        reader.mark_read_button,
        reader.mark_unread_button,
        reader.save_all_attachments_button,
    ]:
        assert not button.icon().isNull()
        assert button.toolTip() and button.accessibleName()
        assert button.autoRaise()
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.width() == button.height() == 32


def test_message_chrome_tracks_palette_without_inverting_html(reader):
    original = QPalette(QApplication.palette())
    palette = QPalette(original)
    palette.setColor(QPalette.ColorRole.Base, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    try:
        QApplication.setPalette(palette)
        QApplication.processEvents()
        html = reader.message_view.rendered_html()
        assert "background: #202124" in html
        assert "color: #eeeeee" in html
        assert "letter-spacing" not in html
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()


def test_long_metadata_is_plain_text_and_elides(reader):
    text = "<b>Sender</b> " + "VeryLongSubject" * 30
    label = ElidedLabel(text)
    label.resize(120, 24)
    label.show()
    QApplication.processEvents()
    try:
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert label.toolTip() == text
        assert label.text() != text
        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
        label.resize(400, 24)
        QApplication.processEvents()
        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
    finally:
        label.close()


def test_message_scrollbar_tracks_theme_and_remains_draggable(reader):
    for index in range(30):
        item = QListWidgetItem(f"Message {index}")
        item.setSizeHint(QSize(280, 102))
        reader.message_list.addItem(item)
    original = QPalette(QApplication.palette())
    scrollbar = reader.message_list.verticalScrollBar()
    try:
        for background, handle in [("#f5f5f5", "#aaaaaa"), ("#292a2e", "#66686d")]:
            palette = QPalette(original)
            palette.setColor(QPalette.ColorRole.Window, QColor(background))
            palette.setColor(QPalette.ColorRole.Mid, QColor(handle))
            QApplication.setPalette(palette)
            scrollbar.setValue(0)
            QTest.mouseMove(reader.message_list.viewport(), QPoint(5, 5))
            QApplication.processEvents()
            assert scrollbar.isVisible() and scrollbar.maximum() > 0

            option = QStyleOptionSlider()
            scrollbar.initStyleOption(option)
            thumb = scrollbar.style().subControlRect(
                QStyle.ComplexControl.CC_ScrollBar,
                option,
                QStyle.SubControl.SC_ScrollBarSlider,
                scrollbar,
            )
            image = scrollbar.grab().toImage()
            ratio = image.devicePixelRatio()
            assert image.pixelColor(
                int(scrollbar.width() / 2 * ratio),
                int(scrollbar.height() * 0.8 * ratio),
            ) == QColor(background)
            assert image.pixelColor(
                int(thumb.center().x() * ratio), int(thumb.center().y() * ratio)
            ) == QColor(handle)
            assert scrollbar.width() == 12

            QTest.mousePress(scrollbar, Qt.MouseButton.LeftButton, pos=thumb.center())
            QTest.mouseMove(scrollbar, QPoint(thumb.center().x(), scrollbar.height() // 2))
            QTest.mouseRelease(
                scrollbar,
                Qt.MouseButton.LeftButton,
                pos=QPoint(thumb.center().x(), scrollbar.height() // 2),
            )
            assert scrollbar.value() > 0
    finally:
        QApplication.setPalette(original)
        QApplication.processEvents()
