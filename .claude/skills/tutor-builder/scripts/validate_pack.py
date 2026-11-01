#!/usr/bin/env python3
"""Проверка пакета заданий: python validate_pack.py packs/мой-пакет.json"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from mathbot.packs import validate  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("Использование: validate_pack.py путь/к/пакету.json")
errors, warns = validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
for w in warns:
    print("⚠️ ", w)
for e in errors:
    print("❌", e)
print("Ошибок:", len(errors), "Предупреждений:", len(warns))
sys.exit(1 if errors else 0)
