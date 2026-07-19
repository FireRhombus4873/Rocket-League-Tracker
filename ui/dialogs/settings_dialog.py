"""
SettingsDialog — opened by the gear button, or auto-prompted on first run when
no in-game username is saved. Collects the local username (used to detect which
team is yours) and an optional list of common teammates to hide from the
Past Encounters card.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from ..theme import (
    BG_DARK, BG_CARD, BG_TABLE, BG_HOVER, TEXT, SUBTEXT,
    ACCENT2, LOSS_CLR, BORDER, SELECT_BG, FONT_UI,
)


class SettingsDialog(QDialog):
    def __init__(self, username: str = "", teammates=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {BG_DARK};
                color: {TEXT};
                font-family: {FONT_UI};
                font-size: 13px;
            }}
            QLineEdit {{
                background-color: {BG_TABLE};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 9px 12px;
                selection-background-color: {SELECT_BG};
            }}
            QLineEdit:focus {{ border-color: {ACCENT2}; }}
            QPushButton {{
                background-color: {BG_CARD};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {SUBTEXT}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(18)

        # ── Header ──────────────────────────────────────────────────────
        title_lbl = QLabel("Settings")
        title_lbl.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
        title_lbl.setStyleSheet(f"color: {TEXT};")
        layout.addWidget(title_lbl)

        # ── Form ────────────────────────────────────────────────────────
        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 4)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def field_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {SUBTEXT};")
            return lbl

        self._username_edit = QLineEdit(username)
        self._username_edit.setPlaceholderText("e.g. FireRhombus4873")
        form.addRow(field_label("In-game name"), self._username_edit)

        teammates_text = ", ".join(teammates or [])
        self._teammates_edit = QLineEdit(teammates_text)
        self._teammates_edit.setPlaceholderText("Optional, comma-separated")
        form.addRow(field_label("Common teammates"), self._teammates_edit)

        layout.addLayout(form)

        # ── Help text ───────────────────────────────────────────────────
        help_lbl = QLabel(
            "Your in-game name is used to detect which team is yours. "
            "Common teammates are filtered out in the encounters card."
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(f"color: {SUBTEXT}; font-size: 11px;")
        layout.addWidget(help_lbl)

        # ── Inline error ────────────────────────────────────────────────
        self._error_lbl = QLabel("")
        self._error_lbl.setStyleSheet(f"color: {LOSS_CLR}; font-size: 11px;")
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(100)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        if not self._username_edit.text().strip():
            self._error_lbl.setText("In-game name is required.")
            self._error_lbl.setVisible(True)
            self._username_edit.setStyleSheet(f"border: 1px solid {LOSS_CLR};")
            self._username_edit.setFocus()
            return
        self.accept()

    def username(self) -> str:
        return self._username_edit.text().strip()

    def teammates(self) -> list:
        return [t.strip() for t in self._teammates_edit.text().split(",") if t.strip()]
