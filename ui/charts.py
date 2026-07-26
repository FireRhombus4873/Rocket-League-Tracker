"""
Hand-rolled QPainter charts for the Analytics tab.

Deliberately dumb: each widget takes already-aggregated numbers through
`set_data(...)` and paints them. No database access, no signals, and no unit
formatting — anything that needs a unit (m:ss) arrives pre-formatted from the
caller, so every number the user sees is formatted in exactly one place.

Why not a charting library: the app ships as one PyInstaller exe with two runtime
deps (PyQt6, psutil) and an empty `hiddenimports`. Three small QPainter widgets
are cheaper than giving that up.

Every colour comes from ui.theme, so the charts restyle with the rest of the app.
No assets are loaded here, so there's deliberately no ASSETS_DIR — don't add one.
"""
import math

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics
from PyQt6.QtCore import Qt, QRect, QRectF, QPointF

from .theme import (
    TEXT, SUBTEXT, FAINT, ACCENT2, WIN_CLR, LOSS_CLR, BORDER, BORDER_SOFT,
)


def _alpha(hex_colour: str, a: float) -> QColor:
    """A theme colour at partial opacity — Qt stylesheets aren't involved here, so
    this is how fills get their translucency."""
    c = QColor(hex_colour)
    c.setAlphaF(a)
    return c


