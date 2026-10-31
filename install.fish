#!/usr/bin/env fish

set PROJECT_DIR (dirname (realpath (status filename)))
cd "$PROJECT_DIR"
or exit 1

echo "== Установка Capri для Linux =="

if not type -q python3
    echo "Не найден python3."
    exit 1
end

if not test -f /etc/os-release
    echo "Не удалось определить дистрибутив."
    exit 1
end

set -l ID (grep '^ID=' /etc/os-release | cut -d= -f2- | string trim -c '"' | string lower)
set -l ID_LIKE (grep '^ID_LIKE=' /etc/os-release | cut -d= -f2- | string trim -c '"' | string lower)
set -l DISTRO "$ID $ID_LIKE"

echo "Обнаружена система: $DISTRO"

switch $DISTRO
    case '*arch*' '*cachyos*' '*manjaro*'
        echo "Используется семейство Arch Linux."

        set -l PACKAGES \
            python python-pip python-virtualenv \
            portaudio libxkbcommon pipewire wireplumber \
            playerctl brightnessctl ffmpeg

        if type -q paru
            paru -S --needed --noconfirm $PACKAGES
            set -l STATUS $status

            if test $STATUS -ne 0
                if test $STATUS -eq 130 -o $STATUS -eq 143
                    echo "Установка отменена."
                    exit $STATUS
                end

                if not type -q pacman
                    echo "pacman не найден."
                    exit 1
                end

                echo "Ошибка paru. Пробую pacman..."
                sudo pacman -S --needed --noconfirm $PACKAGES
                or exit $status
            end
        else if type -q pacman
            sudo pacman -S --needed --noconfirm $PACKAGES
            or exit $status
        else
            echo "Не найден paru или pacman."
            exit 1
        end

    case '*fedora*' '*rhel*' '*centos*'
        echo "Используется семейство Fedora."

        sudo dnf install -y \
            python3 python3-pip python3-devel \
            portaudio portaudio-devel libxkbcommon \
            pipewire wireplumber playerctl brightnessctl ffmpeg
        or exit $status

    case '*debian*' '*ubuntu*'
        echo "Используется семейство Debian / Ubuntu."

        sudo apt update
        or exit $status

        sudo apt install -y \
            python3 python3-pip python3-venv \
            portaudio19-dev libxkbcommon0 \
            pipewire wireplumber playerctl brightnessctl ffmpeg
        or exit $status

    case '*'
        echo "Неизвестный дистрибутив: $DISTRO"
        exit 1
end

if not test -x .venv/bin/python
    python3 -m venv .venv --system-site-packages
    or exit $status
end

.venv/bin/python -m pip install --upgrade pip
or exit $status

.venv/bin/python -m pip install -r requirements-linux.txt
or exit $status

if test -f bundled/vosk-model-small-ru-0.22.zip
    and not test -d models/vosk-model-small-ru-0.22

    mkdir -p models
    or exit $status

    python3 -m zipfile -e bundled/vosk-model-small-ru-0.22.zip models/
    or exit $status
end

# Создаём ярлык в меню приложений с абсолютным путём к этой копии Capri.
if not test -f "$PROJECT_DIR/run.sh"
    echo "Не найден run.sh — ярлык не создан."
    exit 1
end

set DATA_HOME "$HOME/.local/share"
if set -q XDG_DATA_HOME
    set DATA_HOME "$XDG_DATA_HOME"
end
set DESKTOP_DIR "$DATA_HOME/applications"
set ICON_DIR "$DATA_HOME/icons/hicolor/512x512/apps"
set ICON_PATH "$ICON_DIR/capri.png"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
or exit $status

if test -f "$PROJECT_DIR/capri-icon.png"
    cp "$PROJECT_DIR/capri-icon.png" "$ICON_PATH"
    or exit $status
else
    echo "Не найден capri-icon.png рядом с установщиком — будет стандартная иконка."
    set ICON_PATH "applications-graphics"
end

python3 -c ' 
import os, pathlib, sys
project = pathlib.Path(sys.argv[1]).resolve()
desktop_dir = pathlib.Path(sys.argv[2])
icon_path = sys.argv[3]
desktop_dir.mkdir(parents=True, exist_ok=True)
def quote_exec(value):
    backslash = chr(92)
    quote = chr(34)
    value = str(value).replace("%", "%%")
    for char in (backslash, quote, "`", "$" ):
        value = value.replace(char, backslash + char)
    return quote + value + quote
entry = chr(10).join([
    "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=Capri",
    "Comment=Голосовой помощник Capri", "Exec=" + quote_exec(project / "run.sh"),
    "Path=" + str(project), "Terminal=false", "StartupNotify=true",
    "Icon=" + icon_path, "Categories=Utility;"
]) + chr(10)
(desktop_dir / "Capri.desktop").write_text(entry, encoding="utf-8")
' "$PROJECT_DIR" "$DESKTOP_DIR" "$ICON_PATH"
or exit $status

chmod 644 "$DESKTOP_DIR/Capri.desktop"

if type -q update-desktop-database
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1
end

echo
echo "Базовые зависимости Capri установлены."
echo "Ярлык создан: $DESKTOP_DIR/Capri.desktop"
echo "Теперь Capri можно запускать из меню приложений."
echo "Дополнительные функции Wayland/Hyprland могут требовать wtype и hyprlock."
