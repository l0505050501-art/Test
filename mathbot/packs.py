"""Пакеты заданий: любой предмет в виде JSON-банка вопросов. Встроенная математика 6 класса — это тоже «пакет».

Формат пакета (см. skills/tutor-builder/reference/pack-format.md):
{
  "id": "english-5", "title": "Английский, 5 класс",
  "topics": {"to_be": "Глагол to be"},              # основные темы
  "foundation_topics": {"alphabet": "Алфавит"},     # необязательно: темы «ниже», куда спускаемся при пробелах
  "prereq": {"to_be": ["alphabet"]},                # необязательно
  "books": {"spotlight": "Spotlight"},              # необязательно
  "lessons": {"to_be": "📖 Глагол to be ..."},      # мини-урок на каждую тему
  "stories": {"to_be": {"rule": "...", "steps": "1) ... 2) ...",       # необязательно: «исследование» для ребёнка
              "boy": {"hook": "образ из игр и техники", "example": "..."},
              "girl": {"hook": "образ из творчества и быта", "example": "..."}}},
  "questions": [ {"topic": "to_be", "level": 1, "text": "I ___ a pupil. (am/is/are)", "kind": "text",
                  "answer": "am", "accept": [], "explanation": "...", "buttons": ["am", "is", "are"]} ]
}
kind: "num" (число/дробь), "text" (слово/фраза; accept — допустимые варианты), "ints" (несколько целых: answer = [3, -2]).
"""
from __future__ import annotations

import copy
import json
import random
from fractions import Fraction

from . import lessons as lessons_mod
from . import stories
from . import tasks as T
from .engine import BOOKS

MIN_PER_LEVEL, GOOD_PER_LEVEL = 3, 6


def validate(pack: dict) -> tuple[list[str], list[str]]:
    """Вернуть (ошибки, предупреждения)."""
    errors, warns = [], []
    topics = pack.get("topics") or {}
    found = pack.get("foundation_topics") or {}
    if not topics:
        errors.append("нет ни одной темы в «topics»")
    allt = {**topics, **found}
    for k, name in allt.items():
        if not isinstance(name, str) or not name.strip():
            errors.append(f"тема {k}: пустое название")
        if k not in (pack.get("lessons") or {}):
            errors.append(f"тема {k}: нет мини-урока в «lessons»")
    for parent, pre in (pack.get("prereq") or {}).items():
        if parent not in topics:
            errors.append(f"prereq: неизвестная тема {parent}")
        for p in pre:
            if p not in found:
                errors.append(f"prereq: {p} должна быть в «foundation_topics»")
    for k, st in (pack.get("stories") or {}).items():
        if k not in allt:
            errors.append(f"stories: неизвестная тема {k}")
            continue
        for f in ("rule", "steps"):
            if not str(st.get(f, "")).strip():
                errors.append(f"stories.{k}: нет поля {f}")
        for g in ("boy", "girl"):
            if not (isinstance(st.get(g), dict) and str(st[g].get("hook", "")).strip() and str(st[g].get("example", "")).strip()):
                errors.append(f"stories.{k}.{g}: нужны поля hook и example")
    seen: set[str] = set()
    counts: dict[tuple[str, int], int] = {}
    for i, q in enumerate(pack.get("questions") or []):
        where = f"вопрос №{i + 1}"
        if q.get("topic") not in allt:
            errors.append(f"{where}: неизвестная тема {q.get('topic')!r}")
            continue
        if q.get("level") not in (1, 2, 3):
            errors.append(f"{where}: level должен быть 1, 2 или 3")
            continue
        text = q.get("text", "")
        if not text.strip():
            errors.append(f"{where}: пустой текст")
        if text in seen:
            errors.append(f"{where}: повтор вопроса «{text[:40]}»")
        seen.add(text)
        if not q.get("explanation"):
            errors.append(f"{where}: нет explanation (объяснение после ошибки)")
        kind = q.get("kind")
        ans = q.get("answer")
        if kind == "num":
            if T.parse_number(str(ans)) is None:
                errors.append(f"{where}: answer «{ans}» не число")
        elif kind == "ints":
            if not (isinstance(ans, list) and ans and all(isinstance(x, int) for x in ans)):
                errors.append(f"{where}: для kind=ints answer — список целых")
        elif kind == "text":
            if not isinstance(ans, str) or not ans.strip():
                errors.append(f"{where}: пустой answer")
        else:
            errors.append(f"{where}: kind должен быть num, text или ints")
        counts[(q["topic"], q["level"])] = counts.get((q["topic"], q["level"]), 0) + 1
    for k in allt:
        for lvl in (1, 2, 3):
            n = counts.get((k, lvl), 0)
            if n == 0 and lvl == 1:
                errors.append(f"тема {k}: нет вопросов уровня 1")
            elif 0 < n < MIN_PER_LEVEL:
                errors.append(f"тема {k}, уровень {lvl}: вопросов {n}, нужно минимум {MIN_PER_LEVEL}")
            elif 0 < n < GOOD_PER_LEVEL:
                warns.append(f"тема {k}, уровень {lvl}: всего {n} вопросов — будут часто повторяться (лучше {GOOD_PER_LEVEL}+)")
    return errors, warns


