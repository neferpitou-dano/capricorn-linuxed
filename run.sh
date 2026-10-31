#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo "Сначала запусти ./install.sh"
  exit 1
fi
exec .venv/bin/python main.py
