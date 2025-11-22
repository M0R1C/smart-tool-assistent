import argparse
import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from PySide6.QtCore import QObject, QMetaObject, Qt
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener
from pynput.mouse import Listener as MouseListener
from win10toast import ToastNotifier

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

toaster = ToastNotifier()

def show_notification(message):
    def notify():
        toaster.show_toast("Gameplay Recorder", message, duration=5)
    threading.Thread(target=notify, daemon=True).start()


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]


@dataclass
class MouseEvent:
    type: str
    x: int = None
    y: int = None
    button: str = None
    pressed: bool = None
    dx: int = None
    dy: int = None
    timestamp: float = None


@dataclass
class KeyboardEvent:
    type: str
    key: str = None
    pressed: bool = None
    timestamp: float = None


class LowLevelMouseController:
    def __init__(self):
        self.INPUT_MOUSE = 0
        self.MOUSEEVENTF_MOVE = 0x0001
        self.MOUSEEVENTF_LEFTDOWN = 0x0002
        self.MOUSEEVENTF_LEFTUP = 0x0004
        self.MOUSEEVENTF_RIGHTDOWN = 0x0008
        self.MOUSEEVENTF_RIGHTUP = 0x0010
        self.MOUSEEVENTF_MIDDLEDOWN = 0x0020
        self.MOUSEEVENTF_MIDDLEUP = 0x0040
        self.MOUSEEVENTF_WHEEL = 0x0800
        self.MOUSEEVENTF_ABSOLUTE = 0x8000

    def move_relative(self, dx, dy):
        mouse_input = MOUSEINPUT(
            dx=dx,
            dy=dy,
            mouseData=0,
            dwFlags=self.MOUSEEVENTF_MOVE,
            time=0,
            dwExtraInfo=None
        )
        input_struct = INPUT(
            type=self.INPUT_MOUSE,
            mi=mouse_input
        )
        user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

    def click(self, button, pressed):
        if button == "left":
            flags = self.MOUSEEVENTF_LEFTDOWN if pressed else self.MOUSEEVENTF_LEFTUP
        elif button == "right":
            flags = self.MOUSEEVENTF_RIGHTDOWN if pressed else self.MOUSEEVENTF_RIGHTUP
        elif button == "middle":
            flags = self.MOUSEEVENTF_MIDDLEDOWN if pressed else self.MOUSEEVENTF_MIDDLEUP
        else:
            return

        mouse_input = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None
        )
        input_struct = INPUT(
            type=self.INPUT_MOUSE,
            mi=mouse_input
        )
        user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

    def scroll(self, dx, dy):
        if dy != 0:
            mouse_input = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=dy * 120,  # WHEEL_DELTA = 120
                dwFlags=self.MOUSEEVENTF_WHEEL,
                time=0,
                dwExtraInfo=None
            )
            input_struct = INPUT(
                type=self.INPUT_MOUSE,
                mi=mouse_input
            )
            user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))


