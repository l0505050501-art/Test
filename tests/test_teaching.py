import asyncio
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mathbot import bot as B
from mathbot import mailer, report, stories
from mathbot.engine import Settings, Store, Trainer
from mathbot.tasks import ALL_TOPICS, TOPICS, Task

from .test_flow import make, ts, wrong_answer


def diag_session(tr, day=1):
    tr.tick(ts(day, 17))
    sid = tr.today_session(ts(day, 17))["id"]
    tr.start(sid, ts(day, 17, 1))
    return sid


def right(task_row):
    t = Task.load(task_row["task_json"])
    return t.correct_display().replace("−", "-").replace(":", " ") if t.kind != "text" else t.value[0]


def test_stories_cover_every_topic_both_themes():
    assert set(ALL_TOPICS) <= set(stories.S)
    for topic in ALL_TOPICS:
        boy, girl = stories.render(topic, "boy"), stories.render(topic, "girl")
        assert boy != girl and ALL_TOPICS[topic] in boy and "Открытие" in boy and "Пример" in girl
        assert len(boy) < 3500 and len(girl) < 3500
        again = stories.render(topic, "girl", variant=2, extra="Разбор задачи")
        assert "Шаги" in again and "Разбор твоего задания: Разбор задачи" in again
        neutral = stories.render(topic, "none")                     # пол не указан — общее правило
        assert "Исследование" in neutral and neutral != boy


def test_not_covered_in_diagnostic_skips_topic_and_is_not_an_error():
    tr = make()
    sid = diag_session(tr)
    row = tr.next_question(sid, ts(1, 17, 1))
    topic = row["topic"]
    res = tr.not_covered(sid, ts(1, 17, 1, 5))
    assert res.nc and res.ok is False and res.money == 0
    assert tr.diag_counts()[topic] == 3                            # остальные 2 вопроса темы закрыты автоматически
    assert tr.s.one("SELECT COUNT(*) c FROM answers WHERE tag='skip'")["c"] == 2
    assert tr._accuracy(topic) == 0.5                              # «не проходили» не портит точность
    assert tr.not_covered_topics() == [topic]
    nxt = tr.next_question(sid, ts(1, 17, 1, 6))
    assert nxt["topic"] != topic and nxt["seq"] == 2               # автопропуски не съедают лимит занятия
    due = tr.teach_due(sid, ts(1, 17, 2))
    assert due["topic"] == topic and due["reason"] == "nc"


def test_teach_loop_understood_then_retell_then_adult():
    tr = make()
    sid = diag_session(tr)
    row = tr.next_question(sid, ts(1, 17, 1))
    topic = row["topic"]
    tr.not_covered(sid, ts(1, 17, 1, 5))
    due = tr.teach_due(sid, ts(1, 17, 2))
    tr.teach_start(sid, due["id"])
    assert tr.teach_due(sid, ts(1, 17, 2)) is None                 # пока идёт объяснение, новое не начинаем
    chk = tr.next_question(sid, ts(1, 17, 3))
    assert chk["tag"] == "check" and chk["topic"] == topic and chk["level"] == 1
    # 1) понял с первого раза
    res = tr.submit(sid, right(chk), ts(1, 17, 3, 20))
    assert res.teach == "understood"
    assert tr.s.one("SELECT result FROM teach WHERE id=?", (due["id"],))["result"] == "ok"
    # 2) не понял → объяснение по-другому → не понял → нужна помощь взрослого
    tr.teach_enqueue(topic, "idk", "now")
    due = tr.teach_due(sid, ts(1, 17, 4))
    tr.teach_start(sid, due["id"])
    chk = tr.next_question(sid, ts(1, 17, 4, 5))
    res = tr.submit(sid, wrong_answer(Task.load(chk["task_json"])), ts(1, 17, 4, 30))
    assert res.teach == "retell" and res.ok is False
    tr.teach_retold(due["id"])
    chk2 = tr.next_question(sid, ts(1, 17, 5))
    assert chk2["tag"] == "check" and chk2["id"] != chk["id"]
    res = tr.submit(sid, wrong_answer(Task.load(chk2["task_json"])), ts(1, 17, 5, 30))
    assert res.teach == "adult"
    assert tr.s.one("SELECT result FROM teach WHERE id=?", (due["id"],))["result"] == "help"
    assert tr.s.one("SELECT active FROM reviews WHERE topic=?", (topic,))["active"] == 1   # вернёмся к теме завтра
    # проверочные не портят статистику среза
    assert tr.diag_counts()[topic] == 3
    tr.finish(sid, "done", ts(1, 17, 9))
    text = report.build_session_report(tr, sid, "Маша")[1]
    assert "Не проходили в школе" in text and "нужна ваша помощь" in text and "проверочные задания после объяснения: верно 1 из 3" in text


