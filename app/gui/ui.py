"""Shared look and building blocks for every screen, so the whole app looks and behaves the same.

  STYLE           one stylesheet for the whole app (colours, spacing, inputs, tables, cards)
  WheelGuard      the mouse wheel never changes a setting by accident: over a number box, drop-down or date
                  it scrolls the page instead (click into a box and type, or use its arrows, to change it)
  page()          a tab's content area with the standard margins (optionally scrollable)
  card()          a titled panel, optionally with a one-line explanation under the title
  form()          a label/field form with consistent spacing and wrapping labels
  help_label()    grey explanatory text
  section_title() a heading inside a page
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QFormLayout, QFrame,
    QLabel, QScrollArea, QScrollBar, QVBoxLayout, QWidget,
)

GREEN, RED, MUTED, AMBER = "#3fb950", "#f85149", "#8b949e", "#d29922"
BG, PANEL, BORDER, INPUT, TEXT, ACCENT = "#0d1117", "#161b22", "#30363d", "#010409", "#e6edf3", "#1f6feb"
MARGIN, SPACING = 16, 12

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 10.5pt; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QLabel {{ background: transparent; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: {PANEL}; padding: 8px 15px; border: 1px solid {BORDER}; border-bottom: none;
               border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 3px; color: {MUTED}; }}
QTabBar::tab:selected {{ background: {BG}; color: {TEXT}; border-top: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QPushButton {{ background: #21262d; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 16px;
              min-height: 20px; }}
QPushButton:hover {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #6e7681; background: {PANEL}; }}
QPushButton#start {{ background: #238636; border-color: #2ea043; font-weight: bold; }}
QPushButton#start:hover {{ background: #2ea043; }}
QPushButton#stop {{ background: #9e6a03; border-color: #bb8009; font-weight: bold; }}
QPushButton#kill {{ background: #b62324; border-color: #da3633; font-weight: bold; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{
    background: {INPUT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; min-height: 22px; }}
QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {{ min-width: 120px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: #1f3a5f; }}
QPlainTextEdit, QTextEdit, QListWidget {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px; }}
QListWidget::item {{ padding: 6px 4px; }}
QListWidget::item:selected {{ background: #1f3a5f; color: {TEXT}; }}

QTableWidget {{ gridline-color: #21262d; background: {BG}; alternate-background-color: #111821;
               border: 1px solid {BORDER}; border-radius: 6px; }}
QTableWidget::item {{ padding: 4px 8px; }}
QTableWidget::item:selected {{ background: #1f3a5f; color: {TEXT}; }}
QHeaderView::section {{ background: {PANEL}; padding: 8px; border: none; border-bottom: 1px solid {BORDER};
                       color: {MUTED}; font-weight: bold; }}

QGroupBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; margin-top: 16px;
            padding: 10px 12px 12px 12px; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 2px 6px;
                   color: {TEXT}; font-size: 11pt; background: {BG}; border-radius: 4px; }}
QGroupBox QWidget {{ background: {PANEL}; }}
QGroupBox QLineEdit, QGroupBox QSpinBox, QGroupBox QDoubleSpinBox, QGroupBox QDateEdit, QGroupBox QComboBox,
QGroupBox QPlainTextEdit, QGroupBox QTextEdit, QGroupBox QListWidget {{ background: {INPUT}; }}
QGroupBox QTableWidget {{ background: {BG}; }}
QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: normal; }}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #6e7681; border-radius: 4px; background: {INPUT}; }}
QCheckBox::indicator:checked {{ background: #238636; border-color: #2ea043; }}

QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel#help {{ color: {MUTED}; font-size: 9.5pt; font-weight: normal; }}
QLabel#h2 {{ font-size: 12.5pt; font-weight: bold; padding-top: 4px; }}

QListWidget#nav {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; outline: 0; }}
QListWidget#nav::item {{ padding: 10px 12px; border-radius: 6px; margin: 1px 0; }}
QListWidget#nav::item:selected {{ background: #1f3a5f; color: {TEXT}; font-weight: bold; }}
QListWidget#nav::item:hover:!selected {{ background: #21262d; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: {BG}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:vertical {{ height: 3px; }}
QSplitter::handle:horizontal {{ width: 3px; }}
"""


class WheelGuard(QObject):
    """App-wide filter: the mouse wheel over a number box, drop-down, date or slider scrolls the page instead
    of changing the value, so scrolling through Settings can never change a setting by accident."""

    GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and isinstance(obj, self.GUARDED) and not isinstance(obj, QScrollBar):
            area = obj.parent()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parent()
            if area is not None:
                bar = area.verticalScrollBar()
                lines = QApplication.wheelScrollLines() or 3
                bar.setValue(bar.value() - int(event.angleDelta().y() / 120 * lines * max(bar.singleStep(), 20)))
            return True                                                          # never change the value
        return False


def install_wheel_guard(app: QApplication | None = None) -> WheelGuard:
    app = app or QApplication.instance()
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard


def page(scroll: bool = False) -> tuple[QWidget, QVBoxLayout]:
    """A tab's content area with standard margins. Returns (widget to add as the tab, layout to fill)."""
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
    lay.setSpacing(SPACING)
    if not scroll:
        return inner, lay
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(inner)
    return area, lay


def help_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("help")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.AutoText)
    return lbl


def section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("h2")
    return lbl


def form() -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    f.setRowWrapPolicy(QFormLayout.WrapLongRows)
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    return f


def card(title: str, help_text: str | None = None, layout: str = "v"):
    """A titled panel. layout "v" -> QVBoxLayout, "form" -> QFormLayout. Returns (panel, layout)."""
    from PySide6.QtWidgets import QGroupBox
    box = QGroupBox(title)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(8, 14, 8, 8)
    outer.setSpacing(10)
    if help_text:
        outer.addWidget(help_label(help_text))
    if layout == "form":
        f = form()
        outer.addLayout(f)
        return box, f
    return box, outer
