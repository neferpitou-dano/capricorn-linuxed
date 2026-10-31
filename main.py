import json
import datetime as dt
import logging
import os
import queue
import random
import re
import sys
import tempfile
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")

# журнал — до всех библиотек: если какой-то не хватает, Капри падает без окна, и узнать причину можно только тут
logging.basicConfig(filename=os.path.join(BASE, "capri.log"), level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("capri")
log.info("Python %s", sys.version.split()[0])


def fatal(text):
    """Ошибку вывести в Linux-терминал."""
    log.error(text)
    if os.name != "nt":
        print("Капри не запустилась:\n" + text, file=sys.stderr)
        sys.exit(1)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Капри не запустилась", 0x10)
    except Exception:
        pass
    sys.exit(1)


try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QMenu, QSlider,
                                   QSystemTrayIcon, QWidget, QWidgetAction)

    from actions import CAPRI_URL, Actions, Reply, parse_number
    from ai import AI
    from local_ai import LocalAI
    from models_dialog import ModelsDialog
    from persona import LINES, gendered, line, set_user
    from avatar import Avatar
    import zapret
    from voice import (Listener, Speaker, default_input_name, google_stt, input_devices, make_glitch_sound,
                       play_sound_async)
except ImportError as e:
    log.exception("Не хватает библиотек")
    installer = "fish ./install.fish" if os.name != "nt" else "setup.bat"
    launcher = "fish ./run.fish" if os.name != "nt" else "run.bat"
    fatal(f"Не установлены нужные библиотеки ({e.name or e}).\n\nЗапусти {installer}, затем повтори запуск: {launcher}.\n\nPython {sys.version.split()[0]}")

DEFAULTS = {
    "enable_ai": False,
    "local_ai": False,
    "local_model": "qwen3:4b",
    "barge_in": True,
    "mic_device": None,
    "chatter": True,
    "chatter_min_minutes": 15,
    "chatter_max_minutes": 40,
    "roam_chance": 0.5,
    "teleport_on_answer": True,
    "search_when_unsure": True,
    "voice_volume": 100,     # громкость голоса Капри 0..100
    "mic_gain": 1.0,         # громкость микрофона для Капри: 1 — как есть, 2 — вдвое громче
    "mic_sensitivity": 100,  # 100 — слышит всё; меньше — тихий шум и фон отбрасываются
    "user_name_mode": "none",  # как называть пользователя: "custom" — user_name, "windows" — имя пользователя
                               # "pc" — имя компьютера, "none" — не называть
    "user_name": "",
    "user_gender": "male",     # "male" / "female" — в каком роде Капри к тебе обращается
    "browser": "",
    "telegram_client": "",  # пусто — найти Telegram автоматически; иначе путь к .desktop или cmd:команда
    "telegram_client_name": "Telegram",
    "replies": {},           # свои ответы: {"фраза": "что ответит Капри"} — проверяются раньше команд и нейросети  # в каком браузере открывать сайты: "хром", "яндекс", "опера"… или путь к exe; "" — как в Windows
    "custom_commands": {},  # пользовательские голосовые команды: фраза -> тип действия и цель
    "provider_preferences": {},  # выбранные пользователем провайдеры: music/browser/stt/tts
    "anthropic_api_key": "",
    "model": "claude-opus-5-5",
    "wake_words": ["капри", "каприз", "капре", "кепри", "капли"],
    "use_google_stt": True,
    "voice": "ru-RU-SvetlanaNeural",
    "tts_engine": "edge",
    "voice_rate": "+10%",
    "voice_pitch": "+15Hz",
    "voice_shift": 0,
    "voice_lively": False,
    "voice_fx": 1.8,
    "listen_window_seconds": 8,
    "followup_seconds": 6,
    "hide_after_seconds": 12,
    "greet_on_start": True,
    "vosk_model": "models/vosk-model-small-ru-0.22",
    "sites": {
        "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
        "гугл": "https://www.google.com", "google": "https://www.google.com",
        "яндекс": "https://ya.ru", "вк": "https://vk.com", "вконтакте": "https://vk.com",
        "почту": "https://mail.google.com", "почта": "https://mail.google.com",
        "гитхаб": "https://github.com", "твич": "https://www.twitch.tv",
        "википедию": "https://ru.wikipedia.org", "википедия": "https://ru.wikipedia.org",
        "переводчик": "https://translate.google.com", "карты": "https://yandex.ru/maps",
        "погоду": "https://yandex.ru/pogoda", "chatgpt": "https://chatgpt.com",
        "клод": "https://claude.ai", "кинопоиск": "https://www.kinopoisk.ru",
        "авито": "https://www.avito.ru", "озон": "https://www.ozon.ru",
        "вайлдберриз": "https://www.wildberries.ru"
    },
    "apps": {
        "блокнот": "notepad", "калькулятор": "calc", "проводник": "explorer",
        "диспетчер задач": "taskmgr", "параметры": "ms-settings:", "настройки": "ms-settings:",
        "панель управления": "control", "командную строку": "cmd", "командная строка": "cmd",
        "терминал": "wt", "пейнт": "mspaint", "paint": "mspaint", "ворд": "winword",
        "эксель": "excel", "браузер": "https://www.google.com",
        "настройки звука": "ms-settings:sound", "настройки экрана": "ms-settings:display",
        "корзину": "shell:RecycleBinFolder", "загрузки": "shell:Downloads"
    },
    "app_aliases": {
        "хром": "chrome", "гугл хром": "google chrome", "стим": "steam", "дискорд": "discord",
        "телеграм": "telegram", "телеграмм": "telegram", "спотифай": "spotify",
        "опера": "opera", "яндекс браузер": "yandex", "вс код": "visual studio code",
        "визуал студио код": "visual studio code", "обс": "obs", "эпик геймс": "epic games",
        "ватсап": "whatsapp", "зум": "zoom", "фотошоп": "photoshop", "майнкрафт": "minecraft"
    }
}


def _config_blank():
    """config.json нет или он пустой (всё стёрли) — такой можно спокойно создать заново."""
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            return not f.read().strip()
    except FileNotFoundError:
        return True


