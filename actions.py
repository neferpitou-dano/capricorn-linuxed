"""Команды: сайты, приложения, громкость, яркость, диагностика ПК и т.д."""
import ctypes
import datetime
import difflib
import importlib.util
import json
import logging
import os
import platform
import shutil
import shlex
import random
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass

import psutil

import automation
import media
import discovery
import linux_compat
from persona import line

log = logging.getLogger("capri")
NO_WINDOW = 0x08000000
IS_WINDOWS = os.name == "nt"

def _popen_detached(args, **kwargs):
    """Запускает внешнюю GUI-программу отдельно от процесса Капри/родительского терминала."""
    if not IS_WINDOWS:
        kwargs.setdefault("start_new_session", True)
    return subprocess.Popen(args, **kwargs)

def _run_command(args, timeout=4):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False).stdout.strip()
    except Exception:
        return ""


@dataclass
class Reply:
    text: str
    action: str | None = None    # "hide" / "quit"


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\.\-:/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR = dict(zip("абвгдезийклмнопрстуфхцы", "abvgdeziyklmnoprstufhcy"))
TR.update({"ё": "e", "ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ь": "", "э": "e",
           "ю": "yu", "я": "ya"})


def translit(s):
    return "".join(TR.get(c, c) for c in s)


NUM = {"ноль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "одной": 1,
       "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4,
       "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
       "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11,
       "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
       "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
       "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}


def parse_number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total = None
    for w in text.split():
        if w in NUM:
            total = (total or 0) + NUM[w]
        elif total is not None:
            break
    if total is not None:
        return total
    if re.search(r"максимум|на полную|на всю", text):
        return 100
    if re.search(r"половин", text):
        return 50
    if re.search(r"минимум", text):
        return 0
    return None


def run_ps(cmd, timeout=60):
    if not IS_WINDOWS:
        return ""
    full = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference='SilentlyContinue'; " + cmd)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def ps_json(cmd):
    out = run_ps(cmd + " | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


# ---------------- громкость / яркость ----------------

def _endpoint():
    if not IS_WINDOWS:
        raise RuntimeError("Windows audio endpoint API is unavailable on Linux")
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume():
    if not IS_WINDOWS:
        out = _run_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"([0-9.]+)", out)
        if not m:
            raise RuntimeError("wpctl не найден или PipeWire недоступен")
        return round(float(m.group(1)) * 100)
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(v):
    if not IS_WINDOWS:
        if not shutil.which("wpctl"):
            raise RuntimeError("Установи wireplumber (команда wpctl)")
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0, min(100, v))}%"], check=True)
        if v > 0:
            subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], check=False)
        return
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(max(0, min(100, v)) / 100, None)
    if v > 0:
        ep.SetMute(0, None)


def set_mute(on):
    if not IS_WINDOWS:
        if not shutil.which("wpctl"):
            raise RuntimeError("Установи wireplumber (команда wpctl)")
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if on else "0"], check=True)
        return
    _endpoint().SetMute(1 if on else 0, None)


def get_brightness():
    if not IS_WINDOWS:
        out = _run_command(["brightnessctl", "-m"])
        parts = out.split(",")
        if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit() and int(parts[3]):
            return round(int(parts[2]) * 100 / int(parts[3]))
        raise RuntimeError("brightnessctl недоступен или устройство не поддерживается")
    import screen_brightness_control as sbc
    return sbc.get_brightness()[0]


def set_brightness(v):
    if not IS_WINDOWS:
        if not shutil.which("brightnessctl"):
            raise RuntimeError("Установи brightnessctl")
        subprocess.run(["brightnessctl", "set", f"{max(0, min(100, v))}%"], check=True)
        return
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, v)))


# ---------------- поиск приложений ----------------

PROTECTED_PROCS = {"explorer", "python", "pythonw", "svchost", "csrss", "winlogon", "dwm", "lsass", "services",
                   "system", "smss", "wininit", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "runtimebroker",
                   "searchhost", "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "ollama",
                   "ollama app", "conhost", "audiodg"}
SKIP_WORDS = ("uninstall", "удален", "удалить", "деинсталл", "readme", "help", "справка", "documentation",
              "license", "support", "поддержк", "feedback", "release notes", "website")


# как браузеры называют вслух → по чему узнать в списке браузеров Windows (название ключа или exe)
BROWSER_WORDS = [
    (r"(гугл |google )?(хром\w*|chrome)", "chrome"),
    (r"chromium|хромиум", "chromium"),
    (r"яндекс\w*|yandex", "yandex"),
    (r"опер\w*|opera", "opera"),
    (r"(фаер|файр|файер)фокс\w*|мозил+\w*|firefox", "firefox"),
    (r"(майкрософт )?эдж\w*|edge", "edge"),
    (r"брейв\w*|brave", "brave"),
    (r"вивальди|vivaldi", "vivaldi"),
    (r"зен(?:\s+браузер)?|zen(?:\s+browser)?", "zen"),
]
BROWSER_NAMES = {"chrome": "Google Chrome", "chromium": "Chromium", "yandex": "Яндекс Браузер", "opera": "Opera", "firefox": "Firefox",
                 "edge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi", "zen": "Zen Browser"}
# «… в хроме», «… через яндекс браузер», «… в браузере опера» — только в конце фразы
BROWSER_SUFFIX = re.compile(r"\s+(в|через)\s+(браузер\w*\s+)?(?P<b>" + "|".join(p for p, _ in BROWSER_WORDS)
                            + r")(\s+браузер\w*)?$")


def installed_browsers():
    """Найти браузеры в Linux через PATH; в Windows — через реестр."""
    if not IS_WINDOWS:
        found = {}
        for _, key in BROWSER_WORDS:
            candidates = {"chrome": ["google-chrome", "google-chrome-stable", "chrome"],
                          "chromium": ["chromium", "chromium-browser"],
                          "yandex": ["yandex-browser", "yandex"], "opera": ["opera"],
                          "firefox": ["firefox"], "edge": ["microsoft-edge", "microsoft-edge-stable"],
                          "brave": ["brave", "brave-browser"], "vivaldi": ["vivaldi", "vivaldi-stable"],
                          "zen": ["zen-browser", "zen"]}.get(key, [])
            for candidate in candidates:
                path = shutil.which(candidate)
                if path:
                    found[key] = path
                    break
        return found
    import winreg
    found = {}
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet")):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                cmd = winreg.QueryValue(key, name + r"\shell\open\command")
            except OSError:
                continue
            m = re.match(r'^\s*"?([^"]+?\.exe)', cmd)
            if not m or not os.path.isfile(m.group(1)):
                continue
            label = (name + " " + os.path.basename(m.group(1))).lower()
            for _, k in BROWSER_WORDS:
                if k in label or (k == "edge" and "msedge" in label):
                    found.setdefault(k, m.group(1))
    return found


def find_browser(name):
    """«хром» / «яндекс браузер» / путь к exe → путь к exe браузера или None, если такого нет."""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    n = norm(name)
    for pattern, k in BROWSER_WORDS:
        if re.fullmatch(rf"({pattern})( браузер\w*)?|браузер\w* ({pattern})", n):
            return installed_browsers().get(k)
    return None


def installed_programs():
    """Список запускаемых приложений; в Linux берёт .desktop-файлы из стандартных каталогов."""
    if not IS_WINDOWS:
        out = []
        dirs = [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications", "/usr/local/share/applications",
                os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
                "/var/lib/flatpak/exports/share/applications", "/var/lib/snapd/desktop/applications"]
        for directory in dirs:
            if not os.path.isdir(directory):
                continue
            for filename in os.listdir(directory):
                if not filename.endswith(".desktop"):
                    continue
                path = os.path.join(directory, filename)
                try:
                    data = open(path, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if "NoDisplay=true" in data or "Hidden=true" in data:
                    continue
                name = re.search(r"(?m)^Name(?:\[[^]]+\])?=(.+)$", data)
                if name:
                    out.append((norm(name.group(1)), path))
        return out
    import winreg
    out = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
            except OSError:
                continue
            values = {}
            for field in ("DisplayIcon", "InstallLocation", "UninstallString"):
                try:
                    values[field] = str(winreg.QueryValueEx(sub, field)[0])
                except OSError:
                    values[field] = ""
            exe = re.sub(r",\s*-?\d+$", "", values["DisplayIcon"]).strip().strip('"')
            base = os.path.basename(exe).lower()
            if exe.lower().endswith(".exe") and "unins" not in base and "setup" not in base and os.path.isfile(exe):
                out.append((norm(name), exe))
                continue
            # значок — деинсталлятор (так у Steam): ищем рядом exe с названием программы, steam → steam.exe
            folder = values["InstallLocation"].strip().strip('"')
            if not os.path.isdir(folder):
                m = re.match(r'^"?([^"]+?\.exe)', values["UninstallString"] or exe)
                folder = os.path.dirname(m.group(1)) if m else ""
            guess = os.path.join(folder, re.sub(r"[^\w\-]", "", name) + ".exe") if folder else ""
            if guess and os.path.isfile(guess):
                out.append((norm(name), guess))
    return out


class AppIndex:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def build(self):
        items = {}
        try:
            for app in ps_json("Get-StartApps | Select-Object Name,AppID"):
                name = norm(app.get("Name") or "")
                if name and not any(w in name for w in SKIP_WORDS):
                    items[name] = ("appid", app.get("AppID"))
        except Exception:
            log.exception("Get-StartApps")
        home = os.path.expanduser("~")
        dirs = [os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"),
                os.path.join(os.environ.get("PUBLIC", ""), "Desktop")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".lnk", ".url", ".appref-ms")):
                        name = norm(os.path.splitext(f)[0])
                        if name and not any(w in name for w in SKIP_WORDS):
                            # ярлык надёжнее идентификатора из Get-StartApps: у программ вроде Discord
                            # (установщик Squirrel) запуск по идентификатору ничего не открывает
                            if items.get(name, ("appid",))[0] != "file":
                                items[name] = ("file", os.path.join(root, f))
        # На Linux приложения берём из .desktop и PATH: Windows-каталоги выше там отсутствуют.
        if not IS_WINDOWS:
            for name, target in installed_programs():
                if name and not any(w in name for w in SKIP_WORDS):
                    items.setdefault(name, ("file", target))
            for binary in ("firefox", "chromium", "google-chrome", "zen-browser", "spotify", "steam", "lutris", "vlc", "code", "discord", "telegram-desktop"):
                path = shutil.which(binary)
                if path:
                    items.setdefault(norm(binary), ("file", path))
        # установленные программы из реестра — на случай, если ярлыка в «Пуске» нет (бывает у Steam)
        for name, exe in installed_programs():
            if name not in items and not any(w in name for w in SKIP_WORDS):
                items[name] = ("file", exe)
        with self.lock:
            self.items = items
        self.ready.set()
        log.info("Найдено приложений: %d", len(items))

    @staticmethod
    def _sound(s):
        s = re.sub(r"\s", "", translit(s.lower()))
        for a, b in (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("w", "v"), ("x", "ks"), ("z", "s")):
            s = s.replace(a, b)
        return s

    @staticmethod
    def _skeleton(s):
        """Согласные без гласных: «cloud», «claude», «клод» → «kld». Ловит созвучные названия."""
        return re.sub(r"[aeiouy]", "", AppIndex._sound(s))

    @staticmethod
    def sounds_like(a, b):
        """Созвучны, если совпадают согласные и первый звук: «роблокс» = «roblox», но «запрет» ≠ «aseprite»."""
        sk = AppIndex._skeleton(a)
        return len(sk) >= 3 and sk == AppIndex._skeleton(b) and AppIndex._sound(a)[:1] == AppIndex._sound(b)[:1]

    def candidates(self, query, aliases, limit=4):
        """Подходящие приложения, лучшие первыми: [(оценка, название, цель)]."""
        q = norm(query)
        variants = {q, translit(q)}
        if q in aliases:
            variants.add(norm(aliases[q]))
        with self.lock:
            items = list(self.items.items())
        scored = {}
        for name, target in items:
            best = 0.0
            for v in variants:
                if v == name:
                    score = 1.0
                elif len(v) >= 3 and re.search(r"(^|\s)" + re.escape(v), name):
                    score = 0.93 - 0.003 * len(name)
                else:
                    score = difflib.SequenceMatcher(None, v, name).ratio()
                    first = name.split()[0] if name.split() else name
                    if self.sounds_like(v, name):
                        score = max(score, 0.97)  # звучит как всё название: «роблокс» = «roblox»
                    elif self.sounds_like(v, first):
                        score = max(score, 0.86)  # звучит так же: «клод/cloud» ~ «claude»
                best = max(best, score)
            if best >= 0.72 and best > scored.get(name, (0,))[0]:
                scored[name] = (best, target)
        ranked = sorted(((s, n, t) for n, (s, t) in scored.items()), key=lambda x: -x[0])
        return ranked[:limit]

    def find(self, query, aliases):
        found = self.candidates(query, aliases, 1)
        return (found[0][1], found[0][2]) if found else None

    @staticmethod
    def launch(target):
        kind, value = target
        if not IS_WINDOWS:
            if kind == "appid":
                raise OSError("Windows app id не поддерживается в Linux")
            if value.endswith(".desktop"):
                name = os.path.basename(value)[:-8]
                if shutil.which("gtk-launch"):
                    _popen_detached(["gtk-launch", name])
                else:
                    _popen_detached(["xdg-open", value])
            elif os.path.isabs(value) and os.path.exists(value):
                _popen_detached([value])
            else:
                _popen_detached(["xdg-open", value])
            return
        if kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"], creationflags=NO_WINDOW)
            return
        (os.startfile(value) if IS_WINDOWS else _popen_detached(["xdg-open", value]))

    def launch_best(self, query, options):
        """Запустить лучший вариант; если Windows его не открыла (например, Roblox.lnk — «операция
        отменена»), пробуем похожие ярлыки с тем же началом (Roblox Player), а в конце — через проводник,
        как при двойном клике. options: [(оценка, имя, цель)]. Возвращает имя запущенного или None."""
        first = options[0]
        q = first[1].split()[0] if first[1].split() else ""  # «roblox» из найденного «roblox», а не «роблокс»
        with self.lock:
            similar = [(1.0, n, t) for n, t in self.items.items() if n != first[1] and q and n.startswith(q)
                       and not re.search(r"studio|uninstall|update", n)]
        for _, name, target in [first] + sorted(similar, key=lambda x: len(x[1])):
            try:
                self.launch(target)
                return name
            except OSError as e:
                log.info("%s не запустился (%s)", name, e)
        if first[2][0] == "file":
            if IS_WINDOWS:
                subprocess.Popen(["explorer.exe", first[2][1]], creationflags=NO_WINDOW)
            else:
                _popen_detached(["xdg-open", first[2][1]])
            return first[1]
        return None


