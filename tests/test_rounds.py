import asyncio
import random
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mathbot import bot as B
from mathbot import mailer, report
from mathbot.engine import MIN_TASKS_BONUS, Settings, Store, Trainer
from mathbot.tasks import Task

from .test_flow import make, ts


@pytest.fixture(autouse=True)
def no_weekly(monkeypatch):
    monkeypatch.setattr(B, "maybe_weekly", AsyncMock())
    monkeypatch.setattr(mailer, "configured", lambda: False)


def play(tr, sid, day, n, start_min=1, correct=True):
    """Решить n заданий в раунде sid (по 10 секунд на задание) и завершить."""
    now = ts(day, 17, start_min)
    tr.start(sid, now)
    for _ in range(n):
        row = tr.next_question(sid, now, random.Random(n + sid))
        if row is None:
            break
        t = Task.load(row["task_json"])
        now += 10
        if row["tag"] == "check" or correct:
            ans = t.value[0] if t.kind == "text" else t.correct_display().replace("−", "-").replace(":", " ")
        else:
            ans = "99999"
        tr.submit(sid, ans, now)
    tr.finish(sid, "done", now)
    return now


def main_session_done(tr, day=1):
    tr.s.run("UPDATE settings SET value=value")
    tr.tick(ts(day, 17))
    sid = tr.today_session(ts(day, 17))["id"]
    tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
    play(tr, sid, day, 12)
    return sid


def test_unlimited_extra_rounds_and_money_keeps_growing():
    tr = make()
    main_session_done(tr)
    assert tr.has_finished_today(ts(1, 18))
    balances = [tr.balance()]
    for i in range(5):                                           # ребёнок делает ещё и ещё раунды
        sid = tr.create_session(ts(1, 18, i), extra=True)
        tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
        play(tr, sid, 1, 12, start_min=10 + i * 5)
        balances.append(tr.balance())
    assert balances == sorted(balances) and len(set(balances)) == len(balances)     # каждый раунд приносит деньги
    extra_rows = tr.s.q("SELECT * FROM sessions WHERE extra=1")
    assert len(extra_rows) == 5 and all(r["reported"] == 1 for r in extra_rows)   # отдельных отчётов нет
    # а вот «настоящее занятие» может быть создано только один раз за приглашение
    tr.mark_reported(1)
    assert not [a for a in tr.tick(ts(1, 19)) if a[0] == "invite"]


def test_bonus_only_for_real_rounds():
    tr = make()
    sid = main_session_done(tr)
    assert tr.bonus_eligible(sid)                                # 12 заданий — полноценное занятие
    tiny = tr.create_session(ts(1, 18), extra=True)
    tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (tiny,))
    play(tr, tiny, 1, MIN_TASKS_BONUS - 4, start_min=30)         # раунд из нескольких заданий ради бонуса
    assert not tr.bonus_eligible(tiny)


def mk_ctx():
    tr = Trainer(Store(":memory:"), Settings())
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"tr": tr}), bot=MagicMock(), args=[])
    ctx.bot.send_message = AsyncMock()
    return ctx, tr


def test_day_summary_is_one_message_after_cutoff_and_only_once():
    async def run():
        ctx, tr = mk_ctx()
        tr.s.set("child_chat_id", "1")
        tr.s.set("parent_chat_id", "2")
        tr.s.set("child_name", "Маша")
        main_session_done(tr)
        for i in range(3):
            sid = tr.create_session(ts(1, 18, i), extra=True)
            tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
            play(tr, sid, 1, 12, start_min=10 + i * 5)
        assert tr.summary_due(ts(1, 20, 59)) == []               # до 21:00 мама не получает ничего лишнего
        assert tr.summary_due(ts(1, 21, 1)) == ["2026-11-01"]
        await B.send_day_summary(ctx, "2026-11-01")
        msgs = [c.args[1] for c in ctx.bot.send_message.call_args_list if c.args[0] == 2]
        assert len(msgs) == 1 and "Итоги дня" in msgs[0] and "Раундов: 4" in msgs[0] and "ещё 3 дополнительных" in msgs[0]
        assert "💰 Заработано за день" in msgs[0] and "/payout" in msgs[0]
        assert tr.summary_due(ts(1, 21, 5)) == [] and tr.summary_due(ts(2, 10)) == []
        # дополнительный раунд позже вечером: новая сводка, но не чаще раза в час
        sid = tr.create_session(ts(1, 21, 10), extra=True)
        tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
        play(tr, sid, 1, 12, start_min=11)
        assert tr.summary_due(ts(1, 21, 40)) == []               # прошло меньше часа
        assert tr.summary_due(ts(1, 22, 20)) == ["2026-11-01"]
    asyncio.run(run())