def test_weak_topic_in_diagnostic_gets_taught_with_cap():
    tr = make()
    sid = diag_session(tr)
    taught = []
    for q in range(15):
        base = ts(1, 17, 1) + q * 30
        row = tr.next_question(sid, base)
        if row is None:
            break
        if row["tag"] == "check":
            tr.submit(sid, right(row), base + 5)
            continue
        # все ответы неверные: каждая тема получается слабой
        tr.submit(sid, wrong_answer(Task.load(row["task_json"])), base + 5)
        due = tr.teach_due(sid, base + 6)
        if due:
            tr.teach_start(sid, due["id"])
            taught.append(due["topic"])
    assert 1 <= len(taught) <= 2                                   # на срезе не больше двух объяснений за занятие
    queued = tr.teach_report(sid)["queued"]
    assert queued and not set(queued) & set(taught)                # остальное ждёт следующих занятий


def test_idk_in_daily_triggers_teaching_and_finish_requeues():
    tr = make()
    tr.s.run("UPDATE settings SET value=value")
    tr.tick(ts(1, 17))
    sid = tr.today_session(ts(1, 17))["id"]
    tr.s.run("UPDATE sessions SET kind='daily'")
    tr.start(sid, ts(1, 17, 1))
    row = tr.next_question(sid, ts(1, 17, 1), random.Random(1))
    res = tr.give_up(sid, ts(1, 17, 1, 5))
    assert res.tag == "idk"
    due = tr.teach_due(sid, ts(1, 17, 2))
    assert due["topic"] == row["topic"] and due["reason"] == "idk"
    tr.teach_start(sid, due["id"])
    tr.finish(sid, "partial", ts(1, 17, 3))                        # занятие оборвалось на объяснении
    t = tr.s.one("SELECT * FROM teach WHERE id=?", (due["id"],))
    assert t["state"] == "queued" and t["prio"] == "next" and t["session_id"] is None


def test_first_level_up_needs_three_in_a_row():
    tr = make()
    tr.s.run("INSERT INTO sessions(day,kind,status,invited_at,started_at,last_activity) VALUES('2026-11-01','daily','active',1,1,1)")
    sid = 1
    t = Task("percent", 1, "q", "num", __import__("fractions").Fraction(1), "x")
    for _ in range(3):
        tr._update_level(t, True, sid)
    assert tr.s.one("SELECT level FROM topic_state WHERE topic='percent'")["level"] == 2
    assert tr.stars("percent") == "★★☆"


def test_weekly_report_survives_unfinished_sessions():
    tr = make()
    tr.tick(ts(1, 17))                                             # занятие в статусе «ждёт»
    tr.s.run("INSERT INTO sessions(day,kind,status) VALUES('2026-11-02','daily','active')")
    subj, text, _ = report.build_weekly(tr, "2026-11-02", "Маша")
    assert "ждёт" in text and "идёт" in text


# ---------------------------------------------------------------- Телеграм
@pytest.fixture(autouse=True)
def no_weekly(monkeypatch):
    monkeypatch.setattr(B, "maybe_weekly", AsyncMock())


def mk_ctx():
    tr = Trainer(Store(":memory:"), Settings())
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"tr": tr}), bot=MagicMock(), args=[])
    ctx.bot.send_message = AsyncMock()
    return ctx, tr


def upd(chat, text="", data=None):
    msg = SimpleNamespace(text=text, reply_text=AsyncMock(), chat_id=chat)
    q = SimpleNamespace(data=data, message=msg, answer=AsyncMock(), edit_message_reply_markup=AsyncMock()) if data else None
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=SimpleNamespace(id=chat), message=msg, callback_query=q)


def texts(ctx):
    return [c.args[1] for c in ctx.bot.send_message.call_args_list]


def buttons_of(call):
    mk = call.kwargs.get("reply_markup")
    return [b.text for row in mk.inline_keyboard for b in row] if mk else []