# ---------------- диагностика ----------------

def _dir_size(path, limit=200000):
    total, n = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit:
                return total
    return total


def analyze_pc():
    """Возвращает (список проблем, полный текстовый отчёт)."""
    issues, lines = [], []
    add = lines.append
    now = datetime.datetime.now()
    add(f"ДИАГНОСТИКА ПК — {now:%d.%m.%Y %H:%M}")
    add("=" * 60)

    cpu = psutil.cpu_percent(interval=1.5)
    mem = psutil.virtual_memory()
    add(f"Процессор: загрузка {cpu:.0f}% ({psutil.cpu_count()} потоков)")
    add(f"Память: занято {mem.percent:.0f}% ({mem.used / 2**30:.1f} из {mem.total / 2**30:.1f} ГБ)")
    if cpu > 85:
        issues.append(f"процессор загружен на {cpu:.0f} процентов")
    if mem.percent > 85:
        issues.append(f"оперативная память занята на {mem.percent:.0f} процентов")

    up = now - datetime.datetime.fromtimestamp(psutil.boot_time())
    add(f"Время работы без перезагрузки: {up.days} д {up.seconds // 3600} ч")
    if up.days >= 7:
        issues.append(f"компьютер не перезагружался {up.days} дней")

    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        add(f"Батарея: {bat.percent:.0f}% {'(заряжается)' if bat.power_plugged else ''}")

    add("\nДИСКИ:")
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        free_gb = u.free / 2**30
        add(f"  {part.mountpoint} {part.fstype}: свободно {free_gb:.1f} ГБ из {u.total / 2**30:.0f} ГБ ({100 - u.percent:.0f}%)")
        if u.percent > 90 or free_gb < 10:
            issues.append(f"на диске {part.mountpoint[0]} мало места — {free_gb:.0f} гигабайт")

    for d in ps_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus"):
        add(f"  Накопитель {d.get('FriendlyName')}: здоровье {d.get('HealthStatus')}, состояние {d.get('OperationalStatus')}")
        if str(d.get("HealthStatus")) not in ("Healthy", "0"):
            issues.append(f"накопитель {d.get('FriendlyName')} сообщает о проблемах со здоровьем")

    tmp = os.environ.get("TEMP", "")
    if tmp:
        size = _dir_size(tmp) / 2**30
        add(f"  Временные файлы (%TEMP%): {size:.1f} ГБ")
        if size > 3:
            issues.append(f"временные файлы занимают {size:.0f} гигабайт")

    add("\nУСТРОЙСТВА С ОШИБКАМИ:")
    bad = ps_json("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -eq 'Error'} | Select-Object FriendlyName,Class")
    for d in bad:
        add(f"  {d.get('FriendlyName')} ({d.get('Class')})")
    if not bad:
        add("  нет")
    else:
        issues.append(f"устройств с ошибкой драйвера: {len(bad)}")

    add("\nОШИБКИ В ЖУРНАЛАХ WINDOWS ЗА 24 ЧАСА (по источникам):")
    groups = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} | Group-Object ProviderName | "
                     "Sort-Object Count -Descending | Select-Object -First 12 Count,Name")
    total_err = sum(int(g.get("Count", 0)) for g in groups)
    for g in groups:
        add(f"  {g.get('Count'):>4}  {g.get('Name')}")
    if not groups:
        add("  нет")
    if total_err > 30:
        issues.append(f"в журналах {total_err} ошибок за сутки")

    add("\nПОСЛЕДНИЕ ОШИБКИ:")
    for e in ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} -MaxEvents 8 | Select-Object "
                     "@{n='T';e={$_.TimeCreated.ToString('dd.MM HH:mm')}},ProviderName,Id,"
                     "@{n='M';e={if($_.Message){$_.Message.Substring(0,[Math]::Min(220,$_.Message.Length))}}}"):
        msg = re.sub(r"\s+", " ", str(e.get("M") or ""))
        add(f"  [{e.get('T')}] {e.get('ProviderName')} #{e.get('Id')}: {msg}")

    crashes = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=41;"
                     "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    bsod = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=1001;"
                  "ProviderName='Microsoft-Windows-WER-SystemErrorReporting';"
                  "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    crashes = int(crashes) if crashes.isdigit() else 0
    bsod = int(bsod) if bsod.isdigit() else 0
    add(f"\nАварийные выключения за 7 дней: {crashes}; синие экраны (BSOD): {bsod}")
    if crashes:
        issues.append(f"аварийных выключений за неделю: {crashes}")
    if bsod:
        issues.append(f"синих экранов за неделю: {bsod}")

    add("\nТОП ПРОЦЕССОВ ПО ПАМЯТИ:")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    for rss, name in sorted(procs, reverse=True)[:8]:
        add(f"  {rss / 2**20:>7.0f} МБ  {name}")

    add("\nПРОГРАММЫ В АВТОЗАГРУЗКЕ:")
    for s in ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location"):
        add(f"  {s.get('Name')}  [{s.get('Location')}]")

    add("\n" + "=" * 60)
    add("НАЙДЕННЫЕ ПРОБЛЕМЫ:" if issues else "Серьёзных проблем не найдено.")
    for i in issues:
        add(f"  • {i}")
    return issues, "\n".join(lines)


# ---------------- ютуб ----------------

BROWSERS = ("firefox", "chrome", "msedge", "opera", "browser", "brave", "vivaldi", "yandex")


def browser_playing():
    """True, если из браузера сейчас реально идёт звук (по индикатору громкости в микшере Windows)."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    for s in AudioUtilities.GetAllSessions():
        try:
            name = (s.Process.name() if s.Process else "").lower()
            if any(b in name for b in BROWSERS):
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > 0.001:
                    return True
        except Exception:
            continue
    return False


def youtube_window():
    """Окно браузера с открытым YouTube (по заголовку активной вкладки)."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            if "youtube" in buf.value.lower():
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


_ensure_token = [0]  # номер последней проверки «заиграло ли видео»; новая команда отменяет старую


def cancel_ensure():
    _ensure_token[0] += 1


def ensure_playing(wait_load=7.0):
    """Браузеры блокируют автозапуск со звуком у вкладок, открытых программой. Ждём звук;
    если тишина — переключаемся на окно YouTube и жмём K (пуск/пауза в плеере), затем проверяем.
    Если за это время пришла новая команда — тихо выходим, чтобы не кликнуть посреди её работы."""
    _ensure_token[0] += 1
    token = _ensure_token[0]
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    deadline = time.time() + wait_load
    while time.time() < deadline:  # может, запустилось само (или идёт реклама)
        if _ensure_token[0] != token:
            return
        if browser_playing():
            log.info("Видео играет само")
            return
        time.sleep(0.5)
    if _ensure_token[0] != token:
        return
    hwnd = None
    for _ in range(10):  # заголовок вкладки может ещё не смениться на «… - YouTube»
        hwnd = youtube_window()
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        log.info("Окно YouTube не найдено, не нажимаю пуск")
        return
    u = ctypes.windll.user32
    u.keybd_event(0x12, 0, 0, 0)       # «пустой» Alt — без него Windows не даёт сменить активное окно
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    # Клик мышью по плееру — настоящее действие пользователя: и окно активирует, и фокус ставит в плеер,
    # и браузер считает это разрешением на звук. Точка (30% ширины, 35% высоты) попадает в плеер
    # и в обычном, и в широком режиме YouTube.
    from ctypes import wintypes
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = r.left + int((r.right - r.left) * 0.30), r.top + int((r.bottom - r.top) * 0.35)
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # левая кнопка вниз
    u.mouse_event(0x0004, 0, 0, 0, 0)  # вверх
    time.sleep(0.05)
    u.SetCursorPos(old.x, old.y)
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено кликом по плееру")
            return
    send_keys(["k"])  # запасной вариант: фокус уже в плеере
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено клавишей K")
            return
    log.info("Не удалось запустить видео: звука нет")

def youtube_first(query):
    """(id, название) первого обычного видео (не шортс) в поиске YouTube или None."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:
        log.info("Поиск на YouTube не удался: %s", e)
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
    if not m:
        return None
    title = ""
    t = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page[m.end():m.end() + 6000])
    if t:
        try:
            title = json.loads(f'"{t.group(1)}"')
        except ValueError:
            title = t.group(1)
    return m.group(1), title


_desc_cache = {}


def file_description(path):
    """Описание программы из свойств exe-файла (то, что показывает диспетчер задач),
    например «Steam Client WebHelper». Пустая строка, если описания нет."""
    if not path:
        return ""
    if path in _desc_cache:
        return _desc_cache[path]
    desc = ""
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if v.GetFileVersionInfoW(path, 0, size, buf):
                ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
                if v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                    lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
                    key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
                    sptr = ctypes.c_wchar_p()
                    if v.VerQueryValueW(buf, key, ctypes.byref(sptr), ctypes.byref(ln)) and ln.value:
                        desc = sptr.value or ""
    except Exception:
        pass
    _desc_cache[path] = desc
    return desc


def stem_name(name):
    # Склоняем только кириллические слова, не изменяя английские никнеймы.
    out = []
    for w in name.split():
        if len(w) >= 5 and re.fullmatch(r"[А-Яа-яЁё-]+", w):
            stem = re.sub(
                r"(ами|ями|ого|его|ому|ему|ов|ев|ей|ой|ом|ем|ым|им|ою|ею|ах|ях|ам|ям|ую|юю|ая|яя|ые|ие|ы|и|а|я|у|ю|е)$",
                "",
                w,
            )
            w = stem if len(stem) >= 3 else w
        out.append(w)
    return " ".join(out)


def youtube_search_in_tab(text):
    """Поиск на YouTube в текущей вкладке через адресную строку (Ctrl+L): срабатывает, даже если страница
    ещё грузится и поле поиска не в фокусе."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    before = automation.window_title(hwnd)
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(text)
    for attempt in range(2):
        # адрес вводится, только если адресная строка действительно получила фокус (иначе буквы ушли бы
        # в страницу YouTube, где это горячие клавиши)
        if not media.navigate(hwnd, url):
            time.sleep(0.6)
            continue
        for _ in range(10):  # ждём, что вкладка ушла на страницу результатов
            time.sleep(0.4)
            title = automation.window_title(hwnd)
            if title != before and "youtube" in title.lower() and title.lower().startswith(text.lower()[:4]):
                return True
        log.info("Поиск на YouTube не сработал с %d-й попытки, заголовок: %s", attempt + 1, title)
        if not automation.is_foreground(hwnd):
            break  # окно потеряло фокус — второй раз вслепую не печатаем
    return False