def test_extra_round_report_is_skipped_but_main_report_is_sent():
    async def run():
        ctx, tr = mk_ctx()
        tr.s.set("child_chat_id", "1")
        tr.s.set("parent_chat_id", "2")
        sid = main_session_done(tr)
        tr.s.run("UPDATE sessions SET reported=0 WHERE id=?", (sid,))
        await B.send_report(ctx, sid)
        assert any(c.args[0] == 2 and "Ежедневная тренировка" in c.args[1] for c in ctx.bot.send_message.call_args_list)
        extra = tr.create_session(ts(1, 18), extra=True)
        tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (extra,))
        play(tr, extra, 1, 12, start_min=20)
        n = ctx.bot.send_message.call_count
        await B.send_report(ctx, extra)
        assert ctx.bot.send_message.call_count == n and tr.session(extra)["reported"] == 1
    asyncio.run(run())


def test_session_length_by_tasks_and_minutes():
    tr = make()
    tr.set_session_length(tasks=10)                              # «10 слов за раунд»
    tr.s.run("UPDATE settings SET value=value")
    tr.tick(ts(1, 17))
    sid = tr.today_session(ts(1, 17))["id"]
    tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
    play(tr, sid, 1, 40)
    assert tr.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=? AND tag IS NOT 'check'", (sid,))["c"] == 10
    tr.set_session_length(minutes=7)                             # возвращаем длину по времени
    assert tr.session_tasks() == 0 and tr.minutes_for({"minutes": None}) == 7


def test_length_command_parent_only():
    async def run():
        ctx, tr = mk_ctx()
        for uid in (1, 2):
            await B.cmd_start(SimpleNamespace(effective_chat=SimpleNamespace(id=uid), effective_user=SimpleNamespace(id=uid),
                                              message=SimpleNamespace(reply_text=AsyncMock(), text=""), callback_query=None), ctx)

        def u(uid):
            return SimpleNamespace(effective_chat=SimpleNamespace(id=uid), effective_user=SimpleNamespace(id=uid),
                                   message=SimpleNamespace(reply_text=AsyncMock(), text=""), callback_query=None)
        child, mom = u(1), u(2)
        await B.cmd_length(child, SimpleNamespace(**{**ctx.__dict__, "args": ["15", "заданий"]}))
        assert tr.session_tasks() == 0 and "только для родителя" in child.message.reply_text.call_args.args[0]
        await B.cmd_length(mom, SimpleNamespace(**{**ctx.__dict__, "args": ["15", "заданий"]}))
        assert tr.session_tasks() == 15
        await B.cmd_length(mom, SimpleNamespace(**{**ctx.__dict__, "args": ["12", "минут"]}))
        assert tr.session_tasks() == 0 and tr.minutes_for({"minutes": None}) == 12
        await B.cmd_length(mom, SimpleNamespace(**{**ctx.__dict__, "args": ["abc"]}))
        assert "Формат" in mom.message.reply_text.call_args.args[0]
    asyncio.run(run())


def test_weekly_counts_all_rounds_of_a_day():
    tr = make()
    main_session_done(tr)
    for i in range(2):
        sid = tr.create_session(ts(1, 18, i), extra=True)
        tr.s.run("UPDATE sessions SET kind='daily' WHERE id=?", (sid,))
        play(tr, sid, 1, 12, start_min=10 + i * 5)
    text = report.build_weekly(tr, "2026-11-01", "Маша")[1]
    line = next(l for l in text.splitlines() if l.strip().startswith("2026-11-01"))
    assert "раундов: 3" in line and "💰" in line
    assert "Занимался(лась) дней: 1 из 7" in text
