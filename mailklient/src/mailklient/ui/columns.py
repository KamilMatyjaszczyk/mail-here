"""Column reordering and responsive reader toolbar layout."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QWidget

if TYPE_CHECKING:
    from mailklient.ui.main_window import MainWindow

DEFAULT_COLUMN_ORDER = ("folders", "reader", "messages")

COLUMN_WIDTHS = {
    "folders": 230,
    "reader": 610,
    "messages": 360,
}

COLUMN_TITLES = {
    "folders": "mcpMail",
    "reader": "Mail",
    "messages": "Messages",
}


class ColumnDragHandle(QLabel):
    """Small drag handle used to reorder the main columns."""

    def __init__(
        self, title: str, column_name: str, window: ColumnLayoutController
    ) -> None:
        super().__init__(f":: {title}")
        self._column_name = column_name
        self._window = window
        self._press_position: QPoint | None = None
        self._dragging = False
        self.setObjectName(f"column_drag_handle_{column_name}")
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self.setToolTip("Drag to move the column")
        self.setFixedHeight(max(32, self.fontMetrics().height() + 12))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if _event_button(event) != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press_position = _event_position(event)
        self._dragging = False
        self._window._begin_column_drag(self._column_name)
        self.setProperty("dragging", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        if hasattr(event, "accept"):
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_position is None:
            return super().mouseMoveEvent(event)
        distance = (_event_position(event) - self._press_position).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self._dragging = True
            self._window._preview_column_drop(
                self._column_name,
                _event_global_position(event),
            )
        if hasattr(event, "accept"):
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._press_position is None:
            return super().mouseReleaseEvent(event)
        global_position = _event_global_position(event)
        self._window._finish_column_drag(
            self._column_name,
            global_position,
            commit=self._dragging,
        )
        self._press_position = None
        self._dragging = False
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        if hasattr(event, "accept"):
            event.accept()


class ColumnLayoutController:
    def __init__(self, window: MainWindow) -> None:
        self.window = window

    def _build_column_drag_handle(self, column_name: str) -> ColumnDragHandle:
        handle = ColumnDragHandle(COLUMN_TITLES[column_name], column_name, self)
        self.window._column_handles[column_name] = handle
        return handle

    def _update_message_toolbar_layout(self) -> None:
        if not hasattr(self.window, "message_toolbar"):
            return
        width = self.window.message_toolbar.width()
        if width >= _single_row_toolbar_width(self.window):
            mode = "single"
        else:
            mode = "compact"
        if mode != self.window._message_toolbar_mode:
            self._rebuild_message_toolbar(mode=mode)

    def _rebuild_message_toolbar(self, *, mode: str) -> None:
        _clear_layout(self.window.message_action_layout)
        _clear_layout(self.window.message_organize_layout)
        buttons = (
            self.window.compose_button,
            self.window.reply_button,
            self.window.forward_button,
            self.window.archive_button,
            self.window.trash_button,
            self.window.move_button,
            self.window.mark_read_button,
            self.window.mark_unread_button,
        )
        for index, button in enumerate(buttons):
            if index in {1, 3, 6}:
                self.window.message_action_layout.addSpacing(6)
            self.window.message_action_layout.addWidget(button)
        self.window.message_action_layout.addStretch()

        checkbox = self.window.remote_content_checkbox
        checkbox_height = checkbox.sizeHint().height()
        if mode == "single":
            self.window.message_action_layout.addWidget(checkbox)
            height = max(32, checkbox_height) + 8
        else:
            self.window.message_organize_layout.addStretch()
            self.window.message_organize_layout.addWidget(checkbox)
            height = 32 + checkbox_height + 12
        self.window.message_toolbar.setFixedHeight(height)
        self.window._message_toolbar_mode = mode

    def _begin_column_drag(self, _column_name: str) -> None:
        self.window._drag_original_order = self._column_order()
        self.window._drag_source_column = _column_name
        _set_widget_property(
            self.window._column_panels[_column_name], "dragSource", True
        )
        source_handle = self.window._column_handles.get(_column_name)
        if source_handle is not None:
            _set_widget_property(source_handle, "dragging", True)

    def _finish_column_drag(
        self,
        column_name: str,
        global_position: QPoint,
        *,
        commit: bool,
    ) -> None:
        if not commit:
            self._apply_column_order(self.window._drag_original_order)
            self._clear_column_drag_state()
            return

        target_column = self._column_at_global_position(global_position)
        if target_column is None or target_column == column_name:
            self._apply_column_order(self.window._drag_original_order)
            self._clear_column_drag_state()
            return

        target_panel = self.window._column_panels[target_column]
        local_position = target_panel.mapFromGlobal(global_position)
        insert_after_target = local_position.x() > target_panel.width() / 2
        new_order = _reordered_columns(
            self.window._drag_original_order,
            column_name,
            target_column,
            insert_after_target=insert_after_target,
        )
        self._apply_column_order(new_order)
        self._clear_column_drag_state()

    def _preview_column_drop(self, column_name: str, global_position: QPoint) -> None:
        target_column = self._column_at_global_position(global_position)
        if target_column is None or target_column == column_name:
            self._clear_column_drop_preview()
            return

        target_panel = self.window._column_panels[target_column]
        local_position = target_panel.mapFromGlobal(global_position)
        insert_after_target = local_position.x() > target_panel.width() / 2
        preview = (target_column, insert_after_target)
        if preview == self.window._drop_preview:
            return

        self._clear_column_drop_preview()
        self.window._drop_preview = preview
        preview_value = "after" if insert_after_target else "before"
        _set_widget_property(target_panel, "dropPreview", preview_value)
        target_handle = self.window._column_handles.get(target_column)
        if target_handle is not None:
            _set_widget_property(target_handle, "dropPreview", preview_value)

    def _clear_column_drag_state(self) -> None:
        self._clear_column_drop_preview()
        if self.window._drag_source_column is not None:
            _set_widget_property(
                self.window._column_panels[self.window._drag_source_column],
                "dragSource",
                False,
            )
            source_handle = self.window._column_handles.get(
                self.window._drag_source_column
            )
            if source_handle is not None:
                _set_widget_property(source_handle, "dragging", False)
        self.window._drag_source_column = None

    def _clear_column_drop_preview(self) -> None:
        if self.window._drop_preview is None:
            return
        target_column, _insert_after_target = self.window._drop_preview
        target_panel = self.window._column_panels.get(target_column)
        if target_panel is not None:
            _set_widget_property(target_panel, "dropPreview", "")
        target_handle = self.window._column_handles.get(target_column)
        if target_handle is not None:
            _set_widget_property(target_handle, "dropPreview", "")
        self.window._drop_preview = None

    def _column_order(self) -> tuple[str, ...]:
        panel_columns = {
            panel: column_name
            for column_name, panel in self.window._column_panels.items()
        }
        order = []
        for index in range(self.window.main_splitter.count()):
            widget = self.window.main_splitter.widget(index)
            if widget is not None:
                order.append(panel_columns[widget])
        return tuple(order)

    def _column_at_global_position(self, global_position: QPoint) -> str | None:
        for column_name, panel in self.window._column_panels.items():
            local_position = panel.mapFromGlobal(global_position)
            if panel.rect().contains(local_position):
                return column_name
        return None

    def _apply_column_order(self, order: tuple[str, ...]) -> None:
        if not self.window._column_panels:
            return

        for index, column_name in enumerate(order):
            self.window.main_splitter.insertWidget(
                index, self.window._column_panels[column_name]
            )
            stretch = 1 if column_name == "reader" else 0
            self.window.main_splitter.setStretchFactor(index, stretch)

        self.window.main_splitter.setSizes(
            [COLUMN_WIDTHS[column_name] for column_name in order]
        )
        self._update_message_toolbar_layout()


def _clear_layout(layout: QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widget.setParent(layout.parentWidget())


def _set_widget_property(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _single_row_toolbar_width(window: MainWindow) -> int:
    controls = (
        window.compose_button,
        window.reply_button,
        window.forward_button,
        window.archive_button,
        window.trash_button,
        window.move_button,
        window.mark_read_button,
        window.mark_unread_button,
        window.remote_content_checkbox,
    )
    controls_width = sum(
        max(control.minimumWidth(), control.sizeHint().width()) for control in controls
    )
    spacings_width = 2 * (len(controls) - 1) + 18
    margins_width = 8
    buffer_width = 12
    return controls_width + spacings_width + margins_width + buffer_width


def _reordered_columns(
    order: tuple[str, ...],
    source_column: str,
    target_column: str,
    *,
    insert_after_target: bool,
) -> tuple[str, ...]:
    remaining = [column for column in order if column != source_column]
    target_index = remaining.index(target_column)
    insert_index = target_index + 1 if insert_after_target else target_index
    remaining.insert(insert_index, source_column)
    return tuple(remaining)


def _event_button(event: QMouseEvent) -> Qt.MouseButton:
    return event.button()


def _event_position(event: QMouseEvent) -> QPoint:
    return event.position().toPoint()


def _event_global_position(event: QMouseEvent) -> QPoint:
    return event.globalPosition().toPoint()
