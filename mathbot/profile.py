"""Профиль репетитора: предмет, названия, деньги, время. Читается из profile.json (создаётся мастером-брифом)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Profile:
    subject_nom: str = "математика"       # «Математика, Маша: ...»
    subject_gen: str = "математики"       # «Время математики»
    subject_dat: str = "математике"       # «по математике»
    subject_acc: str = "математику"       # «Напоминаю про математику»
    grade_gen: str = "6 класса"           # «знаний 6 класса»
    foundation_label: str = "5 класс"     # откуда «спуск вниз»
    country: str = ""                     # страна, где учится ребёнок (для справки)
    school_style: str = ""                # "старая" (классическая) или "новая" (современная программа)
    child_name: str = ""
    start_time: str = ""                  # «17:00»
    session_minutes: int = 0
    tz: str = ""
    currency: str = "монет"               # валюта кошелька
    price_correct: int = 5                # цена одного верного ответа
    pack: str = ""                        # путь к пакету заданий (пусто — встроенная математика 6 класса)
    books: dict = None                    # {"ключ": "название учебника"}


P = Profile()


def load(path: str | None = None) -> Profile:
    """Загрузить profile.json (путь из PROFILE_PATH, по умолчанию ./profile.json) в глобальный P."""
    path = path or os.environ.get("PROFILE_PATH", "profile.json")
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Profile)}
        for k, v in data.items():
            if k in known:
                setattr(P, k, v)
    return P