class LowLevelKeyboardController:
    def __init__(self):
        self.INPUT_KEYBOARD = 1
        self.KEYEVENTF_KEYUP = 0x0002
        self.KEYEVENTF_SCANCODE = 0x0008

        self.scan_codes = {
            'shift_l': 0x2A, 'shift_r': 0x36, 'ctrl_l': 0x1D, 'ctrl_r': 0x1D, 'alt_l': 0x38,
            'alt_r': 0x38,
            'a': 0x1E, 'b': 0x30, 'c': 0x2E, 'd': 0x20, 'e': 0x12, 'f': 0x21, 'g': 0x22, 'h': 0x23,
            'i': 0x17, 'j': 0x24, 'k': 0x25, 'l': 0x26, 'm': 0x32, 'n': 0x31, 'o': 0x18, 'p': 0x19,
            'q': 0x10, 'r': 0x13, 's': 0x1F, 't': 0x14, 'u': 0x16, 'v': 0x2F, 'w': 0x11, 'x': 0x2D,
            'y': 0x15, 'z': 0x2C,
            '0': 0x0B, '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06, '6': 0x07, '7': 0x08,
            '8': 0x09, '9': 0x0A,
            'space': 0x39, 'enter': 0x1C, 'esc': 0x01, 'tab': 0x0F,
            'f1': 0x3B, 'f2': 0x3C, 'f3': 0x3D, 'f4': 0x3E, 'f5': 0x3F, 'f6': 0x40, 'f7': 0x41,
            'f8': 0x42, 'f9': 0x43, 'f10': 0x44, 'f11': 0x57, 'f12': 0x58,
            'up': 0x48, 'down': 0x50, 'left': 0x4B, 'right': 0x4D,
            'insert': 0x52, 'delete': 0x53, 'home': 0x47, 'end': 0x4F, 'page_up': 0x49, 'page_down': 0x51,
            'backspace': 0x0E, 'caps_lock': 0x3A, 'num_lock': 0x45, 'scroll_lock': 0x46,
        }

    def press_key(self, key_str):
        scan_code = self._get_scan_code(key_str)
        if scan_code is None:
            return

        flags = self.KEYEVENTF_SCANCODE
        if key_str in ['ctrl_r', 'alt_r']:
            flags |= 0x0001

        keybd_input = KEYBDINPUT(
            wVk=0,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None
        )
        input_struct = INPUT(
            type=self.INPUT_KEYBOARD,
            ki=keybd_input
        )
        user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

    def release_key(self, key_str):
        scan_code = self._get_scan_code(key_str)
        if scan_code is None:
            return

        flags = self.KEYEVENTF_SCANCODE | self.KEYEVENTF_KEYUP
        if key_str in ['ctrl_r', 'alt_r']:
            flags |= 0x0001

        keybd_input = KEYBDINPUT(
            wVk=0,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=None
        )
        input_struct = INPUT(
            type=self.INPUT_KEYBOARD,
            ki=keybd_input
        )
        user32.SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))

    def _get_scan_code(self, key_str):
        if key_str.startswith("KeyCode("):
            vk = int(key_str[8:-1])
            scan_code = user32.MapVirtualKeyA(vk, 0)
            return scan_code if scan_code != 0 else None

        key_lower = key_str.lower()
        if key_lower in self.scan_codes:
            return self.scan_codes[key_lower]

        if len(key_str) == 1:
            vk_code = ord(key_str.upper())
            scan_code = user32.MapVirtualKeyA(vk_code, 0)
            return scan_code if scan_code != 0 else None

        print(f"⚠️ Неизвестная клавиша: {key_str}")
        return None


