"""Сквозной прогон Телеграм-обработчиков с подставным ботом."""
import asyncio
import time

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mathbot import bot as B
from mathbot import mailer
from mathbot.engine import Settings, Store, Trainer
from mathbot import report
from mathbot.tasks import TOPICS, Task


@pytest.fixture(autouse=True)
def no_weekly(monkeypatch):
    """Недельная сводка уходит по воскресеньям вечером; тесты не должны зависеть от настоящих часов."""
    monkeypatch.setattr(B, "maybe_weekly", AsyncMock())


def make_ctx():
    tr = Trainer(Store(":memory:"), Settings())
    tr.s.set("start_time", "00:00")
    tr.cfg.invite_until = "23:59"
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"tr": tr}), bot=MagicMock(), args=[])
    ctx.bot.send_message = AsyncMock()
    return ctx, tr


def upd(chat, text="", data=None):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=chat, edit_message_reply_markup=AsyncMock())
    q = SimpleNamespace(data=data, message=msg, answer=AsyncMock(), edit_message_reply_markup=AsyncMock()) if data else None
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=chat), message=msg, callback_query=q)


def sent(ctx):
    return [c.args[1] for c in ctx.bot.send_message.call_args_list]


def test_full_day(monkeypatch):
    emails = []

    async def fake_email(subject, text, html):
        emails.append((subject, text))
        return True
    monkeypatch.setattr(mailer, "send_email", fake_email)
    monkeypatch.setattr(mailer, "configured", lambda: True)

    async def run():
        ctx, tr = make_ctx()
        await B.cmd_start(upd(111), ctx)             # ребёнок
        assert tr.s.get("child_chat_id") == "111"
        mom = upd(222)
        await B.cmd_start(mom, ctx)                  # мама подключается как родитель
        assert tr.s.get("parent_chat_id") == "222"
        await B.cmd_settime(upd(222), SimpleNamespace(**{**ctx.__dict__, "args": ["18:30"]}))
        assert tr.start_time() == "18:30"
        tr.s.set("start_time", "00:00")
        await B.tick(ctx)                            # приглашение
        assert any("Время математики" in m or "проверка" in m for m in sent(ctx))
        await B.on_callback(upd(111, data="go"), ctx)
        assert tr.session(1)["status"] == "invited" and "мальчик или девочка" in sent(ctx)[-1]   # сначала знакомство
        for cb in ("gd:girl", "bk:vilenkin", "st:percent", "tt:w2"):
            await B.on_callback(upd(111, data=cb), ctx)
        assert tr.s.get("book") == "vilenkin" and tr.s.get("school_topic") == "percent" and tr.days_to_test(time.time()) == 10
        assert tr.session(1)["status"] == "active"
        for _ in range(60):
            s = tr.session(1)
            if s["status"] != "active":
                break
            row = tr.pending(1)
            t = Task.load(row["task_json"])
            await B.on_text(upd(111, t.correct_display().replace("−", "-").replace(":", " ")), ctx)
        assert tr.session(1)["status"] == "done"
        assert emails and "верно" in emails[0][0]
        assert any(c.args[0] == 222 for c in ctx.bot.send_message.call_args_list)  # мама получила в телеграм
        assert tr.session(1)["reported"] == 1
        # повторное «начать» в тот же день — вежливый отказ
        await B.cmd_go(upd(111), ctx)
        assert "уже" in sent(ctx)[-1]

    asyncio.run(run())


def test_decline_and_parent_guard(monkeypatch):
    emails = []

    async def fake_email(subject, text, html):
        emails.append(text)
        return True
    monkeypatch.setattr(mailer, "send_email", fake_email)
    monkeypatch.setattr(mailer, "configured", lambda: True)

    async def run():
        ctx, tr = make_ctx()
        await B.cmd_start(upd(111), ctx)
        await B.tick(ctx)
        await B.on_callback(upd(111, data="skip"), ctx)
        assert tr.session(1)["status"] == "declined" and "Отказался" in emails[0]
        # ребёнок не может менять настройки
        u = upd(111)
        await B.cmd_settime(u, SimpleNamespace(**{**ctx.__dict__, "args": ["09:00"]}))
        assert "только для родителя" in u.message.reply_text.call_args.args[0]
        # но может начать заново после отказа
        onboarded(tr)
        await B.cmd_go(upd(111), ctx)
        assert tr.session(2)["status"] == "active"

    asyncio.run(run())


