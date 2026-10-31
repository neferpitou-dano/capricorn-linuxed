#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(dirname -- "$(readlink -f -- "$0")")"
cd -- "$PROJECT_DIR"

echo "== Установка Capri для Linux =="

if ! command -v python3 >/dev/null 2>&1; then
    echo "Ошибка: не найден Python 3."
    exit 1
fi

if [ ! -f /etc/os-release ]; then
    echo "Не удалось определить дистрибутив Linux."
    exit 1
fi

source /etc/os-release

DISTRO_ID="${ID,,}"
DISTRO_LIKE="${ID_LIKE,,}"
DISTRO="${DISTRO_ID} ${DISTRO_LIKE}"

echo "Обнаружена система: ${PRETTY_NAME:-$DISTRO_ID}"

install_arch() {
    local packages=(
        python
        python-pip
        python-virtualenv
        portaudio
        libxkbcommon
        pipewire
        wireplumber
        playerctl
        brightnessctl
        ffmpeg
    )

    if command -v paru >/dev/null 2>&1; then
        echo "Устанавливаю зависимости через paru..."

        if paru -S --needed --noconfirm "${packages[@]}"; then
            return 0
        else
            local status=$?

            if [ "$status" -eq 130 ] || [ "$status" -eq 143 ]; then
                echo "Установка отменена."
                exit "$status"
            fi

            echo "paru завершился с ошибкой. Пробую pacman..."
        fi
    fi

    if command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm "${packages[@]}"
    else
        echo "Не найден pacman."
        return 1
    fi
}

install_fedora() {
    local packages=(
        python3
        python3-pip
        python3-devel
        portaudio
        portaudio-devel
        libxkbcommon
        pipewire
        wireplumber
        playerctl
        brightnessctl
        ffmpeg
    )

    sudo dnf install -y "${packages[@]}"
}

install_debian() {
    local packages=(
        python3
        python3-pip
        python3-venv
        portaudio19-dev
        libxkbcommon0
        pipewire
        wireplumber
        playerctl
        brightnessctl
        ffmpeg
    )

    sudo apt update
    sudo apt install -y "${packages[@]}"
}

case "$DISTRO" in
    *arch*|*cachyos*|*manjaro*)
        install_arch
        ;;
    *fedora*|*rhel*|*centos*)
        install_fedora
        ;;
    *debian*|*ubuntu*)
        install_debian
        ;;
    *)
        echo "Дистрибутив не поддерживается: ${PRETTY_NAME:-$DISTRO_ID}"
        exit 1
        ;;
esac

echo
echo "== Создание виртуального окружения =="

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv --system-site-packages
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-linux.txt

if [ -f bundled/vosk-model-small-ru-0.22.zip ] &&
   [ ! -d models/vosk-model-small-ru-0.22 ]; then
    echo "Распаковываю модель Vosk..."
    mkdir -p models
    python3 -m zipfile -e \
        bundled/vosk-model-small-ru-0.22.zip models/
fi

# Создаём ярлык в меню приложений с абсолютным путём к этой копии Capri.
if [ ! -f "$PROJECT_DIR/run.sh" ]; then
    echo "Не найден run.sh — ярлык не создан."
    exit 1
fi

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/512x512/apps"
ICON_PATH="$ICON_DIR/capri.png"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"

if [ -f "$PROJECT_DIR/capri-icon.png" ]; then
    cp "$PROJECT_DIR/capri-icon.png" "$ICON_PATH"
else
    echo "Не найден capri-icon.png рядом с установщиком — будет стандартная иконка."
    ICON_PATH="applications-graphics"
fi

python3 - "$PROJECT_DIR" "$DESKTOP_DIR" "$ICON_PATH" <<'PYCODE'
import pathlib
import sys

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
    "[Desktop Entry]",
    "Version=1.0",
    "Type=Application",
    "Name=Capri",
    "Comment=Голосовой помощник Capri",
    "Exec=" + quote_exec(project / "run.sh"),
    "Path=" + str(project),
    "Terminal=false",
    "StartupNotify=true",
    "Icon=" + icon_path,
    "Categories=Utility;",
]) + chr(10)

(desktop_dir / "Capri.desktop").write_text(entry, encoding="utf-8")
PYCODE

chmod 644 "$DESKTOP_DIR/Capri.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo
echo "Установка Capri завершена!"
echo "Ярлык создан: $DESKTOP_DIR/Capri.desktop"
echo "Теперь Capri можно запускать из меню приложений."
echo
echo "Примечание: hyprlock и wtype нужны только для соответствующих функций."