@pytest.mark.parametrize("gender,marker", [("boy", "робот"), ("girl", "бусин")])
def test_bot_teaches_after_not_covered_with_themed_story(monkeypatch, gender, marker):
    async def fake_email(*a):
        return True
    monkeypatch.setattr(mailer, "send_email", fake_email)
    monkeypatch.setattr(mailer, "configured", lambda: False)

    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(upd(1), ctx)
        await B.cmd_start(upd(2), ctx)                              # мама
        for cb in (f"gd:{gender}", "bk:vilenkin", "st:none", "tt:none"):
            tr.set_gender(gender) if cb.startswith("gd:") else None
        tr.set_book("vilenkin"); tr.set_school_topic("none", __import__("time").time()); tr.set_test_date(None, __import__("time").time())
        await B.on_callback(upd(1, data="go"), ctx)
        q = ctx.bot.send_message.call_args_list[-1]
        assert {"🤷 Не знаю", "📚 Не проходили"} <= set(buttons_of(q))   # обе кнопки есть на каждом задании
        sid = tr.active_session()["id"]
        row = tr.pending(sid)
        topic = row["topic"]
        await B.on_callback(upd(1, data=f"nc:{row['id']}"), ctx)
        msgs = texts(ctx)
        assert any("ещё не проходили" in m for m in msgs)
        story = next(m for m in msgs if m.startswith("📚 Эту тему"))
        assert "🔎 Исследование" in story and marker in story and "Открытие" in story
        last = ctx.bot.send_message.call_args_list[-1]
        assert last.args[1].startswith("✅ Проверь себя")
        assert "📚 Не проходили" not in buttons_of(last)            # на проверке «не проходили» уже не нужно
        # понял
        chk = tr.pending(sid)
        await B.on_text(upd(1, right(chk)), ctx)
        assert any("Ты понял" in m and "★" in m for m in texts(ctx))
        # отчёт маме: «не проходили» — не ошибка
        tr.finish(sid, "done", ts(1, 17, 30))
        text = report.build_session_report(tr, sid, "Маша")[1]
        assert "📚 Не проходили в школе" in text and "понял после объяснения" in text

    asyncio.run(run())


def test_bot_retell_and_adult_messages():
    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(upd(1), ctx)
        tr.set_gender("girl"); tr.set_book("vilenkin"); tr.set_school_topic("none", __import__("time").time()); tr.set_test_date(None, __import__("time").time())
        await B.on_callback(upd(1, data="go"), ctx)
        sid = tr.active_session()["id"]
        row = tr.pending(sid)
        await B.on_callback(upd(1, data=f"idk:{row['id']}"), ctx)     # в срезе идёт дальше, разбор после темы
        # доводим тему до конца: ещё два неверных
        for _ in range(2):
            r = tr.pending(sid)
            if r["topic"] != row["topic"]:
                break
            await B.on_text(upd(1, wrong_answer(Task.load(r["task_json"]))), ctx)
        msgs = texts(ctx)
        assert any(m.startswith("🧭 Давай разберём тему") for m in msgs)
        chk = tr.pending(sid)
        assert chk["tag"] == "check"
        await B.on_text(upd(1, wrong_answer(Task.load(chk["task_json"]))), ctx)
        msgs = texts(ctx)
        assert any("попробуем по-другому" in m for m in msgs) and any(m.startswith("🔁 Попробуем по-другому") and "Шаги" in m for m in msgs)
        chk2 = tr.pending(sid)
        assert chk2["tag"] == "check" and chk2["id"] != chk["id"]
        await B.on_text(upd(1, wrong_answer(Task.load(chk2["task_json"]))), ctx)
        assert any("позовём взрослого" in m for m in texts(ctx))

    asyncio.run(run())


def test_gender_command_for_parent_only_and_me_shows_stars():
    async def run():
        ctx, tr = mk_ctx()
        await B.cmd_start(upd(1), ctx)
        await B.cmd_start(upd(2), ctx)
        u = upd(1)
        await B.cmd_gender(u, SimpleNamespace(**{**ctx.__dict__, "args": ["девочка"]}))
        assert tr.gender() is None and "только для родителя" in u.message.reply_text.call_args.args[0]
        u = upd(2)
        await B.cmd_gender(u, SimpleNamespace(**{**ctx.__dict__, "args": ["девочка"]}))
        assert tr.gender() == "girl"
        tr.s.run("INSERT INTO sessions(day,kind,status) VALUES('2026-11-01','daily','done')")
        tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,given,correct,seconds) VALUES(1,1,'percent',1,'q','{}',1,2,'x',1,5)")
        u = upd(1)
        await B.cmd_me(u, ctx)
        assert "★" in u.message.reply_text.call_args.args[0]

    asyncio.run(run())


def test_backfill_queues_weak_topics_from_old_diagnostic_once():
    tr = make()
    tr.s.run("INSERT INTO sessions(day,kind,status) VALUES('2026-10-30','diagnostic','done')")
    for topic, results in {"percent": (0, 0, 1), "ratio": (1, 1, 1), "circle": (0, 1, 1)}.items():
        for i, ok in enumerate(results):
            tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,given,correct,seconds) VALUES(1,?,?,?,'q','{}',1,2,'x',?,5)",
                     (i + 1, topic, i + 1, ok))
    assert tr.backfill_teaching() == 1                              # только «Проценты» (2 ошибки из 3)
    assert [r["topic"] for r in tr.s.q("SELECT topic FROM teach")] == ["percent"]
    assert tr.backfill_teaching() == 0                              # повторно не добавляем
    tr.tick(ts(2, 17))
    sid = tr.today_session(ts(2, 17))["id"]
    tr.start(sid, ts(2, 17, 1))
    assert tr.teach_due(sid, ts(2, 17, 1))["topic"] == "percent"    # на первом же занятии начнём с разбора
