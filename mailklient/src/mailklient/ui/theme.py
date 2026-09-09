"""Small, palette-aware accents; native Qt styles own the controls."""

_MAIN_WINDOW_STYLESHEET = """
QSplitter#main_splitter::handle {
    background: palette(mid);
}

QWidget#folder_panel,
QWidget#message_list_panel,
QWidget#message_view_panel {
    background: palette(window);
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
}

QWidget#folder_panel[dropPreview="before"],
QWidget#message_list_panel[dropPreview="before"],
QWidget#message_view_panel[dropPreview="before"] {
    border-left-color: palette(highlight);
}
QWidget#folder_panel[dropPreview="after"],
QWidget#message_list_panel[dropPreview="after"],
QWidget#message_view_panel[dropPreview="after"] {
    border-right-color: palette(highlight);
}

QLabel#column_drag_handle_folders,
QLabel#column_drag_handle_reader,
QLabel#column_drag_handle_messages {
    border-bottom: 1px solid palette(mid);
    padding: 0 4px;
    font-weight: 600;
}
QLabel[dragging="true"],
QLabel[dropPreview="before"],
QLabel[dropPreview="after"] {
    background: palette(highlight);
    color: palette(highlighted-text);
}

QLabel#folder_panel_title,
QLabel#account_panel_title {
    font-weight: 600;
    padding-left: 6px;
}

QListWidget#account_list,
QListWidget#folder_list,
QListWidget#message_list {
    background: transparent;
    border: 0;
}
QListWidget#account_list::item,
QListWidget#folder_list::item {
    min-height: 28px;
    padding: 3px 8px;
    border-radius: 4px;
}
QListWidget#account_list::item:selected,
QListWidget#folder_list::item:selected {
    background: palette(highlight);
    color: palette(highlighted-text);
}

QListWidget#message_list::item {
    background: palette(base);
    border: 1px solid transparent;
    border-radius: 4px;
    margin: 3px 0;
}
QListWidget#message_list::item:hover {
    border-color: palette(mid);
}
QListWidget#message_list::item:selected {
    border-color: palette(highlight);
    border-left: 3px solid palette(highlight);
}
QListWidget#message_list QScrollBar:vertical {
    background: palette(window);
    width: 12px;
    margin: 0;
    border: 0;
}
QListWidget#message_list QScrollBar::handle:vertical {
    background: palette(mid);
    min-height: 28px;
    margin: 0 2px;
    border-radius: 4px;
}
QListWidget#message_list QScrollBar::handle:vertical:hover,
QListWidget#message_list QScrollBar::handle:vertical:pressed {
    background: palette(highlight);
}
QListWidget#message_list QScrollBar::add-page:vertical,
QListWidget#message_list QScrollBar::sub-page:vertical {
    background: palette(window);
}
QListWidget#message_list QScrollBar::add-line:vertical,
QListWidget#message_list QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
    border: 0;
}
QListWidget#message_list QScrollBar::up-arrow:vertical,
QListWidget#message_list QScrollBar::down-arrow:vertical {
    width: 0;
    height: 0;
    background: none;
    border: 0;
}
QWidget#message_item_card {
    background: transparent;
}
QWidget#message_item_card QLabel {
    color: palette(text);
}
QWidget#message_item_card QLabel#message_item_account {
    color: palette(link);
}
QLabel#message_item_sender,
QLabel#message_item_subject {
    font-weight: 600;
}

QWidget#attachment_bar {
    border-top: 1px solid palette(mid);
}
QListWidget#attachment_list,
QTextBrowser#attachment_preview {
    border: 0;
}
QListWidget#attachment_list::item {
    padding: 8px;
}
"""