def _edit_config(change):
    """Прочитать config.json, применить change(data) и записать. Файл с ошибкой не перезаписываем —
    иначе пропадут правки пользователя. Возвращает текст ошибки или None."""
    try:
        if _config_blank():
            data = {}
        else:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:  # Блокнот иногда сохраняет с BOM
                data = json.load(f)
    except Exception:
        log.exception("config.json с ошибкой, не записываю")
        return "в config.json ошибка. Исправь её или удали файл, он создастся заново."
    change(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return None


def save_setting(key, value):
    """Записать одну настройку в config.json, не трогая остальные."""
    _edit_config(lambda data: data.__setitem__(key, value))


def save_entry(section, name, value):
    """Свой сайт или программа: section "sites"/"apps". value=None — убрать; встроенные записи
    убираются пометкой null, иначе вернулись бы из настроек по умолчанию."""
    def change(data):
        entries = data.setdefault(section, {})
        if value is None and name not in DEFAULTS.get(section, {}):
            entries.pop(name, None)
        else:
            entries[name] = value
    return _edit_config(change)


def user_display_name(cfg):
    """Как Капри называет пользователя — по настройке user_name_mode."""
    mode = cfg.get("user_name_mode", "none")
    if mode == "custom":
        return cfg.get("user_name", "").strip()
    if mode == "windows":
        return os.environ.get("USERNAME", "")
    if mode == "pc":
        return os.environ.get("COMPUTERNAME", "")
    return ""


def apply_user(cfg):
    set_user(user_display_name(cfg), cfg.get("user_gender", "male"))


CONFIG_ERROR = ""  # что сказать вслух, если config.json не прочитался


def load_config():
    global CONFIG_ERROR
    CONFIG_ERROR = ""
    cfg = json.loads(json.dumps(DEFAULTS))
    if not _config_blank():
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    for name, value in v.items():  # null — убранная встроенная запись
                        if value is None:
                            cfg[k].pop(name, None)
                        else:
                            cfg[k][name] = value
                else:
                    cfg[k] = v
            missing = [k for k in DEFAULTS if k not in user]
            if missing:  # файл неполный (его создал setup_ai.bat или он из старой версии) — дописываем
                _edit_config(lambda data: [data.setdefault(k, DEFAULTS[k]) for k in missing])
                log.info("В config.json добавлены недостающие настройки: %s", ", ".join(missing))
        except json.JSONDecodeError as e:
            log.error("config.json с ошибкой (строка %d, символ %d: %s), использую настройки по умолчанию",
                      e.lineno, e.colno, e.msg)
            CONFIG_ERROR = (f"В config.json ошибка в строке {e.lineno}, я его не прочитала и работаю "
                            "со стандартными настройками. Проверь там кавычки, запятые и двойные слеши в путях.")
        except Exception:
            log.exception("config.json не прочитался, использую настройки по умолчанию")
            CONFIG_ERROR = "Не смогла прочитать config.json, работаю со стандартными настройками."
    else:  # файла нет или его содержимое стёрли — создаём заново со стандартными настройками
        log.info("config.json пустой или удалён, создаю заново")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, ensure_ascii=False, indent=2)
    return cfg


STOP_RE = re.compile(r"\b(стоп|хватит|молчи|замолчи|помолчи|достаточно|подожди|остановись|перестань|"
                     r"заткнись|умолкни)\b")