class BackgroundRecorder():

    def __init__(self):
        super().__init__()
        self.mouse_events = []
        self.keyboard_events = []
        self.start_time = None
        self.recording = False
        self.filename = None

        self.mouse_listener = None
        self.keyboard_listener = None
        self.hotkey_listener = None

        self.last_mouse_position = None

        self.record_start_time = None
        self.total_duration = 0

    def start_recording(self):
        if self.recording:
            return

        self.mouse_events.clear()
        self.keyboard_events.clear()
        self.start_time = time.perf_counter()
        self.record_start_time = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.filename = f"replay_{timestamp}.json"
        print(f"🎥 Запись начата в {datetime.now().strftime('%H:%M:%S')}\n💾 Будет сохранено в: {self.filename}")
        self.recording = True
        self.last_mouse_position = None

    def stop_recording(self):
        if not self.recording:
            return

        self.recording = False
        self.total_duration = time.perf_counter() - self.start_time

        self.save_recording(self.filename)
        duration_sec = time.time() - self.record_start_time
        print(f"Запись остановлена. Длительность: {duration_sec:.2f} сек\nСобытий записано: мышь - {len(self.mouse_events)}, клавиатура - {len(self.keyboard_events)}")
        show_notification(f"Запись остановлена. Длительность: {duration_sec:.2f} сек\nСобытий записано: мышь - {len(self.mouse_events)}, клавиатура - {len(self.keyboard_events)}")


    def save_recording(self, filename):
        save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'routes_out')
        if not os.path.exists(save_path):
            os.makedirs(save_path)
        full_filename = os.path.join(save_path, filename)

        data = {
            "mouse_events": [asdict(event) for event in self.mouse_events],
            "keyboard_events": [asdict(event) for event in self.keyboard_events],
            "total_duration": self.total_duration,
            "record_date": datetime.now().isoformat(),
            "metadata": {
                "mouse_events_count": len(self.mouse_events),
                "keyboard_events_count": len(self.keyboard_events),
                "recording_mode": "relative_mouse"
            }
        }

        with open(full_filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"✅ Запись сохранена: {filename}")

    def on_mouse_move(self, x, y):
        if not self.recording:
            return

        timestamp = time.perf_counter() - self.start_time

        if self.last_mouse_position:
            dx = x - self.last_mouse_position[0]
            dy = y - self.last_mouse_position[1]

            if dx != 0 or dy != 0:
                event = MouseEvent(
                    type="move_relative",
                    dx=dx,
                    dy=dy,
                    timestamp=timestamp
                )
                self.mouse_events.append(event)

        self.last_mouse_position = (x, y)

    def on_mouse_click(self, x, y, button, pressed):
        if not self.recording:
            return
        timestamp = time.perf_counter() - self.start_time
        event = MouseEvent(
            type="click",
            button=button.name,
            pressed=pressed,
            timestamp=timestamp
        )
        self.mouse_events.append(event)

    def on_mouse_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        timestamp = time.perf_counter() - self.start_time
        event = MouseEvent(
            type="scroll",
            dx=dx,
            dy=dy,
            timestamp=timestamp
        )
        self.mouse_events.append(event)

    def on_key_press(self, key):
        if not self.recording:
            return

        if key == Key.f9 or key == Key.f10:
            return

        timestamp = time.perf_counter() - self.start_time
        key_str = self._key_to_string(key)
        event = KeyboardEvent(
            type="press",
            key=key_str,
            pressed=True,
            timestamp=timestamp
        )
        self.keyboard_events.append(event)

    def on_key_release(self, key):
        if not self.recording:
            return

        if key == Key.f9 or key == Key.f10:
            return

        timestamp = time.perf_counter() - self.start_time
        key_str = self._key_to_string(key)
        event = KeyboardEvent(
            type="release",
            key=key_str,
            pressed=False,
            timestamp=timestamp
        )
        self.keyboard_events.append(event)

    def _key_to_string(self, key):
        if isinstance(key, KeyCode):
            if key.char and key.char.isalpha():
                vk_code = key.vk
                if 65 <= vk_code <= 90:
                    return chr(vk_code).lower()
                elif vk_code >= 192 and vk_code <= 255:
                    russian_to_english = {
                        192: 'a', 193: 'b', 194: 'c', 195: 'd', 196: 'e', 197: 'f', 198: 'g', 199: 'h',
                        200: 'i', 201: 'j', 202: 'k', 203: 'l', 204: 'm', 205: 'n', 206: 'o', 207: 'p',
                        208: 'q', 209: 'r', 210: 's', 211: 't', 212: 'u', 213: 'v', 214: 'w', 215: 'x',
                        216: 'y', 217: 'z', 218: '[', 219: ']', 220: '\\', 221: ';', 222: "'", 223: '`'
                    }
                    return russian_to_english.get(vk_code, f"KeyCode({vk_code})")
            return key.char if key.char else f"KeyCode({key.vk})"
        elif isinstance(key, Key):
            if key == Key.shift:
                return 'shift_l'
            elif key == Key.ctrl:
                return 'ctrl_l'
            elif key == Key.alt:
                return 'alt_l'
            else:
                return key.name
        else:
            return str(key)

    def on_hotkey_press(self, key):
        if key == Key.f9:
            self.start_recording()
        elif key == Key.f10:
            self.stop_recording()
            self.cleanup()

    def start_background_recording(self):
        print("🎮 Gameplay Recorder запущен в фоновом режиме")
        print("=== Горячие клавиши ===")
        print("F9 - Начать запись")
        print("F10 - Остановить запись")
        print("=======================")

        self.mouse_listener = MouseListener(
            on_move=self.on_mouse_move,
            on_click=self.on_mouse_click,
            on_scroll=self.on_mouse_scroll
        )

        self.keyboard_listener = KeyboardListener(
            on_press=self.on_key_press,
            on_release=self.on_key_release
        )

        self.hotkey_listener = KeyboardListener(
            on_press=self.on_hotkey_press
        )

        self.mouse_listener.start()
        self.keyboard_listener.start()
        self.hotkey_listener.start()

        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.cleanup()

    def cleanup(self):
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        print("\n👋 Recorder остановлен")


