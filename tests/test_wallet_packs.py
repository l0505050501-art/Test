import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mathbot import bot as B
from mathbot import mailer, packs, report
from mathbot.engine import Settings, Store, Trainer
from mathbot.tasks import ALL_TOPICS, TOPICS, Task, check, make_task

from .test_features import daily, force
from .test_flow import make, ts

EXAMPLE = Path(__file__).resolve().parents[1] / "packs" / "example-english.json"


def answer(tr, sid, now_s, ok=True, secs=10):
    t0 = ts(1, 17, 0, now_s)
    t = Task("percent", 1, f"q{now_s}", "num", __import__("fractions").Fraction(1), "x")
    force(tr, sid, t0, "percent", 1, t.text, t)
    return tr.submit(sid, "1" if ok else "99", t0 + secs)


def test_wallet_rewards_bonuses_payout_and_cap():
    tr = make()
    sid = daily(tr)
    c = tr.money_cfg()
    assert c["correct"] == 5 and c["fast"] == 2 and c["session"] == 30 and c["master"] == 50
    assert answer(tr, sid, 1, True, 10).money == 7        # верно и быстро: 5 + 2
    assert answer(tr, sid, 2, True, 40).money == 5        # верно, но медленно
    assert answer(tr, sid, 3, False, 5).money == 0        # ошибка денег не стоит, штрафа нет
    assert answer(tr, sid, 4, True, 0.5).money == 5       # слишком быстро (спам) — без бонуса за скорость
    assert tr.session_earned(sid) == (17, 0)
    tr.add_bonus(sid, c["session"])
    assert tr.balance() == 47
    assert tr.pay(20) == 20 and tr.balance() == 27
    assert tr.pay() == 27 and tr.balance() == 0 and tr.pay() == 0
    # цена от мамы пересчитывает бонусы пропорционально
    tr.set_money("грн", 10)
    assert tr.currency() == "грн" and tr.money_cfg()["session"] == 60 and tr.fmt(10) == "10 грн"
    tr.s.set("daily_cap", "20")
    assert answer(tr, sid, 5, True, 10).money <= 20
    assert tr.earned_on(tr.session(sid)["day"]) <= 47 + 20 + 0


def test_ideal_session_lands_near_150_200():
    c = make().money_cfg()
    assert 150 <= 25 * (c["correct"] + c["fast"]) + c["session"] <= 250


def mk_ctx():
    tr = Trainer(Store(":memory:"), Settings())
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"tr": tr}), bot=MagicMock(), args=[])
    ctx.bot.send_message = AsyncMock()
    return ctx, tr


def upd(chat, text=""):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=chat)
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=chat), message=msg, callback_query=None)


def test_money_commands_and_report_lines():
    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(upd(1), ctx)      # ребёнок
        await B.cmd_start(upd(2), ctx)      # мама
        u = upd(2)
        await B.cmd_money(u, SimpleNamespace(**{**ctx.__dict__, "args": ["грн", "10"]}))
        assert tr.currency() == "грн" and tr.price() == 10 and "грн" in u.message.reply_text.call_args.args[0]
        await B.cmd_money(u, SimpleNamespace(**{**ctx.__dict__, "args": ["бонус", "80", "лимит", "300"]}))
        assert tr.money_cfg()["session"] == 80 and tr.money_cfg()["cap"] == 300
        sid = daily(tr)
        answer(tr, sid, 1)
        tr.finish(sid, "done", ts(1, 17, 5))
        tr.add_bonus(sid, 80)
        text = report.build_session_report(tr, sid, "Маша")[1]
        assert "💰 Заработано за занятие: 94 грн" in text and "/payout" in text
        u = upd(2)
        await B.cmd_payout(u, SimpleNamespace(**{**ctx.__dict__, "args": []}))
        assert tr.balance() == 0
        # ребёнок не может менять цены
        u = upd(1)
        await B.cmd_money(u, SimpleNamespace(**{**ctx.__dict__, "args": ["грн", "1000"]}))
        assert tr.price() == 10 and "только для родителя" in u.message.reply_text.call_args.args[0]
    asyncio.run(run())


