"""Qt 6 desktop workspace for local firmware package builds."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
    QAbstractItemView, QCheckBox, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QStyle, QStyledItemDelegate, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .backend import ROOT, BuildError, Catalog, clean_source
from .runtime import Runtime
from . import ota_catalog
from . import __version__, updates
from .bloat_storage import BloatStorageError, plan_deletion, delete_bloat
from .device_layout import coloros
from scripts.materialize_bloat import AssetError, load_manifest, inspect_downloads


ICONS = {
    'layers': '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
    'build': '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h4"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'refresh': '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6a8 8 0 0 1 13.4 4M4.5 14a8 8 0 0 0 13.4 4"/>',
    'trash': '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    'settings': '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="9" cy="18" r="2"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3V7Z"/>',
    'arrow': '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    'download': '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
    'upload': '<path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'monitor': '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    'terminal': '<path d="m5 7 5 5-5 5m8 0h6"/>',
    'file': '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 13h8M8 17h5"/>',
    'link': '<path d="m10 13 4-4m-7 6-2 2a4 4 0 0 0 6 5l4-4a4 4 0 0 0-1-6m3-3 2-2a4 4 0 0 0-6-5L9 5a4 4 0 0 0 1 6" transform="translate(0 -1)"/>',
    'help': '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3v.1"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2"/>',
}


def icon(name, color='#617188', size=20, angle=0):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.translate(size, size); painter.rotate(angle); painter.translate(-size, -size)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class SelectItemDelegate(QStyledItemDelegate):
    """Draw menu rows without the platform's native combo-box decoration."""
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        return QSize(180, 38)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = index.row() == self.combo.currentIndex()
        active = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        if active or selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#eaf0ff' if active else '#f3f6fc'))
            painter.drawRoundedRect(rect, 6, 6)
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor('#3268d9' if active or selected else '#40536e'))
        text_rect = rect.adjusted(12, 0, -34, 0)
        text = painter.fontMetrics().elidedText(str(index.data() or ''), Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if selected:
            x, y = rect.right() - 19, rect.center().y()
            painter.setPen(QPen(QColor('#3268d9'), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(x-4, y), QPointF(x-1, y+3))
            painter.drawLine(QPointF(x-1, y+3), QPointF(x+5, y-4))
        painter.restore()


class SelectPopup(QFrame):
    def __init__(self, combo):
        super().__init__(combo, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.combo = combo
        self.setObjectName('selectPopup')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 14)
        self.panel = QFrame()
        self.panel.setObjectName('selectMenu')
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(22, 42, 73, 35))
        self.panel.setGraphicsEffect(shadow)
        inner = QVBoxLayout(self.panel)
        inner.setContentsMargins(4, 5, 4, 5)
        self.view = QListView()
        self.view.setObjectName('selectOptions')
        self.view.setMouseTracking(True)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setItemDelegate(SelectItemDelegate(combo))
        self.view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view.clicked.connect(combo.accept_option)
        self.view.entered.connect(self.view.setCurrentIndex)
        self.view.installEventFilter(combo)
        inner.addWidget(self.view)
        layout.addWidget(self.panel)

    def hideEvent(self, event):
        self.combo.update()
        super().hideEvent(event)


class ComboBox(QComboBox):
    """A fully styled selector and popup, retaining QComboBox's model/signals."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setMaxVisibleItems(8)
        self.menu = SelectPopup(self)

    def sizeHint(self):
        return QSize(240, 44)

    def minimumSizeHint(self):
        # The custom painter elides long text. Native combo hints can otherwise
        # force the whole Windows form to fit its longest device/version name.
        return QSize(140, 44)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        opened = self.menu.isVisible()
        focused = self.hasFocus() or opened
        hovered = self.underMouse()
        enabled = self.isEnabled()
        border = '#6b91df' if focused else '#b8c9e4' if hovered else '#dce3ee'
        painter.setPen(QPen(QColor(border if enabled else '#e5eaf1'), 1))
        painter.setBrush(QColor('#ffffff' if focused else '#f8fafd' if enabled else '#f3f5f8'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 7, 7)
        painter.setFont(self.font())
        painter.setPen(QColor('#263851' if enabled else '#a3adbc'))
        text_rect = self.rect().adjusted(14, 0, -49, 0)
        text = painter.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        x, y = self.width() - 23, self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#eaf0ff' if focused else '#edf2f8'))
        painter.drawRoundedRect(QRectF(x-12, y-12, 24, 24), 5, 5)
        painter.setPen(QPen(QColor('#3268d9' if focused and enabled else '#8b9bb2'), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        direction = -1 if opened else 1
        painter.drawLine(QPointF(x-3, y-direction), QPointF(x, y+2*direction))
        painter.drawLine(QPointF(x, y+2*direction), QPointF(x+3, y-direction))
        painter.end()

    def showPopup(self):
        if not self.isEnabled() or not self.count():
            return
        view = self.menu.view
        view.setFont(self.font())
        view.setModel(self.model())
        view.setModelColumn(self.modelColumn())
        view.setRootIndex(self.rootModelIndex())
        view.setAccessibleName(self.accessibleName() + ' options')
        current = self.model().index(self.currentIndex(), self.modelColumn(), self.rootModelIndex())
        view.setCurrentIndex(current)
        panel_height = min(self.count(), self.maxVisibleItems()) * 38 + 12
        width, height = self.width() + 20, panel_height + 24
        below = self.mapToGlobal(QPoint(0, self.height() + 5))
        above = self.mapToGlobal(QPoint(0, -5))
        screen = self.screen().availableGeometry()
        x = min(max(screen.left(), below.x() - 10), screen.right() - width + 1)
        y = below.y() - 10
        if y + height > screen.bottom() and above.y() - height >= screen.top():
            y = above.y() - height + 14
        y = max(screen.top(), min(y, screen.bottom() - height + 1))
        self.menu.setGeometry(x, y, width, height)
        self.menu.show()
        view.scrollTo(current, QAbstractItemView.ScrollHint.EnsureVisible)
        view.setFocus(Qt.FocusReason.PopupFocusReason)
        self.update()

    def hidePopup(self):
        self.menu.hide()
        super().hidePopup()
        self.update()

    def accept_option(self, index):
        if index.isValid():
            self.setCurrentIndex(index.row())
            self.activated.emit(index.row())
            self.textActivated.emit(self.currentText())
        self.hidePopup()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched, event):
        if hasattr(self, 'menu') and watched is self.menu.view and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.accept_option(self.menu.view.currentIndex())
                return True
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                self.setFocus(Qt.FocusReason.PopupFocusReason)
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.accept_option(self.menu.view.currentIndex())
                self.focusNextPrevChild(key == Qt.Key.Key_Tab)
                return True
        return super().eventFilter(watched, event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        # Scrolling the form should not silently change its device selection.
        event.ignore()


def label(text, name='', wrap=False):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


class Button(QPushButton):
    """Size styled controls from their content, without platform button minima."""
    def sizeHint(self):
        self.ensurePolished()
        horizontal, vertical = {'primary': (20, 12), 'quiet': (8, 7), 'danger': (8, 7),
                                'region': (15, 8), 'sourceTab': (12, 7)}.get(self.objectName(), (14, 10))
        ancestor = self.parentWidget()
        while ancestor is not None:
            if ancestor.objectName() == 'sidebar':
                horizontal, vertical = 16, 13
                break
            ancestor = ancestor.parentWidget()
        font = QFont(self.font())
        if self.isCheckable():
            # Qt paints checked region text bold without changing self.font().
            # Reserve that width in both states so selection never clips/jumps.
            font.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        width = max(metrics.horizontalAdvance(self.text()), metrics.boundingRect(self.text()).width()) + 2
        height = metrics.height()
        if not self.icon().isNull():
            width += self.iconSize().width() + (6 if self.text() else 0)
            height = max(height, self.iconSize().height())
        return QSize(width + horizontal * 2 + 2, height + vertical * 2 + 2)

    def minimumSizeHint(self):
        return self.sizeHint()


class RecheckButton(Button):
    def __init__(self, callback):
        super().__init__('Recheck')
        self.setObjectName('quiet'); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Recheck bloat files')
        self.setIconSize(QSize(18, 18)); self.angle = 0
        self.animation = QTimer(self); self.animation.setInterval(80)
        self.animation.timeout.connect(self.advance)
        self.clicked.connect(callback); self.set_scanning(False)

    def advance(self):
        self.angle = (self.angle + 24) % 360
        self.setIcon(icon('refresh', '#3268d9', angle=self.angle))

    def set_scanning(self, scanning):
        self.setToolTip('Checking downloaded bloat files…' if scanning else
                        'Recheck that the selected bloat files are downloaded and valid.')
        if scanning:
            self.setEnabled(False); self.animation.start()
        else:
            self.animation.stop(); self.angle = 0; self.setIcon(icon('refresh'))


class CheckBox(QCheckBox):
    """A themed checkbox with native keyboard and accessibility behavior."""
    def sizeHint(self):
        self.ensurePolished()
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 28, 36)

    def minimumSizeHint(self): return self.sizeHint()

    def hitButton(self, point): return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isEnabled(); checked = self.isChecked()
        border = '#3268d9' if active and checked else '#a8bddf' if active and self.hasFocus() else '#dce3ee'
        painter.setPen(QPen(QColor(border), 1.5 if self.hasFocus() else 1))
        painter.setBrush(QColor('#3268d9' if active and checked else '#dce5f3' if checked else '#ffffff' if active else '#f2f4f8'))
        box = QRectF(1, (self.height()-16)/2, 16, 16); painter.drawRoundedRect(box, 4, 4)
        if checked:
            painter.setPen(QPen(QColor('#ffffff'), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            x, y = box.x()+4, box.center().y()
            painter.drawLine(QPointF(x,y),QPointF(x+3,y+3));painter.drawLine(QPointF(x+3,y+3),QPointF(x+9,y-4))
        painter.setPen(QColor('#40536e' if active else '#a5afbf'));painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(25,0,0,0),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.text())
        painter.end()


def button(text, symbol=None, name='', callback=None):
    widget = Button(text)
    widget.setObjectName(name)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if symbol:
        widget.setIcon(icon(symbol, '#ffffff' if name == 'primary' else '#b35454' if name == 'danger' else '#617188'))
        widget.setIconSize(QSize(18, 18))
    if callback:
        widget.clicked.connect(callback)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName('card')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(24, 18, 24, 18)
    layout.setSpacing(12)
    return widget, layout


STYLE = '''
QMainWindow, QWidget { background: #f4f6f9; color: #1b273b; font-family: "Inter", "Segoe UI", "DejaVu Sans"; font-size: 13px; }
QWidget#sidebar { background: #101c30; }
QWidget#sidebar QLabel { background: transparent; color: #91a0b9; }
QWidget#sidebar QLabel#brand { font-size: 18px; font-weight: 700; color: #f7f9fe; }
QWidget#sidebar QLabel#brandSub { font-size: 10px; color: #7487a4; letter-spacing: 2px; }
QWidget#sidebar QLabel#sideHeading { color: #607591; font-size: 10px; font-weight: 600; letter-spacing: 1.8px; }
QWidget#sidebar QPushButton { text-align: left; padding: 13px 16px; background: transparent; border: 0; border-radius: 7px; color: #a3b2c9; font-weight: 500; }
QWidget#sidebar QPushButton:hover { background: #1a2a43; color: white; }
QWidget#sidebar QPushButton:checked { background: #213a61; color: #eef5ff; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #7f8b9c; font-size: 10px; font-weight: 600; letter-spacing: 1.8px; }
QLabel#title { font-size: 29px; font-weight: 700; color: #18283f; }
QLabel#subtitle, QLabel#muted { color: #7b889a; font-size: 12px; }
QLabel#cardTitle { font-size: 15px; font-weight: 650; color: #20324b; }
QLabel#fieldLabel { color: #485871; font-size: 11px; font-weight: 600; }
QLabel#sectionNumber { background: #eef3ff; border-radius: 6px; color: #3b66c8; font-size: 11px; font-weight: 700; padding: 6px; }
QLabel#osBadge { color: #536b92; background: #f0f4fa; border-radius: 5px; padding: 5px 8px; font-size: 10px; }
QFrame#card { background: #ffffff; border: 1px solid #e1e6ef; border-radius: 11px; }
QWidget#innerPanel { background: transparent; }
QFrame#line { background: #edf0f5; border: none; max-height: 1px; min-height: 1px; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #dce3ee; border-radius: 6px; min-height: 22px; padding: 9px 11px; color: #263851; selection-background-color: #dce7ff; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { width: 25px; border: 0; }
QFrame#selectPopup { background: transparent; border: none; }
QFrame#selectMenu { background: #ffffff; border: 1px solid #dce3ee; border-radius: 10px; }
QListView#selectOptions { background: transparent; border: none; outline: none; padding: 0; }
QListView#selectOptions::item { border: none; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #5c83d7; }
QLineEdit:disabled, QComboBox:disabled { background: #f4f6f9; color: #a3adbc; }
QPushButton { border: 1px solid #dce3ee; border-radius: 6px; background: white; padding: 10px 14px; color: #40536e; font-weight: 500; }
QPushButton:hover { background: #f1f5fc; border-color: #b9c9e5; }
QPushButton:pressed { background: #e4ecfa; }
QPushButton:disabled { background: #f2f4f8; color: #a5afbf; border-color: #e6eaf1; }
QPushButton#primary { background: #3268d9; border: 1px solid #3268d9; color: white; font-weight: 600; padding: 12px 20px; }
QPushButton#primary:hover { background: #245ac9; }
QPushButton#primary:disabled { background: #a0b8e8; border-color: #a0b8e8; color: #f0f4ff; }
QPushButton#quiet { background: transparent; border: none; padding: 7px 8px; color: #7b889a; font-size: 11px; }
QPushButton#quiet:hover { color: #3268d9; background: #edf2fc; }
QPushButton#danger { background: transparent; border: none; padding: 7px 8px; color: #b35454; font-size: 11px; }
QPushButton#danger:hover { color: #9b3434; background: #fff0f0; }
QPushButton#danger:disabled { color: #a5afbf; background: transparent; }
QCheckBox#zipOption { background: transparent; font-size: 11px; color: #40536e; }
QPushButton#region { background: #f7f9fc; padding: 8px 15px; color: #76859b; font-size: 11px; }
QPushButton#region:checked { background: #eaf0ff; border: 1px solid #9db7ec; color: #285bc0; font-weight: 700; }
QPushButton#sourceTab { background: #f7f9fc; border: none; padding: 7px 12px; font-size: 11px; color: #8290a4; }
QPushButton#sourceTab:checked { background: #eaf0ff; color: #3268d9; }
QLabel#summaryDevice { font-size: 22px; font-weight: 650; color: #20324b; }
QLabel#summaryValue { color: #3c4f6b; font-size: 12px; }
QLabel#hint { color: #7d8b9e; font-size: 11px; line-height: 1.4; }
QLabel#step { font-size: 10px; color: #94a0b1; }
QLabel#step[active="true"] { color: #3268d9; font-weight: 600; }
QLabel#logTitle { color: #d6e1f2; font-size: 12px; font-weight: 600; }
QFrame#consoleCard { background: #142137; border: 1px solid #233249; border-radius: 9px; }
QFrame#consoleCard QWidget { background: transparent; }
QPlainTextEdit { background: #142137; color: #afc3df; border: none; font-family: "JetBrains Mono", "Consolas", "DejaVu Sans Mono"; font-size: 11px; padding: 8px 14px; selection-background-color: #315682; }
QTextBrowser#documentation { background: #ffffff; color: #40536e; border: none; padding: 0; selection-background-color: #dce7ff; }
QProgressBar { border: none; background: #e3e9f3; border-radius: 2px; max-height: 4px; min-height: 4px; }
QProgressBar::chunk { background: #4277dd; border-radius: 2px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 7px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #c4cddc; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTreeWidget { background: white; border: 0; alternate-background-color: #f8fafd; color: #40536e; padding: 4px; }
QTreeWidget::item { padding: 12px 6px; border-bottom: 1px solid #eef1f6; }
QTreeWidget::item:selected { background: #eaf0ff; color: #2c5fba; }
QHeaderView::section { background: #f5f7fb; color: #7b889a; border: 0; padding: 12px 8px; font-size: 10px; font-weight: 600; }
QToolTip { background: #1b2e49; color: white; border: 0; padding: 6px; }
'''


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Flasher Builder')
        self.setWindowIcon(icon('layers', '#3268d9', 64))
        self.resize(1280, 820)
        self.setMinimumSize(1120, 720)
        self.catalog = Catalog()
        self.selection_valid = False
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.cancel_file = None
        self.last_folder = ROOT / 'builds'
        self.action = None
        self.output_available = False
        self.update_checking = False
        self.latest_update = None
        self.started = None
        self.settings = ROOT / '.flasher-builder.json'
        self.saved = {}
        try:
            self.saved = json.loads(self.settings.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.inputs = []
        self.fields = {}
        self.ota_mode = 'file'
        self.ota_data = None
        self.ota_context = None
        self.ota_fetch_token = 0
        self.ota_fetching = False
        self.ota_link_token = 0
        self.ota_link_pending = False
        self.ota_local = {}
        self.bloat_verified = {}
        self.bloat_scan_token = 0
        self.bloat_probe_key = None
        self.bloat_data = None
        self.bloat_active_token = None
        self.bloat_scan_threads = []
        self.bloat_deleting = False
        self.bloat_check_timer = QTimer(self)
        self.bloat_check_timer.setSingleShot(True)
        self.bloat_check_timer.timeout.connect(self.scan_bloat_status)
        self.build_layout()
        self.restore()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.bloat_watch_timer = QTimer(self)
        self.bloat_watch_timer.timeout.connect(self.refresh_bloat_status)
        self.bloat_watch_timer.start(3000)

    def build_layout(self):
        shell = QWidget()
        self.setCentralWidget(shell)
        horizontal = QHBoxLayout(shell)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 29, 18, 22)
        side.setSpacing(7)
        brand = QHBoxLayout()
        mark = QLabel(); mark.setPixmap(icon('layers', '#77a3ff', 31).pixmap(31, 31))
        brand.addWidget(mark)
        brand_text = QVBoxLayout(); brand_text.setSpacing(2)
        brand_text.addWidget(label('Flasher', 'brand'))
        brand_text.addWidget(label('BUILDER STUDIO', 'brandSub'))
        brand.addLayout(brand_text); brand.addStretch()
        side.addLayout(brand)
        side.addSpacing(38)
        side.addWidget(label('WORKSPACE', 'sideHeading'))
        side.addSpacing(5)
        self.nav = []
        self.nav_group = QButtonGroup(self)
        for i, (text, symbol) in enumerate((('New package', 'build'), ('Build history', 'clock'), ('Environment', 'settings'), ('Build output', 'terminal'))):
            b = button('  ' + text, callback=lambda checked=False, index=i: self.navigate(index))
            b.setIcon(icon(symbol, '#8da9d3'))
            b.setCheckable(True)
            self.nav_group.addButton(b)
            self.nav.append(b)
            side.addWidget(b)
        self.nav[0].setChecked(True)
        self.nav[3].hide()
        side.addStretch()
        self.update_notice = button('Update available', 'download', callback=self.open_update)
        self.update_notice.hide(); side.addWidget(self.update_notice)
        side.addSpacing(9)
        self.documentation_button = button('  Documentation', callback=self.open_documentation)
        self.documentation_button.setIcon(icon('help', '#7b90ad'))
        self.documentation_button.setCheckable(True)
        self.nav_group.addButton(self.documentation_button); self.nav.append(self.documentation_button)
        side.addWidget(self.documentation_button)
        horizontal.addWidget(sidebar)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(32, 20, 32, 16)
        body.setSpacing(12)
        header = QHBoxLayout()
        heading = QVBoxLayout(); heading.setSpacing(6)
        self.title_label = label('New package', 'title'); heading.addWidget(self.title_label)
        self.subtitle = label('Turn a full OTA into a ready-to-use flasher package.', 'subtitle'); heading.addWidget(self.subtitle)
        header.addLayout(heading); header.addStretch()
        body.addLayout(header)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_page())
        self.pages.addWidget(self.history_page())
        self.pages.addWidget(self.environment_page())
        self.output_page = self.console()
        self.pages.addWidget(self.output_page)
        self.pages.addWidget(self.documentation_page())
        body.addWidget(self.pages, 1)
        footer = QHBoxLayout()
        self.workflow_hint = label('CONFIGURE → BLOAT FILES → OTA → BUILD', 'eyebrow')
        footer.addWidget(self.workflow_hint)
        footer.addStretch()
        footer.addWidget(label('Qt 6  /  Local workspace', 'muted'))
        body.addLayout(footer)
        horizontal.addWidget(content, 1)

    def build_page(self):
        page = QWidget(); page.setObjectName('innerPanel')
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(16)
        configure, form = card()
        title = QHBoxLayout()
        title.addWidget(label('01', 'sectionNumber'))
        title.addWidget(label('Package configuration', 'cardTitle'))
        title.addStretch(); self.os_badge = label('OXYGEN OS', 'osBadge'); title.addWidget(self.os_badge)
        form.addLayout(title)
        grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(8)
        for col, (key, text) in enumerate((('device', 'DEVICE'), ('kind', 'FLASHER TYPE'))):
            grid.addWidget(label(text, 'fieldLabel'), 0, col)
            widget = ComboBox(); widget.setMinimumWidth(170); widget.setAccessibleName(text.title())
            self.fields[key] = widget; self.inputs.append(widget)
            grid.addWidget(widget, 1, col)
            grid.setColumnStretch(col, 1)
        form.addLayout(grid)
        form.addWidget(label('REGION', 'fieldLabel'))
        self.region_buttons_panel = QWidget(); self.region_buttons_panel.setObjectName('innerPanel')
        regions = QHBoxLayout(self.region_buttons_panel); regions.setContentsMargins(0, 0, 0, 0); regions.setSpacing(7)
        self.region_group = QButtonGroup(self)
        self.region_buttons = {}
        for region in ('GLO', 'EU', 'IN', 'NA', 'CN'):
            b = button('Global' if region == 'GLO' else region, name='region', callback=lambda checked=False, r=region: self.set_region(r))
            b.setCheckable(True); self.region_group.addButton(b)
            self.region_buttons[region] = b; self.inputs.append(b); regions.addWidget(b)
        regions.addStretch(); form.addWidget(self.region_buttons_panel)
        self.region_combo = ComboBox(); self.region_combo.setAccessibleName('Region')
        self.region_combo.setStyleSheet('padding: 6px 11px;')
        self.region_combo.currentIndexChanged.connect(lambda: self.set_region(self.region_combo.currentData()) if self.region_combo.currentData() else None)
        self.inputs.append(self.region_combo); form.addWidget(self.region_combo); self.region_combo.hide()
        self.selection_info = label('Select a device and region to get started.', 'hint', True)
        form.addWidget(self.selection_info)
        top.addWidget(configure, 1)
        self.configure_card = configure
        source_card, source_layout = card()
        self.ota_card = source_card
        source_head = QHBoxLayout()
        source_head.addWidget(label('03', 'sectionNumber'))
        source_head.addWidget(label('OTA source', 'cardTitle'))
        source_head.addStretch()
        self.source_group = QButtonGroup(self)
        self.file_tab = button('Local file', 'file', 'sourceTab', lambda: self.source_mode(False))
        self.url_tab = button('Download URL', 'link', 'sourceTab', lambda: self.source_mode(True))
        self.catalog_tab = button('Version catalog', 'download', 'sourceTab', lambda: self.source_mode('catalog'))
        for b in (self.file_tab, self.url_tab, self.catalog_tab):
            b.setCheckable(True); self.source_group.addButton(b); source_head.addWidget(b); self.inputs.append(b)
        self.file_tab.setChecked(True)
        source_layout.addLayout(source_head)
        self.catalog_panel = QWidget()
        self.catalog_panel.setObjectName('innerPanel')
        catalog_layout = QVBoxLayout(self.catalog_panel); catalog_layout.setContentsMargins(0, 0, 0, 0); catalog_layout.setSpacing(8)
        catalog_head = QHBoxLayout()
        self.ota_catalog_status = label('Fetch OTA versions for this device and region.', 'hint', True)
        catalog_head.addWidget(self.ota_catalog_status, 1)
        self.ota_fetch_button = button('Fetch versions', 'download', callback=self.fetch_ota_versions)
        self.inputs.append(self.ota_fetch_button); catalog_head.addWidget(self.ota_fetch_button)
        catalog_layout.addLayout(catalog_head)
        self.catalog_roles = QGridLayout(); self.catalog_roles.setContentsMargins(0, 0, 0, 0); self.catalog_roles.setHorizontalSpacing(20)
        self.catalog_roles.setColumnStretch(0, 1); self.catalog_roles.setColumnStretch(1, 1)
        catalog_layout.addLayout(self.catalog_roles)
        self.ota_combos = {}; self.ota_details = {}; self.ota_download_buttons = {}; self.ota_copy_buttons = {}; self.ota_role_panels = {}
        for role, title in (('ota', 'TARGET OTA'), ('base', 'ACE 6 COLOROS BASE OTA')):
            panel = QWidget(); panel.setObjectName('innerPanel')
            pl = QVBoxLayout(panel); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(8)
            pl.addWidget(label(title, 'fieldLabel'))
            combo = ComboBox(); combo.setAccessibleName(title + ' version'); combo.setMinimumWidth(180)
            combo.currentIndexChanged.connect(lambda index, r=role: self.ota_version_changed(r))
            self.ota_combos[role] = combo; self.inputs.append(combo); pl.addWidget(combo)
            detail = label('', 'hint', True); self.ota_details[role] = detail; pl.addWidget(detail)
            row = QHBoxLayout()
            download = button('Download OTA', 'download', callback=lambda checked=False, r=role: self.choose_ota_download(r))
            copy = button('Copy download link', 'link', callback=lambda checked=False, r=role: self.copy_ota_link(r))
            self.ota_download_buttons[role] = download; self.ota_copy_buttons[role] = copy
            self.inputs.extend((download, copy)); row.addWidget(download); row.addWidget(copy); row.addStretch(); pl.addLayout(row)
            self.ota_role_panels[role] = panel; self.catalog_roles.addWidget(panel, 0, 0 if role == 'ota' else 1)
        source_layout.addWidget(self.catalog_panel); self.catalog_panel.hide()
        self.manual_panel = QWidget(); self.manual_panel.setObjectName('innerPanel')
        manual_layout = QHBoxLayout(self.manual_panel); manual_layout.setContentsMargins(0, 0, 0, 0); manual_layout.setSpacing(20)
        target_panel = QWidget(); target_panel.setObjectName('innerPanel')
        target_layout = QVBoxLayout(target_panel); target_layout.setContentsMargins(0, 0, 0, 0); target_layout.setSpacing(8)
        target_layout.addWidget(label('FULL OTA PACKAGE', 'fieldLabel'))
        self.fields['ota'], self.browse_ota = self.source_input(target_layout, 'Choose a full OTA .zip file', 'ota')
        manual_layout.addWidget(target_panel, 1)
        self.base_panel = QWidget(); self.base_panel.setObjectName('innerPanel')
        base_layout = QVBoxLayout(self.base_panel); base_layout.setContentsMargins(0, 0, 0, 0); base_layout.setSpacing(8)
        base_layout.addWidget(label('ACE 6 COLOROS BASE OTA', 'fieldLabel'))
        self.fields['base'], self.browse_base = self.source_input(base_layout, 'Choose BASE OTA or paste its download URL', 'base')
        manual_layout.addWidget(self.base_panel, 1)
        source_layout.addWidget(self.manual_panel)
        self.source_hint = label('Use a full OTA package. Incremental updates are not supported.', 'hint', True)
        source_layout.addWidget(self.source_hint)
        actions = QHBoxLayout(); actions.setSpacing(8)
        actions.addWidget(label('Save package to', 'fieldLabel'))
        self.fields['output'] = QLineEdit(str(ROOT / 'builds')); self.inputs.append(self.fields['output'])
        self.fields['output'].textChanged.connect(self.update_summary)
        actions.addWidget(self.fields['output'], 1)
        output_browse = button('Browse', 'folder', callback=self.choose_output)
        self.inputs.append(output_browse); actions.addWidget(output_browse)
        self.zip_build = CheckBox('ZIP build'); self.zip_build.setObjectName('zipOption')
        self.zip_build.setChecked(True); self.zip_build.setAccessibleName('Create ZIP archive')
        self.zip_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zip_build.setToolTip('Compress the finished build into a ZIP. Uncheck to keep the flasher folder. Recovery installation requires a ZIP.')
        self.zip_build.toggled.connect(self.zip_output_changed)
        self.inputs.append(self.zip_build); actions.addWidget(self.zip_build)
        self.start_button = button('Build package', 'arrow', 'primary', lambda: self.start('build'))
        actions.addWidget(self.start_button)
        self.view_output_button = button('View output', 'terminal', callback=lambda: self.navigate(3))
        self.view_output_button.hide(); actions.addWidget(self.view_output_button)
        self.cancel_button = button('Cancel build', 'stop', callback=self.cancel); self.cancel_button.setEnabled(False)
        self.output_button = button('Open output folder', 'folder', callback=self.open_output)
        summary, summary_layout = card(); summary.setFixedWidth(276); summary_layout.setSpacing(8)
        summary_layout.addWidget(label('YOUR PACKAGE', 'eyebrow'))
        self.summary_device = label('OnePlus 13', 'summaryDevice', True); summary_layout.addWidget(self.summary_device)
        self.summary_kind = label('SuperHybrid Flasher', 'muted', True); summary_layout.addWidget(self.summary_kind)
        line = QFrame(); line.setObjectName('line'); summary_layout.addWidget(line)
        self.summary_values = {}
        for key, title in (('os', 'System'), ('region', 'Region'), ('format', 'Output'), ('engine', 'Build engine')):
            row = QHBoxLayout(); row.addWidget(label(title, 'muted')); row.addStretch()
            value = label('', 'summaryValue'); row.addWidget(value); self.summary_values[key] = value
            summary_layout.addLayout(row)
        line = QFrame(); line.setObjectName('line'); summary_layout.addWidget(line)
        self.space = label('', 'hint', True); summary_layout.addWidget(self.space)
        self.package_summary_card = summary
        notes, nl = card(); nl.setSpacing(8); nl.setContentsMargins(24, 14, 24, 14)
        heading = QHBoxLayout()
        heading.addWidget(label('02', 'sectionNumber'))
        heading.addWidget(label('Bloat files', 'cardTitle')); heading.addStretch()
        self.bloat_open = button('Open folder', 'folder', 'quiet', self.open_bloat_folder)
        heading.addWidget(self.bloat_open)
        self.bloat_recheck = RecheckButton(lambda: self.refresh_bloat_status(force=True))
        heading.addWidget(self.bloat_recheck)
        self.bloat_status = label('Checking local files…', 'hint')
        nl.addLayout(heading)
        self.bloat_info = label('For the selected device and region', 'hint', True)
        self.bloat_status.setWordWrap(True)
        bloat_metadata = QHBoxLayout(); bloat_metadata.setSpacing(12)
        bloat_metadata.addWidget(self.bloat_status, 1); bloat_metadata.addWidget(self.bloat_info, 1)
        nl.addLayout(bloat_metadata)
        folder_row = QHBoxLayout(); folder_row.setSpacing(6)
        self.fields['bloat_dir'] = QLineEdit(str(ROOT / 'bloat'))
        self.fields['bloat_dir'].setReadOnly(True)
        self.fields['bloat_dir'].setAccessibleName('Bloat download folder')
        self.fields['bloat_dir'].setStyleSheet('font-size: 10px; padding: 7px;')
        self.fields['bloat_dir'].textChanged.connect(self.refresh_bloat_status)
        folder_row.addWidget(self.fields['bloat_dir'], 1)
        self.bloat_browse = button('Choose folder', 'folder', callback=self.choose_bloat_folder)
        self.bloat_browse.setToolTip('Choose bloat download folder')
        self.bloat_browse.setAccessibleName('Choose bloat download folder')
        folder_row.addWidget(self.bloat_browse); nl.addLayout(folder_row)
        self.inputs.extend((self.fields['bloat_dir'], self.bloat_browse))
        self.bloat_download = button('Download bloat', 'download', callback=lambda: self.start('bloat'))
        self.bloat_download.setEnabled(False)
        bloat_actions = QHBoxLayout()
        bloat_actions.addWidget(self.bloat_download)
        bloat_actions.addStretch()
        self.bloat_delete = button('Delete bloat', 'trash', 'danger', self.delete_selected_bloat)
        self.bloat_delete.setToolTip('Delete the selected bloat images and unused cached downloads.')
        self.bloat_delete.setEnabled(False); bloat_actions.addWidget(self.bloat_delete)
        nl.addLayout(bloat_actions)
        self.bloat_card = notes
        top.addWidget(notes, 1)
        for frame in (configure, notes, source_card):
            frame.layout().setContentsMargins(18, 12, 18, 12)
            frame.layout().setSpacing(8)
        layout.addLayout(top)
        layout.addWidget(source_card)
        self.abl_card, abl_layout = card()
        abl_layout.setContentsMargins(18, 4, 18, 4); abl_layout.setSpacing(0)
        abl_heading = QHBoxLayout()
        abl_heading.setSpacing(10)
        abl_heading.addWidget(label('04', 'sectionNumber'))
        abl_heading.addWidget(label('Vulnerable ABL', 'cardTitle'))
        self.abl_hint = label('Choose Yes or No to continue.', 'hint')
        abl_heading.addWidget(self.abl_hint)
        abl_heading.addStretch()
        abl_heading.addWidget(label('Pack ABL 16.0.6?', 'fieldLabel'))
        self.abl_group = QButtonGroup(self)
        self.abl_yes = button('Yes', name='region'); self.abl_no = button('No', name='region')
        for value, control in ((1, self.abl_yes), (0, self.abl_no)):
            control.setCheckable(True); self.abl_group.addButton(control, value)
            control.setAccessibleName('Pack vulnerable ABL: ' + control.text())
            abl_heading.addWidget(control); self.inputs.append(control)
        self.abl_group.buttonClicked.connect(self.zip_output_changed)
        abl_layout.addLayout(abl_heading)
        self.abl_card.hide(); layout.addWidget(self.abl_card)
        layout.addStretch(1)
        layout.addLayout(actions)
        self.fields['device'].addItems(list(self.catalog.devices))
        self.fields['device'].currentTextChanged.connect(self.device_changed)
        self.fields['kind'].currentTextChanged.connect(self.kind_changed)
        return page

    def source_input(self, parent_layout, placeholder, key):
        row = QHBoxLayout(); row.setSpacing(8)
        field = QLineEdit(); field.setPlaceholderText(placeholder)
        field.setClearButtonEnabled(True)
        field.textChanged.connect(self.update_summary)
        row.addWidget(field, 1)
        browse = button('Choose ZIP', 'folder', callback=lambda: self.choose_ota(key))
        row.addWidget(browse)
        self.inputs.extend((field, browse))
        parent_layout.addLayout(row)
        return field, browse

    def history_page(self):
        page, layout = card()
        row = QHBoxLayout(); row.addWidget(label('Recent packages', 'cardTitle')); row.addStretch()
        row.addWidget(button('Refresh', 'clock', callback=self.load_history)); layout.addLayout(row)
        self.history = QTreeWidget(); self.history.setHeaderLabels(['PACKAGE', 'REGION', 'VERSION', 'CREATED'])
        self.history.setRootIsDecorated(False); self.history.setAlternatingRowColors(True)
        self.history.itemDoubleClicked.connect(self.open_history)
        self.history.setColumnWidth(0, 430)
        layout.addWidget(self.history, 1)
        layout.addWidget(label('Double-click a package to open its folder. Test packages are labelled TEST.', 'hint'))
        return page

    def environment_page(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        page, layout = card(); scroll.setWidget(page)
        layout.addWidget(label('Build environment', 'cardTitle'))
        layout.addWidget(label('Check the local tools before starting a firmware build.', 'muted'))
        details = QGridLayout(); details.setVerticalSpacing(22); details.setColumnStretch(1, 1)
        for row, (title, value) in enumerate((('APP VERSION', __version__), ('BUILD BACKEND', 'Native Windows' if sys.platform == 'win32' else 'Native Linux'), ('ARCHITECTURE', platform.machine()), ('BUILD TOOLS', 'otaripper  /  lpmake  /  avbtool  /  aria2c'), ('WORKFLOW CATALOG', f'{len(self.catalog.builders)} supported device, flasher and region combinations'))):
            details.addWidget(label(title, 'fieldLabel'), row, 0)
            details.addWidget(label(value, 'summaryValue', True), row, 1)
        layout.addLayout(details)
        self.fields['distro'] = QLineEdit(); self.fields['distro'].hide()  # Old settings compatibility.
        line = QFrame(); line.setObjectName('line'); layout.addWidget(line)
        actions = QHBoxLayout()
        self.check_button = button('Check setup', 'check', 'primary', lambda: self.start('check'))
        self.test_button = button('Run pipeline self-test', 'terminal', callback=lambda: self.start('test'))
        actions.addWidget(self.check_button); actions.addWidget(self.test_button); actions.addStretch(); layout.addLayout(actions)
        layout.addWidget(label('The self-test creates a tiny synthetic OTA and checks the real extraction, packing and ZIP pipeline. Test packages contain synthetic data and must never be flashed.', 'hint', True))
        line = QFrame(); line.setObjectName('line'); layout.addWidget(line)
        self.update_status = label('Updates are checked automatically when the app starts.', 'hint', True)
        layout.addWidget(self.update_status)
        update_actions = QHBoxLayout()
        self.update_check_button = button('Check for updates', 'clock', callback=self.check_updates)
        self.update_open_button = button('Open update release', 'download', callback=self.open_update)
        self.update_open_button.hide()
        update_actions.addWidget(self.update_check_button); update_actions.addWidget(self.update_open_button)
        update_actions.addStretch(); layout.addLayout(update_actions)
        layout.addStretch()
        return scroll

    def documentation_page(self):
        page = QWidget(); page.setObjectName('innerPanel')
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        toolbar = QHBoxLayout(); toolbar.setSpacing(8)
        toolbar.addWidget(button('Guide', 'help', callback=self.open_documentation))
        self.documentation_topics = ComboBox(); self.documentation_topics.setAccessibleName('Documentation section')
        self.documentation_topics.setMinimumWidth(180)
        self.documentation_topics.currentIndexChanged.connect(self.jump_documentation)
        toolbar.addWidget(self.documentation_topics, 1)
        self.documentation_search = QLineEdit(); self.documentation_search.setPlaceholderText('Find in documentation…')
        self.documentation_search.setAccessibleName('Find in documentation'); self.documentation_search.setClearButtonEnabled(True)
        self.documentation_search.textChanged.connect(lambda: self.find_documentation(restart=True))
        self.documentation_search.returnPressed.connect(self.find_documentation)
        toolbar.addWidget(self.documentation_search, 1)
        self.documentation_next = button('Next match', callback=self.find_documentation)
        self.documentation_next.setEnabled(False); toolbar.addWidget(self.documentation_next)
        layout.addLayout(toolbar)
        paper, paper_layout = card(); paper_layout.setContentsMargins(6, 6, 6, 6)
        self.documentation_view = QTextBrowser(); self.documentation_view.setObjectName('documentation')
        self.documentation_view.setAccessibleName('Documentation content')
        self.documentation_view.setOpenLinks(False); self.documentation_view.setOpenExternalLinks(False)
        self.documentation_view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.documentation_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.documentation_view.anchorClicked.connect(self.open_documentation_link)
        paper_layout.addWidget(self.documentation_view); layout.addWidget(paper, 1)
        self.documentation_path = None
        return page

    def open_documentation(self):
        self.load_documentation(ROOT / 'README.md')
        self.navigate(4)

    def load_documentation(self, path):
        self.documentation_path = Path(path).resolve()
        document = self.documentation_view.document()
        font = QFont(QApplication.font()); font.setPixelSize(14)
        document.setDefaultFont(font); document.setDocumentMargin(26)
        document.setBaseUrl(QUrl.fromLocalFile(str(self.documentation_path.parent) + os.sep))
        try:
            text = self.documentation_path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            text = '# Documentation unavailable\n\nKeep README.md and the docs folder beside the application.'
        document.setMarkdown(text)
        self.documentation_topics.blockSignals(True); self.documentation_topics.clear()
        block = document.begin()
        while block.isValid():
            cursor = QTextCursor(block); formatting = block.blockFormat(); level = formatting.headingLevel()
            formatting.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            formatting.setBottomMargin(12)
            if level:
                formatting.setTopMargin(8 if level == 1 else 24); formatting.setBottomMargin(12)
                heading = QTextCharFormat(); heading.setFontPointSize({1: 20, 2: 15, 3: 12}.get(level, 11))
                heading.setFontWeight(QFont.Weight.DemiBold); heading.setForeground(QColor('#20324b'))
                heading.setAnchor(True); heading.setAnchorNames([re.sub(r'[^\w -]', '', block.text()).lower().replace(' ', '-')])
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor); cursor.mergeCharFormat(heading)
                self.documentation_topics.addItem(block.text(), block.position())
            elif formatting.nonBreakableLines():
                formatting.setBackground(QColor('#f3f6fc')); formatting.setLeftMargin(12); formatting.setRightMargin(12)
                formatting.setTopMargin(8); formatting.setBottomMargin(8); formatting.setNonBreakableLines(False)
            cursor.setBlockFormat(formatting)
            fragment = block.begin()
            while not fragment.atEnd():
                part = fragment.fragment()
                if part.isValid() and part.charFormat().anchorHref():
                    link = QTextCursor(document); link.setPosition(part.position())
                    link.setPosition(part.position() + part.length(), QTextCursor.MoveMode.KeepAnchor)
                    color = QTextCharFormat(); color.setForeground(QColor('#3268d9')); link.mergeCharFormat(color)
                fragment += 1
            block = block.next()
        self.documentation_topics.blockSignals(False)
        self.documentation_search.clear()
        cursor = QTextCursor(document); cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.documentation_view.setTextCursor(cursor); self.documentation_view.verticalScrollBar().setValue(0)

    def jump_documentation(self):
        position = self.documentation_topics.currentData()
        if position is None: return
        cursor = QTextCursor(self.documentation_view.document()); cursor.setPosition(position)
        self.documentation_view.setTextCursor(cursor); self.documentation_view.ensureCursorVisible()
        scrollbar = self.documentation_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + self.documentation_view.cursorRect().top() - 12)

    def find_documentation(self, *args, restart=False):
        text = self.documentation_search.text()
        self.documentation_next.setEnabled(bool(text))
        if restart or not text:
            cursor = self.documentation_view.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.documentation_view.setTextCursor(cursor)
        found = bool(text) and self.documentation_view.find(text)
        if text and not found:
            cursor = self.documentation_view.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.documentation_view.setTextCursor(cursor); found = self.documentation_view.find(text)
        self.documentation_search.setToolTip('Press Enter for the next match.' if found or not text else 'No matching text in this document.')
        self.documentation_search.setStyleSheet('' if found or not text else 'border-color: #c68181;')

    def open_documentation_link(self, url):
        if url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url); return
        base = QUrl.fromLocalFile(str(self.documentation_path))
        resolved = base.resolved(url)
        if not resolved.isLocalFile(): return
        path = Path(resolved.toLocalFile()).resolve()
        if not path.is_relative_to(ROOT.resolve()) or path.suffix.lower() != '.md': return
        if path != self.documentation_path: self.load_documentation(path)
        if resolved.fragment(): self.documentation_view.scrollToAnchor(resolved.fragment())

    def check_updates(self):
        if self.update_checking:
            return
        self.update_checking = True; self.update_check_button.setEnabled(False)
        self.update_status.setText('Checking for app updates…')
        def task():
            try:
                result = updates.check_for_updates(ROOT)
                self.events.put(('app-update', (result, '')))
            except updates.UpdateError as e:
                self.events.put(('app-update', (None, str(e))))
        threading.Thread(target=task, daemon=True).start()

    def open_update(self):
        if self.latest_update:
            QDesktopServices.openUrl(QUrl(self.latest_update['url']))

    def console(self):
        area = QWidget(); area.setObjectName('innerPanel')
        vertical = QVBoxLayout(area); vertical.setContentsMargins(0, 0, 0, 0); vertical.setSpacing(9)
        status_row = QHBoxLayout()
        self.status = label('Ready when you are', 'cardTitle'); status_row.addWidget(self.status)
        status_row.addStretch()
        self.elapsed = label('No build running', 'muted'); status_row.addWidget(self.elapsed)
        vertical.addLayout(status_row)
        self.progress = QProgressBar(); self.progress.setRange(0, 5); self.progress.setValue(0); self.progress.setTextVisible(False)
        vertical.addWidget(self.progress)
        stages = QHBoxLayout()
        self.steps = []
        for i, name in enumerate(('01  Prepare OTA', '02  Stage files', '03  Build images', '04  Package ZIP', '05  Complete')):
            item = label(name, 'step'); self.steps.append(item); stages.addWidget(item)
            if i < 4: stages.addStretch()
        vertical.addLayout(stages)
        console = QFrame(); console.setObjectName('consoleCard')
        cl = QVBoxLayout(console); cl.setContentsMargins(5, 8, 5, 6); cl.setSpacing(0)
        row = QHBoxLayout(); row.setContentsMargins(12, 0, 10, 0)
        terminal = QLabel(); terminal.setPixmap(icon('terminal', '#7289aa', 16).pixmap(16, 16)); row.addWidget(terminal)
        row.addWidget(label('Build output', 'logTitle')); row.addStretch()
        row.addWidget(button('Save log', name='quiet', callback=self.save_log)); cl.addLayout(row)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(12000)
        self.log.setPlaceholderText('Waiting for a build. Configure your package above, then select Build package.')
        cl.addWidget(self.log, 1)
        content = QHBoxLayout(); content.setSpacing(20)
        content.addWidget(console, 1)
        summary_column = QVBoxLayout()
        summary_column.addWidget(self.package_summary_card); summary_column.addStretch()
        content.addLayout(summary_column)
        vertical.addLayout(content, 1)
        actions = QHBoxLayout()
        actions.addWidget(button('Back to setup', 'arrow', callback=lambda: self.navigate(0)))
        actions.addWidget(self.cancel_button)
        actions.addStretch(); actions.addWidget(self.output_button)
        vertical.addLayout(actions)
        return area

    def restore(self):
        for key in ('output', 'distro', 'bloat_dir'):
            value = self.saved.get(key)
            if isinstance(value, str): self.fields[key].setText(value)
        device = self.saved.get('device', 'Oneplus 13')
        self.fields['device'].setCurrentText(device if device in self.catalog.devices else 'Oneplus 13')
        self.device_changed()
        self.fields['kind'].setCurrentText(self.saved.get('kind', 'SuperHybrid Flasher'))
        if self.saved.get('region') in self.available_regions(): self.set_region(self.saved['region'])
        self.zip_build.blockSignals(True)
        self.zip_build.setChecked(self.saved.get('zip_output', True) is not False)
        self.zip_build.blockSignals(False)
        self.update_summary()

    def values(self):
        values = {k: widget.currentText() if isinstance(widget, QComboBox) else widget.text().strip() for k, widget in self.fields.items()} | {'region': self.region}
        values['zip_output'] = self.zip_build.isChecked()
        choice = self.abl_group.checkedId()
        values['vulnerable_abl'] = (None if choice == -1 else choice == 1) if self.fields['device'].currentText() == 'OPPO Find X9 Ultra' else False
        if self.ota_mode == 'catalog':
            for role in ('ota', 'base'):
                entry = self.ota_combos[role].currentData()
                local = self.ota_local.get(entry['id']) if entry else None
                values[role] = local if local and Path(local).is_file() else ''
                values[role + '_catalog_id'] = entry['id'] if entry and not values[role] else ''
        return values

    def available_regions(self):
        return self.catalog.regions(self.fields['device'].currentText(), self.fields['kind'].currentText())

    def device_changed(self):
        self.abl_group.setExclusive(False)
        self.abl_yes.setChecked(False); self.abl_no.setChecked(False)
        self.abl_group.setExclusive(True)
        previous = self.fields['kind'].currentText()
        combo = self.fields['kind']; combo.blockSignals(True)
        combo.clear(); combo.addItems(self.catalog.types(self.fields['device'].currentText()))
        if combo.findText(previous) >= 0: combo.setCurrentText(previous)
        combo.blockSignals(False); self.kind_changed()

    def kind_changed(self):
        regions = self.available_regions()
        self.region = self.region if hasattr(self, 'region') and self.region in regions else regions[0]
        use_combo = len(regions) > 5 or any(region not in self.region_buttons for region in regions)
        self.region_buttons_panel.setVisible(not use_combo); self.region_combo.setVisible(use_combo)
        self.region_combo.blockSignals(True); self.region_combo.clear()
        for region in regions: self.region_combo.addItem('Global' if region == 'GLO' else region, region)
        self.region_combo.setCurrentIndex(self.region_combo.findData(self.region)); self.region_combo.blockSignals(False)
        for region, b in self.region_buttons.items():
            b.setVisible(region in regions)
            b.setEnabled(region in regions and not self.busy)
            b.setToolTip('Available' if region in regions else 'This combination is not supported by the workflow')
        self.set_region(self.region)

    def set_region(self, region):
        self.region = region
        if region in self.region_buttons: self.region_buttons[region].setChecked(True)
        self.region_combo.blockSignals(True)
        self.region_combo.setCurrentIndex(self.region_combo.findData(region))
        self.region_combo.blockSignals(False)
        self.update_summary()

    def update_summary(self):
        if not self.fields['kind'].currentText(): return
        values = self.values()
        x9u = values['device'] == 'OPPO Find X9 Ultra'
        self.abl_card.setVisible(x9u)
        for panel in (self.configure_card, self.bloat_card, self.ota_card):
            panel.layout().setContentsMargins(18, 8 if x9u else 12, 18, 8 if x9u else 12)
            panel.layout().setSpacing(6 if x9u else 8)
        self.workflow_hint.setText('CONFIGURE → BLOAT FILES → OTA → ABL → BUILD' if x9u else 'CONFIGURE → BLOAT FILES → OTA → BUILD')
        if x9u:
            choice = values['vulnerable_abl']
            self.abl_hint.setText('Choose Yes or No to continue.' if choice is None else
                                 'ABL 16.0.6 will be packed.' if choice else 'The OTA’s ABL will be kept.')
        needs_base = values['device'] == 'Oneplus Ace 6' and values['kind'] == 'SuperHybrid Flasher' and self.region == 'IN'
        self.base_panel.setVisible(needs_base and self.ota_mode != 'catalog')
        self.refresh_ota_context()
        try:
            self.catalog.selection(values['device'], values['kind'], self.region)
            text = 'Target: OnePlus 15R India OxygenOS OTA · Base: Ace 6 ColorOS OTA' if needs_base else 'Only combinations supported by the build workflow are available.'
            if self.catalog.devices[values['device']] == 'X9U':
                text = 'ColorOS · EU bloat is used for CN.' if self.region == 'CN' else f'ColorOS · {self.region} bloat files.'
            if ota_catalog.needs_matched_odm(values['device'], values['kind'], self.region):
                text = 'Target: OnePlus 15R · ODM: Ace 6T CN · Exact patch match required.'
                self.source_hint.setText('Only odm.img is streamed; the donor must have the same security patch.')
            else:
                self.source_hint.setText('Choose a version to download or build. Temporary links are refreshed automatically.' if self.ota_mode == 'catalog' else
                                         'Paste the direct HTTP(S) link to a full OTA ZIP.' if self.ota_mode == 'url' else
                                         'Use a full OTA package. Incremental updates are not supported.')
            self.selection_valid = True
        except BuildError as e:
            text = str(e); self.selection_valid = False
        self.selection_info.setText(text)
        self.refresh_bloat_status()
        self.update_build_gate()
        self.os_badge.setText('COLOR OS' if coloros(self.catalog.devices[values['device']], self.region) else 'OXYGEN OS')
        if not self.output_available:
            self.update_package_summary(values)

    def update_package_summary(self, values):
        self.summary_device.setText(values['device'].replace('Oneplus', 'OnePlus'))
        self.summary_kind.setText(values['kind'])
        for key, value in dict(os='ColorOS' if coloros(self.catalog.devices[values['device']], values['region']) else 'OxygenOS', region='Global' if values['region'] == 'GLO' else values['region'],
                               format='ZIP + SHA-256' if values.get('zip_output', True) else 'Flasher folder', engine='Native Windows' if sys.platform == 'win32' else 'Native Linux').items():
            self.summary_values[key].setText(value)
        try:
            p = Path(values['output']).expanduser().resolve()
            while not p.exists():
                if p.parent == p: raise OSError('Output drive unavailable.')
                p = p.parent
            free = shutil.disk_usage(p).free / 1024**3
            self.space.setText(f'{free:.1f} GB available on output drive\n' + ('Choose a larger drive for full builds.' if free < 40 else 'Ready for your next build.'))
        except OSError:
            self.space.setText('Choose an accessible output folder.')

    def local_bloat_ready(self):
        values = self.values()
        key = tuple(values[k] for k in ('device', 'kind', 'region', 'bloat_dir'))
        data = self.bloat_data
        return bool(self.selection_valid and key == self.bloat_probe_key and data is not None
                    and 'error' not in data and (data.get('ready') or data.get('total') == 0))

    def update_build_gate(self):
        enabled = not self.busy and not self.bloat_deleting
        self.ota_card.setEnabled(enabled and self.selection_valid)
        self.abl_card.setEnabled(enabled and self.selection_valid)
        error = self.build_requirements_error()
        self.start_button.setEnabled(enabled and not error)
        self.start_button.setToolTip(error or 'Build the package')
        self.update_ota_controls()

    def build_requirements_error(self):
        if not self.selection_valid:
            return 'Choose a supported device, flasher type and region.'
        if self.values()['vulnerable_abl'] is None:
            return 'Step 4: choose Yes or No for vulnerable ABL before building.'
        if not self.local_bloat_ready():
            return 'Required bloat files must be downloaded and verified before building.'
        values = self.values()
        for role in ota_catalog.targets(values['device'], values['kind'], self.region):
            title = 'TARGET' if role == 'ota' else 'BASE'
            if values.get(role + '_catalog_id'):
                continue
            source = clean_source(values[role])
            if not source:
                return f'Select a full {title} OTA ZIP, download URL or catalog version.'
            try:
                url = urlsplit(source)
                if url.scheme.lower() in ('http', 'https'):
                    if not url.hostname: return f'Enter a valid HTTP(S) {title} OTA URL.'
                elif not Path(source).expanduser().is_file():
                    return f'Choose an existing full {title} OTA ZIP.'
            except (OSError, ValueError):
                return f'Choose a valid {title} OTA file or HTTP(S) URL.'
        error = self.odm_catalog_error()
        if error: return error
        if not values['output']:
            return 'Choose an output folder.'
        try:
            output = Path(values['output']).expanduser().resolve()
            while not output.exists():
                if output.parent == output: raise OSError('Output drive unavailable.')
                output = output.parent
            if not output.is_dir() or not os.access(output, os.W_OK):
                return 'Choose a writable output folder.'
        except (OSError, ValueError, RuntimeError):
            return 'Choose a valid output folder.'
        return ''

    def odm_catalog_error(self):
        values = self.values()
        if self.ota_mode != 'catalog' or not ota_catalog.needs_matched_odm(values['device'], values['kind'], self.region):
            return ''
        entry = self.ota_combos['ota'].currentData()
        if not entry or self.ota_data is None:
            return 'Select a target OTA with a matching Ace 6T security patch.'
        try:
            ota_catalog.matching_odm(self.ota_data, entry)
        except ota_catalog.CatalogError as e:
            return str(e)
        return ''

    def refresh_bloat_status(self, *args, force=False):
        if self.busy or self.bloat_deleting or 'bloat_dir' not in self.fields or not hasattr(self, 'region'):
            return
        values = self.values()
        key = tuple(values[k] for k in ('device', 'kind', 'region', 'bloat_dir'))
        if not self.selection_valid:
            self.bloat_scan_token += 1
            self.bloat_check_timer.stop()
            self.bloat_status.setText('Selection unavailable')
            self.bloat_download.setEnabled(False)
            self.bloat_recheck.set_scanning(False); self.bloat_recheck.setEnabled(False)
            self.bloat_delete.setEnabled(False)
            self.update_build_gate()
            return
        if key == self.bloat_probe_key and self.bloat_active_token == self.bloat_scan_token and not force:
            return
        if force:
            self.bloat_verified.clear()
        changed = key != self.bloat_probe_key or self.bloat_data is None or force
        self.bloat_probe_key = key
        self.bloat_scan_token += 1
        if changed:
            self.bloat_data = None
            self.bloat_status.setText('Checking local files…')
            self.bloat_status.setStyleSheet('color: #7d8b9e;')
            self.bloat_download.setText('Download bloat')
            self.bloat_download.setEnabled(False)
            self.bloat_recheck.set_scanning(True); self.bloat_delete.setEnabled(False)
        self.fields['bloat_dir'].setToolTip(values['bloat_dir'])
        self.bloat_check_timer.start(150)
        self.update_build_gate()

    def scan_bloat_status(self):
        if self.busy or self.bloat_deleting:
            return
        token = self.bloat_scan_token
        self.bloat_active_token = token
        values = self.values()
        def inspect():
            try:
                manifest = load_manifest(ROOT / 'bloat/assets-manifest.json')
                device = self.catalog.devices[values['device']]
                data = inspect_downloads(manifest, Path(values['bloat_dir']).expanduser().resolve(),
                    device, values['kind'], values['region'], self.bloat_verified,
                    cancelled=lambda: token != self.bloat_scan_token)
                if data is not None:
                    self.events.put(('bloat-status', (token, data)))
            except Exception as e:
                self.events.put(('bloat-status', (token, {'error': str(e)})))
            finally:
                self.events.put(('bloat-scan-finished', token))
        thread = threading.Thread(target=inspect, daemon=True)
        self.bloat_scan_threads = [t for t in self.bloat_scan_threads if t.is_alive()] + [thread]
        thread.start()

    def apply_bloat_status(self, data):
        self.bloat_data = data
        self.update_bloat_delete()
        if 'error' in data:
            self.bloat_status.setText('Could not check local files')
            self.bloat_status.setToolTip(data['error'])
            self.bloat_status.setStyleSheet('color: #b35454;')
            self.bloat_info.setText(data['error'])
            self.bloat_download.setEnabled(False)
            self.update_build_gate()
            return
        size = data['bytes']
        size_text = f'{size / 1024**3:.2f} GB' if size >= 1024**3 else f'{size / 1024**2:.1f} MB'
        self.bloat_info.setText(f'{data["total"]} verified images · {size_text}' if data['ready'] else
                                f'{data["total"]} images · {size_text} total')
        self.bloat_status.setToolTip('File sizes and SHA-256 checksums checked in the chosen folder.')
        if not data['total']:
            self.bloat_status.setText('No extra bloat needed')
            self.bloat_download.setText('No download needed')
        elif data['ready']:
            self.bloat_status.setText('Already downloaded')
            self.bloat_download.setText('Already downloaded')
        elif data['downloaded']:
            self.bloat_status.setText(f'{data["downloaded"]} of {data["total"]} files downloaded')
            self.bloat_download.setText('Download missing bloat')
        else:
            self.bloat_status.setText('Not downloaded')
            self.bloat_download.setText('Download bloat')
        self.bloat_status.setStyleSheet('color: #347856;' if data['ready'] else 'color: #7d8b9e;')
        self.bloat_download.setIcon(icon('check' if data['ready'] else 'download'))
        self.bloat_download.setEnabled(bool(data['total']) and not data['ready'] and not self.busy)
        self.update_build_gate()

    def update_bloat_delete(self):
        enabled = False
        tooltip = 'Delete the selected bloat images and unused cached downloads.'
        if not self.busy and not self.bloat_deleting and self.selection_valid:
            try:
                values = self.values()
                plan = plan_deletion(load_manifest(ROOT / 'bloat/assets-manifest.json'), values['bloat_dir'],
                    self.catalog.devices[values['device']], values['kind'], values['region'])
                enabled = bool(plan.files or plan.cache)
            except (OSError, ValueError, RuntimeError, AssetError, BloatStorageError) as e:
                tooltip = 'Could not prepare deletion: ' + str(e)
        self.bloat_delete.setEnabled(enabled)
        self.bloat_delete.setToolTip(tooltip)

    def delete_selected_bloat(self):
        if self.busy or self.bloat_deleting:
            return
        values = self.values()
        try:
            plan = plan_deletion(load_manifest(ROOT / 'bloat/assets-manifest.json'), values['bloat_dir'],
                self.catalog.devices[values['device']], values['kind'], values['region'])
        except (OSError, ValueError, RuntimeError, AssetError, BloatStorageError) as e:
            QMessageBox.warning(self, 'Could not delete bloat', str(e)); return
        if not plan.files and not plan.cache:
            self.refresh_bloat_status(force=True); return
        names = '\n'.join(p.relative_to(plan.root).as_posix() for p in plan.files)
        message = (f'Delete local bloat for {values["device"]} / {values["region"]}?\n\n'
            f'{len(plan.files)} image file(s) and {len(plan.cache)} unused download cache file(s) '
            f'({plan.bytes / 1024**3:.2f} GB) will be removed from:\n{plan.root}\n\n'
            + (f'Selected image files:\n{names}\n\n' if names else '') +
            'You will need to download the bloat again before building.')
        answer = QMessageBox.question(self, 'Delete selected bloat?', message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.bloat_deleting = True; self.bloat_scan_token += 1
        self.bloat_check_timer.stop(); self.bloat_data = None
        self.bloat_recheck.set_scanning(False); self.bloat_recheck.setEnabled(False)
        self.bloat_delete.setEnabled(False); self.bloat_download.setEnabled(False)
        self.bloat_status.setText('Deleting local files…')
        for control in self.inputs: control.setEnabled(False)
        self.check_button.setEnabled(False); self.test_button.setEnabled(False)
        self.update_build_gate()
        scans = list(self.bloat_scan_threads)
        def task():
            error = ''
            try:
                for scan in scans: scan.join()
                delete_bloat(plan)
            except (OSError, BloatStorageError) as e:
                error = str(e)
            self.events.put(('bloat-deleted', error))
        threading.Thread(target=task, daemon=True).start()

    def choose_bloat_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose bloat download folder', self.fields['bloat_dir'].text())
        if folder:
            self.fields['bloat_dir'].setText(folder)
            self.save_preferences(self.values())

    def open_bloat_folder(self):
        folder = Path(self.fields['bloat_dir'].text()).expanduser().resolve()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def save_preferences(self, values):
        try:
            self.settings.write_text(json.dumps({k: values[k] for k in
                ('device', 'kind', 'region', 'output', 'distro', 'bloat_dir', 'zip_output', 'vulnerable_abl')}, indent=2) + '\n', encoding='utf-8')
        except OSError as e:
            self.log.appendPlainText('Could not save preferences: ' + str(e))

    def zip_output_changed(self):
        self.update_summary()
        if hasattr(self, 'region'):
            self.save_preferences(self.values())

    def source_mode(self, url):
        self.ota_mode = 'catalog' if url == 'catalog' else 'url' if url else 'file'
        catalog = self.ota_mode == 'catalog'
        self.catalog_panel.setVisible(catalog)
        self.manual_panel.setVisible(not catalog)
        self.fields['ota'].setVisible(not catalog)
        self.fields['ota'].setPlaceholderText('https://… / full-OTA.zip' if url else 'Choose a full OTA .zip file')
        self.browse_ota.setVisible(self.ota_mode == 'file')
        self.source_hint.setText('Choose a version to download or build. Temporary links are refreshed automatically.' if catalog else
                                 'Paste the direct HTTP(S) link to a full OTA ZIP.' if url else 'Use a full OTA package. Incremental updates are not supported.')
        self.ota_link_token += 1; self.ota_link_pending = False
        self.update_summary()
        if catalog and self.ota_data is None: self.fetch_ota_versions()

    def refresh_ota_context(self):
        context = (self.fields['device'].currentText(), self.fields['kind'].currentText(), self.region)
        if context != self.ota_context:
            if self.ota_context and ota_catalog.uses_na_api(context[0], context[2]) != ota_catalog.uses_na_api(self.ota_context[0], self.ota_context[2]):
                self.ota_data = None
            self.ota_context = context
            self.ota_fetch_token += 1; self.ota_fetching = False
            self.ota_link_token += 1; self.ota_link_pending = False
            self.populate_ota_versions()
            if self.ota_mode == 'catalog' and self.ota_data is None:
                self.fetch_ota_versions()
        roles = ota_catalog.targets(*context)
        self.ota_role_panels['base'].setVisible('base' in roles)
        self.catalog_roles.removeWidget(self.ota_role_panels['ota'])
        self.catalog_roles.addWidget(self.ota_role_panels['ota'], 0, 0, 1, 1 if 'base' in roles else 2)

    def populate_ota_versions(self):
        counts = []
        for role, combo in self.ota_combos.items():
            previous = combo.currentData()
            combo.blockSignals(True); combo.clear()
            target = ota_catalog.targets(*self.ota_context).get(role)
            if self.ota_data is not None and target:
                entries = ota_catalog.releases_for(self.ota_data, *target)
                for entry in entries:
                    combo.addItem(entry['version'] + (' · Latest' if entry.get('is_latest') else ''), entry)
                if previous:
                    for i in range(combo.count()):
                        if combo.itemData(i)['id'] == previous['id']: combo.setCurrentIndex(i); break
                counts.append(f'{target[0]} / {target[1]}: {len(entries)} versions')
            combo.blockSignals(False)
            self.ota_version_changed(role)
        stale = 'Offline · cached versions · ' if self.ota_data and self.ota_data.get('_stale') else ''
        self.ota_catalog_status.setText(stale + ' · '.join(counts) if counts else 'Fetch available full OTA versions for your device and region.')
        self.update_ota_controls()

    def fetch_ota_versions(self):
        if self.busy or self.ota_fetching: return
        self.ota_fetch_token += 1; token = self.ota_fetch_token
        self.ota_fetching = True
        self.ota_catalog_status.setText('Fetching available versions…')
        self.update_ota_controls()
        na = ota_catalog.uses_na_api(self.ota_context[0], self.ota_context[2])
        def task():
            try:
                self.events.put(('ota-catalog', (token, ota_catalog.load_catalog(ROOT / '.ota-catalog', na=na), None)))
            except Exception as e:
                self.events.put(('ota-catalog', (token, None, str(e))))
        threading.Thread(target=task, daemon=True).start()

    def ota_version_changed(self, role):
        self.ota_link_token += 1; self.ota_link_pending = False
        entry = self.ota_combos[role].currentData()
        if not entry:
            self.ota_details[role].setToolTip('')
            self.ota_details[role].setText('No version selected. Fetch versions or use a local file / direct URL.')
        else:
            details = [f'{entry["device"]} / {entry["region"]}', entry.get('model', '')]
            if isinstance(entry.get('size'), int): details.append(f'{entry["size"] / 1024**3:.2f} GiB')
            if entry.get('security_patch'): details.append('Patch ' + entry['security_patch'])
            if entry.get('package_type') == 'reconstructed_full': details.append('Reconstructed full package')
            local = self.ota_local.get(entry['id'])
            if local and Path(local).is_file(): details.append('Saved locally')
            self.ota_details[role].setToolTip(local or '')
            self.ota_details[role].setText(' · '.join(d for d in details if d))
            if role == 'ota' and ota_catalog.needs_matched_odm(*self.ota_context):
                try:
                    donor = ota_catalog.matching_odm(self.ota_data or {'releases': []}, entry)
                    self.ota_details[role].setText(self.ota_details[role].text() + ' · ODM only: ' + donor['version'])
                except ota_catalog.CatalogError as e:
                    self.ota_details[role].setText(self.ota_details[role].text() + ' · ODM unavailable: no exact patch match.')
                    self.ota_details[role].setToolTip(str(e))
        self.update_build_gate()

    def update_ota_controls(self):
        if not hasattr(self, 'ota_fetch_button'): return
        self.ota_fetch_button.setEnabled(not self.busy and not self.bloat_deleting and not self.ota_fetching)
        for role, combo in self.ota_combos.items():
            entry = combo.currentData()
            enabled = bool(entry) and not self.busy and not self.bloat_deleting and not self.ota_link_pending
            local = self.ota_local.get(entry['id']) if entry else None
            downloaded = bool(local and Path(local).is_file())
            self.ota_download_buttons[role].setText('Already downloaded' if downloaded else 'Download OTA')
            self.ota_download_buttons[role].setToolTip(local if downloaded else 'Save the selected full OTA ZIP')
            self.ota_download_buttons[role].setEnabled(enabled and not downloaded)
            self.ota_copy_buttons[role].setEnabled(enabled)

    def copy_ota_link(self, role):
        entry = self.ota_combos[role].currentData()
        if not entry or self.busy or self.bloat_deleting or self.ota_link_pending: return
        self.ota_link_token += 1; token = self.ota_link_token
        self.ota_link_pending = True; self.update_ota_controls()
        self.ota_details[role].setText('Resolving a fresh download link…')
        def task():
            try:
                url = ota_catalog.resolve_link(entry)
                self.events.put(('ota-link', (token, role, url, None)))
            except Exception as e:
                self.events.put(('ota-link', (token, role, None, str(e))))
        threading.Thread(target=task, daemon=True).start()

    def choose_ota_download(self, role):
        entry = self.ota_combos[role].currentData()
        if not entry or self.busy: return
        filename = re.sub(r'[^\w.()+-]', '_', entry['version']) + '.zip'
        path, _ = QFileDialog.getSaveFileName(self, 'Save full OTA ZIP', str(Path(self.fields['output'].text()) / filename), 'OTA ZIP (*.zip)')
        if not path: return
        if Path(path).exists():
            QMessageBox.warning(self, 'File already exists', 'Choose another filename or select the existing ZIP in the Local file tab.'); return
        self.ota_download_request = (role, entry['id'], path)
        self.start('ota')

    def navigate(self, index):
        if index == 3 and not self.output_available: return
        self.pages.setCurrentIndex(index); self.nav[index].setChecked(True)
        titles = [('New package', 'Turn a full OTA into a ready-to-use flasher package.'),
                  ('Build history', 'Your packages, checksums and build records in one place.'),
                  ('Environment', 'Check your local engine and verify the build pipeline.'),
                  ('Build output', 'Progress, results and logs for your latest task.'),
                  ('Documentation', 'Guides and reference for building your flasher package.')]
        self.title_label.setText(titles[index][0]); self.subtitle.setText(titles[index][1])
        if index == 1: self.load_history()

    def choose_ota(self, key):
        path, _ = QFileDialog.getOpenFileName(self, 'Select full OTA', '', 'OTA ZIP (*.zip);;All files (*)')
        if path:
            self.fields[key].setText(path)
            if key == 'ota': self.file_tab.setChecked(True); self.source_mode(False)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Build output folder', self.fields['output'].text())
        if path: self.fields['output'].setText(path); self.update_summary()

    def start(self, action):
        if self.bloat_deleting:
            return
        if self.busy: return
        values = self.values()
        if action == 'build' and not self.local_bloat_ready():
            QMessageBox.warning(self, 'Bloat files required', 'Complete step 2 first: download the required bloat files or choose a folder that already contains them.')
            return
        if action == 'build' and self.build_requirements_error():
            QMessageBox.warning(self, 'Build not ready', self.build_requirements_error()); return
        if action == 'ota':
            role, release_id, path = self.ota_download_request
            values.update(download_role=role, download_path=path)
            values[role] = ''; values[role + '_catalog_id'] = release_id
        try:
            output = Path(values['bloat_dir'] if action == 'bloat' else values['output']).expanduser().resolve()
            output.mkdir(parents=True, exist_ok=True)
            values['bloat_dir'] = str(Path(values['bloat_dir']).expanduser().resolve())
            if action != 'bloat':
                values['output'] = str(output)
                self.current_output = self.last_folder = output
            else:
                self.last_folder = output
            self.cancel_file = output / ('.cancel-' + uuid.uuid4().hex)
            self.save_preferences(values)
        except OSError as e:
            QMessageBox.warning(self, 'Output folder unavailable', str(e)); return
        self.busy = True; self.action = action; self.started = time.monotonic()
        self.output_available = True
        self.nav[3].show(); self.view_output_button.show()
        self.update_package_summary(values)
        self.package_summary_card.setVisible(action in ('build', 'bloat', 'ota'))
        self.navigate(3)
        self.set_busy(True); self.log.clear()
        self.status.setText({'check': 'Checking your environment', 'test': 'Running pipeline self-test',
                             'build': 'Preparing your build', 'bloat': 'Downloading selected bloat', 'ota': 'Downloading selected OTA'}[action])
        labels = ('01  Check files', '02  Download', '03  Verify images', '04  Save files', '05  Ready') if action == 'bloat' else \
                 ('01  Resolve OTA', '02  Download', '03  Verify ZIP', '04  Verify checksum', '05  Ready') if action == 'ota' else \
                 ('01  Prepare OTA', '02  Stage files', '03  Build images', '04  Package ZIP' if values['zip_output'] else '04  Finalize folder', '05  Complete')
        for widget, text in zip(self.steps, labels): widget.setText(text)
        self.progress.setRange(0, 0)
        self.highlight_step(-1)
        def task():
            try:
                command = Runtime(values['distro']).command(values, action, self.cancel_file)
                if '--output' in command: self.events.put(('output-root', command[command.index('--output') + 1]))
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      encoding='utf-8', errors='replace', bufsize=1, **options) as process:
                    for line in process.stdout: self.events.put(('line', line))
                    code = process.wait()
                self.events.put(('done', code))
            except Exception as e:
                self.events.put(('line', f'ERROR: {e}\n')); self.events.put(('done', 1))
        threading.Thread(target=task, daemon=True).start()

    def set_busy(self, busy):
        for widget in self.inputs: widget.setEnabled(not busy)
        if busy:
            self.ota_link_token += 1; self.ota_link_pending = False
        self.update_build_gate()
        self.check_button.setEnabled(not busy); self.test_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy and self.action != 'check')
        self.cancel_button.setText('Cancel download' if self.action in ('bloat', 'ota') else 'Cancel build')
        self.bloat_download.setEnabled(False)
        if busy:
            self.bloat_scan_token += 1
            self.bloat_check_timer.stop()
            self.bloat_recheck.set_scanning(False); self.bloat_recheck.setEnabled(False)
            self.bloat_delete.setEnabled(False)
        if busy and self.action == 'bloat':
            self.bloat_status.setText('Downloading and verifying…')
            self.bloat_download.setText('Downloading…')
        if not busy:
            if self.action == 'bloat': self.bloat_data = None
            self.kind_changed()

    def highlight_step(self, index):
        for i, item in enumerate(self.steps):
            item.setProperty('active', i == index)
            item.style().unpolish(item); item.style().polish(item)

    def poll(self):
        if self.busy and self.started:
            seconds = int(time.monotonic() - self.started)
            self.elapsed.setText(f'{seconds // 60:02d}:{seconds % 60:02d} elapsed')
        try:
            for _ in range(300):
                kind, value = self.events.get_nowait()
                if kind == 'app-update':
                    result, error = value
                    self.update_checking = False; self.update_check_button.setEnabled(True)
                    if error:
                        self.update_status.setText(error)
                    elif result:
                        self.latest_update = result
                        self.update_status.setText(f'Flasher Builder {result["version"]} is available. You have v{__version__}. Open the release to download the new package.')
                        self.update_notice.setText('Update ' + result['version'])
                        self.update_notice.setToolTip('A newer Flasher Builder release is available. Open its download page.')
                        self.update_notice.show(); self.update_open_button.show()
                    else:
                        self.latest_update = None
                        self.update_status.setText(f'Flasher Builder v{__version__} is up to date.')
                        self.update_notice.hide(); self.update_open_button.hide()
                elif kind == 'ota-catalog':
                    token, data, error = value
                    if token == self.ota_fetch_token:
                        self.ota_fetching = False
                        if error:
                            self.ota_catalog_status.setText(error)
                            self.update_ota_controls()
                        else:
                            self.ota_data = data
                            self.populate_ota_versions()
                elif kind == 'ota-link':
                    token, role, url, error = value
                    if token == self.ota_link_token:
                        self.ota_link_pending = False
                        if url:
                            QApplication.clipboard().setText(url)
                            self.ota_details[role].setText('Fresh download link copied. Temporary links expire; copy again when needed.')
                        else:
                            self.ota_details[role].setText(error)
                        self.update_ota_controls()
                elif kind == 'bloat-deleted':
                    self.bloat_deleting = False; self.bloat_verified.clear()
                    self.set_busy(False)
                    self.refresh_bloat_status(force=True)
                    if value: QMessageBox.warning(self, 'Could not delete all bloat files', value)
                elif kind == 'bloat-scan-finished':
                    if self.bloat_active_token == value: self.bloat_active_token = None
                    if self.bloat_scan_token == value and not self.busy and not self.bloat_deleting:
                        self.bloat_recheck.set_scanning(False)
                        self.bloat_recheck.setEnabled(self.selection_valid)
                elif kind == 'bloat-status':
                    token, data = value
                    if token == self.bloat_scan_token and not self.busy:
                        self.apply_bloat_status(data)
                elif kind == 'output-root': self.backend_output = Path(value)
                elif kind == 'line':
                    if value.startswith('@@BLOAT_RESULT '):
                        self.log.appendPlainText('Bloat files saved and verified in the selected folder.\n')
                    elif value.startswith('@@OTA_RESULT '):
                        role, release_id, path = self.ota_download_request
                        self.ota_local[release_id] = path
                        self.last_folder = Path(path).parent
                        self.ota_version_changed(role)
                        self.log.appendPlainText('Full OTA saved and verified: ' + path + '\nThe build will reuse this local ZIP.\n')
                    elif value.startswith('@@RESULT '):
                        try:
                            result = json.loads(value[len('@@RESULT '):])
                            relative = Path(result.get('folder', result['job'])).relative_to(self.backend_output)
                            self.last_folder = self.current_output.joinpath(*relative.parts)
                        except (ValueError, KeyError): pass
                    else:
                        self.log.moveCursor(QTextCursor.MoveOperation.End); self.log.insertPlainText(value)
                    if value.startswith('STEP '):
                        self.status.setText(value.strip().replace('STEP ', '').replace(' • ', '   ·   '))
                        try: self.highlight_step(int(value[5]) - 1)
                        except ValueError: pass
                elif kind == 'done':
                    cancelled = value == 130 or (value != 0 and self.cancel_file and self.cancel_file.exists())
                    self.busy = False
                    self.progress.setRange(0, 5); self.progress.setValue(5 if value == 0 else 0)
                    cancelled_status = ('Download cancelled · completed files kept' if self.action in ('bloat', 'ota') else
                                        'Build cancelled · output cleaned' if self.action == 'build' and value == 130 else
                                        'Task cancelled' if value == 130 else 'Task stopped · see output for details')
                    self.status.setText(cancelled_status if cancelled else
                                        ('Bloat downloaded · all files verified' if self.action == 'bloat' else 'OTA downloaded · ready to build' if self.action == 'ota' else 'Environment ready' if self.action == 'check' else 'Self-test passed · test data only' if self.action == 'test' else 'Package complete · ready in your output folder') if value == 0 else 'Download failed · see output below' if self.action in ('bloat', 'ota') else 'Build failed · see output below')
                    self.highlight_step(4 if value == 0 else -1)
                    self.set_busy(False)
                    if self.cancel_file: self.cancel_file.unlink(missing_ok=True)
                    if self.pages.currentIndex() == 1: self.load_history()
                    if value == 0 and self.action in ('bloat', 'ota') and self.pages.currentIndex() == 3:
                        self.navigate(0)
                    if self.closing: self.close(); return
        except queue.Empty: pass

    def cancel(self):
        if self.busy and self.cancel_file:
            try:
                self.cancel_file.touch(); self.status.setText('Stopping download…' if self.action in ('bloat', 'ota') else 'Stopping build…'); self.cancel_button.setEnabled(False)
            except OSError as e: QMessageBox.warning(self, 'Could not cancel', str(e))

    def open_output(self):
        try:
            self.last_folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_folder)))
        except OSError as e: QMessageBox.warning(self, 'Could not open folder', str(e))

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Save build log', 'flasher-builder.log', 'Log files (*.log)')
        if path:
            try: Path(path).write_text(self.log.toPlainText(), encoding='utf-8')
            except OSError as e: QMessageBox.warning(self, 'Could not save log', str(e))

    def load_history(self):
        self.history.clear()
        output = Path(self.fields['output'].text()).expanduser()
        paths = list(output.glob('*/build.json')) + list(output.glob('self-test-*/results/*/build.json'))
        for path in sorted(paths, reverse=True)[:200]:
            try:
                info = json.loads(path.read_text(encoding='utf-8'))
                title = ('TEST · ' if info.get('test_fixture') else '') + info['device'] + ' / ' + info['flasher_type']
                folder = path.parent
                if info.get('output_format') == 'folder':
                    title += ' · Folder'
                    artifact = folder / info['artifact']
                    if artifact.parent == folder and artifact.is_dir(): folder = artifact
                item = QTreeWidgetItem([title, info['region'], info['version'], info['created'].replace('T', '  ')])
                item.setData(0, Qt.ItemDataRole.UserRole, str(folder)); self.history.addTopLevelItem(item)
            except (OSError, ValueError, KeyError): pass

    def open_history(self, item, column):
        QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(0, Qt.ItemDataRole.UserRole)))

    def closeEvent(self, event):
        if self.bloat_deleting:
            event.ignore(); return
        if self.busy:
            if self.closing:
                event.ignore(); return
            answer = QMessageBox.question(self, 'Task running', 'Cancel the running task and close when it has stopped?')
            if answer == QMessageBox.StandardButton.Yes: self.closing = True; self.cancel()
            event.ignore()
        else: event.accept()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName('Flasher Builder')
    application.setApplicationVersion(__version__)
    application.setStyle('Fusion')
    application.setStyleSheet(STYLE)
    font = QFont('Inter' if 'Inter' in QFontDatabase.families() else 'Segoe UI' if sys.platform == 'win32' else 'DejaVu Sans', 10)
    application.setFont(font)
    try:
        window = App(); window.show()
        if '--smoke-test' in sys.argv:
            output = Path(sys.argv[sys.argv.index('--test-output') + 1]) if '--test-output' in sys.argv else ROOT / 'builds' / 'qt-smoke-test'
            output.mkdir(parents=True, exist_ok=True)
            window.settings = output / 'settings.json'
            window.fields['output'].setText(str(output.resolve()))
            started = time.monotonic()
            def launch_test():
                window.navigate(2)
                window.start('test')
            def finish_test():
                if window.busy and time.monotonic() - started < 90:
                    return
                success = not window.busy and window.status.text() == 'Self-test passed · test data only'
                window.open_documentation()
                documentation_ok = (window.pages.currentIndex() == 4 and window.documentation_view.isReadOnly()
                                    and window.documentation_topics.findText('Building a package') >= 0)
                window.documentation_search.setText('bloat')
                documentation_ok = documentation_ok and window.documentation_view.textCursor().selectedText().lower() == 'bloat'
                success = success and documentation_ok
                window.fields['device'].setCurrentText('OPPO Find X9 Ultra')
                abl_choice_ok = (window.values()['vulnerable_abl'] is None and not window.abl_card.isHidden()
                                 and not window.start_button.isEnabled() and 'Step 4' in window.build_requirements_error())
                window.abl_yes.click()
                yes_command = Runtime().command(window.values(), 'build', str(output/'cancel'))
                abl_choice_ok = abl_choice_ok and window.values()['vulnerable_abl'] is True and '--vulnerable-abl' in yes_command
                window.abl_no.click()
                no_command = Runtime().command(window.values(), 'build', str(output/'cancel'))
                abl_choice_ok = abl_choice_ok and window.values()['vulnerable_abl'] is False and '--stock-abl' in no_command
                window.fields['device'].setCurrentText('Oneplus 13')
                abl_choice_ok = abl_choice_ok and window.abl_card.isHidden()
                window.fields['device'].setCurrentText('OPPO Find X9 Ultra')
                abl_choice_ok = abl_choice_ok and window.values()['vulnerable_abl'] is None
                success = success and abl_choice_ok
                (output / 'qt-smoke-test.log').write_text(window.log.toPlainText(), encoding='utf-8')
                (output / 'qt-smoke-test.json').write_text(json.dumps({'passed': success, 'status': window.status.text(), 'frozen': bool(getattr(sys, 'frozen', False)), 'app_version': __version__, 'documentation_in_app': documentation_ok, 'x9u_abl_choice_required': abl_choice_ok}, indent=2) + '\n', encoding='utf-8')
                window.navigate(0)
                window.grab().save(str(output / 'qt-preview.png'))
                application.exit(0 if success else 1)
            smoke_timer = QTimer(window)
            smoke_timer.timeout.connect(finish_test)
            QTimer.singleShot(100, launch_test)
            smoke_timer.start(300)
        else:
            QTimer.singleShot(1500, window.check_updates)
    except Exception as e:
        QMessageBox.critical(None, 'Flasher Builder could not start', str(e)); return 1
    return application.exec()
