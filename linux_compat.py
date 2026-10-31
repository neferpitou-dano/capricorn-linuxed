"""Linux distro and package manager compatibility."""

import os
import shutil
from pathlib import Path


def get_distro():
    """Определяет семейство Linux по /etc/os-release."""
    data = {}

    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.lower()] = value.strip().strip('"').lower()
    except OSError:
        return "unknown"

    distro_id = data.get("id", "")
    id_like = data.get("id_like", "")

    if any(x in (distro_id + " " + id_like) for x in ("arch", "cachyos", "manjaro")):
        return "arch"

    if any(x in (distro_id + " " + id_like) for x in ("fedora", "rhel", "centos")):
        return "fedora"

    if any(x in (distro_id + " " + id_like) for x in ("debian", "ubuntu")):
        return "debian"

    return "unknown"


DISTRO = get_distro()


def get_package_manager():
    """Возвращает доступный пакетный менеджер."""
    if DISTRO == "arch":
        if shutil.which("paru"):
            return "paru"
        if shutil.which("pacman"):
            return "pacman"

    elif DISTRO == "fedora" and shutil.which("dnf"):
        return "dnf"

    elif DISTRO == "debian" and shutil.which("apt"):
        return "apt"

    return None


def get_install_command(package, manager=None):
    """Формирует команду установки одного пакета."""
    manager = manager or get_package_manager()

    if manager in ("paru", "pacman"):
        return [manager, "-S", "--needed", "--noconfirm", package]

    if manager == "dnf":
        return ["sudo", "dnf", "install", "-y", package]

    if manager == "apt":
        return ["sudo", "apt", "install", "-y", package]

    return None


def get_update_command(manager=None):
    """Формирует команду полного обновления системы."""
    manager = manager or get_package_manager()

    if manager == "paru":
        return ["paru", "-Syyu", "--noconfirm"]

    if manager == "pacman":
        return ["sudo", "pacman", "-Syyu", "--noconfirm"]

    if manager == "dnf":
        return ["sudo", "dnf", "upgrade", "--refresh", "-y"]

    if manager == "apt":
        return ["sudo", "apt", "update"]

    return None
