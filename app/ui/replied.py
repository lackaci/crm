"""Searchable sent-reply history, separate from the active Inbox/review queue."""
from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QSplitter)
from app.db import replied


class RepliedEmailsWidget(QWidget):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path, self.offset, self.selected_reply_id = path, 0, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search replied emails or sent responses')
        self.search_input.setAccessibleName('Search replied emails')
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(180)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._search)
        self.search_input.textChanged.connect(lambda: self.search_timer.start())
        actions = QHBoxLayout()
        actions.addWidget(self.search_input, 1)
        self.refresh_button = QPushButton('Refresh history')
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.count_label = QLabel()
        layout.addWidget(self.count_label)
        self.reply_table = QTableWidget(0, 3)
        self.reply_table.setHorizontalHeaderLabels(['Recipient', 'Subject', 'Sent'])
        self.reply_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.reply_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.reply_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reply_table.verticalHeader().hide()
        self.reply_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.reply_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.reply_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.reply_table.itemSelectionChanged.connect(self._select)
        self.details_label = QLabel('Select a replied email.')
        self.details_label.setTextFormat(Qt.TextFormat.PlainText)
        self.details_label.setWordWrap(True)
        self.incoming_view, self.reply_view = QPlainTextEdit(), QPlainTextEdit()
        self.incoming_view.setReadOnly(True)
        self.reply_view.setReadOnly(True)
        self.incoming_view.setAccessibleName('Original email')
        self.reply_view.setAccessibleName('Sent reply')
        reader = QWidget()
        reading = QVBoxLayout(reader)
        reading.addWidget(self.details_label)
        reading.addWidget(QLabel('Original email'))
        reading.addWidget(self.incoming_view, 1)
        reading.addWidget(QLabel('Sent reply'))
        reading.addWidget(self.reply_view, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.reply_table)
        splitter.addWidget(reader)
        splitter.setSizes([200, 400])
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.page_label = QLabel()
        footer.addWidget(self.page_label, 1)
        self.previous_button, self.next_button = QPushButton('Previous'), QPushButton('Next')
        self.previous_button.clicked.connect(lambda: self.change_page(-1))
        self.next_button.clicked.connect(lambda: self.change_page(1))
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)
        self.refresh()

    def _search(self):
        self.offset = 0
        self.refresh()

    def change_page(self, direction):
        self.offset = max(0, self.offset + direction * 100)
        self.refresh()

    def refresh(self):
        previous = self.selected_reply_id
        search = self.search_input.text()
        total = replied.count(self.path, search)
        self.offset = min(self.offset, max(0, (total - 1) // 100 * 100))
        rows = replied.messages(self.path, search, offset=self.offset)
        blocker = QSignalBlocker(self.reply_table)
        self.reply_table.setRowCount(len(rows))
        selected_row = 0
        for index, row in enumerate(rows):
            for column, value in enumerate((row['recipient'], row['subject'], row['sent_at'] + ' UTC')):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row['reply_id'])
                self.reply_table.setItem(index, column, item)
            if row['reply_id'] == previous:
                selected_row = index
        self.reply_table.clearSelection()
        del blocker
        if rows:
            self.reply_table.selectRow(selected_row)
        else:
            self.selected_reply_id = None
            self.details_label.setText('No replied emails match this search.' if search else 'No replies have been sent yet.')
            self.incoming_view.clear()
            self.reply_view.clear()
        self.count_label.setText(f'{total} replied email(s). Successfully sent replies are kept here, outside Inbox.')
        self.page_label.setText(f'{self.offset + 1 if rows else 0}–{self.offset + len(rows)} of {total}')
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + len(rows) < total)

    def _select(self):
        items = self.reply_table.selectedItems()
        if not items:
            return
        row = replied.get_reply(self.path, items[0].data(Qt.ItemDataRole.UserRole))
        if not row:
            return
        self.selected_reply_id = row['reply_id']
        self.details_label.setText(f"To: {row['recipient']}\nFrom: {row['sender']} · Sent: {row['sent_at']} UTC\nCustomer: {row['customer_name'] or 'Not linked'}\nSubject: {row['subject']}")
        self.incoming_view.setPlainText(f"From: {row['from_name']} <{row['from_email']}>\nSubject: {row['incoming_subject']}\n\n{row['incoming_body']}")
        self.reply_view.setPlainText(row['reply_body'])