class GameplayPlayer():
    def __init__(self, replay_file, cooldown=0, sensitivity=1.0):
        self.replay_file = replay_file
        self.cooldown = cooldown
        self.sensitivity = sensitivity
        self.mouse_controller = LowLevelMouseController()
        self.keyboard_controller = LowLevelKeyboardController()  # Теперь использует скан-коды
        self.events = []

    def load_replay(self):
        if not os.path.exists(self.replay_file):
            raise FileNotFoundError(f"Файл реплея не найден: {self.replay_file}")

        with open(self.replay_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        events = []

        for mouse_event in data['mouse_events']:
            events.append(('mouse', mouse_event))

        for keyboard_event in data['keyboard_events']:
            events.append(('keyboard', keyboard_event))

        self.events = sorted(events, key=lambda x: x[1]['timestamp'])

        print(f"📁 Загружен реплей: {self.replay_file}")
        print(f"📊 Событий: {len(self.events)}")
        print(f"⏱️ Длительность: {data['total_duration']:.2f} сек")
        if 'record_date' in data:
            print(f"📅 Дата записи: {data['record_date']}")
        print(f"🎯 Чувствительность мыши: {self.sensitivity}")
        print("🎯 Режим ввода: низкоуровневый WinAPI")

    def _get_vk_code(self, key_str):
        if key_str.startswith("KeyCode("):
            vk = int(key_str[8:-1])
            return vk

        key_lower = key_str.lower()
        if key_lower in self.key_map:
            return self.key_map[key_lower]

        if len(key_str) == 1:
            return ord(key_str.upper())

        print(f"⚠️ Неизвестная клавиша: {key_str}")
        return None

    def play(self):
        if not self.events:
            self.load_replay()

        if self.cooldown > 0:
            print(f"⏳ Ожидание {self.cooldown} секунд перед воспроизведением...")
            time.sleep(self.cooldown)
            print("🎬 Старт!")

        start_time = time.perf_counter()
        events_played = 0

        for event_type, event_data in self.events:
            current_time = time.perf_counter() - start_time
            target_time = event_data['timestamp']

            if current_time < target_time:
                time.sleep(target_time - current_time)

            if event_type == 'mouse':
                self._handle_mouse_event(event_data)
            else:
                self._handle_keyboard_event(event_data)

            events_played += 1
            if events_played % 100 == 0:
                progress = (events_played / len(self.events)) * 100
                print(f"📈 Прогресс: {progress:.1f}% ({events_played}/{len(self.events)})")

        print(f"✅ Воспроизведение завершено! Событий обработано: {events_played}")

    def _handle_mouse_event(self, event):
        try:
            if event['type'] == 'move_relative':
                original_dx = event['dx']
                original_dy = event['dy']
                dx = original_dx * self.sensitivity
                dy = original_dy * self.sensitivity

                if not hasattr(self, 'mouse_accumulator_x'):
                    self.mouse_accumulator_x = 0.0
                    self.mouse_accumulator_y = 0.0

                self.mouse_accumulator_x += dx
                self.mouse_accumulator_y += dy

                dx_int = int(self.mouse_accumulator_x)
                dy_int = int(self.mouse_accumulator_y)

                self.mouse_accumulator_x -= dx_int
                self.mouse_accumulator_y -= dy_int

                if dx_int == 0 and abs(self.mouse_accumulator_x) >= 0.8:
                    dx_int = 1 if self.mouse_accumulator_x > 0 else -1
                    self.mouse_accumulator_x -= dx_int

                if dy_int == 0 and abs(self.mouse_accumulator_y) >= 0.8:
                    dy_int = 1 if self.mouse_accumulator_y > 0 else -1
                    self.mouse_accumulator_y -= dy_int

                if dx_int != 0 or dy_int != 0:
                    self.mouse_controller.move_relative(dx_int, dy_int)

            elif event['type'] == 'click':
                self.mouse_controller.click(event['button'], event['pressed'])
            elif event['type'] == 'scroll':
                self.mouse_controller.scroll(event['dx'], event['dy'])
        except Exception as e:
            print(f"⚠️ Ошибка обработки события мыши: {e}")

    def _handle_keyboard_event(self, event):
        try:
            if event['pressed']:
                self.keyboard_controller.press_key(event['key'])
            else:
                self.keyboard_controller.release_key(event['key'])
        except Exception as e:
            print(f"⚠️ Ошибка обработки события клавиатуры {event['key']}: {e}")


def main():
    parser = argparse.ArgumentParser(description='🎮 Gameplay Recorder and Player')
    subparsers = parser.add_subparsers(dest='command', help='Команда')

    record_parser = subparsers.add_parser('record', help='Запустить фоновый рекордер')

    play_parser = subparsers.add_parser('play', help='Воспроизвести запись')
    play_parser.add_argument('filename', help='Файл с записью для воспроизведения')
    play_parser.add_argument('--cooldown', type=float, default=3,
                             help='Задержка перед воспроизведением в секундах (по умолчанию: 3)')
    play_parser.add_argument('--sens', type=float, default=1.0,
                             help='Чувствительность мыши (множитель, по умолчанию: 1.0)')

    args = parser.parse_args()

    if args.command == 'record':
        recorder = BackgroundRecorder()
        recorder.start_background_recording()

    elif args.command == 'play':
        player = GameplayPlayer(args.filename, args.cooldown, args.sens)
        player.play()

    else:
        parser.print_help()
        print("\nПримеры использования:")
        print("  python replayer.py record                    - Запуск фонового рекордера")
        print("  python replayer.py play gameplay.json        - Воспроизведение с задержкой 3 сек")
        print("  python replayer.py play gameplay.json --cooldown 5  - Воспроизведение с задержкой 5 сек")
        print("  python replayer.py play gameplay.json --sens 0.7    - Воспроизведение с чувствительностью 0.7")


if __name__ == "__main__":
    main()