class _ChartBase(QWidget):
    """Shared plumbing: a transparent background, an antialiased painter, and a
    muted empty state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Mandatory, not cosmetic. APP_STYLESHEET's `QMainWindow, QWidget` rule
        # matches every custom widget, and a stylesheet background makes Qt fill
        # the rect before paintEvent — so without this each chart paints a BG_DARK
        # rectangle over the card it sits in.
        self.setStyleSheet("background: transparent; border: none;")
        # Every chart is fixed-height, which keeps the tab's scroll-area maths
        # trivial: the content height is just the sum of its cards.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _open_painter(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        return p

    def _draw_empty(self, p: QPainter, text: str = "Not enough data yet"):
        p.setPen(QColor(FAINT))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class WinRateTrendChart(_ChartBase):
    """Per-session win rate with a rolling-average overlay.

    `set_data(points, rolling_window)` where each point is a
    `get_analytics()["winRateBySession"]` row, chronological:
        {"sessionNum": 7, "matches": 8, "wins": 5, "losses": 3,
         "winPct": 0.625, "rollingWinPct": 0.55}
    """
    HEIGHT = 224
    PAD_L, PAD_R, PAD_T, PAD_B = 40, 14, 26, 26
    DOT_MIN_SPACING = 9      # px between points; below this the dots are dropped
    MAX_X_LABELS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self._points = []
        self._window = 5
        self._hover  = -1

    def set_data(self, points: list, rolling_window: int = 5):
        self._points = list(points or [])
        self._window = rolling_window or 5
        self._hover  = -1
        self.update()

    # -- geometry ---------------------------------------------------------
    def _plot_rect(self) -> QRect:
        return QRect(self.PAD_L, self.PAD_T,
                     max(1, self.width()  - self.PAD_L - self.PAD_R),
                     max(1, self.height() - self.PAD_T - self.PAD_B))

    def _x_for(self, index: int, plot: QRect) -> float:
        n = len(self._points)
        if n <= 1:
            return plot.center().x()
        return plot.left() + plot.width() * index / (n - 1)

    def _y_for(self, value: float, plot: QRect) -> float:
        return plot.bottom() - max(0.0, min(1.0, value)) * plot.height()

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event):
        p = self._open_painter()
        if not self._points:
            self._draw_empty(p, "No sessions recorded yet")
            return

        plot = self._plot_rect()
        self._draw_grid(p, plot)
        self._draw_series(p, plot)
        self._draw_legend(p, plot)
        self._draw_x_labels(p, plot)
        self._draw_hover(p, plot)

    def _draw_grid(self, p: QPainter, plot: QRect):
        p.setFont(QFont("Segoe UI", 8))
        for value in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = self._y_for(value, plot)
            # The 50% line is the only reference the eye needs, so it's the only
            # dashed one.
            if value == 0.5:
                pen = QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine)
                pen.setDashPattern([3, 4])
            else:
                pen = QPen(QColor(BORDER_SOFT), 1)
            p.setPen(pen)
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))

            p.setPen(QColor(FAINT))
            label = QRect(0, int(y) - 8, self.PAD_L - 8, 16)
            p.drawText(label,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{value:.0%}")

    def _draw_series(self, p: QPainter, plot: QRect):
        per_session = [QPointF(self._x_for(i, plot), self._y_for(pt.get("winPct", 0.0), plot))
                       for i, pt in enumerate(self._points)]
        rolling = [QPointF(self._x_for(i, plot), self._y_for(pt.get("rollingWinPct", 0.0), plot))
                   for i, pt in enumerate(self._points)]

        p.setPen(QPen(QColor(FAINT), 1))
        if len(per_session) > 1:
            p.drawPolyline(*per_session)

        # A long history degrades to a clean line instead of a caterpillar.
        n = len(self._points)
        spacing = plot.width() / (n - 1) if n > 1 else plot.width()
        if spacing >= self.DOT_MIN_SPACING:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(SUBTEXT)))
            for point in per_session:
                p.drawEllipse(point, 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # Drawn last, thicker, in the accent — it reads as the primary signal.
        pen = QPen(QColor(ACCENT2), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if len(rolling) > 1:
            p.drawPolyline(*rolling)
        elif rolling:
            p.setBrush(QBrush(QColor(ACCENT2)))
            p.drawEllipse(rolling[0], 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_legend(self, p: QPainter, plot: QRect):
        p.setFont(QFont("Segoe UI", 8))
        metrics = QFontMetrics(p.font())
        entries = [(FAINT, "Per session"), (ACCENT2, f"{self._window}-session avg")]

        widths = [12 + 5 + metrics.horizontalAdvance(t) for _, t in entries]
        x = plot.right() - sum(widths) - 14 * (len(entries) - 1)
        y = self.PAD_T - 14
        for (colour, text), width in zip(entries, widths):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(colour)))
            p.drawRoundedRect(QRectF(x, y + 5, 12, 2), 1, 1)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor(FAINT))
            p.drawText(QRect(int(x) + 17, y - 2, width, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       text)
            x += width + 14

    def _draw_x_labels(self, p: QPainter, plot: QRect):
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(FAINT))
        metrics = QFontMetrics(p.font())
        n = len(self._points)
        step = max(1, math.ceil(n / self.MAX_X_LABELS))
        # Always label the newest session, then work backwards on `step`.
        wanted = sorted({n - 1, *range(0, n, step)})

        last_right = -1e9
        for i in wanted:
            text  = str(self._points[i].get("sessionNum", ""))
            half  = metrics.horizontalAdvance(text) / 2 + 7
            x     = self._x_for(i, plot)
            if x - half < last_right:
                continue          # would collide with the label we just drew
            p.drawText(QRect(int(x - 24), plot.bottom() + 6, 48, 16),
                       Qt.AlignmentFlag.AlignCenter, text)
            last_right = x + half

    def _draw_hover(self, p: QPainter, plot: QRect):
        if not (0 <= self._hover < len(self._points)):
            return
        point = self._points[self._hover]
        x = self._x_for(self._hover, plot)
        y = self._y_for(point.get("winPct", 0.0), plot)

        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(int(x), plot.top(), int(x), plot.bottom())
        p.setPen(QPen(QColor(ACCENT2), 1.5))
        p.setBrush(QBrush(QColor(TEXT)))
        p.drawEllipse(QPointF(x, y), 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)

    # -- interaction ------------------------------------------------------
    def mouseMoveEvent(self, event):
        if not self._points:
            return
        plot = self._plot_rect()
        n = len(self._points)
        spacing = plot.width() / (n - 1) if n > 1 else plot.width()
        nearest, best = -1, spacing / 2 + 1
        for i in range(n):
            distance = abs(event.position().x() - self._x_for(i, plot))
            if distance <= best:
                nearest, best = i, distance

        if nearest != self._hover:
            self._hover = nearest
            if nearest >= 0:
                point = self._points[nearest]
                self.setToolTip(
                    f"Session {point.get('sessionNum', '?')} · "
                    f"{point.get('wins', 0)}W {point.get('losses', 0)}L · "
                    f"{point.get('winPct', 0):.0%}\n"
                    f"{self._window}-session avg {point.get('rollingWinPct', 0):.0%}"
                )
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.setToolTip("")
            self.update()


class WinRateBarChart(_ChartBase):
    """Win rate per labelled bucket. Used for both time-of-day and day-of-week, so
    the y axis is always a 0..1 rate — don't feed it counts.

    `set_data(bars)` where each bar is a `get_analytics()["timeOfDay"]` /
    `["dayOfWeek"]` row:
        {"label": "16–20", "matches": 12, "wins": 7, "losses": 5, "winPct": 0.583}
    """
    HEIGHT = 196
    PAD_L, PAD_R, PAD_T, PAD_B = 34, 10, 20, 36
    BAR_MAX_W = 44
    LOW_CONFIDENCE = 3       # fewer matches than this -> drawn neutral

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self._bars  = []
        self._hover = -1

    def set_data(self, bars: list):
        self._bars  = list(bars or [])
        self._hover = -1
        self.update()

    def _plot_rect(self) -> QRect:
        return QRect(self.PAD_L, self.PAD_T,
                     max(1, self.width()  - self.PAD_L - self.PAD_R),
                     max(1, self.height() - self.PAD_T - self.PAD_B))

    def _bar_geometry(self, index: int, plot: QRect):
        slot  = plot.width() / len(self._bars)
        width = min(self.BAR_MAX_W, slot * 0.56)
        left  = plot.left() + slot * index + (slot - width) / 2
        return left, width

    def paintEvent(self, event):
        p = self._open_painter()
        if not self._bars:
            self._draw_empty(p)
            return

        plot = self._plot_rect()
        self._draw_grid(p, plot)
        for i, bar in enumerate(self._bars):
            self._draw_bar(p, plot, i, bar)

    def _draw_grid(self, p: QPainter, plot: QRect):
        p.setFont(QFont("Segoe UI", 8))
        for value in (0.0, 0.5, 1.0):
            y = plot.bottom() - value * plot.height()
            if value == 0.5:
                pen = QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine)
                pen.setDashPattern([3, 4])
            else:
                pen = QPen(QColor(BORDER_SOFT), 1)
            p.setPen(pen)
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))

            p.setPen(QColor(FAINT))
            p.drawText(QRect(0, int(y) - 8, self.PAD_L - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{value:.0%}")

    def _draw_bar(self, p: QPainter, plot: QRect, index: int, bar: dict):
        left, width = self._bar_geometry(index, plot)
        matches = bar.get("matches", 0)
        pct     = bar.get("winPct", 0.0)
        label   = bar.get("label", "")

        if not matches:
            # A stub on the baseline keeps the x axis legible without implying 0%.
            p.setPen(QPen(QColor(BORDER), 2))
            p.drawLine(int(left), plot.bottom(), int(left + width), plot.bottom())
            p.setPen(QColor(FAINT))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(int(left) - 10, plot.bottom() - 22, int(width) + 20, 16),
                       Qt.AlignmentFlag.AlignCenter, "—")
            self._draw_bar_labels(p, plot, left, width, label, "0")
            return

        # A single lucky Tuesday shouldn't get painted green.
        if matches < self.LOW_CONFIDENCE:
            colour = SUBTEXT
        else:
            colour = WIN_CLR if pct >= 0.5 else LOSS_CLR

        top    = plot.bottom() - max(pct * plot.height(), 2.5)
        fill_a = 0.34 if index == self._hover else 0.22

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha(colour, fill_a)))
        p.drawRoundedRect(QRectF(left, top, width, plot.bottom() - top), 3, 3)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # The fill carries the shape, the cap carries the colour.
        p.setPen(QPen(QColor(colour), 2.5))
        p.drawLine(QPointF(left, top), QPointF(left + width, top))

        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        if top - plot.top() < 14:
            value_rect = QRect(int(left) - 10, int(top) + 4, int(width) + 20, 14)
        else:
            value_rect = QRect(int(left) - 10, int(top) - 19, int(width) + 20, 14)
        p.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, f"{pct:.0%}")

        self._draw_bar_labels(p, plot, left, width, label, str(matches))

    def _draw_bar_labels(self, p: QPainter, plot: QRect, left: float, width: float,
                         label: str, count: str):
        box = QRect(int(left) - 14, 0, int(width) + 28, 16)
        p.setPen(QColor(FAINT))
        p.setFont(QFont("Segoe UI", 9))
        box.moveTop(plot.bottom() + 6)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        p.setFont(QFont("Segoe UI", 8))
        box.moveTop(plot.bottom() + 19)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, count)

    def mouseMoveEvent(self, event):
        if not self._bars:
            return
        plot  = self._plot_rect()
        slot  = plot.width() / len(self._bars)
        index = int((event.position().x() - plot.left()) // slot)
        if not (0 <= index < len(self._bars)):
            index = -1

        if index != self._hover:
            self._hover = index
            if index >= 0:
                bar = self._bars[index]
                if bar.get("matches", 0):
                    self.setToolTip(
                        f"{bar.get('label', '')} · {bar['matches']} matches · "
                        f"{bar.get('wins', 0)}W {bar.get('losses', 0)}L · "
                        f"{bar.get('winPct', 0):.0%}"
                    )
                else:
                    self.setToolTip(f"{bar.get('label', '')} · no matches")
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.setToolTip("")
            self.update()


class ComparisonBars(_ChartBase):
    """Rows of `label | two horizontal bars | values`, one row per metric.

    Each row is scaled to its own maximum, so metrics on wildly different scales
    (goals ~1.5, score ~400) stay legible together — bars are only ever compared
    *within* a row, never between rows. Pass `"max"` on a row to pin its scale
    instead (rates pass 1.0 so a 58% bar really is 58% of the track).

    `set_data(rows)` where each row is:
        {"label": "GOALS", "left": 1.81, "right": 0.94,
         "left_text": "1.81", "right_text": "0.94", "max": None}

    Height follows the row count, so the widget resizes once when data first
    arrives. No hover — every number is already printed.
    """
    ROW_H, LEGEND_H, PAD_T, PAD_B = 34, 22, 4, 6
    LABEL_W, VALUE_W, BAR_H, BAR_GAP = 108, 62, 9, 5

    def __init__(self, parent=None, *, left_label="In wins", right_label="In losses",
                 left_colour=WIN_CLR, right_colour=LOSS_CLR):
        super().__init__(parent)
        self._rows         = []
        self._left_label   = left_label
        self._right_label  = right_label
        self._left_colour  = left_colour
        self._right_colour = right_colour
        self._apply_height()

    def _apply_height(self):
        rows = max(1, len(self._rows))
        self.setFixedHeight(self.PAD_T + self.LEGEND_H + self.ROW_H * rows + self.PAD_B)

    def set_data(self, rows: list):
        self._rows = list(rows or [])
        self._apply_height()
        self.update()

    def paintEvent(self, event):
        p = self._open_painter()
        if not self._rows:
            self._draw_empty(p)
            return

        self._draw_legend(p)
        for i, row in enumerate(self._rows):
            self._draw_row(p, i, row)

    def _draw_legend(self, p: QPainter):
        p.setFont(QFont("Segoe UI", 8))
        metrics = QFontMetrics(p.font())
        x = 0
        for colour, text in ((self._left_colour, self._left_label),
                             (self._right_colour, self._right_label)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(colour)))
            p.drawRoundedRect(QRectF(x, self.PAD_T + 7, 10, 3), 1.5, 1.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            width = metrics.horizontalAdvance(text)
            p.setPen(QColor(SUBTEXT))
            p.drawText(QRect(int(x) + 15, self.PAD_T, width + 4, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       text)
            x += 15 + width + 16

    def _draw_row(self, p: QPainter, index: int, row: dict):
        top      = self.PAD_T + self.LEGEND_H + index * self.ROW_H
        track_x  = self.LABEL_W
        track_w  = max(1, self.width() - self.LABEL_W - self.VALUE_W)
        left_v   = row.get("left", 0) or 0
        right_v  = row.get("right", 0) or 0
        scale    = row.get("max") or max(left_v, right_v)

        p.setPen(QColor(SUBTEXT))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.drawText(QRect(0, top, self.LABEL_W - 8, self.ROW_H),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   row.get("label", ""))

        bars = (
            (left_v,  row.get("left_text", ""),  self._left_colour,
             top + 6),
            (right_v, row.get("right_text", ""), self._right_colour,
             top + 6 + self.BAR_H + self.BAR_GAP),
        )
        for value, text, colour, bar_top in bars:
            if scale <= 0:
                p.setPen(QPen(QColor(BORDER), 2))
                p.drawLine(track_x, int(bar_top + self.BAR_H / 2),
                           track_x + 8, int(bar_top + self.BAR_H / 2))
                display = text or "—"
            else:
                # Clamped so a tiny non-zero value is still visible.
                length = max(3.0, track_w * value / scale) if value > 0 else 0.0
                if length:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(_alpha(colour, 0.85)))
                    p.drawRoundedRect(QRectF(track_x, bar_top, length, self.BAR_H),
                                      self.BAR_H / 2, self.BAR_H / 2)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                display = text

            p.setPen(QColor(TEXT))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(self.width() - self.VALUE_W, int(bar_top) - 4,
                             self.VALUE_W - 2, self.BAR_H + 8),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       display)
