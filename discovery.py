"""Автообнаружение приложений и доступных движков Capri.

Ничего не устанавливает и не запускает во время сканирования. Возвращает только
программы/инструменты, которые удалось обнаружить локально, плюс веб-сервисы.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import re
import shlex
import shutil
import subprocess
from pathlib import Path

BROWSER_BINARIES = {
    "Firefox": ("firefox",),
    "Chromium": ("chromium", "chromium-browser"),
    "Google Chrome": ("google-chrome-stable", "google-chrome", "chrome"),
    "Microsoft Edge": ("microsoft-edge-stable", "microsoft-edge"),
    "Brave": ("brave-browser", "brave"),
    "Opera": ("opera",),
    "Vivaldi": ("vivaldi-stable", "vivaldi"),
    "Zen Browser": ("zen-browser", "zen"),
    "Yandex Browser": ("yandex-browser", "yandex"),
}
MUSIC_APPS = {
    "Яндекс Музыка": ("yandex-music", "yandexmusic"),
    "Spotify": ("spotify", "spotify-launcher"),
    "VLC": ("vlc",),
    "Audacious": ("audacious",),
    "Strawberry": ("strawberry",),
    "Clementine": ("clementine",),
    "Rhythmbox": ("rhythmbox",),
    "Elisa": ("elisa",),
}
GAME_LAUNCHERS = {
    "Steam": ("steam",),
    "Lutris": ("lutris",),
    "Heroic Games Launcher": ("heroic", "heroic-games-launcher"),
    "Bottles": ("bottles",),
    "Prism Launcher": ("prismlauncher",),
    "Minecraft Launcher": ("minecraft-launcher",),
}


def _desktop_entries():
    entries = []
    if os.name == "nt":
        roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path.home() / "Desktop"]
        seen = set()
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for path in root.rglob("*.lnk"):
                    if path in seen:
                        continue
                    seen.add(path)
                    entries.append({"name": path.stem, "target": str(path), "exec": "", "kind": "shortcut",
                                    "desktop_file": str(path)})
            except OSError:
                continue
        return entries
    dirs = [Path.home() / ".local/share/applications", Path("/usr/share/applications"),
            Path("/usr/local/share/applications"), Path.home() / ".local/share/flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications")]
    seen = set()
    for directory in dirs:
        try:
            files = directory.glob("*.desktop")
            for path in files:
                if path in seen:
                    continue
                seen.add(path)
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if re.search(r"(?m)^(NoDisplay|Hidden)=true\s*$", content, re.I):
                    continue
                name_match = re.search(r"(?m)^Name(?:\[[^]]+\])?=(.+)$", content)
                exec_match = re.search(r"(?m)^Exec=(.+)$", content)
                if not name_match:
                    continue
                name = name_match.group(1).strip()
                exec_line = exec_match.group(1).strip() if exec_match else ""
                launch_target = str(path)
                kind = "desktop"
                if exec_line:
                    try:
                        tokens = shlex.split(exec_line)
                        executable = next((token for token in tokens if not token.startswith("%")), "")
                        resolved = shutil.which(executable) if executable else None
                        if resolved:
                            launch_target, kind = resolved, "binary"
                    except ValueError:
                        pass
                entries.append({"name": name, "target": launch_target, "exec": exec_line,
                                "desktop_file": str(path), "kind": kind})
        except OSError:
            continue
    return entries


def _app_match(entries, aliases):
    aliases = tuple(a.lower() for a in aliases)
    # Prefer a directly launchable binary; a .desktop entry may have Exec with wrappers or placeholders.
    for alias in aliases:
        binary = shutil.which(alias)
        if binary:
            return {"name": alias, "target": binary, "kind": "binary"}
    for entry in entries:
        label = (entry["name"] + " " + Path(entry.get("desktop_file", entry["target"])).stem + " " + entry.get("exec", "")).lower()
        if any(alias in label for alias in aliases):
            return {"name": entry["name"], "target": entry["target"], "kind": entry.get("kind", "desktop"),
                    "desktop_file": entry.get("desktop_file", entry["target"])}
    return None


def _find_any(binaries):
    for binary in binaries:
        path = shutil.which(binary)
        if path:
            return {"name": binary, "target": path, "kind": "binary"}
    return None


def scan():
    """Return structured local capabilities. This function is safe to run repeatedly."""
    entries = _desktop_entries()
    browsers = {}
    for name, binaries in BROWSER_BINARIES.items():
        item = _app_match(entries, (name.lower(), *binaries))
        if item:
            browsers[name] = item

    music = {}
    for name, binaries in MUSIC_APPS.items():
        item = _app_match(entries, (name.lower(), *binaries))
        if item:
            music[name] = item
    # These web services are available without a native client; opening them is a real fallback.
    music["Yandex Music (веб)"] = {"name": "Yandex Music", "target": "https://music.yandex.ru", "kind": "web"}
    music["VK Музыка (веб)"] = {"name": "VK Музыка", "target": "https://vk.com/music", "kind": "web"}
    if "Spotify" not in music:
        music["Spotify (веб)"] = {"name": "Spotify", "target": "https://open.spotify.com", "kind": "web"}

    games = {}
    for name, binaries in GAME_LAUNCHERS.items():
        item = _app_match(entries, (name.lower(), *binaries))
        if item:
            games[name] = item

    vosk_model = Path(__file__).resolve().parent / "models/vosk-model-small-ru-0.22"
    sounddevice_available = importlib.util.find_spec("sounddevice") is not None
    microphone_found = False
    if sounddevice_available:
        try:
            import sounddevice as sd
            microphone_found = any(float(device.get("max_input_channels", 0)) > 0 for device in sd.query_devices())
        except Exception:
            microphone_found = False
    modules = {
        "Vosk (офлайн-распознавание)": importlib.util.find_spec("vosk") is not None,
        "Модель Vosk установлена": vosk_model.is_dir(),
        "Google Speech Recognition (дополнительное распознавание)": importlib.util.find_spec("speech_recognition") is not None,
        "Edge TTS (онлайн-озвучка)": importlib.util.find_spec("edge_tts") is not None,
        "PyTorch (для Silero TTS)": importlib.util.find_spec("torch") is not None,
        "Библиотека sounddevice": sounddevice_available,
        "Микрофон обнаружен": microphone_found,
        "Мультимедиа playerctl": shutil.which("playerctl") is not None,
        "PipeWire wpctl": shutil.which("wpctl") is not None,
        "Яркость brightnessctl": shutil.which("brightnessctl") is not None,
        "Ollama (локальный ИИ)": shutil.which("ollama") is not None,
    }
    return {
        "platform": platform.platform(),
        "browsers": browsers,
        "music": music,
        "games": games,
        "modules": modules,
        "desktop_apps": len(entries),
    }


def find_provider(category: str, requested: str):
    """Find a provider by a loose human-readable name. Returns (canonical, item) or None."""
    data = scan()
    group = data.get(category, {})
    q = requested.casefold().replace("ё", "е").strip()
    aliases = {
        "yandex": ("яндекс", "яндекс музыку", "яндекс музыки", "yandex", "yandex music",
                   "яндекс music", "я музыка", "я музыку", "яндекс музыка", "мьюзик яндекс"),
        "spotify": ("spotify", "спотифай", "спотифай", "споти"),
        "vk": ("vk", "вк", "вконтакте", "вк музыка"),
        "firefox": ("firefox", "фаерфокс", "файрфокс", "мозилла"),
        "chrome": ("chrome", "хром", "гугл хром"),
        "chromium": ("chromium", "хромиум"),
        "zen": ("zen", "зен браузер"),
        "edge": ("edge", "эдж"),
        "brave": ("brave", "брейв"),
        "opera": ("opera", "опера"),
        "vivaldi": ("vivaldi", "вивальди"),
        "vlc": ("vlc", "влс", "влц"),
        "audacious": ("audacious", "одасиус"),
        "strawberry": ("strawberry", "клубника"),
        "steam": ("steam", "стим"),
        "lutris": ("lutris", "лутрис"),
        "heroic": ("heroic", "хероик"),
        "bottles": ("bottles", "ботлс"),
    }
    q_aliases = next((v for k, v in aliases.items() if q == k or q in v), (q,))
    for canonical, item in group.items():
        haystack = (canonical + " " + item.get("name", "")).casefold().replace("ё", "е")
        if any(alias and alias in haystack for alias in q_aliases):
            return canonical, item
    # Allow direct command lookup for an installed browser even when the display name differs.
    for canonical, item in group.items():
        if q and q in canonical.casefold():
            return canonical, item
    return None
