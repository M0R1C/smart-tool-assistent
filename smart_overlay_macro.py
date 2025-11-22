from __future__ import annotations

import datetime
import difflib
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import Qt, QSettings, QSize, Slot, Signal, QPoint, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QMouseEvent, QCursor, QDoubleValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QComboBox, QSpinBox, QPushButton, QCheckBox, QKeySequenceEdit, QSystemTrayIcon,
    QMenu, QMessageBox, QFrame, QLineEdit, QFormLayout, QTextEdit, QSizePolicy
)
from replayer import BackgroundRecorder, GameplayPlayer

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except Exception:
    keyboard = None
    KEYBOARD_AVAILABLE = False

ORG, APP = "SmartMacro", "Overlay"
SET_KEY_HOTKEY, SET_KEY_AUTOSTART = "hotkey", "autostart"
SET_KEY_HOTKEY2 = "hotkey2"
SET_KEY_SENSITIVITY = "sensitivity"
DEFAULT_HOTKEY = "Ctrl+Alt+O"
DEFAULT_HOTKEY2 = "Ctrl+Alt+P"

BASE_BG = "#141a20"
PANEL_BG = "#19232e"
PANEL_BG_HL = "#1f2b36"
BORDER = "#223240"
TEXT = "#dde7ef"
ACCENT = "#18a999"

alpha_overlay = 190
alpha_panels = 240

HOME_BASE = os.path.join(os.path.expanduser("~"), "SmartOverlayMacro")
os.makedirs(HOME_BASE, exist_ok=True)

selected_macro = ""

@dataclass
class MacroTask:
    id: str
    title: str


class GlobalHotkeyManager:
    def __init__(self, on_trigger, on_trigger2=None):
        self.on_trigger = on_trigger
        self.on_trigger2 = on_trigger2
        self._hotkey = None
        self._hotkey2 = None
        self._registered = False
        self._lock = threading.RLock()

    def _norm(self, seq: str) -> str:
        return seq.replace(" ", "").lower()

    def register(self, sequence_str: str, sequence_str2: str = None):
        with self._lock:
            self.unregister()
            self._hotkey = sequence_str
            self._hotkey2 = sequence_str2

            if not KEYBOARD_AVAILABLE:
                return

            try:
                if hasattr(self.on_trigger, 'on_f9_pressed') and sequence_str == 'f9':
                    keyboard.add_hotkey('f9', self.on_trigger.on_f9_pressed)
                elif hasattr(self.on_trigger, 'on_f10_pressed') and sequence_str == 'f10':
                    keyboard.add_hotkey('f10', self.on_trigger.on_f10_pressed)
                else:
                    if sequence_str:
                        keyboard.add_hotkey(self._norm(sequence_str), self.on_trigger)

                if sequence_str2 and self.on_trigger2:
                    keyboard.add_hotkey(self._norm(sequence_str2), self.on_trigger2)

                self._registered = True
            except Exception as e:
                print("[Hotkey] Failed to register:", e)
                self._registered = False

    def unregister(self):
        with self._lock:
            if not KEYBOARD_AVAILABLE:
                return
            try:
                keyboard.clear_all_hotkeys()
            except Exception:
                pass
            self._registered = False

    def shutdown(self):
        self.unregister()