def wiki_summary(query, sentences=2):
    """Первые предложения статьи русской Википедии по запросу, подготовленные для озвучки. None — не нашлось."""
    headers = {"User-Agent": "CapriAssistant/1.0 (personal voice assistant)"}

    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        found = get("https://ru.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
                    + urllib.parse.quote(query))["query"]["search"]
        if not found:
            return None
        page = get("https://ru.wikipedia.org/api/rest_v1/page/summary/"
                   + urllib.parse.quote(found[0]["title"].replace(" ", "_")))
    except Exception as e:
        log.info("Википедия недоступна: %s", e)
        return None
    if page.get("type") == "disambiguation":
        return None
    text = page.get("extract", "").replace("́", "")  # знаки ударения
    text = re.sub(r"\s*\([^()]*\)", "", text)       # транскрипции, даты и пр. в скобках — не для озвучки
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text.strip())
    out = " ".join(parts[:sentences]).strip()
    if len(out) > 300:
        out = out[:300].rsplit(" ", 1)[0] + "…"
    return out or None


# ---------------- папки ----------------

SHELL_FOLDERS = {  # имена значений в реестре User Shell Folders (учитывают перенос в OneDrive)
    "desktop": "Desktop", "documents": "Personal", "pictures": "My Pictures", "music": "My Music",
    "videos": "My Video", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}
KNOWN_FOLDERS = [  # (регулярка по названию, ключ папки или shell:-путь, как назвать вслух)
    (r"фото\w*|фотк\w*|изображени\w*|картинк\w*|снимк\w*", "pictures", "Изображения"),
    (r"документ\w*", "documents", "Документы"),
    (r"загрузк\w*|скачанн\w*|закачк\w*", "downloads", "Загрузки"),
    (r"рабоч\w+ стол\w*", "desktop", "Рабочий стол"),
    (r"музык\w*", "music", "Музыка"),
    (r"видео\w*", "videos", "Видео"),
    (r"скриншот\w*", "screenshots", "Скриншоты"),
    (r"(этот|мой) компьютер|компьютер", "shell:MyComputerFolder", "Этот компьютер"),
    (r"корзин\w*", "shell:RecycleBinFolder", "Корзина"),
]
DRIVE_LETTERS = {"ц": "C", "си": "C", "с": "C", "д": "D", "ди": "D", "де": "D", "е": "E", "и": "E",
                 "ф": "F", "эф": "F", "джи": "G", "г": "G", "же": "G"}


def known_dir(key):
    if key == "screenshots":
        return os.path.join(known_dir("pictures"), "Screenshots")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, SHELL_FOLDERS[key])[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), {"documents": "Documents", "pictures": "Pictures",
                                                      "music": "Music", "videos": "Videos",
                                                      "downloads": "Downloads"}.get(key, "Desktop"))


def find_folder(name, search=False):
    """(путь, название) по сказанному имени папки или None. search=True — искать и среди своих папок."""
    name = norm(name)
    drive = re.fullmatch(r"(диск|раздел) (?P<l>\S+)", name)
    if drive:
        letter = DRIVE_LETTERS.get(drive.group("l"), drive.group("l").upper())
        if len(letter) == 1 and os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\", f"диск {letter}"
    for pattern, key, label in KNOWN_FOLDERS:
        if re.fullmatch(pattern, name):
            path = key if key.startswith("shell:") else known_dir(key)
            if key.startswith("shell:") or os.path.isdir(path):
                return path, label
    if not search:
        return None
    return search_folder(name)


def search_folder(name, limit=40000):
    """Ищем папку по имени (с транслитом и нечётким сравнением) в домашних папках, до 4 уровней вглубь."""
    targets = {name, translit(name)}
    roots = [known_dir(k) for k in ("desktop", "documents", "downloads", "pictures", "videos", "music")]
    roots.append(os.path.expanduser("~"))
    best, best_score, seen = None, 0.0, 0
    for root in dict.fromkeys(roots):
        base_depth = root.rstrip("\\").count("\\")
        for cur, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in
                       ("appdata", "node_modules", "__pycache__", "site-packages", "windows")]
            if cur.count("\\") - base_depth >= 4:
                dirs[:] = []
            for d in dirs:
                seen += 1
                dn = norm(d)
                for t in targets:
                    score = 1.0 if dn == t else (0.9 if t in dn.split() or dn.startswith(t)
                                                 else difflib.SequenceMatcher(None, t, dn).ratio())
                    if score > best_score:
                        best, best_score = (os.path.join(cur, d), d), score
            if best_score == 1.0 or seen > limit:
                break
        if best_score == 1.0:
            break
    return best if best_score >= 0.8 else None


# ---------------- клавиши ----------------