def onboarded(tr):
    tr.set_gender("boy")
    tr.set_book("vilenkin")
    tr.set_school_topic("none", time.time())
    tr.set_test_date(None, time.time())


def test_lesson_idk_hint_minimum_and_reward(monkeypatch):
    async def fake_email(*a):
        return True
    monkeypatch.setattr(mailer, "send_email", fake_email)
    monkeypatch.setattr(mailer, "configured", lambda: True)

    async def run():
        ctx, tr = make_ctx()
        await B.cmd_start(upd(111), ctx)
        onboarded(tr)
        tr.s.set("reward_text", "кино")
        tr.s.set("reward_days", "1")
        tr.s.run("UPDATE settings SET value=value WHERE 0")
        # делаем диагностику «пройденной», чтобы шла обычная тренировка
        for t in TOPICS:
            for lvl in (1, 2, 3):
                tr.s.run("INSERT INTO sessions(day,kind,status) SELECT '2020-01-01','diagnostic','done' WHERE NOT EXISTS (SELECT 1 FROM sessions WHERE id=1)")
        sid0 = tr.s.one("SELECT id FROM sessions WHERE kind='diagnostic'")["id"]
        for t in TOPICS:
            for lvl in (1, 2, 3):
                tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,given,correct,seconds) VALUES(?,?,?,?,?,?,1,2,'x',1,5)",
                         (sid0, 1, t, lvl, f"{t}{lvl}", "{}"))
        assert tr.diagnostic_done()
        await B.on_callback(upd(111, data="min"), ctx)           # режим «3 минуты»
        s = tr.session(tr.s.one("SELECT MAX(id) m FROM sessions")["m"])
        assert s["kind"] == "daily" and s["minutes"] == 3
        row = tr.pending(s["id"])
        before = len(sent(ctx))
        await B.on_callback(upd(111, data=f"idk:{row['id']}"), ctx)     # «Не знаю»
        assert any("Правильный ответ" in m for m in sent(ctx)[before:])
        assert tr.s.one("SELECT error_tag FROM answers WHERE id=?", (row["id"],))["error_tag"] == "idk"
        assert tr.s.one("SELECT COUNT(*) c FROM reviews WHERE active=1")["c"] == 1      # ошибка встала в очередь повторения
        # дальше отвечаем верно до конца 3-минутного занятия, время сдвигаем
        for _ in range(8):
            r = tr.pending(s["id"])
            if not r:
                break
            tr.s.run("UPDATE answers SET sent_at=sent_at-60 WHERE id=?", (r["id"],))
            tr.s.run("UPDATE sessions SET started_at=started_at-100 WHERE id=?", (s["id"],))
            await B.on_text(upd(111, Task.load(r["task_json"]).correct_display().replace("−", "-").replace(":", " ")), ctx)
        assert tr.session(s["id"])["status"] == "done"
        assert any("Твоя награда: кино" in m for m in sent(ctx))
        _, text, _ = report.build_session_report(tr, s["id"], "Маша")
        assert "Режим «минимум»" in text and "Очки за занятие" in text and "Пора выдать награду" in text and "Школа:" in text

    asyncio.run(run())


def test_claude_helper_optional(monkeypatch):
    from mathbot import ai
    calls = []
    monkeypatch.setattr(ai, "_call", lambda system, user, max_tokens=450: calls.append(user) or "Объяснение от Claude")

    async def run():
        ctx, tr = make_ctx()
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert not ai.enabled() and await ai.answer_question(tr.s, "как сократить дробь?") is None
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDE_MONTHLY_LIMIT", "2")
        assert await ai.answer_question(tr.s, "как сократить дробь?") == "Объяснение от Claude"
        assert "<question>как сократить дробь?</question>" in calls[0]       # вопрос ребёнка — только как данные
        assert await ai.explain(tr.s, "Проценты", "15% от 60", "4", "9") == "Объяснение от Claude"
        assert "лимит" in (await ai.answer_question(tr.s, "ещё")).lower()      # месячный лимит
        assert len(calls) == 2

    asyncio.run(run())


def test_token_never_logged(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(B, "build_app", lambda: SimpleNamespace(run_polling=lambda **k: None))
    B.main()
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