class DragBar(QFrame):
    def __init__(self, parent_overlay: QFrame):
        super().__init__(parent_overlay)
        self.parent_overlay = parent_overlay
        self._drag_offset: QPoint | None = None
        self.setFixedHeight(28)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setObjectName("DragBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        title = QLabel("Smart Overlay Macro")
        title.setObjectName("TitleLabel")
        lay.addWidget(title)
        lay.addStretch(1)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.parent_overlay.frameGeometry().topLeft()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.parent_overlay.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_offset = None
        super().mouseReleaseEvent(e)


class ProgressHUD(QFrame):
    def __init__(self):
        super().__init__(None)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setObjectName("ProgressHUD")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._drag_offset: QPoint | None = None
        self.setStyleSheet(f"""
            QFrame#ProgressHUD {{
                background-color: rgba(25,35,46,240);
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
            QLabel#HudLabel {{
                color: {TEXT};
                padding: 8px 14px;
                font-weight: 700;
                font-size: 18px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel("0/0")
        self.label.setObjectName("HudLabel")
        lay.addWidget(self.label)

    def set_text(self, t: str):
        self.label.setText(t)

    def place_top_center(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        self.adjustSize()
        w = self.width()
        x = geo.x() + (geo.width() - w) // 2
        y = geo.y() + 20
        self.setGeometry(x, y, self.width(), self.height())


class OverlayPanel(QFrame):
    start_task = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.setObjectName("OverlayPanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.setLayout(layout)

        self.key_edit = QKeySequenceEdit(self)
        self.key_edit2 = QKeySequenceEdit(self)
        self.save_settings_btn = QPushButton("Сохранить настройки", self)

        self.task_combo = CustomComboBox()
        self.task_combo.selectionChanged.connect(self.on_task_selected)
        self.task_combo.setFixedWidth(400)
        self.macro_combo = MacroComboBox(self)
        self.macro_combo.setFixedWidth(400)
        self.console_output = QTextEdit(self)
        self.console_output.setFixedWidth(450)
        self.console_output.setStyleSheet("""
            QTextEdit {
                background: #151b21; 
                border: 1px solid #223240; 
                border-radius: 8px; 
                color: #dde7ef; 
                padding: 6px 8px;
            }
        """)
        self.repeat_spin = self.create_spinbox()
        self.wave_spin = self.create_spinbox()
        self.cooldown_input = QLineEdit(self)
        self.cooldown_input.setFixedWidth(100)
        self.sensitivity_input = QLineEdit(self)
        self.sensitivity_input.setFixedWidth(100)
        self.sensitivity_settings = QLineEdit(self)
        self.sensitivity_settings.setFixedWidth(100)

        global selected_macro

        self.add_tab("Задачи", [
            {"type": "combo", "label": "Задача:", "widget": self.task_combo, "inline": True, "align": "right", "row_margin": "0px, 0px, 0px, 8px"},
            {"type": "spinbox", "label": "Повторений:", "widget": self.repeat_spin, "inline": True, "align": "right", "row_margin": "0px, 9px, 0px, 8px"},
            {"type": "spinbox", "label": "Волны:", "widget": self.wave_spin, "inline": True, "align": "right", "row_margin": "0px, 9px, 0px, 8px"},
            {"type": "button", "widget": QPushButton("Запустить"), "action": self.on_run_clicked, "align": "right"}
        ])
        self.add_tab("TAS", [
            {"type": "combo", "label": "Реплеи:", "widget": self.macro_combo, "inline": True, "align": "right", "row_margin": "0px, 0px, 0px, 8px"},
            [
                {"type": "lineedit", "label": "Отсрочка старта:", "widget": self.cooldown_input, "inline": True},
                {"type": "lineedit", "label": "Чувствительность:", "widget": self.sensitivity_input, "inline": True},
                {"row_margin": "0px, 8px, 0px, 8px"}
            ],
            {"type": "text", "widget": self.console_output, "read_only": True, "align": "center", "row_margin": "6px, 17px, 4px, 0px"},
            [
                {"type": "button", "widget": QPushButton("Проиграть"), "action": lambda: self.on_play_clicked(cooldown=self.cooldown_input.text(),
                                                                                                              sensativity=self.sensitivity_input.text(),
                                                                                                              filename=selected_macro
                                                                                                             )},
                {"type": "button", "widget": QPushButton("Запись"), "action": lambda: self.on_record_clicked()},
                {"row_margin": "2px, 87px, 6px, 0px"}
            ]
        ])
        self.add_tab("Настройки", [
            {"type": "keysequence", "label": "Cочетание клавиш для вызова оверлея:", "widget": self.key_edit, "inline": True, "align": "right", "row_margin": "9px, 9px, 0px, 8px"},
            {"type": "keysequence", "label": "Cочетание клавиш для автовзлома:", "widget": self.key_edit2, "inline": True, "align": "right", "row_margin": "0px, 9px, 0px, 8px"},
            {"type": "lineedit", "label": "Стабильная чувствительность:", "widget": self.sensitivity_settings, "inline": True, "align": "right", "row_margin": "0px, 9px, 0px, 8px"},
            {"label": "Создатель: @Sansenskiy", "row_margin": "0px, 0px, 0px, 8px"},
            {"label": f"Вся информация и актуальные версии здесь: <a href='https://github.com/M0R1C/smart-tool-assistent'>GitHuB</a>", "row_margin": "0px, 0px, 0px, 8px"},
            [
                {"label": "Версия: 0.4.5-251125", "align": "left"},
                {"type": "button", "widget": self.save_settings_btn, "align": "right"},
                {"row_margin": "0px, 9px, 8px, 8px"}
            ]

        ])

        self.save_settings_btn.setFixedWidth(160)

        self.macro_combo.selectionChanged.connect(self.on_macro_selected)

    def add_tab(self, name: str, fields: list):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        for field in fields:
            if isinstance(field, list):
                field_layout = QHBoxLayout()
                field_layout.setContentsMargins(0, 0, 0, 0)
                field_layout.setSpacing(10)

                row_margin = None
                for subfield in field:
                    if isinstance(subfield, dict) and subfield.get("row_margin"):
                        row_margin = subfield["row_margin"]
                        break

                if row_margin:
                    margins = self._parse_margin(row_margin)
                    field_layout.setContentsMargins(*margins)

                for subfield in field:
                    if isinstance(subfield, dict) and not subfield.get("row_margin"):
                        self.add_form_field(field_layout, subfield)

                layout.addLayout(field_layout)
            else:
                if isinstance(field, dict) and field.get("row_margin"):
                    field_layout = QHBoxLayout()
                    margins = self._parse_margin(field["row_margin"])
                    field_layout.setContentsMargins(*margins)

                    self.add_form_field(field_layout, field)

                    layout.addLayout(field_layout)
                else:
                    self.add_form_field(layout, field)

        self.tabs.addTab(tab, name)

    def add_form_field(self, layout, field):
        label = QLabel(field.get("label", ""))
        widget = field.get("widget")

        align = field.get("align", "left")
        alignment = self._get_alignment(align)

        if field.get("inline", False):
            field_layout = QHBoxLayout()
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(10)
            field_layout.addWidget(label)

            if alignment:
                field_layout.addWidget(widget, alignment=alignment)
            else:
                field_layout.addWidget(widget)

            if isinstance(widget, QLineEdit):
                widget.setFixedWidth(100)

            layout.addLayout(field_layout)
        else:
            layout.addWidget(label)
            if widget:
                if isinstance(widget, QPushButton):
                    widget.clicked.connect(field.get("action", lambda: None))
                    widget.setFixedWidth(100)
                if field.get("read_only", False):
                    widget.setReadOnly(True)
                if alignment:
                    container = QWidget()
                    container_layout = QHBoxLayout(container)
                    container_layout.setContentsMargins(0, 0, 0, 0)
                    container_layout.addWidget(widget, alignment=alignment)
                    layout.addWidget(container)
                else:
                    layout.addWidget(widget)

    def create_spinbox(self):
        spinbox = QSpinBox()
        spinbox.setStyleSheet("""
                            QSpinBox {
                                background-color: #2d3b47;  /* Фон как у кастомных элементов */
                                border: 1px solid #223240;
                                border-radius: 8px;
                                margin: 0px 0px 0px 10px;
                                padding: 6px 8px;
                                color: #dde7ef;  /* Цвет текста */
                            }
                            QSpinBox::up-button, QSpinBox::down-button {
                                background-color: none;  /* Кнопки вверх/вниз */
                                border: none;
                                color: none;
                            }
                            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                                background-color: none;  /* Цвет при наведении */
                            }
                        """)
        spinbox.setRange(1, 9999)
        spinbox.setValue(1)
        return spinbox

    def create_line_edit(self, default_value="1"):
        line_edit = QLineEdit()
        line_edit.setStyleSheet("""
                    QLineEdit {
                        background-color: #2d3b47;  /* Фон как у кастомных элементов */
                        border: 1px solid #223240;
                        border-radius: 8px;
                        margin: 0px;
                        padding: 6px 8px;
                        color: #dde7ef;  /* Цвет текста */
                    }
                """)
        line_edit.setText(default_value)
        return line_edit

    def on_task_selected(self, task_name):
        if task_name == "Фарм клиньев":
            self.selected_task = "farm_wedges"
        elif task_name == "Фарм волн":
            self.selected_task = "farm_waves"

    def on_record_clicked(self):
        try:
            import threading
            recorder = BackgroundRecorder()
            background_thread = threading.Thread(target=recorder.start_background_recording)
            background_thread.start()

            self.update_console_output("🎮 Gameplay Recorder запущен в фоновом режиме")
            self.update_console_output("=== Горячие клавиши ===")
            self.update_console_output("F9 - Начать запись")
            self.update_console_output("F10 - Остановить запись")
            self.update_console_output("=======================")

        except Exception as e:
            self.console_output.append(f"Ошибка при запуске записи: {str(e)}")

    def on_play_clicked(self, cooldown: int | str = 5, sensativity: int | float = 1.0, filename: str = ''):
        try:
            cooldown = int(cooldown)
            sensativity = float(sensativity)
            filename = str(filename)

            import threading
            save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes_out')
            full_filename = os.path.join(save_path, f'{filename}.json')

            player = GameplayPlayer(sensitivity=sensativity, cooldown=cooldown, replay_file=full_filename)
            background_thread = threading.Thread(target=player.play)
            background_thread.start()

            self.update_console_output(f"📁 Загружен реплей: {filename}.json")
            self.update_console_output(f"🎯 Чувствительность мыши: {sensativity}")
            self.update_console_output(f"🎯 Режим ввода: низкоуровневый WinAPI")
            self.update_console_output(f"⏳ Ожидание {cooldown} секунд перед воспроизведением...")

        except Exception as e:
            self.console_output.append(f"Ошибка при запуске проигрывания: {str(e)}")

    def _load_macros(self):
        self.macro_combo.menu.clear()
        try:
            for file_name in os.listdir("routes_out"):
                if file_name.endswith(".json"):
                    macro_name = file_name.replace(".json", "")
                    # Добавляем новый элемент в выпадающий список
                    action = self.macro_combo.menu.addAction(macro_name)
                    action.triggered.connect(lambda _, name=macro_name: self.on_macro_selected(name))
        except FileNotFoundError:
            print("Папка 'routes_out' не найдена.")
            self.macro_combo.menu.addAction("Нет макросов", lambda: self.on_macro_selected("Нет макросов"))

    def on_macro_selected(self, selected_macro):
        self.selected_macro = selected_macro
        print(f"Выбран макрос: {self.selected_macro}")
        self.macro_combo.button.setText(self.selected_macro)

    def update_console_output(self, message: str):
        self.console_output.append(message)

    def _get_alignment(self, align_str: str) -> Qt.Alignment:
        align_map = {
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight
        }
        return align_map.get(align_str.lower(), Qt.AlignmentFlag.AlignLeft)

    def _parse_margin(self, margin_str: str) -> tuple:
        margins = margin_str.replace('px', '').split(',')
        margins = [int(m.strip()) for m in margins]

        if len(margins) == 1:
            return (margins[0], margins[0], margins[0], margins[0])
        elif len(margins) == 2:
            return (margins[1], margins[0], margins[1], margins[0])
        elif len(margins) == 4:
            return (margins[3], margins[0], margins[1], margins[2])
        else:
            return (0, 0, 0, 0)

    @Slot()
    def on_run_clicked(self):
        if not hasattr(self, 'selected_task'):
            self.task_combo.button.setStyleSheet("""
                background-color: #2d3b47;
                color: #dde7ef;
                border: 2px solid red;
                border-radius: 8px;
                padding: 6px 8px;
            """)
            return
        reps = self.repeat_spin.value()
        self.start_task.emit(self.selected_task, reps, self.wave_spin.value())
        self.task_combo.button.setStyleSheet("""
            background-color: #2d3b47;
            color: #dde7ef;
            border: 1px solid #223240;
            border-radius: 8px;
            padding: 6px 8px;
        """)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_offset = None
        super().mouseReleaseEvent(e)


class CustomComboBox(QWidget):
    selectionChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget {
                background-color: #2d3b47;
                border: 1px solid #223240;
                border-radius: 8px;
                padding: 6px 0px;
            }
            QPushButton {
                background-color: #2d3b47;
                color: #dde7ef;
                border: none;
                text-align: left;
                width: 100%;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #18a999;
            }
            QMenu {
                background-color: #19232e;
                border: 1px solid #223240;
                border-radius: 8px;
            }
            QMenu::item {
                padding: 8px;
                color: #dde7ef;
            }
            QMenu::item:selected {
                background-color: #18a999;
                color: #000000;
            }
        """)

        self.button = QPushButton("Выберите задачу", self)
        self.button.clicked.connect(self.showMenu)

        self.menu = QMenu(self)
        self.menu.addAction("Фарм клиньев", lambda: self.on_item_selected("Фарм клиньев"))
        self.menu.addAction("Фарм волн", lambda: self.on_item_selected("Фарм волн"))

        layout = QVBoxLayout(self)
        layout.addWidget(self.button)
        self.setLayout(layout)

    def showMenu(self):
        self.menu.exec(self.button.mapToGlobal(self.button.rect().bottomLeft()))

    def on_item_selected(self, text):
        self.selectionChanged.emit(text)
        self.button.setText(text)


