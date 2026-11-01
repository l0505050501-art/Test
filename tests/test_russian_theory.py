import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mathbot import bot as B
from mathbot import mailer, packs, profile, stories
from mathbot.engine import Settings, Store, Trainer
from mathbot.tasks import ALL_TOPICS, TAG_THEORY, TAGS, TOPICS, Task, check, make_task

from .test_flow import make, ts, wrong_answer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def restore_packs():
    snap = packs.snapshot()
    yield
    packs.restore(snap)


@pytest.fixture(autouse=True)
def no_weekly(monkeypatch):
    monkeypatch.setattr(B, "maybe_weekly", AsyncMock())
    monkeypatch.setattr(mailer, "configured", lambda: False)


# ---------------------------------------------------------------- теория на ошибку
def test_every_error_type_has_a_short_theory():
    assert set(TAG_THEORY) == set(TAGS) - {"idk", "nc"}
    for tag, text in TAG_THEORY.items():
        assert text.startswith("🧠 Запомни:") and len(text) <= 260, tag      # лёгкий формат: пара строк и пример
        assert text.count("\n") == 0


def test_micro_theory_fallback_chain():
    assert stories.micro_theory("percent", "pct_divide") == TAG_THEORY["pct_divide"]
    assert stories.micro_theory("percent", None).startswith("🧠 Запомни: 1% — это одна сотая")        # правило темы
    assert stories.micro_theory("percent", "idk").startswith("🧠 Запомни:")
    assert stories.micro_theory("percent", "pct_divide", "🧠 Своя теория") == "🧠 Своя теория"       # теория из пакета главнее
    assert Task.load(Task("a", 1, "q", "text", ["x"], "e", theory="🧠 t").dump()).theory == "🧠 t"


def mk_ctx():
    tr = Trainer(Store(":memory:"), Settings())
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"tr": tr}), bot=MagicMock(), args=[])
    ctx.bot.send_message = AsyncMock()
    return ctx, tr


def u(chat, text=""):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=chat),
                           message=SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=chat), callback_query=None)


def texts(ctx):
    return [c.args[1] for c in ctx.bot.send_message.call_args_list]


def force_question(tr, sid, task, now):
    return tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at) VALUES(?,?,?,?,?,?,?)",
                    (sid, 1, task.topic, task.level, task.text, task.dump(), now))


def test_theory_after_error_in_daily_but_not_in_diagnostic():
    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(u(1), ctx)
        tr.set_gender("boy")
        # ежедневное занятие: ошибка «сложил числители и знаменатели» → теория именно про неё
        tr.s.run("INSERT INTO sessions(day,kind,status,invited_at,started_at,last_activity) VALUES('2026-11-01','daily','active',1,1,1)")
        t = Task("fractions_add", 3, "Вычисли: 1/2 + 1/3", "num", __import__("fractions").Fraction(5, 6), "5/6")
        t.traps = [["2/5", "add_num_den"]]
        force_question(tr, 1, t, 1)
        await B.on_text(u(1, "2/5"), ctx)
        msg = next(m for m in texts(ctx) if m.startswith("❌"))
        assert TAG_THEORY["add_num_den"] in msg and "⚠️" not in msg
        # та же ошибка ещё раз в этом занятии: не повторяем теорию (нудно), только короткая подсказка
        tr.s.run("UPDATE answers SET answered_at=2, correct=1 WHERE answered_at IS NULL")
        force_question(tr, 1, t, 3)
        n = len(texts(ctx))
        await B.on_text(u(1, "2/5"), ctx)
        msg2 = next(m for m in texts(ctx)[n:] if m.startswith("❌"))
        assert "🧠" not in msg2 and "⚠️" in msg2
        # срез знаний: теории посреди темы нет
        tr.s.run("INSERT INTO sessions(day,kind,status,invited_at,started_at,last_activity) VALUES('2026-11-02','diagnostic','active',1,1,1)")
        tr.s.run("UPDATE sessions SET status='done' WHERE id=1")
        tr.s.run("UPDATE answers SET answered_at=2, correct=1 WHERE answered_at IS NULL")
        t2 = Task("fractions_add", 3, "Вычисли: 1/2 + 1/4", "num", __import__("fractions").Fraction(3, 4), "3/4")
        t2.traps = [["2/6", "add_num_den"]]
        force_question(tr, 2, t2, 5)
        n = len(texts(ctx))
        await B.on_text(u(1, "2/6"), ctx)
        assert all("🧠" not in m for m in texts(ctx)[n:])

    asyncio.run(run())


# ---------------------------------------------------------------- русский язык
def test_russian_pack_is_valid_and_answers_work(restore_packs):
    pack = json.loads((ROOT / "packs" / "russian-6.json").read_text(encoding="utf-8"))
    errors, warns = packs.validate(pack)
    assert errors == [] and warns == []
    assert len(pack["questions"]) == 90 and set(pack["topics"]) == {"bezudarnye", "pristavki", "chered", "nn", "ne"}
    packs.apply(pack)
    rnd = random.Random(1)
    for q in pack["questions"]:
        t = Task("x", q["level"], q["text"], "text", [q["answer"]] + q["accept"], q["explanation"])
        assert check(t, q["answer"]) and check(t, q["answer"].upper() if len(q["answer"]) < 3 else q["answer"])
        wrong = {"а": "о", "о": "а", "е": "и", "и": "е", "н": "нн", "нн": "н", "слитно": "раздельно", "раздельно": "слитно"}[q["answer"]]
        assert not check(t, wrong), q["text"]
        assert q["theory"].startswith("🧠 Запомни:") and q["explanation"]
        assert set(q["buttons"]) >= {q["answer"]}
    assert check(Task("x", 1, "q", "text", ["о", "o"], "e"), "o")                    # латинская «o» вместо русской не ошибка
    # срез знаний: 5 тем × 3 вопроса — ровно одно занятие
    tr = make()
    tr.tick(ts(1, 17))
    sid = tr.today_session(ts(1, 17))["id"]
    tr.start(sid, ts(1, 17, 1))
    now = ts(1, 17, 1)
    n = 0
    while not tr.should_finish(sid, now):
        row = tr.next_question(sid, now, rnd)
        now += 8
        tr.submit(sid, Task.load(row["task_json"]).value[0], now)
        n += 1
    assert n == 15 and tr.diagnostic_done()
    # теория: для русского берётся главное правило темы, образы — для мальчика и девочки
    assert stories.micro_theory("pristavki", None).startswith("🧠 Запомни: ПРЕ-")
    assert "Исследование: Безударные гласные" in stories.render("bezudarnye", "girl") and "босс" in stories.render("bezudarnye", "boy")


def test_profile_russian_loads(restore_packs, monkeypatch):
    monkeypatch.chdir(ROOT)
    saved = profile.P.__dict__.copy()
    try:
        profile.load(str(ROOT / "profile.russian.json"))
        assert profile.P.subject_gen == "русского языка" and profile.P.pack == "packs/russian-6.json"
        packs.load_file(profile.P.pack)
        assert list(TOPICS) == ["bezudarnye", "pristavki", "chered", "nn", "ne"]
    finally:
        profile.P.__dict__.update(saved)


def test_dockerfile_ships_packs_and_profiles():
    text = (ROOT / "Dockerfile").read_text()
    assert "COPY packs ./packs" in text and "COPY profile*.json ./" in text
