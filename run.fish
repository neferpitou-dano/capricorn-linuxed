#!/usr/bin/env fish
set -l root (dirname (realpath (status filename)))
cd $root
if not test -x .venv/bin/python
    echo 'Сначала запусти: fish ./install.fish'
    exit 1
end
exec .venv/bin/python main.py