def fullscreen_app_active():
    """Открыта игра или видео на весь экран — тогда не лезем со спонтанными репликами."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False  # рабочий стол
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.right - r.left) >= u.GetSystemMetrics(0) and (r.bottom - r.top) >= u.GetSystemMetrics(1)
    except Exception:
        return False


class Capri(QObject):
    summon = Signal()
    vanish = Signal()
    mode = Signal(str, float)
    subtitle = Signal(str)
    quit_app = Signal()
    anim = Signal(str)
    roam = Signal()
    hop = Signal()
    move_to = Signal(str)
    text_input_requested = Signal()
    package_input_requested = Signal()
    afk_changed = Signal(bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        # Применяем сохранённые голосом предпочтения до создания движков.
        prefs = cfg.get("provider_preferences", {})
        if prefs.get("tts") in ("edge", "silero"):
            cfg["tts_engine"] = prefs["tts"]
        if prefs.get("stt") in ("google", "vosk"):
            cfg["use_google_stt"] = prefs["stt"] == "google"
        if prefs.get("browser"):
            cfg["browser"] = prefs["browser"]
        words = sorted(cfg["wake_words"], key=len, reverse=True)
        self.wake_re = re.compile(r"(^|\s)(" + "|".join(map(re.escape, words)) + r")(?=\s|$|[,.!?])",
                                  re.IGNORECASE)
        self.ai = AI(cfg)
        self.local = LocalAI(cfg) if cfg.get("local_ai") else None
        if self.local:
            self.local.on_model_change = self._remember_model
        self.actions = Actions(cfg, BASE, self.ai, save=save_entry)
        self.config_mtime = self._config_mtime()
        self.zapret_checked = False  # предлагали ли уже исправить zapret в этом сеансе
        self.actions.local = self.local
        self.speaker = Speaker(cfg)
        self.actions.speaker = self.speaker
        self.jobs = queue.Queue()
        self.awake_until = 0.0
        self.afk = False
        self.last_partial_wake = 0.0
        self.speaking = False
        self.interrupted = False
        self.speech_words = set()
        self.speech_end = 0.0
        self.last_activity = time.time()
        self.recent_chatter = []
        self.listener = None
        self.glitch_wav = os.path.join(tempfile.gettempdir(), "capri_glitch.wav")
        try:
            make_glitch_sound(self.glitch_wav, self.speaker.volume)
        except Exception:
            self.glitch_wav = None
        self.reminders_path = os.path.join(BASE, "reminders.json")
        self.reminder_state = None  # None / content / when
        self.reminder_content = ""
        self.reminder_lock = threading.Lock()
        threading.Thread(target=self._reminder_watch, daemon=True, name="CapriReminders").start()

    def start(self):
        threading.Thread(target=self._worker, daemon=True).start()
        if self.local:
            threading.Thread(target=self.local.start_server, daemon=True).start()
        model =os.path.join(BASE, self.cfg["vosk_model"])
        if os.path.isdir(model):
            self.listener = Listener(model, self.on_final, self.on_partial, self.on_listener_error,
                                     device_name=self.cfg.get("mic_device") or None,
                                     gain=float(self.cfg.get("mic_gain", 1.0)),
                                     sensitivity=int(self.cfg.get("mic_sensitivity", 100)))
            self.listener.start()
        else:
            self.jobs.put(("say", line("no_model")))
            return
        # вступление: анимация capri + приветствие
        self.jobs.put(("say", line("greet") if self.cfg.get("greet_on_start") else "", "capri"))
        if CONFIG_ERROR:
            self.jobs.put(("say", CONFIG_ERROR))
        threading.Thread(target=self._chatter_loop, daemon=True).start()  # сам проверяет, включено ли
        threading.Thread(target=self._prewarm_voice, daemon=True).start()

    def _prewarm_voice(self):
        """Через полминуты после запуска озвучиваем в фоне постоянные фразы и свои ответы — потом они
        звучат сразу, без ожидания сервера Microsoft."""
        time.sleep(30)
        static = [gendered(t) for variants in LINES.values() for t in variants if "{" not in t]
        static += list(self.cfg.get("replies", {}).values())
        self.speaker.prewarm(static)

    # ----- спонтанные появления -----
    def _chatter_loop(self):
        def next_delay():
            lo, hi = self.cfg["chatter_min_minutes"], self.cfg["chatter_max_minutes"]
            return random.uniform(lo, max(lo, hi)) * 60
        due = time.time() + next_delay()
        while True:
            time.sleep(15)
            now = time.time()
            if not self.cfg.get("chatter"):  # выключено галочкой в трее — ждём, отсчёт начнётся заново
                due = now + next_delay()
                continue
            if self.afk:  # пока Капри сидит, повторно сама не появляется
                due = now + next_delay()
                continue
            if now < due:
                continue
            quiet = now - self.last_activity > 120 and self.jobs.empty() and not self.speaking
            if quiet and not fullscreen_app_active():
                self.jobs.put(("chatter",))
                due = now + next_delay()
            else:
                due = now + 60  # занят или в игре/фильме — попробуем позже

    def chatter_line(self):
        hour = time.localtime().tm_hour
        text = None
        if 0 <= hour < 5 and random.random() < 0.5:
            text = line("chatter_night")
        elif 6 <= hour < 11 and random.random() < 0.3:
            text = line("chatter_morning")
        elif self.local and random.random() < 0.4:
            text = self.local.chatter()
        for _ in range(5):
            if text and text not in self.recent_chatter:
                break
            text = line("chatter")
        self.recent_chatter = (self.recent_chatter + [text])[-6:]
        return text

    # ----- события микрофона (поток слушателя) -----
    def has_wake(self, text):
        return bool(self.wake_re.search(text.lower()))

    def strip_wake(self, text):
        t = text.lower()
        last = None
        for last in self.wake_re.finditer(t):
            pass
        if last and t[last.end():].strip(" ,.!?"):
            t = t[last.end():]  # берём то, что сказано после имени (до него могло быть эхо)
        t = self.wake_re.sub(" ", t)
        return re.sub(r"^[\s,.!?]+|[\s,.!?]+$", "", t)

    def appear(self, seconds):
        if self.glitch_wav:
            play_sound_async(self.glitch_wav)
        self.summon.emit()
        self.mode.emit("listening", float(seconds))

    # ----- перебивание -----
    def is_echo(self, text):
        """Микрофон слышит голос самой Капри: все слова фразы есть в том, что она говорит."""
        words = text.lower().replace("ё", "е").split()
        return bool(words) and all(w in self.speech_words for w in words)

    def wants_interrupt(self, text):
        t = text.lower()
        return (self.has_wake(t) or STOP_RE.search(t)) and not self.is_echo(t)

    def interrupt(self):
        if not self.speaking or self.interrupted:
            return
        self.interrupted = True
        self.speaker.stop()
        self.awake_until = float("inf")
        self.mode.emit("listening", 0.0)
        self.subtitle.emit(line("listen"))
        log.info("Капри перебили")

    def on_partial(self, text):
        now = time.time()
        if self.speaking:
            if self.cfg.get("barge_in") and self.wants_interrupt(text):
                self.interrupt()
            return
        if now - self.last_partial_wake > 3 and now > self.awake_until and self.has_wake(text):
            self.last_partial_wake = now
            self.appear(0.0)

    def on_final(self, text, audio):
        now = time.time()
        if self.speaking and not self.interrupted:
            if not (self.cfg.get("barge_in") and self.wants_interrupt(text)):
                return  # эхо собственной речи
            self.interrupt()
        if now - self.speech_end < 1.0 and self.is_echo(text):
            return
        woke = self.has_wake(text)
        if not woke and now > self.awake_until:
            return
        cmd = self.strip_wake(text) if woke else text
        if STOP_RE.match(cmd.strip()) and len(cmd.split()) <= 3:
            # «Капри, стоп» — замолчать и слушать дальше
            self.awake_until = now + self.cfg["listen_window_seconds"]
            return
        if len(cmd) < 2:
            self.awake_until = now + self.cfg["listen_window_seconds"]
            self.subtitle.emit("")
            if now - self.last_partial_wake > 3:
                self.appear(0.0)
            return
        self.awake_until = 0
        self.jobs.put(("voice", cmd, audio, woke))

    def on_listener_error(self, err, kind="mic"):
        self.jobs.put(("say", line("model_broken" if kind == "model" else "no_mic")))

    def set_profile(self, mode, name, gender):
        """«Как ко мне обращаться…»: как называть пользователя и в каком роде. Сразу и запоминается."""
        for key, value in (("user_name_mode", mode), ("user_name", name), ("user_gender", gender)):
            self.cfg[key] = value
            save_setting(key, value)
        apply_user(self.cfg)
        log.info("Обращение: %s, %s", user_display_name(self.cfg) or "без имени", gender)

    def set_chatter(self, on):
        """Галочка в трее: появляется ли Капри сама (поболтать, погулять по экрану). Сразу и запоминается."""
        self.cfg["chatter"] = bool(on)
        save_setting("chatter", bool(on))
        log.info("Самостоятельные появления: %s", "включены" if on else "выключены")
    def set_enable_ai(self, on):
        """Галочка в трее: разрешать ли обращения к ИИ."""
        self.cfg["enable_ai"] = bool(on)
        save_setting("enable_ai", bool(on))
        log.info("ИИ: %s", "включён" if on else "выключен")
    def set_search_when_unsure(self, on):
        """Галочка в трее: открывать ли Google, когда Капри не знает ответа. Сразу и запоминается."""
        self.cfg["search_when_unsure"] = bool(on)
        save_setting("search_when_unsure", bool(on))
        log.info("Поиск в браузере, если не знаю: %s", "включён" if on else "выключен")

    def _remember_model(self, name):
        """Ollama нашла другую скачанную модель — записываем её, чтобы не искать при каждом запуске."""
        self.cfg["local_model"] = name
        save_setting("local_model", name)

    # ----- config.json на лету -----
    @staticmethod
    def _config_mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def reload_config_if_changed(self, speak=True):
        """Правки config.json (сайты, программы, названия) подхватываются без перезапуска.
        speak=False — из окна в трее: там говорить нельзя (речь блокирует окно)."""
        mtime = self._config_mtime()
        if mtime == self.config_mtime:
            return
        fresh = load_config()
        if CONFIG_ERROR:  # с ошибкой — работаем как было и говорим, где ошибка
            if speak:
                self.config_mtime = mtime
                self.say(CONFIG_ERROR)
            return
        self.config_mtime = mtime
        for key in ("sites", "apps", "app_aliases", "search_when_unsure", "browser", "chatter", "replies", "custom_commands",
                    "provider_preferences", "use_google_stt", "tts_engine", "user_name_mode", "user_name", "user_gender"):
            self.cfg[key] = fresh[key]
        prefs = self.cfg.get("provider_preferences", {})
        if prefs.get("tts") in ("edge", "silero"):
            self.cfg["tts_engine"] = prefs["tts"]
        if prefs.get("stt") in ("google", "vosk"):
            self.cfg["use_google_stt"] = prefs["stt"] == "google"
        if prefs.get("browser"):
            self.cfg["browser"] = prefs["browser"]
        self.speaker.engine = self.cfg.get("tts_engine", "edge")
        self.actions.set_lists(self.cfg)
        apply_user(self.cfg)
        log.info("config.json перечитан")

    # ----- кнопки -----
    # ----- микрофон -----
    def current_mic(self):
        if self.listener and self.listener.active_name:
            return self.listener.active_name
        return self.cfg.get("mic_device") or default_input_name()

    def set_mic(self, name, announce=True):
        """Сменить устройство ввода на ходу и запомнить выбор (None — как в Windows по умолчанию)."""
        self.cfg["mic_device"] = name
        save_setting("mic_device", name)
        if self.listener:
            self.listener.set_device(name)
        log.info("Выбран микрофон: %s", name or "по умолчанию")
        if announce:
            self.jobs.put(("say", line("mic_set", t=name or "микрофон Windows по умолчанию")))

    def set_mic_tuning(self, gain=None, sensitivity=None):
        """Громкость ввода и чувствительность микрофона для Капри — сразу и запоминаются."""
        if gain is not None:
            self.cfg["mic_gain"] = round(float(gain), 2)
            save_setting("mic_gain", self.cfg["mic_gain"])
            if self.listener:
                self.listener.gain = self.cfg["mic_gain"]
        if sensitivity is not None:
            self.cfg["mic_sensitivity"] = int(sensitivity)
            save_setting("mic_sensitivity", self.cfg["mic_sensitivity"])
            if self.listener:
                self.listener.sensitivity = self.cfg["mic_sensitivity"]

    def set_voice_fx(self, strength):
        """Сила цифровых эффектов голоса: 0 — чистый голос, 1.8 — как задумано, больше — сильнее."""
        self.speaker.fx = round(float(strength), 2)
        self.cfg["voice_fx"] = self.speaker.fx
        save_setting("voice_fx", self.speaker.fx)

    def set_voice_volume(self, volume):
        """Громкость голоса Капри 0..100 — только её, общая громкость Windows не меняется."""
        self.speaker.volume = int(volume)
        self.cfg["voice_volume"] = int(volume)
        save_setting("voice_volume", int(volume))
        if self.glitch_wav:
            try:
                make_glitch_sound(self.glitch_wav, self.speaker.volume)
            except Exception:
                pass

    def voice_volume_command(self, cmd):
        """Меняет громкость речи Капри на указанное число процентных пунктов.

        Например, при текущих 50% команда «говори на 14% громче» даст 64%,
        а не установит громкость на 14%. Системную громкость не меняет.
        """
        c = cmd.lower().replace("ё", "е")
        if not re.search(r"\b(говори|говорить|голос|разговаривай|озвучивай)\b", c):
            return None
        louder = bool(re.search(r"громче|громк\w*|погромче", c))
        quieter = bool(re.search(r"тише|потише", c))
        if not louder and not quieter:
            return None

        # Число задаёт величину изменения в процентных пунктах, не целевую громкость.
        # Без числа используем шаг 10 п.п.; итоговый уровень всегда ограничен диапазоном 0..100.
        amount = parse_number(c)
        if amount is None:
            amount = 10
        amount = max(0, min(100, amount))
        current = max(0, min(100, int(self.speaker.volume)))
        delta = amount if louder else -amount
        target = max(0, min(100, current + delta))
        if target == current:
            message = f"Моя громкость уже {target}%."
        else:
            self.set_voice_volume(target)
            message = f"Готово! Громкость моего голоса теперь {target}%."
        return Reply(message, "voice_volume")

    def mic_command(self, action):
        if action == "mic_info":
            return Reply(line("mic_info", t=self.current_mic()))
        devices = [n for n, _ in input_devices()]
        if len(devices) < 2:
            return Reply(line("mic_only_one", t=self.current_mic()))
        cur = self.cfg.get("mic_device") or self.current_mic()
        idx = next((i for i, n in enumerate(devices) if n == cur or cur.startswith(n) or n.startswith(cur)), -1)
        nxt = devices[(idx + 1) % len(devices)]
        self.set_mic(nxt, announce=False)
        return Reply(line("mic_set", t=nxt))

    def call_from_click(self):
        if self.afk:
            self.afk = False
            self.afk_changed.emit(False)
        if self.speaking:
            self.interrupt()
            return
        self.awake_until = float("inf")
        self.appear(0.0)
        self.subtitle.emit(line("listen"))

    def text_command(self, text):
        self.summon.emit()
        self.jobs.put(("text", text))

    # ----- рабочий поток -----
    def _worker(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while True:
            job = self.jobs.get()
            try:
                self._process(job)
            except Exception:
                log.exception("Ошибка обработки %s", job[0])
                self.say(line("crash"))

    def _process(self, job):
        kind = job[0]
        if kind == "reminder_due":
            self.summon.emit()
            self.awake_until = 0
            self.say("Напоминание! " + str(job[1]))
            self.mode.emit("idle", 0.0)
            return
        if kind == "chatter":
            # Самостоятельное появление: одна реплика, затем фиксированная AFK-поза.
            self.summon.emit()
            interrupted = self.say(self.chatter_line())
            if not interrupted:
                self.awake_until = 0
                self.afk = True
                self.afk_changed.emit(True)
                self.mode.emit("idle", 0.0)
            return
        self.last_activity = time.time()
        if kind == "say":
            self.summon.emit()
            if len(job) > 2:
                self.anim.emit(job[2])
            if not job[1]:
                self.mode.emit("idle", 0.0)
                return
            if not self.say(job[1]):
                self.mode.emit("idle", 0.0)
            return
        if kind == "voice":
            _, cmd, audio, woke = job
            if self.afk:
                self.afk = False
                self.afk_changed.emit(False)
            self.summon.emit()
            self.mode.emit("thinking", 0.0)
            self.subtitle.emit("> " + cmd)
            if self.cfg.get("use_google_stt"):
                better = google_stt(audio)
                if better:
                    better = better.lower()
                    if len(better) >= 2:
                        cmd = better
        else:
            cmd = job[1]

        # Пока идёт создание напоминания, следующие фразы считаются ответами на вопросы Капри.
        if self.reminder_state == "content":
            normalized = re.sub(r"[.!?,]+", " ", cmd.lower().replace("ё", "е"))
            end_match = re.search(r"\bконец\s+замет(?:к\w*)?\b", normalized)
            if end_match:
                # Убираем завершающую фразу по её позиции в исходном распознанном тексте.
                raw_match = re.search(r"конец\s+замет(?:к\w*)?", cmd, re.IGNORECASE)
                content_part = cmd[:raw_match.start()].strip(" ,.!?\t\n") if raw_match else ""
                content = (self.reminder_content + " " + content_part).strip()
                if not content:
                    self.reminder_state = "content"
                    self.awake_until = float("inf")
                    self.say("Я не расслышала содержимое. Повтори напоминание и в конце скажи: конец заметки.")
                    self.awake_until = float("inf")
                    self.mode.emit("listening", 0.0)
                    return
                self.reminder_content = content
                self.reminder_state = "when"
                self.awake_until = float("inf")
                self.say("Когда напомнить? Например: через 10 минут, завтра в 9:30 или сегодня в 18:00.")
                self.awake_until = float("inf")
                self.mode.emit("listening", 0.0)
                return
            # Если маркер конца ещё не произнесён, накапливаем фрагменты распознавания.
            self.reminder_content = (self.reminder_content + " " + cmd.strip()).strip()
            self.awake_until = float("inf")
            self.mode.emit("listening", 0.0)
            return
        if self.reminder_state == "when":
            due = self._parse_reminder_time(cmd)
            if due is None:
                self.awake_until = float("inf")
                self.say("Не смогла понять время. Скажи, например: через 15 минут, завтра в 9:30 или сегодня в 18:00.")
                self.awake_until = float("inf")
                self.mode.emit("listening", 0.0)
                return
            content = self.reminder_content
            self._save_reminder(content, due)
            self.reminder_state = None
            self.reminder_content = ""
            self.awake_until = 0
            self.say(f"Напоминание сохранено. Напомню {due.strftime('%d.%m в %H:%M')}: {content}")
            self.mode.emit("idle", 0.0)
            return

        if self.has_wake(cmd):
            # имя могло остаться в тексте («капри нажми …»), если Капри уже слушала без него
            stripped = self.strip_wake(cmd)
            if len(stripped) < 2:
                # просто позвали по имени — не болтаем, а слушаем
                self.awake_until = float("inf")
                self.mode.emit("listening", 0.0)
                self.subtitle.emit(line("listen"))
                return
            cmd = stripped
        normalized_cmd = re.sub(r"[!?.,]+", " ", cmd.lower().replace("ё", "е")).strip()
        if re.fullmatch(r"(?:пожалуйста\s+)?сяд(?:ь|ьте)", normalized_cmd):
            if self.say("Хорошо, посижу."):
                return
            self.awake_until = 0
            self.afk = True
            self.afk_changed.emit(True)
            self.mode.emit("idle", 0.0)
            return

        log.info("Команда: %s", cmd)
        self.mode.emit("thinking", 0.0)
        self.subtitle.emit("> " + cmd)
        self.reload_config_if_changed()

        # Голосовой вызов поля ввода обрабатываем сигналом, чтобы Qt-окно
        # создавалось в главном GUI-потоке, а не в рабочем потоке распознавания.
        # Формулировки без слова «открой», чтобы их не путать с запуском приложений.
        text_input_phrases = (
            "давай текстом", "перейди в текстовый режим",
            "текстовый режим", "введем команду текстом",
            "введём команду текстом", "принимай текстовую команду",
            "дай ввести команду", "текстовая команда",
        )
        normalized_cmd = re.sub(r"[!?.,]+", " ", cmd.lower()).strip()
        if any(phrase in normalized_cmd for phrase in text_input_phrases):
            self.text_input_requested.emit()
            self.say("Включаю текстовый ввод.")
            self.awake_until = 0
            self.mode.emit("idle", 0.0)
            return

        reply = self.voice_volume_command(cmd)
        if reply is None:
            reply = self.actions.handle(cmd)
        if reply is not None and reply.action in ("mic_info", "mic_next"):
            reply = self.mic_command(reply.action)
        if reply is None:
                            if self.cfg.get("enable_ai"):
                                if self.ai and self.ai.available:
                                    answer = self.ai.ask(cmd)
                                else:
                                    answer = None
                                reply = Reply(answer, "answer") if answer else self.actions.fallback(cmd)
                            else:
                                reply = self.actions.fallback(cmd)
        if reply.action == "answer" and self.cfg.get("teleport_on_answer"):
            self.hop.emit()  # отвечает, скача по экрану красной молнией
        if reply.action == "wave":
            self.anim.emit("capri")
        elif reply.action and reply.action.startswith("move:"):
            self.move_to.emit(reply.action[5:])
        elif reply.action == "roam":
            self.vanish.emit()
            time.sleep(0.8)
            self.roam.emit()
            time.sleep(1.5)
        if self.say(reply.text):
            return  # перебили — уже слушаем новую команду

        if reply.action == "quit":
            self.quit_app.emit()
        elif reply.action == "package_input":
            self.package_input_requested.emit()
        elif reply.action == "reminder_start":
            self.reminder_state = "content"
            self.reminder_content = ""
            self.awake_until = float("inf")
            self.mode.emit("listening", 0.0)
        elif reply.action == "hide":
            self.vanish.emit()
        else:
            self.awake_until = 0
            self.mode.emit("idle", 0.0)

    @staticmethod
    def _parse_reminder_time(text):
        """Разбирает основные русские форматы времени без сторонних зависимостей."""
        s = re.sub(r"[,.!?]+", " ", str(text or "").lower().replace("ё", "е"))
        s = re.sub(r"\s+", " ", s).strip()
        now = dt.datetime.now().replace(second=0, microsecond=0)
        numbers = {"одну": 1, "одна": 1, "один": 1, "раз": 1, "две": 2, "два": 2,
                   "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
                   "восемь": 8, "девять": 9, "десять": 10, "полчаса": 30}
        rel = re.search(r"через\s+(?P<n>\d+|[а-я]+)\s+(?P<u>секунд\w*|минут\w*|час\w*|дн\w*)", s)
        if "через полчаса" in s:
            return now + dt.timedelta(minutes=30)
        if "через минуту" in s:
            return now + dt.timedelta(minutes=1)
        if "через час" in s:
            return now + dt.timedelta(hours=1)
        if rel:
            nraw, unit = rel.group("n"), rel.group("u")
            try: n = int(nraw)
            except ValueError: n = numbers.get(nraw)
            if n is not None:
                if unit.startswith("секунд"): result = now + dt.timedelta(seconds=n)
                elif unit.startswith("минут"): result = now + dt.timedelta(minutes=n)
                elif unit.startswith("час"): result = now + dt.timedelta(hours=n)
                else: result = now + dt.timedelta(days=n)
                return result.replace(second=0, microsecond=0)
        # Абсолютное время: «сегодня в 18:30», «завтра в 9», «в 21:00».
        match = re.search(r"(?:в\s*)?(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)?", s)
        if not match:
            return None
        hour = int(match.group(1)); minute = int(match.group(2) or 0); period = match.group(3)
        if minute > 59 or hour > 24 or (hour == 24 and minute != 0):
            return None
        if period == "утра" and hour == 12: hour = 0
        elif period in ("дня", "вечера") and hour < 12: hour += 12
        elif period == "ночи" and hour == 12: hour = 0
        elif hour > 23: return None
        day = now.date()
        if "послезавтра" in s: day += dt.timedelta(days=2)
        elif "завтра" in s: day += dt.timedelta(days=1)
        result = dt.datetime.combine(day, dt.time(hour, minute))
        if result <= now and "сегодня" in s:
            return None
        if result <= now and "завтра" not in s and "послезавтра" not in s:
            result += dt.timedelta(days=1)
        return result

    def _read_reminders(self):
        try:
            with open(self.reminders_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except Exception:
            log.exception("Не удалось прочитать reminders.json")
            return []

    def _save_reminder(self, content, due):
        with self.reminder_lock:
            reminders = self._read_reminders()
            reminders.append({"id": str(time.time_ns()), "content": content,
                              "due": due.isoformat(timespec="minutes"), "notified": False})
            temp_path = self.reminders_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.reminders_path)

    def _reminder_watch(self):
        """Проверяет сохранённые напоминания, пока работает Капри."""
        while True:
            try:
                now = dt.datetime.now()
                due_items = []
                with self.reminder_lock:
                    reminders = self._read_reminders()
                    changed = False
                    for item in reminders:
                        if item.get("notified"):
                            continue
                        try:
                            due = dt.datetime.fromisoformat(item.get("due", ""))
                        except (TypeError, ValueError):
                            item["notified"] = True
                            changed = True
                            continue
                        if due <= now:
                            item["notified"] = True
                            changed = True
                            due_items.append(item.get("content", ""))
                    if changed:
                        temp_path = self.reminders_path + ".tmp"
                        with open(temp_path, "w", encoding="utf-8") as f:
                            json.dump(reminders, f, ensure_ascii=False, indent=2)
                        os.replace(temp_path, self.reminders_path)
                for content in due_items:
                    self.jobs.put(("reminder_due", content))
            except Exception:
                log.exception("Ошибка проверки напоминаний")
            time.sleep(2)

    def say(self, text):
        """Озвучивает текст. Возвращает True, если Капри перебили."""
        log.info("Капри: %s", text)
        self.subtitle.emit(text)
        self.mode.emit("speaking", 0.0)
        barge_in = self.cfg.get("barge_in") and self.listener
        if self.listener and not barge_in:
            self.listener.muted.set()
        self.speech_words = set(re.findall(r"\w+", text.lower().replace("ё", "е")))
        self.interrupted = False
        self.speaking = True
        try:
            self.speaker.say(text)
        finally:
            self.speaking = False
            self.speech_end = time.time()
            if barge_in and not self.interrupted:
                self.listener.reset_req.set()  # выбрасываем эхо её голоса
            if self.listener and not barge_in:
                time.sleep(0.25)
                self.listener.muted.clear()
        self._check_voice_blocked()
        return self.interrupted

    def _check_voice_blocked(self):
        """Голос Microsoft не прошёл из-за сети, а запущен zapret — один раз за сеанс объясняем
        и предлагаем добавить сервер голоса в исключения zapret."""
        err = self.speaker.edge_error
        if self.zapret_checked or not err or not zapret.NETWORK_ERROR.search(err) or not zapret.running():
            return
        self.zapret_checked = True
        all_lists = zapret.find_all_lists()
        todo = [lst for lst in all_lists if not zapret.excluded(lst)]
        if not all_lists:
            self.jobs.put(("say", line("zapret_manual")))
        elif not todo:
            self.jobs.put(("say", line("zapret_restart")))
        else:
            def fix():
                try:
                    for lst in todo:  # во все найденные копии zapret — какая из них запущена, не видно
                        zapret.add_exclude(lst)
                        log.info("Сервер голоса добавлен в исключения zapret (имя и адреса): %s", lst)
                except OSError as e:
                    log.exception("Не удалось дописать исключение zapret")
                    return Reply(line("zapret_fail", e=e))
                return Reply(line("zapret_fixed"))
            self.actions.pending = fix
            self.awake_until = float("inf")  # ждём ответ без тайм-аута
            self.jobs.put(("say", line("zapret_offer")))


def tray_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(255, 34, 96))
    p.setPen(QPen(QColor(150, 70, 255), 4))
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor(6, 24, 20))
    p.setPen(QPen(QColor(40, 255, 150), 4))
    p.drawEllipse(14, 22, 36, 20)
    p.setBrush(QColor(40, 255, 150))
    p.drawEllipse(27, 27, 10, 10)
    p.end()
    return QIcon(pm)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    cfg = load_config()

    avatar = Avatar(os.path.join(BASE, "assets"))
    avatar.hide_after = float(cfg["hide_after_seconds"])
    apply_user(cfg)
    capri = Capri(cfg)
    capri.summon.connect(avatar.summon)
    capri.vanish.connect(avatar.vanish)
    capri.mode.connect(avatar.set_mode)
    capri.subtitle.connect(avatar.set_subtitle)
    capri.quit_app.connect(app.quit)
    capri.anim.connect(avatar.play_anim)
    capri.roam.connect(avatar.start_roam)
    capri.hop.connect(avatar.start_hopping)
    capri.move_to.connect(avatar.move_to_named)
    capri.afk_changed.connect(avatar.set_afk)
    avatar.clicked.connect(capri.call_from_click)

    def ask_text():
        text, ok = QInputDialog.getText(avatar, "Капри — текстовая команда", "Напиши команду или вопрос:")
        if ok and text.strip():
            capri.text_command(text.strip())

    capri.text_input_requested.connect(ask_text)

    def ask_package_name():
        name, ok = QInputDialog.getText(
            avatar,
            "Капри — установка приложения",
            "Какой пакет установить? Введи название пакета, например firefox или zen-browser-bin:"
        )
        if ok and name.strip():
            capri.text_command("скачай " + name.strip())

    capri.package_input_requested.connect(ask_package_name)

    windows = {}  # открытые окна держим, пока открыты (иначе их соберёт сборщик мусора); одно окно каждого вида

    def show_window(kind, make):
        old = windows.get(kind)
        if old is not None and old.isVisible():
            old.close()
        dlg = windows[kind] = make()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_entries():
        from entries_dialog import EntriesDialog
        capri.reload_config_if_changed(speak=False)
        show_window("entries", lambda: EntriesDialog(capri.actions))

    def open_sound():
        from sound_dialog import SoundDialog
        show_window("sound", lambda: SoundDialog(capri))

    def open_profile():
        from profile_dialog import ProfileDialog
        show_window("profile", lambda: ProfileDialog(capri))
    def open_models():
        if not capri.local:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                avatar,
                "Локальный ИИ",
                "Локальный ИИ отключён в настройках."
            )
            return

        dialog = ModelsDialog(capri.local, capri._remember_model, avatar)
        dialog.exec()

    def open_telegram_client_settings():
        """Выбор Telegram Desktop/форка под Linux; выбранный ярлык сохраняется в config.json."""
        if os.name == "nt":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(avatar, "Telegram", "Выбор Linux-клиента доступен в Linux.")
            return

        desktop_dirs = [
            os.path.expanduser("~/.local/share/applications"),
            "/usr/share/applications",
            "/var/lib/flatpak/exports/share/applications",
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        ]
        clients = []
        tokens = ("telegram", "ayu", "64gram", "forkgram", "kotatogram", "kotato")
        for directory in desktop_dirs:
            if not os.path.isdir(directory):
                continue
            for current, _, files in os.walk(directory):
                for filename in files:
                    if not filename.endswith(".desktop"):
                        continue
                    path = os.path.join(current, filename)
                    try:
                        with open(path, encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        entry = content.split("[Desktop Entry]", 1)[-1].split("\n[", 1)[0]
                        fields = {}
                        for row in entry.splitlines():
                            if "=" in row and not row.lstrip().startswith("#"):
                                key, value = row.split("=", 1)
                                fields[key.strip()] = value.strip()
                        name = fields.get("Name", os.path.splitext(filename)[0])
                        searchable = (name + " " + filename + " " + fields.get("Exec", "")).lower()
                        if not any(token in searchable for token in tokens):
                            continue
                        if not any(item["path"] == path for item in clients):
                            clients.append({"name": name, "path": path})
                    except OSError:
                        log.exception("Не удалось прочитать ярлык %s", path)
        clients.sort(key=lambda item: item["name"].lower())
        labels = ["Автоопределение (Telegram)"] + [f'{item["name"]} — {item["path"]}' for item in clients]
        labels.append("Указать команду запуска вручную…")
        current_path = capri.cfg.get("telegram_client", "")
        current_label = next((label for label, item in zip(labels[1:], clients) if item["path"] == current_path),
                             labels[-1] if str(current_path).startswith("cmd:") else labels[0])
        selected, ok = QInputDialog.getItem(avatar, "Telegram-клиент", "Выбери клиент или форк:", labels,
                                            labels.index(current_label) if current_label in labels else 0, False)
        if not ok:
            return
        if selected == labels[0]:
            values = ("", "Telegram")
        elif selected == labels[-1]:
            command, ok = QInputDialog.getText(avatar, "Команда запуска", "Команда запуска (например, flatpak run org.telegram.desktop):")
            if not ok or not command.strip():
                return
            name, ok = QInputDialog.getText(avatar, "Название клиента", "Как называется клиент?", text=capri.cfg.get("telegram_client_name", "Telegram"))
            if not ok or not name.strip():
                return
            values = ("cmd:" + command.strip(), name.strip())
        else:
            item = clients[labels.index(selected) - 1]
            values = (item["path"], item["name"])
        for key, value in zip(("telegram_client", "telegram_client_name"), values):
            capri.cfg[key] = value
            save_setting(key, value)
        capri.config_mtime = capri._config_mtime()
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(avatar, "Telegram", f"Выбран клиент: {values[1]}\nНастройка сохранена.")

    menu = QMenu()
    for title, fn in (("Локальный ИИ — модели…", open_models),
                      ("Написать команду…", ask_text),
                      ("Мои сайты, программы, ответы и команды…", open_entries),
                      ("Telegram — клиент и форки…", open_telegram_client_settings),
                      ("Как ко мне обращаться…", open_profile),
                      ("SEARCH", None),
                      ("BROWSER", None),
                      ("Открыть config.json", lambda: os.startfile(CONFIG_PATH) if os.name == "nt" else __import__("subprocess").Popen(["gnome-text-editor", CONFIG_PATH], start_new_session=(os.name != "nt"))),
                      ("Капри на GitHub (обновления)…", lambda: capri.actions.web(CAPRI_URL)),
                      (None, None),
                      ("Выход", app.quit)):
        if title is None:
            menu.addSeparator()
            continue
        if title == "BROWSER":
            # Единый выбор браузера для всех веб-команд Capri.
            import discovery
            browser_menu = menu.addMenu("Браузер для сайтов")
            browser_group = QActionGroup(browser_menu)
            browser_group.setExclusive(True)
            system_browser = "__system__"

            def selected_browser_value():
                value = capri.actions.provider_preferences.get("browser") or capri.cfg.get("browser")
                if not value:
                    return system_browser
                # Нормализуем старые настройки вроде "zen" в имя из списка обнаруженных браузеров.
                if value != system_browser:
                    match = discovery.find_provider("browsers", str(value))
                    if match:
                        return match[0]
                return str(value)

            def browser_menu_title(value, available):
                if value == system_browser:
                    return "Браузер для сайтов — системный"
                if value in available:
                    return f"Браузер для сайтов — {value}"
                return f"Браузер для сайтов — не найден: {value}"

            def choose_browser(value):
                err = capri.actions._save_provider("browser", value)
                if err:
                    log.error("Не удалось сохранить выбор браузера: %s", err)
                    capri.say("Не получилось сохранить выбор браузера: " + err)
                    return
                capri.config_mtime = capri._config_mtime()
                capri.cfg["provider_preferences"] = dict(capri.actions.provider_preferences)
                available = list(discovery.scan().get("browsers", {}).keys())
                browser_menu.setTitle(browser_menu_title(value, available))
                for action in browser_group.actions():
                    action.setChecked(action.data() == value)
                log.info("Браузер для сайтов: %s", "системный по умолчанию" if value == system_browser else value)

            def fill_browser_menu(m=browser_menu):
                capri.reload_config_if_changed(speak=False)
                for old in list(browser_group.actions()):
                    browser_group.removeAction(old)
                    m.removeAction(old)
                    old.deleteLater()
                m.clear()  # убрать разделители/сообщение «браузеры не найдены» от прошлого открытия
                found = discovery.scan().get("browsers", {})
                selected = selected_browser_value()
                options = [(system_browser, "Системный браузер по умолчанию")]
                options.extend((name, name) for name in found)
                if selected not in (system_browser, *found.keys()):
                    options.append((selected, f"{selected} (сейчас не найден)"))
                for value, label in options:
                    action = QAction(label, m)
                    action.setCheckable(True)
                    action.setData(value)
                    browser_group.addAction(action)
                    m.addAction(action)
                    action.triggered.connect(lambda _=False, v=value: choose_browser(v))
                    action.setChecked(value == selected)
                m.setTitle(browser_menu_title(selected, list(found.keys())))
                if not found:
                    m.addSeparator()
                    unavailable = QAction("Установленные браузеры не найдены", m)
                    unavailable.setEnabled(False)
                    m.addAction(unavailable)

            browser_menu.aboutToShow.connect(fill_browser_menu)
            fill_browser_menu()
            continue
        if title == "SEARCH":
            # Эти пункты показывают текущее состояние прямо в названии,
            # чтобы было ясно, включена функция или выключена.
            def state_label(label, enabled):
                return f"{label}: {'ВКЛ' if enabled else 'ВЫКЛ'}"

            search_act = QAction(menu)
            search_act.setCheckable(True)
            search_act.toggled.connect(capri.set_search_when_unsure)
            search_act.toggled.connect(
                lambda on, act=search_act: act.setText(state_label("Искать в браузере, если не знаю", on))
            )
            menu.addAction(search_act)

            ai_act = QAction(menu)
            ai_act.setCheckable(True)
            ai_act.toggled.connect(capri.set_enable_ai)
            ai_act.toggled.connect(
                lambda on, act=ai_act: act.setText(state_label("Использовать ИИ", on))
            )
            menu.addAction(ai_act)

            chatter_act = QAction(menu)
            chatter_act.setCheckable(True)
            chatter_act.toggled.connect(capri.set_chatter)
            chatter_act.toggled.connect(
                lambda on, act=chatter_act: act.setText(state_label("Появляться самой", on))
            )
            menu.addAction(chatter_act)

            def sync_search(a=search_act, b=chatter_act, c=ai_act):
                capri.reload_config_if_changed(speak=False)
                for act, key, default, label in (
                    (a, "search_when_unsure", True, "Искать в браузере, если не знаю"),
                    (b, "chatter", True, "Появляться самой"),
                    (c, "enable_ai", False, "Использовать ИИ"),
                ):
                    enabled = bool(capri.cfg.get(key, default))
                    act.blockSignals(True)
                    act.setChecked(enabled)
                    act.setText(state_label(label, enabled))
                    act.blockSignals(False)

            sync_search()
            menu.aboutToShow.connect(sync_search)
            continue
        act = QAction(title, menu)
        act.triggered.connect(fn)
        menu.addAction(act)

    tray = QSystemTrayIcon(tray_icon())
    tray.setToolTip("Капри — голосовой ассистент")
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: capri.call_from_click()
                           if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    avatar.menu_requested.connect(lambda pos: menu.popup(pos))

    capri.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log.exception("Капри упала при запуске")
        fatal(f"Что-то сломалось при запуске: {e}\n\nПодробности — в файле capri.log в папке Капри.")