def test_reports_go_to_telegram_without_email(monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)

    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(upd(1), ctx)
        sid = daily(tr)
        answer(tr, sid, 1)
        tr.finish(sid, "done", ts(1, 17, 5))
        await B.send_report(ctx, sid)                     # мамы ещё нет — отчёт не потерян
        assert tr.session(sid)["reported"] == 0
        await B.cmd_start(upd(2), ctx)                    # мама подключилась
        await B.send_report(ctx, sid)
        assert tr.session(sid)["reported"] == 1
        assert any(c.args[0] == 2 and "Ежедневная тренировка" in c.args[1] for c in ctx.bot.send_message.call_args_list)
    asyncio.run(run())


@pytest.fixture
def restore_packs():
    snap = packs.snapshot()
    yield
    packs.restore(snap)


def test_example_pack_validates_and_works(restore_packs):
    pack = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    errors, _ = packs.validate(pack)
    assert errors == []
    packs.apply(pack)
    assert list(TOPICS) == ["to_be", "plurals"] and "percent" not in ALL_TOPICS
    rnd = random.Random(1)
    for topic in TOPICS:
        for lvl in (1, 2, 3):
            for _ in range(30):
                t = make_task(topic, lvl, rnd)
                canon = t.value[0] if t.kind == "text" else t.correct_display()
                assert check(t, canon) and check(t, canon.upper())
                assert Task.load(t.dump()).text == t.text
    t = make_task("to_be", 3, random.Random(2))
    while "дома" not in t.text:
        t = make_task("to_be", 3, rnd)
    assert check(t, "i'm at home") and check(t, "I AM AT HOME") and not check(t, "I is at home")
    # английское «no» не превращается в русское «нет»
    yn = Task("to_be", 1, "x", "text", ["no"])
    assert check(yn, "no") and not check(yn, "yes")
    # полный срез знаний и занятие на пакете
    tr = make()
    tr.s.run("UPDATE settings SET value=value")
    sid = None
    for day in range(1, 4):
        if tr.diagnostic_done():
            break
        tr.tick(ts(day, 17))
        sid = tr.today_session(ts(day, 17))["id"]
        tr.start(sid, ts(day, 17, 1))
        now = ts(day, 17, 1)
        while not tr.should_finish(sid, now):
            row = tr.next_question(sid, now, rnd)
            now += 10
            tr.submit(sid, Task.load(row["task_json"]).value[0], now)
        tr.finish(sid, "done", now)
    assert tr.diagnostic_done()
    assert "СРЕЗ ЗНАНИЙ" in report.build_session_report(tr, sid, "Маша")[1]


def test_pack_state_restored_after_previous_test():
    assert "percent" in TOPICS and len(TOPICS) == 13


def test_validator_catches_problems():
    bad = {"topics": {"a": "A"}, "lessons": {}, "questions": [
        {"topic": "a", "level": 1, "kind": "num", "text": "q", "answer": "abc", "explanation": ""},
        {"topic": "b", "level": 1, "kind": "text", "text": "q2", "answer": "x", "explanation": "e"},
        {"topic": "a", "level": 1, "kind": "text", "text": "q", "answer": "x", "explanation": "e"}]}
    errs, _ = packs.validate(bad)
    joined = "\n".join(errs)
    for part in ("нет мини-урока", "не число", "нет explanation", "неизвестная тема", "повтор вопроса", "минимум 3"):
        assert part in joined, part
    with pytest.raises(ValueError):
        packs.apply(bad)


def test_pack_stories_validated_and_used(restore_packs):
    from mathbot import stories
    pack = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    pack["stories"] = {"to_be": {"rule": "I — am, he/she/it — is, we/you/they — are.", "steps": "1) Найди, кто делает. 2) Выбери am, is или are.",
                                 "boy": {"hook": "Робот сообщает о себе: I am a robot.", "example": "He is a pilot."},
                                 "girl": {"hook": "Подруга говорит: I am Anna.", "example": "She is my sister."}}}
    assert packs.validate(pack)[0] == []
    bad = json.loads(json.dumps(pack))
    del bad["stories"]["to_be"]["girl"]["hook"]
    bad["stories"]["zzz"] = {}
    errs = "\n".join(packs.validate(bad)[0])
    assert "stories.to_be.girl" in errs and "stories: неизвестная тема zzz" in errs
    packs.apply(pack)
    assert "Робот сообщает" in stories.render("to_be", "boy") and "Подруга говорит" in stories.render("to_be", "girl")
    assert "Исследование" in stories.render("plurals", "boy")        # для темы без stories — мини-урок
