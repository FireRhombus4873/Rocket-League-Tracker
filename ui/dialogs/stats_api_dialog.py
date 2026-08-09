"""
StatsApiDialog — shown when Rocket League's Stats API is switched off, which
leaves the tracker with nothing to connect to. Explains the problem, then
offers to set PacketSendRate in the game's config file directly.

Deliberately does no file IO of its own: `write_callback(rate)` is supplied by
main.py and returns an error string to show inline, or None on success. That
keeps the write where every other action lives, and lets the dialog stay open
on a permissions failure so the user can elevate and retry.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox,
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from ..theme import (
    BG_DARK, BG_CARD, BG_TABLE, BG_HOVER, TEXT, SUBTEXT, FAINT,
    ACCENT, ACCENT2, LOSS_CLR, BORDER, SELECT_BG, FONT_UI,
)

class StatsApiDialog(QDialog):
    """The rate bounds are passed in rather than defined here: statsApiConfig
    owns them (it validates against the same numbers), and nothing under ui/
    imports a root module. No defaults, so the two can't drift apart.
    """

    def __init__(self, config_path, min_rate, max_rate, recommended,
                 current_rate=None, note="", write_callback=None, parent=None):
        super().__init__(parent)
        self._write_callback = write_callback

        self.setWindowTitle("Stats API Disabled")
        self.setMinimumWidth(540)
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {BG_DARK};
                color: {TEXT};
                font-family: {FONT_UI};
                font-size: 13px;
            }}
            QSpinBox {{
                background-color: {BG_TABLE};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 8px 10px;
                selection-background-color: {SELECT_BG};
            }}
            QSpinBox:focus {{ border-color: {ACCENT2}; }}
            /* No ::up-button/::down-button rules on purpose — styling either
               sub-control makes Qt stop drawing the native arrows, leaving a
               blank slab beside the field. The value is typed or arrow-keyed. */
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
        layout.setSpacing(16)

        # ── Header ──────────────────────────────────────────────────────
        title_lbl = QLabel("Rocket League isn't sending stats")
        title_lbl.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
        title_lbl.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(title_lbl)

        rate_desc = "is switched off" if not current_rate else f"is set to {current_rate}"
        body_lbl = QLabel(
            f"The tracker reads match data from Rocket League's Stats API, which {rate_desc} "
            "in your game config. Nothing can be recorded until it's turned on.\n\n"
            "Set how many updates per second the game should send:"
        )
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(f"color: {SUBTEXT};")
        layout.addWidget(body_lbl)

        # ── Rate input ──────────────────────────────────────────────────
        rate_row = QHBoxLayout()
        rate_row.setSpacing(10)

        self._rate_spin = QSpinBox()
        self._rate_spin.setRange(min_rate, max_rate)
        self._rate_spin.setValue(recommended)
        self._rate_spin.setFixedWidth(110)
        self._rate_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rate_row.addWidget(self._rate_spin)

        hint_lbl = QLabel(
            f"updates per second &nbsp;·&nbsp; {min_rate}–{max_rate}, "
            f"<b>{recommended} recommended</b>"
        )
        hint_lbl.setStyleSheet(f"color: {SUBTEXT}; font-size: 12px;")
        rate_row.addWidget(hint_lbl)
        rate_row.addStretch()
        layout.addLayout(rate_row)

        # ── The file being edited ───────────────────────────────────────
        path_lbl = QLabel(str(config_path))
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_lbl.setStyleSheet(
            f"color: {FAINT}; font-size: 11px; font-family: Consolas, monospace;"
            f"background-color: {BG_TABLE}; border: 1px solid {BORDER};"
            "border-radius: 6px; padding: 8px 10px;"
        )
        layout.addWidget(path_lbl)

        if note:
            note_lbl = QLabel(note)
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet(f"color: {ACCENT2}; font-size: 11px;")
            layout.addWidget(note_lbl)

        # ── Inline error ────────────────────────────────────────────────
        # Writes land in Program Files, so a failure here is expected enough to
        # warrant staying on this dialog rather than bouncing to a message box.
        self._error_lbl = QLabel("")
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setStyleSheet(f"color: {LOSS_CLR}; font-size: 11px;")
        self._error_lbl.setVisible(False)
        layout.addWidget(self._error_lbl)

        # ── Buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Not Now")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(self.reject)
        self._confirm_btn = QPushButton("Confirm")
        self._confirm_btn.setFixedWidth(100)
        self._confirm_btn.setDefault(True)
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._confirm_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self):
        if self._write_callback is None:
            self.accept()
            return

        self._error_lbl.setVisible(False)
        self._confirm_btn.setEnabled(False)
        try:
            error = self._write_callback(self.rate())
        finally:
            self._confirm_btn.setEnabled(True)

        if error:
            self._error_lbl.setText(error)
            self._error_lbl.setVisible(True)
            return
        self.accept()

    def rate(self) -> int:
        return self._rate_spin.value()