VK = {"win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
      "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "printscreen": 0x2C, "capslock": 0x14, "menu": 0x5D}
EXTENDED = {"win", "delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down", "menu"}
KEY_WORDS = {  # как клавиши произносят (Vosk пишет кириллицей, Google — часто латиницей)
    "win": "вин винд винда виндовс виндоус виндоуз виндос виндус виндо windows window win wind винды "
           "виндоуса виндовса",
    "ctrl": "контрол контроль контрл кнтрл ктрл контр ctrl control ctl",
    "alt": "альт алт alt", "shift": "шифт shift", "tab": "таб табуляция tab",
    "enter": "энтер ентер enter ввод return", "esc": "эскейп искейп эскейф эск esc escape",
    "space": "пробел space спейс", "backspace": "бэкспейс бекспейс backspace",
    "delete": "делит дилит делейт delete del дел", "insert": "инсерт insert", "home": "хоум home",
    "end": "энд end", "printscreen": "принтскрин printscreen prtsc", "capslock": "капслок capslock",
    "left": "влево лево налево left", "right": "вправо право направо right",
    "up": "вверх наверх up", "down": "вниз down", "menu": "меню",
}
WORD_TO_KEY = {w: k for k, ws in KEY_WORDS.items() for w in ws.split()}
LETTER_NAMES = {"эй": "a", "би": "b", "си": "c", "ди": "d", "и": "e", "эф": "f", "джи": "g", "эйч": "h",
                "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эм": "m", "эн": "n", "оу": "o", "пи": "p",
                "кью": "q", "ар": "r", "эс": "s", "ти": "t", "ю": "u", "ви": "v", "дабл ю": "w",
                "дабл-ю": "w", "экс": "x", "икс": "x", "уай": "y", "игрек": "y", "зед": "z", "зет": "z",
                # по-русски: «контрол цэ», «вин дэ», и одиночные буквы «вин д»
                "а": "a", "бэ": "b", "б": "b", "цэ": "c", "ц": "c", "дэ": "d", "д": "d", "е": "e",
                "гэ": "g", "г": "g", "ха": "h", "ка": "k", "к": "k", "эль": "l", "л": "l", "м": "m",
                "н": "n", "о": "o", "пэ": "p", "п": "p", "эр": "r", "р": "r", "с": "s", "тэ": "t", "т": "t",
                "у": "u", "вэ": "v", "в": "v", "зэ": "z", "з": "z"}
SKIP_WORDS_KEYS = {"и", "плюс", "кнопку", "кнопка", "кнопки", "клавишу", "клавиши", "клавиша", "клавиш",
                   "сочетание", "сочетания", "комбинацию", "комбинация", "стрелку", "стрелка", "стрелочку",
                   "на", "клавиатуре", "одновременно", "мне", "пожалуйста", "plus", "and"}


def split_glued(word):
    """Google иногда склеивает сочетание в одно слово: «wintab», «альттаб», «ctrlshiftesc».
    Режем на известные названия клавиш (одиночная буква/цифра допустима только последней)."""
    names = sorted((w for w in WORD_TO_KEY if len(w) > 1), key=len, reverse=True)

    def go(rest):
        if not rest:
            return []
        for n in names:
            if rest.startswith(n):
                tail = go(rest[len(n):])
                if tail is not None:
                    return [WORD_TO_KEY[n]] + tail
        f = re.match(r"[fф](1[0-2]|[1-9])", rest)
        if f:
            tail = go(rest[f.end():])
            if tail is not None:
                return ["f" + f.group(1)] + tail
        if len(rest) == 1 and re.fullmatch(r"[a-z0-9]", rest):
            return [rest]
        return None
    return go(word)


def parse_keys(text):
    """«вин таб» / «ctrl shift esc» / «эф пять» → ['win', 'tab'] ... Возвращает (клавиши, нераспознанное)."""
    t = re.sub(r"[+\-_,.]", " ", norm(text))
    for a, b in (("пейдж ап", "pageup"), ("page up", "pageup"), ("пейдж даун", "pagedown"),
                 ("page down", "pagedown"), ("принт скрин", "printscreen"), ("print screen", "printscreen"),
                 ("капс лок", "capslock"), ("caps lock", "capslock"), ("дабл ю", "дабл-ю"),
                 ("бэк спейс", "backspace"), ("back space", "backspace")):
        t = t.replace(a, b)
    words = t.split()
    keys, i = [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in WORD_TO_KEY or w in VK:
            keys.append(WORD_TO_KEY.get(w, w))
        elif re.fullmatch(r"[fф]\d{1,2}", w):
            keys.append("f" + w[1:])
        elif w in ("эф", "ф", "f") and (nxt.isdigit() or nxt in NUM):
            keys.append(f"f{int(nxt) if nxt.isdigit() else NUM[nxt]}")
            i += 1
        elif w in LETTER_NAMES and not (w == "и" and keys and i + 1 < len(words)):
            keys.append(LETTER_NAMES[w])
        elif re.fullmatch(r"[a-z0-9]", w):
            keys.append(w)
        elif w in NUM and NUM[w] < 10:
            keys.append(str(NUM[w]))
        elif w not in SKIP_WORDS_KEYS:
            glued = split_glued(w)
            if not glued:
                return [], w
            keys += glued
        i += 1
    return keys, None



def send_keys(keys):
    """Отправить клавиши через Windows API или Linux wtype."""
    if not IS_WINDOWS:
        normalized = [
            "super" if str(k).lower() == "win" else str(k).lower()
            for k in keys
        ]
        if not automation._send_keys(normalized):
            raise RuntimeError(
                "Не удалось отправить клавиши. Проверь установку wtype."
            )
        return

    u = ctypes.windll.user32
    codes = []
    for k in keys:
        if k in VK:
            code = VK[k]
        elif re.fullmatch(r"f\d{1,2}", k) and 1 <= int(k[1:]) <= 12:
            code = 0x6F + int(k[1:])
        else:
            code = ord(k.upper())
        codes.append((code, 1 if k in EXTENDED else 0))

    for code, ext in codes:
        u.keybd_event(code, 0, ext, 0)
        time.sleep(0.02)

    for code, ext in reversed(codes):
        u.keybd_event(code, 0, ext | 2, 0)
        time.sleep(0.02)

 


# ---------------- обработчик команд ----------------

REMEMBER_RE = (r"^(запомни|сохрани|добавь)( мне| себе)? (?P<what>(этот |эту |это |текущ\w+ |открыт\w+ )?"
               r"(сайт\w*|страниц\w*|вкладк\w*|ссылк\w*|программ\w*|приложени\w*|игр\w*))\s*(?P<name>.*)$")
FORGET_RE = (r"^(забудь|удали|убери)( мой| мою| из памяти)? (?P<what>сайт\w*|страниц\w*|ссылк\w*|программ\w*|"
             r"приложени\w*|игр\w*) (?P<name>.+)$")


CAPRI_URL = "https://github.com/kystt29/capricorn"
# «есть обновления?», «где скачать новую версию», «твой гитхаб» — страница Капри (не «обнови страницу»)
UPDATES_RE = (r"^(есть |проверь |проверить |какие |когда |а )?(новые )?обновлени\w*( есть| вышли| для тебя| капри)?$|"
              r"(есть|проверь|проверить|вышл\w*|появил\w*|скачать|где|какие|нет ли)\b.*\bобновлени|"
              r"(что|как)( там| же| у тебя| у нас)? (по|с) обновлени|"
              r"обнови (себя|капри)|обновись|"
              # «новая версия вышла?» — да, «новая версия windows» — нет: только без уточнения или про Капри
              r"^(есть |вышла |появилась |где )?(нов\w*|последн\w*) верси\w*( капри| тебя)?( вышла| есть| появилась)?$|"
              r"(скачать|где) (\S+ )?(нов\w*|последн\w*) верси\w*( капри| тебя)?$|"
              r"(твой|свой|твою|свою) (гитхаб|страниц\w*|репозитори\w*)|гитхаб капри|где тебя (скачать|взять)")


# «когда я скажу X, отвечай Y», «на фразу X отвечай Y» — свой ответ; «забудь ответ на X»
REPLY_RE = (r"^((когда|если) (я )?(говорю|скажу|произнесу|спрошу)|на (фразу|слова|вопрос))\s+(?P<p>.+?)\s*,?\s+"
            r"(ты )?(отвечай|ответь|говори|скажи)( мне)?\s+(?P<r>.+)$")
FORGET_REPLY_RE = r"^(забудь|удали|убери) (свой |мой |этот )?ответ (на|для) (фразу |слова |вопрос )?(?P<p>.+)$"


class Actions:
    def __init__(self, cfg, base_dir, ai, save=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.ai = ai
        self.save = save  # save(раздел, название, значение или None) → текст ошибки или None
        self.browser_once = None  # браузер, названный в текущей команде («открой ютуб в хроме»)
        self.set_lists(cfg)
        self.index = AppIndex()
        threading.Thread(target=self.index.build, daemon=True).start()
        self.pending = None
        self.choice = None  # варианты приложений, из которых пользователь сейчас выбирает
        self.youtube_opened_at = 0.0  # когда Капри последний раз открывала YouTube
        self.local = None
        self.routes = [
            (r"^(выход|выключись|отключись|заверши работу|завершить работу)$", self.quit),
            (r"^(спрячься|скройся|исчезни|пока|отбой|свободна)\b|"
             r"^(уйди|уходи|убирайся|свали|сгинь|испарись|скрой себя|убери себя)\b|"
             r"(уйди|уходи|убирайся|свали|пропади|исчезни) (с|из) (экрана|монитора|глаз)", self.hide),
            (REMEMBER_RE, self.remember_cmd),
            (FORGET_RE, self.forget_cmd),
            (UPDATES_RE, self.updates),
            (REPLY_RE, self.reply_cmd),
            (FORGET_REPLY_RE, self.forget_reply_cmd),
            (r"помаши|помахай|махни|помаши ручкой|поздоровайся", self.wave),
            (r"погуляй|прогуляйся|пройдись|прогулк", self.roam),
            (r"что ты (умеешь|можешь)|как\w* (у тебя )?команд|(назови|перечисли|скажи) (\S+ )?команд|"
             r"список команд|помощь$", self.help),
            (r"(перемест|подвин|сдвин|переди|встань|иди|отойди|переберись)\w*\s.*(угол|влево|вправо|лев|прав|"
             r"верх|низ|центр|середин|другую сторону)", self.move),
            (r"(обнови|обновить|установи обновления на) (компьютер|пк|комп|систему)", self.update_system),
            (r"^скачай(?:\s+(.+))?$", self.install_package),
            (r"^(напомни(?: мне)?(?: пожалуйста)?|создай напоминание|создать напоминание|добавь напоминание|поставь напоминание|новое напоминание|напоминание)$", self.reminder_start),
            (r"^(?:выбери|назови|скажи|сгенерируй|загадай|дай) (?:мне )?(?:случайное )?число от .+ до .+$|^(?:рандом|случайное число|рандомное число) от .+ до .+$", self.random_number),
            (r"^(?:подбрось|кинь|брось|подкинь) (?:мне )?(?:монетку|монету)$|^(?:орел или решка|орёл или решка|монетка|подбрось монетку)$", self.coin_flip),
            (r"(выключи|перезагрузи|перезапусти) (компьютер|пк|комп|систему)", self.power),
            (r"заблокируй (компьютер|пк|комп|экран)", self.lock),
            (r"громкост|громче|тише|(вкл|выкл|отключ|убер|верни|включ)\w* звук|звук (на|в) |без звука|замуть", self.volume),
            (r"яркост|ярче|темнее", self.brightness),
            (r"(проанализ|анализ|диагност|провер|просканир|сканир)\w*.*(пк|компьютер|комп|систем|ошибк)", self.analyze),
            # --- микрофон (выполняет main: там живёт слушатель) ---
            (r"(какой|какое) (сейчас )?(микрофон|устройство ввода)|что за микрофон|(через )?какой микрофон",
             lambda c, m: Reply("", "mic_info")),
            (r"(смени|поменяй|переключи|следующий|другой) (\S+ )?(микрофон|устройство ввода)",
             lambda c, m: Reply("", "mic_next")),
            (r"что (сейчас )?(играет|звучит|за (песн|трек|музык|видео)\w*)", self.media_info),
            (rf"^({automation.TYPE_VERBS})\s+\S", self.type_cmd),
            (r"^(открой|зайди в|запусти) (телеграм\w*|telegram|телег\w*|тг)$", self.open_telegram),
            (r"(закрой|закрыть|убери) (эту |текущую |\S+ )?вкладк", self.tab),
            (r"нов(ую|ая) вкладк|\b(верни|восстанови) (закрыт\w* )?вкладк|(следующ|предыдущ)\w* вкладк", self.tab),
            (r"^(создай|сделай|создать) (?P<what>.*(папк|файл|документ|таблиц).*)$", self.create),
            (r"^(найди|поищи|загугли|погугли|расскажи|узнай|посмотри)( мне)?( в интернете| в гугле| в сети)? "
             r"(информаци\w*|инфу|инфо|что-нибудь|что нибудь|всё|все|сведения|данные|статью)( (о|об|про|по))? "
             r"(?P<q>.+)$", self.info),
            (r"^(открой|включи|покажи|найди|запусти|поставь|врубай|вруби)( мне)?( какое-нибудь| какой-нибудь| "
             r"какое нибудь| любое| любой| одно| одно)? (?P<kind>видео\w*|видос\w*|"
             r"ролик\w*|клип\w*|песн\w*|трек\w*|музык\w*)( на ютуб\w*| в ютуб\w*)?( (с|со|про|о|об|где|на тему))? "
             r"(?P<q>.+)$", self.video),
            (r"^(открой|включи|покажи|запусти|поставь)( мне)? (на|в) (ютуб\w*|youtube) (?P<q>.+)$", self.video),
            (r"^(найди|поищи|загугли|погугли|ищи)( в интернете| в гугле| в google)? (?P<q>.+)$", self.search),
            (r"^(открой|запусти|включи|зайди на|перейди на|покажи) (?P<t>.+)$", self.open),
            (r"^((сними|снять) (задачу|процесс\w*)|(заверши|завершить|убей|убить|останови|закрой) "
             r"(все )?(процесс\w*|задачу|задачи)|кильни|килльни|грохни|прибей) (?P<t>.+)$", self.kill_process),
            (r"что (сейчас )?(грузит|нагружает|жрет|ест|тормозит)|(какие|какой) процесс\w* (грузят|нагружают|жрут|"
             r"тормозят)|самые (тяжелые|прожорливые) процессы", self.top_processes),
            (r"^(закрой|заверши|выключи|убей) (?!окно$)(?P<t>.+)$", self.close),
            (r"который час|сколько времени|сколько сейчас времени|время сейчас", self.time),
            (r"какое (сегодня )?число|какая (сегодня )?дата|какой сегодня день", self.date),
        ]

    def handle(self, cmd):
        c = norm(cmd)
        cancel_ensure()  # новая команда — старая проверка «заиграло ли видео» больше не нужна
        # Надёжно распознаём короткие формулировки напоминания до остальных маршрутов.
        # STT иногда добавляет «пожалуйста» или оставляет обращение «Капри» в тексте.
        if re.fullmatch(r"(?:(?:капри|капри)\s+)?напомни(?:\s+мне)?(?:\s+пожалуйста)?", c):
            return self.reminder_start(c, None)
        if self.pending:
            action, self.pending = self.pending, None
            if re.match(r"^(да|давай|подтверждаю|конечно|ага)\b", c):
                return action()
            if re.match(r"^(нет|не надо|отмена|стоп)\b", c):
                return Reply(line("cancel"))
        if self.choice:  # ждём ответ на «какое открыть / закрыть?»
            options, self.choice = self.choice, None
            pick = self.resolve_choice(c, options)
            if pick is not None:
                name, act = options[pick]
                if callable(act):
                    return act()
                started = self.index.launch_best(name, [(1.0, name, act)])
                return Reply(line("launch", t=started) if started else line("app_missing", t=name))
            if re.match(r"^(никак\w*|ни одно|ни то|отмена|не надо|нет|ничего)\b", c):
                return Reply(line("cancel"))
            # ответ не про выбор — значит, это новая команда
        custom_command = self.run_custom_command(c)
        if custom_command:
            return custom_command
        custom = self.custom_reply(c)  # своя фраза целиком — не делим её на цепочку команд
        if custom:
            return custom
        # «запомни … как X и Y» — название целиком, не цепочка команд
        whole = any(re.match(p, c) for p in (REMEMBER_RE, FORGET_RE, REPLY_RE, FORGET_REPLY_RE))
        steps = None if whole else automation.split_chain(c)
        if steps:
            try:
                return self.run_chain(steps)
            except Exception as e:
                log.exception("Цепочка %s", steps)
                return Reply(line("error", e=e))
        return self.handle_one(c)

    @staticmethod
    def resolve_choice(c, options):
        """«второе», «два», «клод», «первую» → номер варианта или None."""
        ords = [(r"перв\w*|один|одну|1", 0), (r"втор\w*|два|две|2", 1), (r"трет\w*|три|3", 2),
                (r"последн\w*", len(options) - 1)]
        for pattern, idx in ords:
            if re.search(rf"(^|\s)({pattern})(\s|$)", c) and idx < len(options):
                return idx
        words = re.sub(r"^(открой|запусти|давай|вот|это|ну)\s+", "", c)
        best, best_i = 0.0, None
        for i, (name, _) in enumerate(options):
            for v in {words, translit(words)}:
                s = 1.0 if v and v in name else difflib.SequenceMatcher(None, v, name).ratio()
                if AppIndex.sounds_like(v, name.split()[0] if name.split() else name):
                    s = max(s, 0.86)
                if s > best:
                    best, best_i = s, i
        return best_i if best >= 0.6 else None

    def web(self, url):
        """Открыть URL в браузере, выбранном пользователем; системный — только по явному выбору."""
        preferred = self.provider_preferences.get("browser") or self.cfg.get("browser")
        system_choices = ("__system__", "system", "default", "system-default")

        # Браузер, названный в текущей голосовой команде, имеет приоритет над общим выбором.
        one_time = self.browser_once
        if one_time:
            exe = find_browser(one_time)
            if exe:
                try:
                    _popen_detached([exe, url])
                    return
                except Exception:
                    log.exception("Не удалось открыть URL в браузере из команды %s", one_time)

        # Если пользователь явно выбрал системный браузер, не пытаемся угадать другой.
        if not one_time and (not preferred or preferred in system_choices):
            webbrowser.open(url)
            return

        if not one_time and preferred:
            exe = find_browser(preferred)
            if exe:
                try:
                    _popen_detached([exe, url])
                    return
                except Exception:
                    log.exception("Не удалось запустить выбранный браузер %s", preferred)

            match = discovery.find_provider("browsers", str(preferred))
            if match:
                _, item = match
                try:
                    if item.get("kind") == "binary":
                        _popen_detached([item["target"], url])
                        return
                    if item.get("kind") == "desktop":
                        desktop_file = item.get("desktop_file", item["target"])
                        desktop_id = os.path.basename(desktop_file)
                        if desktop_id.endswith(".desktop"):
                            desktop_id = desktop_id[:-8]
                        gtk_launch = shutil.which("gtk-launch")
                        if gtk_launch:
                            _popen_detached([gtk_launch, desktop_id, url])
                            return
                        # Запасной вариант, если ярлык найден, но gtk-launch не установлен.
                        exec_line = item.get("exec", "")
                        argv = shlex.split(exec_line)
                        args = []
                        has_url = False
                        for token in argv:
                            if token in ("%u", "%U", "%f", "%F"):
                                if not has_url:
                                    args.append(url)
                                    has_url = True
                            elif token.startswith("%"):
                                continue
                            else:
                                args.append(token)
                        if args:
                            if not has_url:
                                args.append(url)
                            _popen_detached(args)
                            return
                except Exception:
                    log.exception("Не удалось открыть URL в выбранном браузере %s", preferred)

            # Не открываем незаметно Firefox вместо выбранного Zen (или другого выбранного браузера).
            log.error("Выбранный браузер %r не удалось запустить; URL не перенаправлен в другой браузер: %s",
                      preferred, url)
            return

        # Если указан явный браузер, но он недоступен, сохраняем старое системное поведение как fallback.
        webbrowser.open(url)

    def handle_one(self, c):
        m = BROWSER_SUFFIX.search(c)
        self.browser_once, missing = None, None
        if m:  # «открой ютуб в хроме» — браузер только для этой команды
            if find_browser(m.group("b")):
                self.browser_once = m.group("b")
            else:
                missing = next((BROWSER_NAMES[k] for p, k in BROWSER_WORDS if re.fullmatch(p, m.group("b"))),
                               m.group("b"))
            c = c[:m.start()].strip()
        try:
            r = self._handle_one(c)
        finally:
            self.browser_once = None
        if r and missing:
            r.text = line("browser_missing", b=missing) + " " + r.text
        return r

    def _handle_one(self, c):
        provider_reply = self.provider_command(c)
        if provider_reply:
            return provider_reply
        r = self.custom_reply(c)  # свои ответы — раньше команд и нейросети
        if r:
            return r
        for pattern, fn in self.routes:
            m = re.search(pattern, c)
            if m:
                try:
                    r = fn(c, m)
                except Exception as e:
                    log.exception("Команда %s", c)
                    return Reply(line("error", e=e))
                if r:
                    return r
        return None

    # --- свои сайты и программы ---
    def set_lists(self, cfg):
        """Сайты, программы и названия из настроек — при запуске и когда config.json поменяли."""
        self.sites = {norm(k): v for k, v in cfg.get("sites", {}).items()}
        self.apps = {norm(k): v for k, v in cfg.get("apps", {}).items()}
        self.aliases = {norm(k): v for k, v in cfg.get("app_aliases", {}).items()}
        self.replies = {norm(k): v for k, v in cfg.get("replies", {}).items() if v}
        # Для пользовательских фраз сравниваем и кириллицу, и её латинскую транслитерацию:
        # Vosk может вернуть «тест», а другой движок — «test». Обычные маршруты не меняем.
        self.custom_commands = {norm(translit(k)): v for k, v in cfg.get("custom_commands", {}).items()
                                if isinstance(v, dict) and v.get("action") and v.get("target")}
        self.provider_preferences = dict(cfg.get("provider_preferences", {}))
        # If exactly one native browser is discoverable, use it automatically; multiple choices stay user-controlled.
        if not self.provider_preferences.get("browser") and not cfg.get("browser"):
            try:
                browsers = discovery.scan().get("browsers", {})
                if len(browsers) == 1:
                    self.provider_preferences["browser"] = next(iter(browsers))
            except Exception:
                log.exception("Автоопределение браузера")

    def run_custom_command(self, phrase):
        """Выполняет пользовательскую команду, созданную в окне «Команды»."""
        # Сопоставление устойчиво к латинской записи русских слов: «мой тест» == «мой test».
        command = self.custom_commands.get(norm(translit(phrase)))
        if not command:
            return None
        kind, target = command.get("action"), str(command.get("target", "")).strip()
        if not target:
            return Reply("У этой пользовательской команды не указано действие.")
        try:
            if kind == "site":
                if not re.match(r"^https?://", target, re.I):
                    return Reply("Не могу открыть сайт: адрес должен начинаться с http:// или https://.")
                self.web(target)
                return Reply("Открываю сайт.")
            if kind == "app":
                if target.startswith(("http://", "https://")) or os.path.exists(os.path.expanduser(target)):
                    self._run(os.path.expanduser(target))
                else:
                    # shell=False: пользовательская команда не исполняется через оболочку.
                    args = shlex.split(target, posix=True)
                    if not args:
                        return Reply("Команда запуска пустая.")
                    _popen_detached(args, creationflags=NO_WINDOW if IS_WINDOWS else 0)
                return Reply("Запускаю.")
            if kind == "text":
                automation.input_text(target)
                return Reply("Ввела текст.")
            if kind == "keys":
                keys, unknown = parse_keys(target)
                if unknown or not keys:
                    return Reply("Не распознала сочетание клавиш. Проверь его во вкладке «Команды».")
                send_keys(keys)
                return Reply("Нажала сочетание клавиш.")
        except Exception as e:
            log.exception("Пользовательская команда %r завершилась ошибкой", phrase)
            return Reply(f"Не получилось выполнить команду: {e}")
        return Reply("У этой команды неизвестный тип действия.")

    def _save_provider(self, category, value):
        """Persist a provider preference in config.json and apply it for this session."""
        prefs = self.cfg.setdefault("provider_preferences", {})
        if self.save:
            err = self.save("provider_preferences", category, value)
            if err:
                return err
        prefs[category] = value
        self.provider_preferences = dict(prefs)
        if category == "browser":
            self.cfg["browser"] = value
        elif category == "stt":
            self.cfg["use_google_stt"] = value == "google"
        elif category == "tts":
            self.cfg["tts_engine"] = value
            if getattr(self, "speaker", None) is not None:
                self.speaker.engine = value
                if value == "silero":
                    threading.Thread(target=self.speaker._warmup, daemon=True).start()
        return None

    def _provider_scan_reply(self):
        found = discovery.scan()
        parts = []
        for title, key in (("браузеры", "browsers"), ("музыкальные приложения", "music"), ("игровые лаунчеры", "games")):
            items = list(found.get(key, {}).keys())
            if items:
                parts.append(title + ": " + ", ".join(items[:8]))
        available = [name for name, ok in found.get("modules", {}).items() if ok]
        missing = [name for name, ok in found.get("modules", {}).items() if not ok]
        if available:
            parts.append("доступные компоненты: " + ", ".join(available[:8]))
        if missing:
            parts.append("не обнаружены или не установлены: " + ", ".join(missing[:5]))
        if not parts:
            parts.append("не нашла дополнительных приложений, но могу открывать сайты и выполнять встроенные команды")
        return Reply("Проверила окружение. " + ". ".join(parts) + ".")

    @staticmethod
    def _provider_requested(c, category):
        # Разрешаем поправить ошибочный выбор короткой репликой: «нет, используй ...».
        c = re.sub(r"^(?:нет|не то|не эту|не тот|не тот вариант|неправильно выбрала)\s+", "", c)
        patterns = {
            "music": [r"^для музыки (?:используй|выбери|активируй) (.+)$",
                      r"^(?:используй|выбери|активируй) (.+?) для музыки$",
                      r"^(?:переключи|поменяй|смени) музыку на (.+)$", r"^музыку через (.+)$"],
            "browser": [r"^для браузера (?:используй|выбери|активируй) (.+)$",
                        r"^(?:используй|выбери|активируй) (.+?) (?:как браузер|для браузера)$",
                        r"^(?:переключи|поменяй|смени) браузер на (.+)$", r"^открывай сайты через (.+)$"],
            "stt": [r"^для распознавания речи (?:используй|выбери|активируй) (.+)$",
                    r"^(?:используй|выбери|активируй) (.+?) для распознавания речи$",
                    r"^(?:переключи|поменяй|смени) распознавание на (.+)$"],
            "tts": [r"^для (?:озвучки|синтеза речи) (?:используй|выбери|активируй) (.+)$",
                    r"^(?:используй|выбери|активируй) (.+?) (?:для голоса|для озвучки)$",
                    r"^(?:переключи|поменяй|смени) голосовой движок на (.+)$"],
        }
        for pattern in patterns[category]:
            m = re.fullmatch(pattern, c)
            if m:
                return m.group(1).strip()
        return None

    def provider_command(self, c):
        """Voice-first discovery and explicit user override for providers."""
        if re.fullmatch(r"(какие )?(программы|приложения|браузеры) ты (нашла|обнаружила)|"
                        r"что (ты )?(нашла|обнаружила) на компьютере|проверь (программы|приложения)|"
                        r"просканируй компьютер|что у меня установлено", c):
            return self._provider_scan_reply()

        # «включи музыку» uses the selected provider; it never silently changes the user's choice.
        if re.fullmatch(r"(включи|запусти|открой|поставь) (мне )?(музыку|музыкальный плеер)", c):
            chosen = self.provider_preferences.get("music")
            if not chosen:
                found = discovery.scan().get("music", {})
                native = [(name, item) for name, item in found.items() if item.get("kind") != "web"]
                if len(native) == 1:
                    chosen = native[0][0]
                    err = self._save_provider("music", chosen)
                    if err:
                        return Reply("Нашла один музыкальный плеер, но не смогла сохранить выбор: " + err)
                elif not native:
                    return Reply("Не нашла установленный музыкальный плеер. Могу открыть Яндекс Музыку или Spotify в браузере. Скажи: «используй Яндекс Музыку для музыки» или «используй Spotify для музыки».")
                else:
                    return Reply("Нашла несколько музыкальных приложений: " + ", ".join(name for name, _ in native[:6]) + ". Скажи, например: «используй Spotify для музыки».")
            match = discovery.find_provider("music", chosen)
            if not match:
                return Reply(f"Ранее выбранный музыкальный сервис {chosen} больше не найден. Скажи, какой использовать теперь.")
            name, item = match
            try:
                if item["kind"] == "web":
                    self.web(item["target"])
                elif item["kind"] == "desktop":
                    self.index.launch(("file", item["target"]))
                elif item["kind"] == "shortcut" and IS_WINDOWS:
                    os.startfile(item["target"])
                else:
                    _popen_detached([item["target"]])
                return Reply(f"Открываю {item.get('name', name)}.")
            except Exception as e:
                log.exception("Не удалось открыть музыкальный сервис %s", name)
                return Reply(f"Не получилось запустить {name}: {e}")

        for category in ("music", "browser", "stt", "tts"):
            requested = self._provider_requested(c, category)
            if not requested:
                continue
            if category in ("music", "browser"):
                match = discovery.find_provider(category + "s" if category == "browser" else category, requested)
                if not match:
                    options = discovery.scan().get("browsers" if category == "browser" else "music", {})
                    return Reply(f"Не нашла «{requested}» среди доступных вариантов. "
                                 f"Могу предложить: {', '.join(options.keys()) or 'ничего подходящего' }.")
                canonical, item = match
                if category == "browser" and item.get("kind") == "web":
                    return Reply("Этот вариант не является установленным браузером, поэтому не могу выбрать его для открытия сайтов.")
                err = self._save_provider(category, canonical)
                if err:
                    return Reply("Не получилось сохранить выбор: " + err)
                if category == "music":
                    return Reply(f"Хорошо, для музыки выбрала {item.get('name', canonical)}. Скажи «включи музыку», чтобы открыть его.")
                return Reply(f"Хорошо, теперь буду открывать сайты через {canonical}.")

            requested_n = requested.casefold().replace("ё", "е").strip()
            if category == "stt":
                if any(x in requested_n for x in ("vosk", "офлайн", "локальн")):
                    model = os.path.join(self.base_dir, self.cfg.get("vosk_model", "models/vosk-model-small-ru-0.22"))
                    if not os.path.isdir(model):
                        return Reply("Не могу выбрать Vosk: папка модели распознавания не найдена. Сначала установи модель речи.")
                    value, label = "vosk", "Vosk офлайн"
                elif any(x in requested_n for x in ("google", "гугл", "онлайн")):
                    if importlib.util.find_spec("speech_recognition") is None:
                        return Reply("Google-распознавание недоступно: не установлен пакет SpeechRecognition. Пока оставляю текущий вариант.")
                    value, label = "google", "Vosk с дополнительной проверкой через Google"
                else:
                    return Reply("Для распознавания речи доступны Vosk (офлайн) и Google как дополнительная проверка. Скажи: «используй Vosk для распознавания речи».")
                err = self._save_provider("stt", value)
                return Reply("Не получилось сохранить выбор: " + err if err else f"Готово. Выбран режим: {label}.")

            if any(x in requested_n for x in ("silero", "офлайн", "локальн")):
                if importlib.util.find_spec("torch") is None:
                    return Reply("Silero пока недоступен: не установлен PyTorch. Ничего не переключала.")
                value, label = "silero", "Silero офлайн"
            elif any(x in requested_n for x in ("edge", "microsoft", "онлайн")):
                if importlib.util.find_spec("edge_tts") is None:
                    return Reply("Edge TTS пока недоступен: пакет edge-tts не установлен. Ничего не переключала.")
                value, label = "edge", "Microsoft Edge TTS"
            else:
                return Reply("Для озвучки доступны Edge TTS (онлайн) и Silero (офлайн при установленном PyTorch).")
            err = self._save_provider("tts", value)
            return Reply("Не получилось сохранить выбор: " + err if err else f"Готово. Переключила озвучку на {label}.")
        return None

    def custom_reply(self, c):
        """Свой ответ на фразу из config.json → "replies" (точно или почти дословно) или None."""
        if not self.replies:
            return None
        if c in self.replies:
            return Reply(self.replies[c])
        close = difflib.get_close_matches(c, list(self.replies), n=1, cutoff=0.85)
        if close:
            return Reply(self.replies[close[0]])
        # фраза целиком внутри команды и лишнего немного: «ну как дела» → «как дела»
        for phrase in sorted(self.replies, key=len, reverse=True):
            if len(c) - len(phrase) <= 12 and re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", c):
                return Reply(self.replies[phrase])
        return None

    def remember(self, section, name, target):
        """Записать сайт / программу / свой ответ (section "sites" / "apps" / "replies") в config.json
        и сразу начать её узнавать. target=None — забыть. Возвращает текст ошибки или None."""
        name = norm(name)
        storage_section = "custom_commands" if section == "commands" else section
        err = self.save(storage_section, name, target) if self.save else None
        if err:
            return err
        book = {"sites": self.sites, "apps": self.apps, "replies": self.replies,
                "commands": self.custom_commands, "custom_commands": self.custom_commands}.get(section)
        if book is None:
            return "неизвестный тип записи"
        stored = self.cfg.setdefault(storage_section, {})
        if target is None:
            book.pop(name, None)
            stored.pop(name, None)
        else:
            book[name] = target
            stored[name] = target
        return None

    def remember_cmd(self, c, m):
        """«запомни этот сайт как музыка» — адрес открытой вкладки; «запомни эту программу как запрет» —
        программа, окно которой сейчас на экране."""
        name = re.sub(r"^(как|под названием|под именем|с названием|назови)\s+", "", (m.group("name") or "").strip())
        name = name.strip(" «»\"")
        site = re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what"))
        if not name:
            return Reply(line("remember_noname", what="этот сайт" if site else "эту программу"))
        if site:
            if not IS_WINDOWS:
                return Reply("На Linux пока не умею считывать адрес открытой вкладки этой командой. Сайты можно добавить через настройки.")
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            url = media.page_url(hwnd)
            if not url:
                return Reply(line("remember_nosite"))
            err = self.remember("sites", name, url)
            short = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            return Reply(line("remember_fail", e=err) if err else line("remember_site", t=name, url=short))
        if IS_WINDOWS:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            exe = automation.window_exe(hwnd)
        else:
            exe = automation.foreground_app_target()
        base = os.path.splitext(os.path.basename(exe or ""))[0].lower()
        if not exe or base in PROTECTED_PROCS or base == "applicationframehost":
            return Reply(line("remember_noapp"))
        err = self.remember("apps", name, exe)
        return Reply(line("remember_fail", e=err) if err else line("remember_app", t=name, app=base))

    def forget_cmd(self, c, m):
        name = norm(m.group("name"))
        section = "sites" if re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what")) else "apps"
        book = self.sites if section == "sites" else self.apps
        if name not in book:
            return Reply(line("forget_missing", t=name))
        err = self.remember(section, name, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=name))

    def updates(self, c, m):
        """Страница Капри на GitHub — там новые версии."""
        self.web(CAPRI_URL)
        return Reply(line("updates"))

    def reply_cmd(self, c, m):
        """«Когда я скажу „как дела“, отвечай „лучше всех“» — свой ответ на фразу."""
        phrase = m.group("p").strip(" ,")
        text = m.group("r").strip(" ,")
        text = text[:1].upper() + text[1:]
        if not re.search(r"[.!?…]$", text):
            text += "."
        err = self.remember("replies", phrase, text)
        return Reply(line("remember_fail", e=err) if err else line("reply_saved", p=phrase, r=text))

    def forget_reply_cmd(self, c, m):
        phrase = norm(m.group("p"))
        close = phrase if phrase in self.replies else next(
            iter(difflib.get_close_matches(phrase, list(self.replies), n=1, cutoff=0.85)), None)
        if not close:
            return Reply(line("forget_missing", t=phrase))
        err = self.remember("replies", close, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=close))

    def fallback(self, cmd, force_search=False):
        """Без ИИ: вопрос ищем в Google (если search_when_unsure не выключен), остальное — «не знаю команды»."""
        c = norm(cmd)
        if force_search or re.match(r"^(что|кто|как|какой|какая|какое|какие|почему|зачем|сколько|где|когда|куда|"
                     r"откуда|чем|чей|расскажи|объясни|скажи)\b", c):
            if not self.cfg.get("search_when_unsure", True):
                return Reply(line("dont_know"))
            self.web("https://www.google.com/search?q=" + urllib.parse.quote(cmd))
            return Reply(line("search_q"))
        return Reply(line("unknown"))

    # --- команды ---
    def quit(self, c, m):
        return Reply(line("quit"), "quit")

    def hide(self, c, m):
        return Reply(line("hide"), "hide")

    def wave(self, c, m):
        return Reply(line("wave"), "wave")

    def roam(self, c, m):
        return Reply(line("roam"), "roam")


    def _launch_telegram_client(self):
        """Запустить выбранный Telegram-клиент с Linux fallback."""
        if IS_WINDOWS:
            found = self.index.find("телеграм", {"телеграм": "telegram"}) if self.index.ready.wait(8) else None
            if not found:
                return False
            self.index.launch(found[1])
            return True

        selected = str(self.cfg.get("telegram_client", "") or "").strip()

        # Сначала пробуем явно выбранный пользователем клиент.
        if selected:
            try:
                if selected.startswith("cmd:"):
                    command = shlex.split(selected[4:])
                    if command:
                        subprocess.Popen(command, start_new_session=True)
                        return True

                elif selected.endswith(".desktop") and os.path.isfile(selected):
                    self.index.launch(("file", selected))
                    return True

                elif os.path.isabs(selected) and os.path.exists(selected):
                    subprocess.Popen([selected], start_new_session=True)
                    return True

                else:
                    command = shlex.split(selected)
                    if command:
                        subprocess.Popen(command, start_new_session=True)
                        return True

            except (OSError, ValueError) as exc:
                log.exception("Не удалось запустить выбранный Telegram-клиент: %s", exc)

        # Прямой запуск установленного Telegram Desktop.
        telegram_bin = "/usr/bin/Telegram"
        if os.path.isfile(telegram_bin) and os.access(telegram_bin, os.X_OK):
            try:
                subprocess.Popen([telegram_bin], start_new_session=True)
                return True
            except OSError:
                log.exception("Не удалось запустить /usr/bin/Telegram")

        # Последняя попытка — поиск через индекс приложений.
        found = self.index.find("телеграм", {"телеграм": "telegram"}) if self.index.ready.wait(8) else None
        if found:
            try:
                self.index.launch(found[1])
                return True
            except OSError:
                log.exception("Не удалось запустить Telegram через индекс")

        return False

    def random_number(self, c, m):
        """Выбирает случайное целое число в указанном включительном диапазоне."""
        s = norm(c).replace("ё", "е")
        match = re.search(r"\bот\s+(.+?)\s+до\s+(.+?)\s*$", s)
        if not match:
            return Reply("Назови диапазон, например: выбери число от 1 до 100.")
        low = parse_number(match.group(1))
        high = parse_number(match.group(2))
        if low is None or high is None:
            return Reply("Не поняла границы диапазона. Скажи, например: выбери число от 1 до 100.")
        if low > high:
            low, high = high, low
        if high - low > 10**12:
            return Reply("Диапазон слишком большой. Выбери числа в пределах триллиона.")
        value = random.randint(low, high)
        return Reply(f"Выпало число {value}.")

    def coin_flip(self, c, m):
        """Симулирует подбрасывание монеты."""
        result = random.choice(("орёл", "решка"))
        return Reply(f"Выпало: {result}!")

    def reminder_start(self, c, m):
        """Начать диалог создания напоминания; дальнейшие шаги ведёт main.py."""
        return Reply("Давай создадим напоминание. Скажи его содержимое, а в конце произнеси: конец заметки.", "reminder_start")

    def open_telegram(self, c, m):
        """Открыть выбранный Telegram-клиент, не взаимодействуя с чатами."""
        if self._launch_telegram_client():
            return Reply("Открываю Telegram.")
        return Reply(line("app_missing", t=self.cfg.get("telegram_client_name", "Telegram")), "fail")

    def run_chain(self, steps):
        """Выполняет шаги по очереди, держа в уме, в каком окне мы сейчас (Telegram, YouTube, браузер…)."""
        log.info("Цепочка: %s", steps)
        context = None   # ключевые слова заголовка окна, с которым работаем
        kind = None      # youtube / browser / None
        said = []
        in_search = False  # курсор уже стоит в поле поиска
        for step in steps:
            s = norm(step)
            typed = automation.parse_type(s)
            if typed:
                text, send = typed
                if context and not automation.wait_and_focus(context, timeout=6):
                    # не смогли переключиться — ничего не печатаем, чтобы не написать в чужое окно
                    return Reply(" ".join(said + [line("focus_fail")]))
                if kind == "youtube":
                    youtube_search_in_tab(text)
                    said.append(line("yt", q=text))
                    continue
                if kind in ("youtube", "browser"):
                    # на сайте «напиши X» = поиск X: сначала курсор в строку поиска, потом Enter
                    automation.input_text(text)
                    if send is not False:
                        time.sleep(0.1)
                        send_keys(["enter"])
                    said.append(line("web", q=text))
                    continue
                if send is None:
                    send = self.cfg.get("type_press_enter", True)
                automation.input_text(text)
                if send:
                    time.sleep(0.1)
                    send_keys(["enter"])
                said.append(line("sent" if send else "typed", t=text))
                continue
            q = re.match(r"^(найди|поищи)\s+(?P<q>.+)$", s)
            if q and kind == "youtube":
                if kind == "youtube":
                    if not automation.wait_and_focus(context, timeout=6):
                        return Reply(" ".join(said + [line("focus_fail")]))
                    youtube_search_in_tab(q.group("q"))
                    said.append(line("yt", q=q.group("q")))
                continue
            r = self.handle_one(s)
            if r is None:
                said.append(line("chain_skip", t=step))
                continue
            if r.action in ("fail", "ask"):  # шаг не удался или нужно уточнить — дальше не идём
                return Reply(" ".join(said + [r.text]))
            words = s.split()
            for w in words:
                if w in automation.APP_WINDOWS:
                    context = automation.APP_WINDOWS[w]
                    kind = {"ютуб": "youtube", "youtube": "youtube"}.get(w, "browser" if w == "браузер" else None)
            if context and re.match(r"^(открой|запусти|зайди|перейди)\b", s):
                automation.wait_and_focus(context, timeout=10)
                time.sleep(2.5 if kind in ("youtube", "browser") else 0.4)  # сайту нужно загрузиться
                in_search = False
        return Reply(" ".join(said) if said else line("done"))

    def help(self, c, m):
        return Reply(line("help"))

    def move(self, c, m):
        """Куда переместиться: «в левый нижний угол», «вправо», «в центр», «в другую сторону»."""
        if re.search(r"другую сторону|в другую", c):
            where = "other"
        elif re.search(r"центр|середин", c):
            where = "center"
        else:
            h = "left" if re.search(r"лев|влево", c) else "right" if re.search(r"прав|вправо", c) else ""
            v = "top" if re.search(r"верх|наверх", c) else "bottom" if re.search(r"низ|вниз", c) else ""
            where = "-".join(x for x in (v, h) if x) or "other"
        return Reply(line("move"), "move:" + where)
    def update_system(self, c, m):
        """Запускает обновление системы в видимом терминале."""
        if IS_WINDOWS:
            return Reply("Обновление системы доступно только в Linux.")

        manager = linux_compat.get_package_manager()
        command = linux_compat.get_update_command(manager)

        if not command:
            return Reply("Не удалось определить пакетный менеджер этого дистрибутива.")

        terminals = (
            ("konsole", ["-e", "bash", "-lc"]),
            ("kitty", []),
            ("foot", []),
            ("alacritty", ["-e"]),
            ("wezterm", ["start", "--"]),
            ("xterm", ["-e"]),
        )
        terminal = next(((exe, args) for exe, args in terminals if shutil.which(exe)), None)
        if not terminal:
            return Reply("Не нашла терминал для обновления.")

        exe, args = terminal
        update_command = shlex.join(command)

        script = (
            "printf '\\n=== Обновление системы ===\\n'; "
            "printf 'Введи пароль, если его запросит система.\\n'; "
            + update_command + "; status=$?; "
            "if [ $status -eq 0 ]; then printf '\\nОбновление завершено успешно.\\n'; "
            "else printf '\\nОбновление завершилось с ошибкой (код %s).\\n' \"$status\"; fi; "
            "printf '\\nНажми Enter, чтобы закрыть это окно...'; read -r _"
        )

        try:
            if exe == "konsole":
                subprocess.Popen([exe, *args, script], start_new_session=True)
            elif exe in ("kitty", "foot"):
                subprocess.Popen([exe, "bash", "-lc", script], start_new_session=True)
            else:
                subprocess.Popen([exe, *args, "bash", "-lc", script], start_new_session=True)
        except Exception as e:
            log.exception("Не удалось открыть терминал обновления")
            return Reply(f"Не получилось запустить обновление: {e}")

        return Reply("Открыла терминал обновления. Если потребуется, введи пароль там.")

    def install_package(self, c, m):
        """Устанавливает пакет через пакетный менеджер текущего дистрибутива."""
        raw_name = (m.group(1) or "").strip().strip(".,!?;:")
        vague = {
            "", "что-то", "что то", "что-нибудь", "что нибудь", "что угодно",
            "что нибудь скачать", "приложение", "какое-нибудь приложение",
            "какое нибудь приложение", "что-нибудь скачать",
            "что то скачать", "что-нибудь установить", "что нибудь установить",
        }

        if norm(raw_name) in vague:
            return Reply("Какое приложение скачать? Сейчас открою окно, где можно ввести его название.", "package_input")

        if IS_WINDOWS:
            return Reply("Установка пакетов таким способом доступна только в Linux.")

        if not re.fullmatch(r"[A-Za-z0-9@._+:-]+", raw_name):
            return Reply("Для этой команды требуется точное имя пакета, например: «Капри, скачай firefox».")

        manager = linux_compat.get_package_manager()
        command = linux_compat.get_install_command(raw_name, manager)

        if not command:
            return Reply("Не удалось определить пакетный менеджер этого дистрибутива.")

        terminal = next(
            (exe for exe in ("konsole", "kitty", "foot", "alacritty", "wezterm", "xterm") if shutil.which(exe)),
            None,
        )
        if not terminal:
            return Reply("Не нашла терминал для установки.")

        install_command = shlex.join(command)

        script = (
            "printf '\\n=== Установка пакета: %s ===\\n' " + shlex.quote(raw_name) + "; "
            "printf 'Введи пароль, если его запросит система.\\n'; "
            + install_command + "; status=$?; "
            "if [ $status -eq 0 ]; then "
            "  printf '\\nУстановка завершена успешно.\\n'; "
            "elif [ $status -eq 130 ] || [ $status -eq 143 ]; then "
            "  printf '\\nУстановка отменена пользователем.\\n'; "
            + (
                "elif [ $status -ne 0 ]; then "
                "  printf '\\nparu завершился с ошибкой. Пробую pacman...\\n'; "
                + shlex.join(linux_compat.get_install_command(raw_name, "pacman")) + "; fallback_status=$?; "
                "  if [ $fallback_status -eq 0 ]; then "
                "    printf '\\nУстановка через pacman завершена успешно.\\n'; "
                "  else printf '\\nОшибка pacman (код %s).\\n' \"$fallback_status\"; fi; "
                if manager == "paru" and shutil.which("pacman")
                else ""
            )
            + "fi; "
            "printf '\\nНажми Enter, чтобы закрыть это окно...'; read -r _"
        )

        try:
            if terminal == "konsole":
                args = [terminal, "-e", "bash", "-lc", script]
            elif terminal in ("kitty", "foot"):
                args = [terminal, "bash", "-lc", script]
            elif terminal == "wezterm":
                args = [terminal, "start", "--", "bash", "-lc", script]
            else:
                args = [terminal, "-e", "bash", "-lc", script]

            subprocess.Popen(args, start_new_session=True)
        except Exception as e:
            log.exception("Не удалось открыть терминал установки пакета")
            return Reply(f"Не получилось запустить установку: {e}")

        return Reply(f"Открыла терминал для установки {raw_name}. Если потребуется, введи пароль там.")

    def power(self, c, m):
        restart = m.group(1).startswith("перезаг") or m.group(1).startswith("перезап")
        flag = "/r" if restart else "/s"

        def do():
            if not IS_WINDOWS:
                subprocess.Popen(["systemctl", "reboot" if restart else "poweroff"])
            else:
                subprocess.Popen(["shutdown", flag, "/t", "10"], creationflags=NO_WINDOW)
            return Reply(line("power_do", what_noun="перезагрузка" if restart else "выключение"))
        self.pending = do
        verb = "перезагрузить" if restart else "выключить"
        return Reply(line("power_ask", what=verb, what_cap=verb.capitalize()))

    def lock(self, c, m):
        if not IS_WINDOWS:
            if shutil.which("hyprlock"):
                subprocess.Popen(["hyprlock"])
                return Reply(line("lock"))
            return Reply("Для блокировки экрана установи hyprlock из репозиториев своего дистрибутива.")
        ctypes.windll.user32.LockWorkStation()
        return Reply(line("lock"))

    def volume(self, c, m):
        if re.search(r"(выключи|выключить|отключи|убери|убрать)\w* звук|без звука|замуть", c):
            set_mute(True)
            return Reply(line("mute"))
        if re.search(r"(включи|включить|верни)\w* звук", c) and parse_number(c) is None:
            set_mute(False)
            return Reply(line("unmute"))
        n = parse_number(c)
        cur = get_volume()
        if re.search(r"громче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 10)
        elif re.search(r"тише|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 10)
        elif n is not None:
            new = n
        else:
            return Reply(line("vol_now", n=cur))
        new = max(0, min(100, new))
        set_volume(new)
        return Reply(line("vol_set", n=new))

    def brightness(self, c, m):
        try:
            cur = get_brightness()
        except Exception:
            return Reply(line("bright_fail"))
        n = parse_number(c)
        if re.search(r"ярче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 15)
        elif re.search(r"темнее|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 15)
        elif n is not None:
            new = n
        else:
            return Reply(line("bright_now", n=cur))
        new = max(0, min(100, new))
        try:
            set_brightness(new)
        except Exception:
            return Reply(line("bright_reject"))
        return Reply(line("bright_set", n=new))

    def analyze(self, c, m):
        issues, report = analyze_pc()
        folder = os.path.join(self.base_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        (os.startfile(path) if IS_WINDOWS else _popen_detached(["xdg-open", path]))
        use_ai = self.cfg.get("enable_ai") and self.ai and self.ai.available
        spoken = self.ai.summarize_report(report) if use_ai else None
        if not spoken and self.local:
            spoken = self.local.summarize_report(report)
        if spoken:
            return Reply(spoken)
        if not issues:
            return Reply(line("scan_ok"))
        head = "; ".join(issues[:4])
        return Reply(line("scan_bad", n=len(issues), head=head))

    def search(self, c, m):
        q = m.group("q")
        yt = re.search(r"(на|в) (ютубе|ютуб|youtube)", q)
        if yt:
            q = q.replace(yt.group(0), "").strip()
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return Reply(line("yt", q=q))
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        return Reply(line("web", q=q))

    def open(self, c, m):
        if re.search(r"ютуб|youtube", c):
            self.youtube_opened_at = time.time()  # следующее «напиши …» будет поиском на YouTube
        t = re.sub(r"^(мне |пожалуйста |программу |приложение )", "", m.group("t"))
        t = re.sub(r" пожалуйста$", "", t).strip()
        if t in ("звук",):
            return None
        site_mode = t.startswith("сайт ")
        if site_mode:
            t = t[5:].strip()
        else:
            explicit = re.match(r"^(папк\w*|каталог\w*|директори\w*)\s+(?P<n>.+)$", t)
            found = find_folder(explicit.group("n") if explicit else t, search=bool(explicit))
            if found:
                (os.startfile(found[0]) if IS_WINDOWS else _popen_detached(["xdg-open", found[0]]))
                return Reply(line("folder_open", t=found[1]))
            if explicit:
                return Reply(line("folder_missing", t=explicit.group("n")))

        if not site_mode and t in self.apps:
            self._run(self.apps[t])
            return Reply(line("open", t=t))

        if t in self.sites:
            self.web(self.sites[t])
            return Reply(line("open", t=t))
        close = difflib.get_close_matches(t, list(self.sites), n=1, cutoff=0.82)
        if close:
            self.web(self.sites[close[0]])
            return Reply(line("open", t=close[0]))

        domain = re.sub(r"\s*(точка|dot)\s*", ".", t).replace(" ", "")
        if re.fullmatch(r"[\w\-]+(\.[\w\-]+)*\.[a-zа-я]{2,}", domain):
            self.web("https://" + domain)
            return Reply(line("open", t=domain))

        if not site_mode:
            self.index.ready.wait(timeout=8)
            options = self.index.candidates(t, self.aliases)
            if not options:
                return Reply(line("app_missing", t=t))
            top = options[0]
            close = [o for o in options[1:] if top[0] - o[0] < 0.1]
            if top[0] >= 1.0 or not close:  # одно явно подходит — запускаем
                started = self.index.launch_best(t, [top])
                return Reply(line("launch", t=started) if started else line("app_missing", t=t))
            # несколько похожих — спрашиваем, какое открыть
            self.choice = [(n, tg) for _, n, tg in [top] + close[:2]]
            names = [n.title() for n, _ in self.choice]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {nm}" for i, nm in enumerate(names))
            return Reply(line("choose_app", t=listed), "ask")
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(t))
        return Reply(line("site_unknown", t=t))

    def kill_process(self, c, m):
        """«сними задачу steam client helper», «заверши процесс хром принудительно», «кильни дискорд»."""
        t = re.sub(r"^(все |всех )?(процесс\w*|задач\w*)\s+", "", m.group("t").strip())
        return self.close(c, None, target=t, force=bool(re.search(r"принудительн|насильн|жестк|совсем", c)))

    def close(self, c, m, target=None, force=False):
        t = target if target is not None else m.group("t").strip()
        force = force or bool(re.search(r"принудительн|насильн|жестк", c))
        t = re.sub(r"\s+(принудительно|насильно|жестко|совсем)$", "", t).strip()
        if re.fullmatch(r"(окно|это|это окно)", t):
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x73, 0, 0, 0)
            u.keybd_event(0x73, 0, 2, 0)
            u.keybd_event(0x12, 0, 2, 0)
            return Reply(line("closed_window"))
        q = norm(t)
        variants = {q, translit(q)}
        if q in self.aliases:
            variants.add(norm(self.aliases[q]))
        if q in self.apps:
            variants.add(os.path.splitext(os.path.basename(self.apps[q]))[0].lower())
        if self.index.ready.is_set():  # «закрой клод» → приложение Claude → процесс claude
            for _, app_name, _ in self.index.candidates(q, self.aliases, 2):
                variants.add(app_name.split()[0])
        variants = {v.replace(" ", "") for v in variants if len(v.replace(" ", "")) >= 3}

        words = [w for w in translit(q).split() if len(w) >= 3]  # для сравнения с описанием из диспетчера задач
        me = psutil.Process().username()
        best = {}  # имя процесса → (оценка, [процессы])
        for p in psutil.process_iter(["name", "pid", "username", "exe"]):
            name = (p.info["name"] or "").lower().removesuffix(".exe")
            # только свои программы: системные процессы и службы не трогаем никогда
            if not name or name in PROTECTED_PROCS or p.info["username"] != me or p.pid == os.getpid():
                continue
            score = 0.0
            for v in variants:
                if name == v:
                    s = 1.0
                elif name.startswith(v) or (len(name) >= 4 and v.startswith(name) and len(name) >= len(v) * 0.6):
                    s = 0.9  # «steam» не считается совпадением для «steamclienthelper»
                else:
                    s = difflib.SequenceMatcher(None, v, name).ratio()
                    if AppIndex.sounds_like(v, name):
                        s = max(s, 0.86)
                score = max(score, s)
            if score < 0.95 and words:
                # «steam client helper» → описание «Steam Client WebHelper» у steamwebhelper.exe
                desc = norm(file_description(p.info["exe"]))
                if desc:
                    if desc == " ".join(words):
                        score = 1.0
                    elif len(words) >= 2 and all(any(w in d for d in desc.split()) for w in words):
                        score = max(score, 0.92)
            if score >= 0.85:
                prev = best.get(name, (0.0, []))
                best[name] = (max(prev[0], score), prev[1] + [p])
        if not best:
            return Reply(line("not_running", t=t))
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        top_name, (top_score, _) = ranked[0]
        close = [kv for kv in ranked[1:] if top_score - kv[1][0] < 0.1]
        if top_score < 1.0 and close:  # несколько похожих — спрашиваем, что закрыть
            self.choice = [(n, (lambda procs=procs, n=n: self._kill(n, procs, force))) for n, (_, procs) in
                           [ranked[0]] + close[:2]]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {n}" for i, (n, _) in enumerate(self.choice))
            return Reply(line("choose_close", t=listed), "ask")
        return self._kill(top_name, best[top_name][1], force)

    @staticmethod
    def _kill(name, procs, force=False):
        """Завершить процессы: сначала вежливо (как «Снять задачу»), а если не закрылись за 3 секунды
        или просили принудительно — жёстко."""
        alive = []
        for p in procs:
            try:
                p.kill() if force else p.terminate()
                alive.append(p)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                return Reply(line("kill_denied", t=name))
        _, still = psutil.wait_procs(alive, timeout=3)
        if still:
            for p in still:
                try:
                    p.kill()
                except Exception:
                    pass
            _, still = psutil.wait_procs(still, timeout=2)
        if still:
            return Reply(line("kill_failed", t=name))
        return Reply(line("closed", names=name))

    def top_processes(self, c, m):
        """«Что грузит компьютер» — три процесса, которые больше всего едят процессор и память."""
        procs = list(psutil.process_iter(["name", "memory_info"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        time.sleep(1.0)
        cpu, mem = {}, {}
        for p in procs:
            try:
                n = (p.info["name"] or "").removesuffix(".exe")
                if n.lower() in ("system idle process", "idle", ""):
                    continue
                cpu[n] = cpu.get(n, 0) + p.cpu_percent(None) / psutil.cpu_count()
                mem[n] = mem.get(n, 0) + p.info["memory_info"].rss / 2**20
            except Exception:
                pass
        top_cpu = sorted(cpu.items(), key=lambda kv: -kv[1])[:3]
        top_mem = sorted(mem.items(), key=lambda kv: -kv[1])[:3]
        c_txt = ", ".join(f"{n} {v:.0f} процентов" for n, v in top_cpu if v >= 1) or "ничего особо не грузит"
        m_txt = ", ".join(f"{n} {v / 1024:.1f} гигабайта" if v >= 1024 else f"{n} {v:.0f} мегабайт"
                          for n, v in top_mem)
        return Reply(line("top_procs", cpu=c_txt, mem=m_txt))

    def create(self, c, m):
        what = m.group("what")
        place_m = re.search(r"\s(на|в|во)\s+(рабоч\w+ стол\w*|документ\w*|загрузк\w*|фото\w*|изображени\w*|"
                            r"картинк\w*|музык\w*|видео|папк\w* \S+)$", what)
        place, place_name = known_dir("desktop"), "на рабочем столе"
        if place_m:
            spot = find_folder(re.sub(r"^папк\w* ", "", place_m.group(2)), search=True)
            if not spot:
                return Reply(line("folder_missing", t=place_m.group(2)))
            place, place_name = spot[0], f"в папке «{spot[1]}»"
            what = what[:place_m.start()]
        kind = re.match(r"(нов\w+ )?(?P<kind>папк\w*|текстов\w+ файл\w*|текстов\w+ документ\w*|"
                        r"документ\w*|файл\w*|таблиц\w*)( (с )?(именем|названием))?\s*(?P<name>.*)$", what)
        if not kind:
            return None
        k, name = kind.group("kind"), kind.group("name").strip()
        ext = ""
        dot = re.search(r"\s*(точка|dot|\.)\s*(?P<e>[a-zа-я0-9]+)$", name)
        if dot:  # «файл заметки точка txt»
            ext, name = "." + translit(dot.group("e")), name[:dot.start()].strip()
        if k.startswith("папк"):
            default = "Новая папка"
        elif k.startswith("таблиц"):
            default, ext = "Новая таблица", ext or ".xlsx"
        elif k.startswith("документ") and not k.startswith("текст"):
            default, ext = "Новый документ", ext or ".docx"
        else:
            default, ext = "Новый файл", ext or ".txt"
        name = re.sub(r'[<>:"/\\|?*]', "", name).strip() or default
        name = name[:1].upper() + name[1:]
        path = os.path.join(place, name + ext)
        n = 2
        while os.path.exists(path):  # ничего не перезаписываем
            path = os.path.join(place, f"{name} ({n}){ext}")
            n += 1
        if k.startswith("папк"):
            os.makedirs(path)
        else:
            open(path, "x", encoding="utf-8").close()  # пустые .docx/.xlsx Office открывает как новые
        return Reply(line("created", t=os.path.basename(path), where=place_name))

    def info(self, c, m):
        """Открывает поиск Google и вслух пересказывает начало статьи из Википедии, если она нашлась."""
        q = m.group("q").strip()
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        summary = wiki_summary(q)
        if summary:
            return Reply(line("info", t=summary))
        return Reply(line("web", q=q))

    def video(self, c, m):
        q = m.group("q").strip()
        q = re.sub(r"\s+(на|в) (ютуб\w*|youtube)$", "", q)  # «включи песню X на ютубе»
        q = re.sub(r"^(на|в) (ютуб\w*|youtube)\s*", "", q).strip()
        if not q or q in ("на", "в", "с", "про", "о"):  # «открой какое-нибудь видео на ютубе»
            q = random.choice(["интересное видео", "смешные моменты", "залипательное видео", "лучшие клипы"])
        kind = m.groupdict().get("kind") or ""
        search = q + (" песня" if re.match(r"песн|трек", kind) else "")
        found = youtube_first(search)
        if not found:  # не вышло достать первое видео — хотя бы откроем поиск
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(search))
            return Reply(line("yt", q=q))
        vid, title = found
        self.web(f"https://www.youtube.com/watch?v={vid}")
        threading.Thread(target=ensure_playing, daemon=True).start()
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 70:
            title = title[:67].rsplit(" ", 1)[0] + "…"
        return Reply(line("video", t=title) if title else line("video_untitled"))

    def tab(self, c, m):
        if re.search(r"нов(ую|ая) вкладк", c):
            send_keys(["ctrl", "t"])
            return Reply(line("tab_new"))
        if re.search(r"\b(верни|восстанови)", c):
            send_keys(["ctrl", "shift", "t"])
            return Reply(line("tab_restore"))
        if re.search(r"следующ", c):
            send_keys(["ctrl", "tab"])
            return Reply(line("done"))
        if re.search(r"предыдущ", c):
            send_keys(["ctrl", "shift", "tab"])
            return Reply(line("done"))
        n = max(1, min(20, parse_number(c) or 1))
        for _ in range(n):
            send_keys(["ctrl", "w"])
            time.sleep(0.12)
        return Reply(line("tab_closed", n=n) if n > 1 else line("tab_closed_one"))

    def time(self, c, m):
        return Reply(line("time", t=f"{datetime.datetime.now():%H:%M}"))

    def date(self, c, m):
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                  "сентября", "октября", "ноября", "декабря"]
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        d = datetime.date.today()
        return Reply(line("date", t=f"{days[d.weekday()]}, {d.day} {months[d.month - 1]}"))

    def media_info(self, c, m):
        info = media.now_playing()
        if not info or not info[0]:
            return Reply(line("media_none"))
        return Reply(line("media_info", t=info[0]))

    def type_cmd(self, c, m):
        """«напиши привет» — печатает в активное окно и жмёт Enter."""
        parsed = automation.parse_type(c)
        if not parsed:
            return None

        text, send = parsed
        title = automation.foreground_title().lower()
        recent_yt = time.time() - self.youtube_opened_at < 120

        if "youtube" in title or (
            recent_yt and automation.find_window(("youtube",))
        ):
            if "youtube" not in title and not automation.wait_and_focus(
                ("youtube",), timeout=4
            ):
                return Reply(line("focus_fail"))

            youtube_search_in_tab(text)
            return Reply(line("yt", q=text))

        if send is None:
            send = self.cfg.get("type_press_enter", True)

        automation.input_text(text)

        if send:
            time.sleep(0.1)
            send_keys(["enter"])

        return Reply(line("sent" if send else "typed", t=text))

    def _run(self, target):
        if target.startswith(("http://", "https://")):
            self.web(target)
            return
        try:
            if IS_WINDOWS:
                os.startfile(target)
            elif target.endswith(".desktop") and os.path.isfile(target):
                # .desktop нужен в том числе для Flatpak-приложений: запускаем запись рабочего стола.
                if shutil.which("gio"):
                    subprocess.Popen(["gio", "launch", target], start_new_session=True)
                else:
                    desktop_id = os.path.basename(target)[:-8]
                    subprocess.Popen(["gtk-launch", desktop_id], start_new_session=True)
            elif os.path.isfile(target) and os.access(target, os.X_OK):
                # Запомненные Linux-программы хранят путь к исполняемому файлу;
                # запускаем его напрямую, а не открываем через файловый менеджер.
                subprocess.Popen([target], start_new_session=True)
            else:
                subprocess.Popen(["xdg-open", target], start_new_session=True)
        except OSError:
            if IS_WINDOWS:
                subprocess.Popen(target, shell=True, creationflags=NO_WINDOW)
            else:
                log.exception("Не удалось запустить сохранённую программу: %s", target)
                raise