class MacroComboBox(QWidget):
    selectionChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget {
                background-color: #2d3b47;
                border: 1px solid #223240;
                border-radius: 8px;
                padding: 6px 0px;
            }
            QPushButton {
                background-color: #2d3b47;
                color: #dde7ef;
                border: none;
                text-align: left;
                width: 100%;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #18a999;
            }
            QMenu {
                background-color: #19232e;
                border: 1px solid #223240;
                border-radius: 8px;
            }
            QMenu::item {
                padding: 8px;
                color: #dde7ef;
            }
            QMenu::item:selected {
                background-color: #18a999;
                color: #000000;
            }
        """)

        self.button = QPushButton("Выберите макрос", self)
        self.button.clicked.connect(self.showMenu)

        self.menu = QMenu(self)
        self._load_macros()

        layout = QVBoxLayout(self)
        layout.addWidget(self.button)
        self.setLayout(layout)

    def _load_macros(self):
        try:
            self.menu.clear()
            for file_name in os.listdir("routes_out"):
                if file_name.endswith(".json"):
                    macro_name = file_name.replace(".json", "")
                    action = self.menu.addAction(macro_name)
                    action.triggered.connect(lambda _, name=macro_name: self.on_item_selected(name))
        except FileNotFoundError:
            print("Папка 'routes_out' не найдена.")
            self.menu.addAction("Нет макросов", lambda: self.on_item_selected("Нет макросов"))

    def showMenu(self):
        self._load_macros()
        self.menu.exec(self.button.mapToGlobal(self.button.rect().bottomLeft()))

    def on_item_selected(self, text):
        global selected_macro
        selected_macro = text
        self.selectionChanged.emit(text)
        self.button.setText(text)


class TaskRunner:
    def __init__(self, ui: 'MainWindow'):
        self.ui = ui
        self.tesseract_ready = False
        self.template_dir = self._select_template_dir()
        self._init_ocr()
        self.settings = QSettings(ORG, APP)

    def _select_template_dir(self) -> str:
        try:
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[1]
                sw, sh = mon['width'], mon['height']
        except Exception:
            try:
                import pyautogui
                sw, sh = pyautogui.size()
            except Exception:
                sw, sh = 1920, 1080
        res = f"{sw}x{sh}"
        path = os.path.join("assets", res)
        os.makedirs("assets", exist_ok=True)
        try:
            dbg = os.path.join(HOME_BASE, "last_asset_dir.txt")
            with open(dbg, "w", encoding="utf-8") as f:
                f.write(f"{res}\n{path}\n")
        except Exception:
            pass
        return path

    def _asset_path(self, name: str) -> str:
        p1 = os.path.join(self.template_dir, name)
        if os.path.isfile(p1):
            return p1
        p2 = os.path.join("assets", name)
        return p2

    def _init_ocr(self):
        self.ocr_mode = None
        try:
            import pytesseract
            if hasattr(pytesseract, "pytesseract"):
                pt = pytesseract.pytesseract
            else:
                pt = pytesseract
            if not shutil.which("tesseract"):
                win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
                if os.path.isfile(win_path):
                    pt.tesseract_cmd = win_path
            self.ocr_mode = ("pytesseract", pt)
            self.tesseract_ready = True
            return
        except Exception:
            pass
        try:
            import easyocr
            self.ocr_mode = ("easyocr", easyocr.Reader(['en'], gpu=False))
            self.tesseract_ready = True
        except Exception:
            self.ocr_mode = None
            self.tesseract_ready = False

    def _debug_dir(self):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        d = os.path.join(HOME_BASE, "debug_runs", f'run_{ts}')
        os.makedirs(d, exist_ok=True)
        return d

    def _log(self, debug_dir, msg):
        try:
            with open(os.path.join(debug_dir, "debug.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
        except Exception:
            pass

    def _ensure_libs(self):
        try:
            import mss, pyautogui, cv2, numpy  # noqa
        except Exception as e:
            self.ui.notify("Не хватает зависимостей",
                           f"Установи: pip install opencv-python mss pyautogui pytesseract\n(или easyocr)\n\nОшибка: {e}")
            return False
        if not self.tesseract_ready:
            self.ui.notify("OCR не доступен",
                           "Поставь Tesseract (Windows installer) и пакет pytesseract, либо pip install easyocr.")
        return True

    def _grab_region(self, rel_x, rel_y, rel_w, rel_h, save_path=None):
        import mss, numpy as np, cv2
        with mss.mss() as sct:
            mon = sct.monitors[1]
            x = int(mon['left'] + mon['width'] * rel_x)
            y = int(mon['top'] + mon['height'] * rel_y)
            w = int(mon['width'] * rel_w)
            h = int(mon['height'] * rel_h)
            bbox = {'left': x, 'top': y, 'width': w, 'height': h}
            img = np.array(sct.grab(bbox))[:, :, :3]
        if save_path:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            cv2.imwrite(save_path, img)
        return (x, y, w, h), img

    BADMAP_ROI = (0.00, 0.00, 0.22, 0.28)
    BADMAP_THRESH = 0.95
    BADMAP_CHECK_INTERVAL = 0.5
    BADMAP_WINDOW_SEC = 30.0

    def _iter_badmap_templates(self):
        import glob, os
        patterns = [
            os.path.join(self.template_dir, "bad_mup_*.png"),
            os.path.join(self.template_dir, "bad_mup_*.jpg"),
            os.path.join("assets", "bad_mup_*.png"),
            os.path.join("assets", "bad_mup_*.jpg"),
        ]
        seen = set()
        for pat in patterns:
            for path in glob.glob(pat):
                base = os.path.basename(path)
                stem, _ = os.path.splitext(base)
                if base in seen:
                    continue
                seen.add(base)
                yield stem, path

    def _find_route_for_map(self, map_id: str) -> str | None:
        import os
        candidates = [
            os.path.join(self.template_dir, "routes", f"{map_id}.json"),
            os.path.join("assets", "routes", f"{map_id}.json"),
            os.path.join("routes", f"{map_id}.json"),
            os.path.join("routes_out", f"{map_id}.json"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def _detect_bad_map_once(self, debug_dir) -> tuple[bool, str | None]:
        (rx, ry, rw, rh), region = self._grab_region(*self.BADMAP_ROI,
                                                     save_path=os.path.join(debug_dir, "region_badmap_probe.png"))
        for map_id, tpl in self._iter_badmap_templates():
            hit = self._find_template(
                region_img=region,
                template_path=tpl,
                debug_dir=debug_dir,
                tag=f"badmap_{map_id}",
                thresh=self.BADMAP_THRESH
            )
            if hit:
                self._log(debug_dir, f"[badmap] DETECTED: {map_id}")
                return True, map_id
        return False, None

    def _iter_console_templates(self):
        import glob, os
        patterns = [
            os.path.join(self.template_dir, "console_*.png"),
            os.path.join(self.template_dir, "console_*.jpg"),
            os.path.join("assets", "console_*.png"),
            os.path.join("assets", "console_*.jpg"),
        ]
        seen = set()
        for pat in patterns:
            for path in glob.glob(pat):
                base = os.path.basename(path)
                stem, _ = os.path.splitext(base)
                if base in seen:
                    continue
                seen.add(base)
                yield stem, path

    def _detect_console_once(self, debug_dir) -> tuple[bool, str | None]:
        """
        Одноразовая проверка правой половины экрана на наличие консолей.
        Возвращает (found, console_id) — если найдено, console_id = 'console_X'.
        """
        # Захватываем правую половину экрана (0.5, 0.0, 0.5, 1.0)
        (rx, ry, rw, rh), region = self._grab_region(0.5, 0.0, 0.5, 1.0,
                                                     save_path=os.path.join(debug_dir, "region_console_probe.png"))

        for console_id, tpl in self._iter_console_templates():
            hit = self._find_template(
                region_img=region,
                template_path=tpl,
                debug_dir=debug_dir,
                tag=f"console_{console_id}",
                thresh=0.8  # Можно настроить порог чувствительности
            )
            if hit:
                self._log(debug_dir, f"[console] DETECTED: {console_id}")
                return True, console_id
        return False, None

    def run_autocrack(self):
        """
        Основной метод для автовзлома консоли
        """
        if not self._ensure_libs():
            return

        debug_dir = self._debug_dir()
        self._log(debug_dir, "=== Autocrack started ===")

        found, console_id = self._detect_console_once(debug_dir)

        if found:
            print(f"[AUTOCRACK] Найдена известная консоль: {console_id}")

            success = self._run_console_route(console_id, debug_dir)

            if success:
                print(f"[AUTOCRACK] Запущен реплей для консоли: {console_id}")
            else:
                print(f"[AUTOCRACK] Не найден реплей для консоли: {console_id}")

        else:
            self._log(debug_dir, "[autocrack] No known consoles detected")
            print("[AUTOCRACK] Известные консоли не обнаружены")
            self.ui.notify("Автовзлом", "Консоли не обнаружены")

    def _find_route_for_console(self, console_id: str) -> str | None:
        import os
        candidates = [
            os.path.join(self.template_dir, "routes", "console", f"{console_id}.json"),
            os.path.join("assets", "routes", "console", f"{console_id}.json"),
            os.path.join("routes", "console", f"{console_id}.json"),
            os.path.join("routes_out", "console", f"{console_id}.json"),
            # Также проверяем в корне routes_out для обратной совместимости
            os.path.join("routes_out", f"{console_id}.json"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def _run_console_route(self, console_id: str, debug_dir, speed=1.0, sens=1.0) -> bool:
        route = self._find_route_for_console(console_id)
        if not route:
            return False
        try:
            from replayer import GameplayPlayer

            sensitivity_value = float(self.settings.value(SET_KEY_SENSITIVITY, "1.0"))
            cooldown = 0

            player = GameplayPlayer(
                sensitivity=float(sensitivity_value),
                cooldown=cooldown,
                replay_file=route
            )

            import threading
            thread = threading.Thread(target=player.play)
            thread.daemon = True
            thread.start()

            return True
        except Exception as e:
            self._log(debug_dir, f"[console] play failed: {e}")
            return False

    def _find_template(self, region_img=None, template_path=None, debug_dir=None, tag="", thresh=0.7, rel_rect=None):
        import cv2, mss
        if region_img is None:
            if rel_rect is None:
                rel_rect = (0.0, 0.0, 1.0, 1.0)
            (rx, ry, rw, rh), region_img = self._grab_region(*rel_rect)
        else:
            rx = ry = 0

        if not template_path or not os.path.isfile(template_path):
            if debug_dir:
                self._log(debug_dir, f"Template missing: {template_path}")
            return None

        template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            if debug_dir:
                self._log(debug_dir, f"Template load failed: {template_path}")
            return None

        grayR = cv2.cvtColor(region_img, cv2.COLOR_BGR2GRAY)
        grayT = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        result = cv2.matchTemplate(grayR, grayT, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < thresh:
            return None

        th, tw = grayT.shape[:2]
        x, y = max_loc

        try:
            dbg = region_img.copy()
            cv2.rectangle(dbg, (x, y), (x + tw, y + th), (0, 255, 255), 2)
            if debug_dir:
                import os as _os
                cv2.imwrite(_os.path.join(debug_dir, f"match_{tag}.png"), dbg)
        except Exception:
            pass

        return (rx + x, ry + y, tw, th, max_val)

    def _normalize_text(self, s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()

    def _fuzzy_contains_word(self, text: str, word: str, threshold: float = 0.78) -> bool:
        tokens = text.split()
        for t in tokens:
            if difflib.SequenceMatcher(None, t, word).ratio() >= threshold:
                return True
        for i in range(max(0, len(text)-len(word)-2)):
            chunk = text[i:i+len(word)+2]
            if difflib.SequenceMatcher(None, chunk, word).ratio() >= threshold:
                return True
        return False

    def _fuzzy_all(self, text: str, words: list[str], thr: float = 0.78) -> bool:
        return all(self._fuzzy_contains_word(text, w, thr) for w in words)

    def _ocr_text(self, img_bgr, debug_dir=None, tag="ocr"):
        import cv2, os
        scale = 2.0
        keys = ["commission", "completed", "rewards", "dropped", "challenge", "again", "exit", "trial", "rank"]

        def preprocess_variant(mode: str):
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if mode == "adaptive":
                up = cv2.medianBlur(up, 3)
                thr = cv2.adaptiveThreshold(up, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                            cv2.THRESH_BINARY, 31, 9)
                return thr
            else:
                th = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
                inv = 255 - th
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3,3))
                mor = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, kernel, iterations=1)
                return mor

        def run_ocr(pre_img, suffix):
            text_all = ""
            boxes = []
            if self.ocr_mode and self.ocr_mode[0] == "pytesseract":
                pt = self.ocr_mode[1]
                try:
                    from pytesseract import image_to_data
                    config = r'--oem 3 --psm 6 -l eng tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 '
                    data = image_to_data(pre_img, config=config, output_type='dict')
                    for i in range(len(data["text"])):
                        word = (data["text"][i] or "").strip()
                        if not word:
                            continue
                        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                        x = int(round(x / scale)); y = int(round(y / scale))
                        w = int(max(1, round(w / scale))); h = int(max(1, round(h / scale)))
                        wl = self._normalize_text(word)
                        if not wl:
                            continue
                        boxes.append((wl, (x, y, w, h)))
                        text_all += wl + " "
                except Exception as e:
                    self._log(debug_dir, f"pytesseract data error: {e}")
            elif self.ocr_mode and self.ocr_mode[0] == "easyocr":
                reader = self.ocr_mode[1]
                try:
                    res = reader.readtext(pre_img)
                    for bbox, word, conf in res:
                        wl = self._normalize_text(word or "")
                        if not wl:
                            continue
                        xs = [p[0] for p in bbox]; ys = [p[1] for p in bbox]
                        x = int(round(min(xs) / scale)); y = int(round(min(ys) / scale))
                        w = int(max(1, round((max(xs)-min(xs)) / scale)))
                        h = int(max(1, round((max(ys)-min(ys)) / scale)))
                        boxes.append((wl, (x, y, w, h)))
                        text_all += wl + " "
                except Exception as e:
                    self._log(debug_dir, f"easyocr error: {e}")

            if debug_dir is not None:
                cv2.imwrite(os.path.join(debug_dir, f"ocr_{tag}.png"), pre_img)
                with open(os.path.join(debug_dir, f"ocr_{tag}.txt"), "w", encoding="utf-8") as f:
                    f.write(text_all.strip())
            return text_all.strip(), boxes

        pre1 = preprocess_variant("adaptive")
        t1, b1 = run_ocr(pre1, "adaptive")
        pre2 = preprocess_variant("morph_inv")
        t2, b2 = run_ocr(pre2, "morph")

        score1 = sum(self._fuzzy_contains_word(t1, k) for k in keys)
        score2 = sum(self._fuzzy_contains_word(t2, k) for k in keys)
        if score2 > score1:
            return t2, b2
        return t1, b1

    def _move_click_abs(self, x, y):
        try:
            import pyautogui, time as _t, mss
            with mss.mss() as sct:
                mon = sct.monitors[1]
                phys_w = mon['width']; phys_h = mon['height']
                off_x  = mon.get('left', 0); off_y  = mon.get('top', 0)
            py_w, py_h = pyautogui.size()

            if phys_w != py_w or phys_h != py_h:
                scale_x = phys_w / float(py_w)
                scale_y = phys_h / float(py_h)
                x_py = int(round((x - off_x) / scale_x))
                y_py = int(round((y - off_y) / scale_y))
            else:
                x_py = int(x - off_x)
                y_py = int(y - off_y)

            self._log(self._debug_dir(), f"Click ABS phys({x},{y}) -> py({x_py},{y_py}); phys({phys_w}x{phys_h}) vs py({py_w}x{py_h}), off({off_x},{off_y})")
            pyautogui.moveTo(x_py, y_py, duration=0.12)
            _t.sleep(0.03)
            pyautogui.click()
        except Exception as e:
            self._log(self._debug_dir(), f"_move_click_abs error: {e}")

    def _send_key(self, key):
        try:
            if KEYBOARD_AVAILABLE:
                import keyboard as _kb
                _kb.press_and_release(key)
            else:
                import pyautogui
                pyautogui.press(key.lower())
        except Exception as e:
            self._log(self._debug_dir(), f"_send_key error: {e}")

    def send_key(self, key):
        return self._send_key(key)

    def _find_text_and_click(self, rel_rect, keywords, debug_dir, tag, max_tries=10, delay=0.5):
        import cv2
        for i in range(max_tries):
            (rx, ry, rw, rh), region = self._grab_region(
                *rel_rect, save_path=os.path.join(debug_dir, f"region_{tag}_{i}.png")
            )
            text, boxes = self._ocr_text(region, debug_dir, f"{tag}_{i}")
            tl = self._normalize_text(text)

            try:
                dbg = region.copy()
                for wl, (x, y, w, h) in boxes:
                    cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 255), 2)
                    cv2.putText(dbg, wl[:20], (x, max(0, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.imwrite(os.path.join(debug_dir, f"boxes_{tag}_{i}.png"), dbg)
            except Exception as e:
                self._log(debug_dir, f"draw boxes error: {e}")

            self._log(debug_dir, f"[{tag} try {i}] OCR text: '{tl}' (tokens={len(tl.split())})")

            hit_box = None
            hit_key = None
            for wl, (x, y, w, h) in boxes:
                for kw in keywords:
                    if isinstance(kw, (list, tuple)):
                        if all(self._fuzzy_contains_word(wl, k) for k in kw) or \
                           any(self._fuzzy_contains_word(wl, k) for k in kw):
                            hit_box, hit_key = (x, y, w, h), str(kw)
                            break
                    else:
                        if self._fuzzy_contains_word(wl, kw):
                            hit_box, hit_key = (x, y, w, h), kw
                            break
                if hit_box:
                    break

            if not hit_box:
                for kw in keywords:
                    if isinstance(kw, (list, tuple)):
                        if all(self._fuzzy_contains_word(tl, k) for k in kw):
                            hit_box, hit_key = (rw // 2, rh // 2, 1, 1), str(kw)
                            break
                    else:
                        if self._fuzzy_contains_word(tl, kw):
                            hit_box, hit_key = (rw // 2, rh // 2, 1, 1), kw
                            break

            if hit_box:
                cx, cy, ww, hh = hit_box
                absx, absy = rx + cx + ww // 2, ry + cy + hh // 2
                self._log(debug_dir, f"[{tag} try {i}] HIT by '{hit_key}' at local({cx},{cy},{ww}x{hh}) -> ABS({absx},{absy})")
                self._move_click_abs(absx, absy)
                return True

            time.sleep(delay)

        self._log(debug_dir, f"[{tag}] No hit after {max_tries} tries. Keywords={keywords}")
        return False

    def _mission_completed(self, debug_dir, done_idx: int) -> bool:
        import time
        tag = f'progress_done{done_idx}_{int(time.time())}'
        (rx, ry, rw, rh), region = self._grab_region(
            0.10, 0.55, 0.80, 0.45,
            save_path=os.path.join(debug_dir, f'region_{tag}.png')
        )
        text, boxes = self._ocr_text(region, debug_dir, f'{tag}')
        tl = self._normalize_text(text)

        trial_rank = self._fuzzy_all(tl, ["trial", "rank"])
        chall_any  = self._fuzzy_all(tl, ["challenge", "again"]) or \
                     (self._fuzzy_contains_word(tl, "challenge") and self._fuzzy_contains_word(tl, "again"))

        if trial_rank or chall_any:
            return True

        hit_tpl = self._find_template(
            region_img=region,
            template_path=self._asset_path('trial_rank.png'),
            debug_dir=debug_dir,
            tag=f'trial_rank_tpl_{done_idx}',
            thresh=0.62
        )
        if hit_tpl:
            return True

        return False

    def _retry_find_and_click(self, rel_rect, asset_name, debug_dir, tag, max_tries=5, delay=1.0):
        found = None
        for i in range(max_tries):
            (rx, ry, rw, rh), region = self._grab_region(*rel_rect,
                save_path=os.path.join(debug_dir, f"region_{tag}_{i}.png"))
            hit = self._find_template(region_img=region,
                                      template_path=self._asset_path(asset_name),
                                      debug_dir=debug_dir, tag=f"{tag}_try{i}", thresh=0.68)
            if hit:
                found = (rx, ry, hit)
                break
            time.sleep(delay)
        if not found:
            return False
        rx, ry, (x, y, w, h, score) = found
        cx, cy = rx + x + w // 2, ry + y + h // 2
        self._move_click_abs(cx, cy)
        return True

    def _click_booster_start(self, debug_dir, tries: int = 10, delay: float = 0.8) -> bool:
        rel_rect = (0.20, 0.36, 0.60, 0.38)

        hit = self._find_template(
            region_img=None,
            template_path=self._asset_path("start.png"),
            debug_dir=debug_dir,
            tag="start_tpl",
            rel_rect=rel_rect,
            thresh=0.70
        )
        if hit:
            x, y, w, h, score = hit
            absx, absy = int(x + w // 2), int(y + h // 2)
            self._log(debug_dir, f"[start] Template hit score={score:.3f} at ABS({absx},{absy})")
            self._move_click_abs(absx, absy)
            return True

        self._log(debug_dir, "[start] Not found by OCR nor template")
        return False

    def run_farm_wedges(self, repeats: int):
        if not self._ensure_libs():
            return

        self.template_dir = self._select_template_dir()

        debug_dir = self._debug_dir()
        self._log(debug_dir, f"=== New run started === (assets dir: {self.template_dir})")

        sensitivity_value = float(self.settings.value(SET_KEY_SENSITIVITY, "1.0"))
        print(f"Using sensitivity: {sensitivity_value}")

        self.ui.overlay.hide()
        time.sleep(0.2)
        self._send_key('esc')
        time.sleep(0.8)

        ok = self._retry_find_and_click((0.0, 0.0, 0.25, 1.0), 'combat.png', debug_dir, 'combat')
        if not ok:
            self.ui.notify('Не найдено', f'Combat не найден. Смотри {debug_dir}')
            return
        time.sleep(1.0)

        ok = self._retry_find_and_click((0.0, 0.0, 1.0, 0.125), 'commissions.png', debug_dir, 'commissions')
        if not ok:
            self.ui.notify('Не найдено', f'Commissions не найден. Смотри {debug_dir}')
            return
        time.sleep(0.6)

        done = 0
        total = max(1, int(repeats))
        self.ui.hud_show(f"{done}/{total}")

        while done < total:
            started = False
            for i in range(5):
                (rx, ry, rw, rh), region = self._grab_region(0.0, 0.0, 0.20, 0.25,
                    save_path=os.path.join(debug_dir, f'region_mstart_{done}_{i}.png'))
                hit = self._find_template(region_img=region,
                                          template_path=self._asset_path('mission_start.png'),
                                          debug_dir=debug_dir, tag=f'mstart_try{done}_{i}', thresh=0.62)
                if hit:
                    started = True
                    break
                time.sleep(1.0)
            if not started:
                self._log(debug_dir, "mission start marker not found (continuing anyway)")

            self.ui.hud_update(f"{done}/{total}")

            completed = False
            t_win_start = time.time()
            bad_check_enabled = True
            next_bad_check = 0.0

            while not completed:
                if self._mission_completed(debug_dir, done):
                    completed = True
                    break

                now = time.time()
                if bad_check_enabled and now - t_win_start <= self.BADMAP_WINDOW_SEC:
                    if now >= next_bad_check:
                        next_bad_check = now + self.BADMAP_CHECK_INTERVAL
                        found, map_id = self._detect_bad_map_once(debug_dir)
                        if found and map_id:
                            import threading
                            save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes_out')
                            print(f'bad_mup_{map_id}.json')
                            full_filename = os.path.join(save_path, f'{map_id}.json')
                            player = GameplayPlayer(sensitivity=float(sensitivity_value), cooldown=int(2), replay_file=full_filename)
                            background_thread = threading.Thread(target=player.play)
                            background_thread.start()
                            bad_check_enabled = False
                else:
                    bad_check_enabled = False

                time.sleep(0.10)

            done += 1
            self.ui.hud_update(f"{done}/{total}")

            if done < total:
                self.ui.notify("Повтор миссии", f"Запуск попытки {done+1}/{total}...")

                rel_rect = (0.45, 0.55, 0.50, 0.45)

                ok = self._find_text_and_click(
                    rel_rect,
                    keywords=[("challenge", "again"), "challenge", "again"],
                    debug_dir=debug_dir,
                    tag=f'ch_again_ocr_{done}'
                )
                if not ok:
                    hit = self._find_template(
                        region_img=None,
                        template_path=self._asset_path("challenge_again.png"),
                        debug_dir=debug_dir,
                        tag=f'ch_again_tpl_{done}',
                        rel_rect=rel_rect,
                        thresh=0.70
                    )
                    if hit:
                        x, y, w, h, score = hit
                        absx, absy = int(x + w // 2), int(y + h // 2)
                        self._move_click_abs(absx, absy)
                        ok = True

                time.sleep(0.1)
                if not self._click_booster_start(debug_dir, tries=30, delay=0.1):
                    self.ui.notify("Старт не найден", "Кнопка Start в окне бустеров не обнаружена.")
                    self._log(debug_dir, "Start button not found on boosters dialog.")
                    break

                time.sleep(2.0)
                continue
            else:
                rel_rect = (0.45, 0.55, 0.50, 0.45)
                hit = self._find_template(
                    region_img=None,
                    template_path=self._asset_path("exit.png"),
                    debug_dir=debug_dir,
                    tag='exit_tpl',
                    rel_rect=rel_rect,
                    thresh=0.70
                )
                if hit:
                    x, y, w, h, score = hit
                    absx, absy = int(x + w // 2), int(y + h // 2)
                    self._move_click_abs(absx, absy)
                    ok = True
                if not ok:
                    self.ui.notify("Не найдено", "Кнопка Exit не найдена.")
                break

        self.ui.hud_hide()
        self.ui.notify('Готово', f'Повторы выполнены: {done}/{total}\nDebug: {debug_dir}')

    def run_farm_waves(self, repeats: int, waves: int):
        if not self._ensure_libs():
            return

        self.template_dir = self._select_template_dir()

        debug_dir = self._debug_dir()

        sensitivity_value = float(self.settings.value(SET_KEY_SENSITIVITY, "1.0"))
        print(f"Using sensitivity: {sensitivity_value}")

        self.ui.overlay.hide()
        time.sleep(0.2)
        self._send_key('esc')
        time.sleep(0.8)

        ok = self._retry_find_and_click((0.0, 0.0, 0.25, 1.0), 'combat.png', debug_dir, 'combat')
        if not ok:
            self.ui.notify('Не найдено', f'Combat не найден. Смотри {debug_dir}')
            return
        time.sleep(1.0)

        ok = self._retry_find_and_click((0.0, 0.0, 1.0, 0.125), 'commissions.png', debug_dir, 'commissions')
        if not ok:
            self.ui.notify('Не найдено', f'Commissions не найден. Смотри {debug_dir}')
            return
        time.sleep(0.6)

        doner = 0
        totalr = max(1, int(repeats))
        donew = 0
        totalw = max(1, int(waves))
        self.ui.hud_show(f"{doner}/{totalr}")

        while doner < totalr:
            donew = 0
            totalw = max(1, int(waves))
            started = False
            for i in range(5):
                (rx, ry, rw, rh), region = self._grab_region(0.0, 0.0, 0.20, 0.25,
                                                             save_path=os.path.join(debug_dir,
                                                                                    f'region_mstart_{doner}_{i}.png'))
                hit = self._find_template(region_img=region,
                                          template_path=self._asset_path('mission_start.png'),
                                          debug_dir=debug_dir, tag=f'mstart_try{doner}_{i}', thresh=0.62)
                if hit:
                    started = True
                    break
                time.sleep(1.0)
            if not started:
                self._log(debug_dir, "mission start marker not found (continuing anyway)")

            while donew < totalw:
                repeat_found = False
                while not repeat_found:
                    rel_rect = (0.25, 0.35, 0.50, 0.40)

                    (rx, ry, rw, rh), region = self._grab_region(*rel_rect,
                                                                 save_path=os.path.join(debug_dir,
                                                                                        f'region_repeat_check_{doner}.png'))

                    hit = self._find_template(region_img=region,
                                              template_path=self._asset_path("repeat.png"),
                                              debug_dir=debug_dir,
                                              tag=f'repeat_check_{doner}',
                                              thresh=0.70)

                    if hit:
                        donew += 1
                        repeat_found = True
                        print(donew,totalw)
                        if not donew >= totalw:
                            x, y, w, h, score = hit
                            absx, absy = int(rx + x + w // 2), int(ry + y + h // 2)
                            self._move_click_abs(absx, absy)
                            self._log(debug_dir, f"Repeat found and clicked at ABS({absx},{absy})")

                        time.sleep(0.2)

                        if not self._click_booster_start(debug_dir, tries=30, delay=0.1):
                            self.ui.notify("Старт не найден", "Кнопка Start в окне бустеров не обнаружена.")
                            self._log(debug_dir, "Start button not found on boosters dialog.")
                            break
                    else:
                        time.sleep(1.0)

                if donew >= totalw:
                    print('волны пройдены, жмаю retreat')
                    doner += 1
                    self.ui.hud_update(f"{doner}/{totalr}")
                    rel_rect = (0.25, 0.35, 0.50, 0.40)
                    hit = self._find_template(
                        region_img=None,
                        template_path=self._asset_path("retreat.png"),
                        debug_dir=debug_dir,
                        tag='retreat_tpl',
                        rel_rect=rel_rect,
                        thresh=0.70
                    )
                    if hit:
                        x, y, w, h, score = hit
                        absx, absy = int(x + w // 2), int(y + h // 2)
                        self._move_click_abs(absx, absy)
                    break
            if doner < totalr:
                print('Ищю повтор')
                self.ui.notify("Повтор миссии", f"Запуск попытки {doner + 1}/{totalr}...")

                rel_rect = (0.45, 0.55, 0.50, 0.45)

                ok = self._find_text_and_click(
                    rel_rect,
                    keywords=[("challenge", "again"), "challenge", "again"],
                    debug_dir=debug_dir,
                    tag=f'ch_again_ocr_{doner}'
                )
                if not ok:
                    hit = self._find_template(
                        region_img=None,
                        template_path=self._asset_path("challenge_again.png"),
                        debug_dir=debug_dir,
                        tag=f'ch_again_tpl_{doner}',
                        rel_rect=rel_rect,
                        thresh=0.70
                    )
                    if hit:
                        x, y, w, h, score = hit
                        absx, absy = int(x + w // 2), int(y + h // 2)
                        self._move_click_abs(absx, absy)
                        ok = True

                time.sleep(0.3)
                if not self._click_booster_start(debug_dir, tries=5, delay=0.2):
                    self.ui.notify("Старт не найден", "Кнопка Start в окне бустеров не обнаружена.")
                    self._log(debug_dir, "Start button not found on boosters dialog.")
                    break

                time.sleep(2.0)
                continue
            else:
                rel_rect = (0.45, 0.55, 0.50, 0.45)
                hit = self._find_template(
                    region_img=None,
                    template_path=self._asset_path("exit.png"),
                    debug_dir=debug_dir,
                    tag='exit_tpl',
                    rel_rect=rel_rect,
                    thresh=0.70
                )
                if hit:
                    x, y, w, h, score = hit
                    absx, absy = int(x + w // 2), int(y + h // 2)
                    self._move_click_abs(absx, absy)
                    ok = True
                if not ok:
                    self.ui.notify("Не найдено", "Кнопка Exit не найдена.")
                break

        self.ui.hud_hide()
        self.ui.notify('Готово', f'Повторы выполнены: {donew}/{totalw}\nDebug: {debug_dir}')


class MainWindow(QMainWindow):
    toggle_overlay_signal = Signal()
    message_signal = Signal(str, str)
    hud_set_text_signal = Signal(str)
    hud_show_signal = Signal(str)
    hud_hide_signal = Signal()

    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG, APP)
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon.fromTheme("applications-system"))
        menu = QMenu()
        menu.addAction(QAction("Показать/Скрыть оверлей", self, triggered=self.toggle_overlay))
        menu.addAction(QAction("Выход", self, triggered=self.close))
        self.tray.setContextMenu(menu)
        self.tray.show()

        self.overlay = OverlayPanel()
        self.overlay.start_task.connect(self.start_task)
        self.overlay.hide()
        hotkey = self.settings.value(SET_KEY_HOTKEY, DEFAULT_HOTKEY)
        self.overlay.key_edit.setKeySequence(QKeySequence(str(hotkey)))
        self.overlay.save_settings_btn.clicked.connect(self.save_settings)

        hotkey = self.settings.value(SET_KEY_HOTKEY, DEFAULT_HOTKEY)
        hotkey2 = self.settings.value(SET_KEY_HOTKEY2, DEFAULT_HOTKEY2)
        sensitivity = self.settings.value(SET_KEY_SENSITIVITY, "1.0")

        self.overlay.key_edit.setKeySequence(QKeySequence(str(hotkey)))
        self.overlay.key_edit2.setKeySequence(QKeySequence(str(hotkey2)))
        self.overlay.sensitivity_settings.setText(str(sensitivity))

        self.hotkey_mgr = GlobalHotkeyManager(
            on_trigger=self.toggle_overlay_async,
            on_trigger2=self.on_autocrack_triggered
        )
        self.hotkey_mgr.register(str(hotkey), str(hotkey2))

        self.apply_theme()

        self.tray.showMessage("Smart Overlay Macro",
                              f"Оверлей скрыт. Горячая клавиша: {hotkey}",
                              QSystemTrayIcon.MessageIcon.Information, 4000)
        self.setVisible(False)
        self.toggle_overlay_signal.connect(self.toggle_overlay)

        self.runner = TaskRunner(self)

        self.message_signal.connect(self._on_message)

        self.hud = ProgressHUD()
        self.hud_set_text_signal.connect(self._hud_set_text)
        self.hud_show_signal.connect(self._hud_show)
        self.hud_hide_signal.connect(self._hud_hide)

    def start_task(self, task_id: str, repeats: int, waves: int):
        self.overlay.hide()
        if task_id == "farm_wedges":
            threading.Thread(target=self.runner.run_farm_wedges, args=(repeats,), daemon=True).start()
        elif task_id == "farm_waves":
            threading.Thread(target=self.runner.run_farm_waves, args=(repeats, waves,), daemon=True).start()
        else:
            self.notify("Неизвестная задача", task_id)

    def on_autocrack_triggered(self):
        print("=== Autocrack started ===")
        sensitivity = self.overlay.sensitivity_settings.text()
        try:
            sensitivity_value = float(sensitivity)
            print(f"Запуск поиска консолей с чувствительностью {sensitivity_value}")
            threading.Thread(target=self.runner.run_autocrack, daemon=True).start()

        except ValueError:
            self.notify("Ошибка", "Некорректное значение чувствительности")

    def notify(self, title: str, text: str):
        self.message_signal.emit(title, text)

    def hud_update(self, text: str):
        self.hud_set_text_signal.emit(text)
    def hud_show(self, text: str):
        self.hud_show_signal.emit(text)
    def hud_hide(self):
        self.hud_hide_signal.emit()

    @Slot(str, str)
    def _on_message(self, title: str, text: str):
        self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 5000)
        box = QMessageBox(QMessageBox.Icon.Information, title, text)
        box.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.show()
        QTimer.singleShot(6000, box.close)

    @Slot(str)
    def _hud_set_text(self, t: str):
        self.hud.set_text(t)
        self.hud.place_top_center()

    @Slot(str)
    def _hud_show(self, t: str):
        self.hud.set_text(t)
        self.hud.place_top_center()
        self.hud.show()

    @Slot()
    def _hud_hide(self):
        self.hud.hide()

    @Slot()
    def save_settings(self):
        seq = self.overlay.key_edit.keySequence()
        seq_str = seq.toString(QKeySequence.SequenceFormat.PortableText) or DEFAULT_HOTKEY

        seq2 = self.overlay.key_edit2.keySequence()
        seq_str2 = seq2.toString(QKeySequence.SequenceFormat.PortableText) or DEFAULT_HOTKEY2

        sensitivity = self.overlay.sensitivity_settings.text()

        self.settings.setValue(SET_KEY_HOTKEY, seq_str)
        self.settings.setValue(SET_KEY_HOTKEY2, seq_str2)
        self.settings.setValue(SET_KEY_SENSITIVITY, sensitivity)

        self.hotkey_mgr.register(seq_str, seq_str2)
        self.tray.showMessage("Настройки сохранены", f"Открытие интерфейса: {seq_str}\n"
                                                     f"Авто взлом консоли: {seq_str2}\n"
                                                     f"Стабильная чувствительность: {sensitivity}",
                              QSystemTrayIcon.MessageIcon.Information, 2500)

    def apply_theme(self):
        app = QApplication.instance()
        if app:
            app.setStyle("Fusion")
            app.setStyleSheet(
                f"""
                QFrame#OverlayPanel {{ background-color: rgba(20,26,32,{alpha_overlay}); border-radius: 18px; }}
                QFrame#OverlayPanel QFrame, QFrame#OverlayPanel QTabWidget, QFrame#OverlayPanel QStackedWidget {{ background: transparent; }}
                QWidget {{ color: {TEXT}; font-size: 14px; background: none; }}
                QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 12px; background: rgba(25,35,46,{alpha_panels}); margin-top: 4px; }}
                QWidget#qt_tabwidget_stackedwidget {{ background: transparent; border: none; }}
                QTabBar::tab {{ background: {PANEL_BG}; padding: 8px 14px; border-top-left-radius: 10px; border-top-right-radius: 10px; }}
                QTabBar::tab:selected {{ background: {PANEL_BG_HL}; color: #bfeeed; }}
                QLabel {{ color: {TEXT}; }}
                QTextEdit {{background-color: #151b21; border: 1px solid #223240; border-radius: 8px; color: #dde7ef; padding: 6px 8px; height: 200px; }}
                QLineEdit {{background-color: #2d3b47; border: 1px solid #223240; border-radius: 8px; margin: 0px 0px 0px 0px; padding: 6px 8px; color: #dde7ef;}}
                QComboBox, QSpinBox, QKeySequenceEdit {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px; }}
                QPushButton {{ background: {ACCENT}; color: #06202f; border: none; border-radius: 10px; padding: 8px 10px; font-weight: 600;}}
                QPushButton:hover {{ background: #19bfb0; }}
                QPushButton:pressed {{ background: #149c8e; }}
                QCheckBox::indicator {{ width: 18px; height: 18px; }}
                QCheckBox::indicator:unchecked {{ border: 1px solid {BORDER}; background: {PANEL_BG}; }}
                QCheckBox::indicator:checked {{ background: {ACCENT}; }}
                QComboBox {{
                    background-color: #2d3b47;
                    border: 1px solid #223240;
                    border-radius: 8px;
                    padding: 6px 8px;
                }}

                QComboBox::down-arrow {{
                    image: none;
                }}
                
                QComboBox::drop-down {{
                    border: none;
                    background: transparent;
                }}
                
                QComboBox QAbstractItemView {{
                    background-color: #19232e;
                    border: 1px solid #223240;
                    selection-background-color: #18a999;
                    selection-color: #dde7ef;
                    border-radius: 8px;
                    padding: 4px 0;
                }}
                """
            )

    @Slot()
    def toggle_overlay(self):
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.center_overlay()
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()

    def toggle_overlay_async(self):
        self.toggle_overlay_signal.emit()

    def center_overlay(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        width, height = 520, 300
        x = geo.x() + (geo.width() - width) // 2
        y = geo.y() + (geo.height() - height) // 2
        self.overlay.setGeometry(x, y, width, height)

    def closeEvent(self, event):
        try:
            self.hotkey_mgr.shutdown()
        except Exception:
            pass
        event.accept()


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            ctypes.windll.user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    import ctypes
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    win = MainWindow()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