def _gen(q_list: list[dict], topic: str, level: int):
    def gen(rnd: random.Random) -> T.Task:
        q = rnd.choice(q_list)
        kind = q["kind"]
        if kind == "num":
            value = T.parse_number(str(q["answer"]))
        elif kind == "ints":
            value = list(q["answer"])
        else:
            value = [q["answer"]] + list(q.get("accept", []))
        return T.Task(topic, level, q["text"], kind, value, q["explanation"], tuple(q.get("buttons", ())), theory=q.get("theory", ""))
    return gen


def snapshot() -> dict:
    return {"T": copy.deepcopy((T.TOPICS, T.TOPICS5, T.PREREQ)), "G": {k: dict(v) for k, v in T.GENERATORS.items()},
            "L": dict(lessons_mod.LESSONS), "B": dict(BOOKS), "S": dict(stories.S)}


def restore(s: dict) -> None:
    for d, src in zip((T.TOPICS, T.TOPICS5, T.PREREQ), s["T"]):
        d.clear(); d.update(src)
    T.ALL_TOPICS.clear(); T.ALL_TOPICS.update({**T.TOPICS, **T.TOPICS5})
    T.GENERATORS.clear(); T.GENERATORS.update(s["G"])
    lessons_mod.LESSONS.clear(); lessons_mod.LESSONS.update(s["L"])
    BOOKS.clear(); BOOKS.update(s["B"])
    stories.S.clear(); stories.S.update(s["S"])


def apply(pack: dict, replace: bool = True) -> None:
    """Подключить пакет. replace=True — вместо встроенной математики, False — вместе с ней."""
    errors, _ = validate(pack)
    if errors:
        raise ValueError("Пакет заданий содержит ошибки:\n- " + "\n- ".join(errors))
    if replace:
        for d in (T.TOPICS, T.TOPICS5, T.PREREQ, T.GENERATORS, lessons_mod.LESSONS, stories.S):
            d.clear()
    T.TOPICS.update(pack["topics"])
    T.TOPICS5.update(pack.get("foundation_topics") or {})
    T.PREREQ.update({k: list(v) for k, v in (pack.get("prereq") or {}).items()})
    for k in T.TOPICS:
        T.PREREQ.setdefault(k, [])
    T.ALL_TOPICS.clear(); T.ALL_TOPICS.update({**T.TOPICS, **T.TOPICS5})
    lessons_mod.LESSONS.update(pack.get("lessons") or {})
    for k, st in (pack.get("stories") or {}).items():
        stories.S[k] = (st["rule"], st["steps"], (st["boy"]["hook"], st["boy"]["example"]), (st["girl"]["hook"], st["girl"]["example"]))
    if pack.get("books"):
        BOOKS.clear(); BOOKS.update(pack["books"]); BOOKS.setdefault("unknown", "не знаю / другой")
    by: dict[tuple[str, int], list[dict]] = {}
    for q in pack["questions"]:
        by.setdefault((q["topic"], q["level"]), []).append(q)
    for topic in T.ALL_TOPICS:
        T.GENERATORS[topic] = {}
        have = [lvl for lvl in (1, 2, 3) if by.get((topic, lvl))]
        for lvl in (1, 2, 3):
            src = lvl if lvl in have else max([h for h in have if h < lvl] or [have[0]])   # нет уровня — берём ближайший ниже
            T.GENERATORS[topic][lvl] = [_gen(by[(topic, src)], topic, lvl)]


def load_file(path: str, replace: bool = True) -> dict:
    with open(path, encoding="utf-8") as f:
        pack = json.load(f)
    apply(pack, replace)
    return pack
