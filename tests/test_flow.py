import random
from datetime import datetime
from zoneinfo import ZoneInfo

from mathbot import report
from mathbot.engine import DIAG_CHUNK, Settings, Store, Trainer
from mathbot.tasks import TOPICS, Task

TZ = ZoneInfo("Europe/Moscow")


def ts(day: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(2026, 11, day, hh, mm, ss, tzinfo=TZ).timestamp()


def make() -> Trainer:
    st = Store(":memory:")
    st.set("child_chat_id", "42")
    st.set("start_time", "17:00")
    return Trainer(st, Settings())


def wrong_answer(t):
    if t.kind == "ints":
        return "7777 8888"
    if t.kind == "text":
        v = t.value[0]
        return {"да": "нет", "нет": "да", ">": "=", "<": "="}.get(v, "99x")
    return "7777"


def solve(tr, sid, now, correct_rate=1.0, step=20, rnd=None):
    rnd = rnd or random.Random(1)
    while not tr.should_finish(sid, now):
        row = tr.next_question(sid, now)
        t = Task.load(row["task_json"])
        now += step
        if rnd.random() < correct_rate:
            ans = t.correct_display().replace("−", "-")
            if t.kind == "ints":
                ans = ans.replace(":", " ")
        else:
            ans = wrong_answer(t)
        ok, _, bad = tr.submit(sid, ans, now)
        assert bad is None
    return now


def test_no_invite_before_time_and_after_cutoff():
    tr = make()
    assert tr.tick(ts(1, 16, 59)) == []
    assert tr.tick(ts(1, 21, 30)) == []
    assert tr.tick(ts(1, 17, 0)) == [("invite", 1)]
    assert tr.tick(ts(1, 17, 1)) == []  # повторно не приглашаем


def test_reminders_then_missed_report():
    tr = make()
    assert tr.tick(ts(1, 17, 0))[0][0] == "invite"
    assert tr.tick(ts(1, 17, 29)) == []
    assert tr.tick(ts(1, 17, 30)) == [("remind1", 1)]
    assert tr.tick(ts(1, 18, 0)) == [("remind2", 1)]
    assert tr.tick(ts(1, 19, 0)) == [("report", 1)]
    assert tr.session(1)["status"] == "missed"
    subj, text, html = report.build_session_report(tr, 1, "Маша")
    assert "Не приступал" in text and "Маша" in subj
    tr.mark_reported(1)
    assert tr.tick(ts(1, 19, 5)) == []


def test_decline_reported():
    tr = make()
    tr.tick(ts(1, 17))
    tr.decline(1, ts(1, 17, 2))
    assert ("report", 1) in tr.tick(ts(1, 17, 3))
    assert "Отказался" in report.build_session_report(tr, 1)[1]


def test_diagnostic_covers_all_topics_over_days_then_daily():
    tr = make()
    days = 0
    while not tr.diagnostic_done():
        d = 1 + days
        assert tr.tick(ts(d, 17))[0][0] == "invite"
        sid = tr.today_session(ts(d, 17))["id"]
        assert tr.session(sid)["kind"] == "diagnostic"
        tr.start(sid, ts(d, 17, 5))
        now = solve(tr, sid, ts(d, 17, 5), correct_rate=0.7, rnd=random.Random(d))
        tr.finish(sid, "done", now)
        assert tr.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=?", (sid,))["c"] <= DIAG_CHUNK
        days += 1
        assert days < 10
    assert days == -(-3 * len(TOPICS) // 15)   # 3 вопроса на тему, по 15 за занятие
    assert all(r["level"] in (1, 2, 3) for r in tr.s.q("SELECT * FROM topic_state"))
    assert len(tr.s.q("SELECT * FROM topic_state")) == len(TOPICS)
    subj, text, _ = report.build_session_report(tr, sid, "Маша")
    assert "СРЕЗ ЗНАНИЙ" in text and "Срез завершён" in text
    # следующий день — обычная тренировка ~10 минут
    nd = days + 1
    assert ("invite", nd) in [(a, nd) for a, _ in tr.tick(ts(nd, 17))]
    sid2 = tr.today_session(ts(nd, 17))["id"]
    assert tr.session(sid2)["kind"] == "daily"
    tr.start(sid2, ts(nd, 17, 5))
    now = solve(tr, sid2, ts(nd, 17, 5), correct_rate=0.8, step=25, rnd=random.Random(5))
    tr.finish(sid2, "done", now)
    st = report.session_stats(tr, sid2)
    assert 20 <= st["n"] <= 30
    assert 9 * 60 <= st["wall"] <= 11 * 60
    assert st["wrong"] > 0
    subj, text, html = report.build_session_report(tr, sid2, "Маша")
    assert "Ошибки:" in text and "<table" in html and "10:" in text or "9:" in text
    print(text)


def test_idle_session_becomes_partial_and_wrong_format_is_free():
    tr = make()
    tr.tick(ts(1, 17))
    tr.start(1, ts(1, 17, 1))
    row = tr.next_question(1, ts(1, 17, 1))
    ok, _, bad = tr.submit(1, "бла-бла", ts(1, 17, 2))
    assert ok is None and bad  # переспросили без штрафа
    assert tr.pending(1)["id"] == row["id"]
    tr.submit(1, wrong_answer(Task.load(row["task_json"])), ts(1, 17, 3))
    acts = tr.tick(ts(1, 17, 30))
    assert ("report", 1) in acts and tr.session(1)["status"] == "partial"
    assert "Начал" in report.build_session_report(tr, 1)[1]


def test_levels_adapt_and_weak_topics_prioritised():
    tr = make()
    tr.tick(ts(1, 17))
    sid = 1
    tr.s.run("UPDATE sessions SET kind='daily'")
    tr.start(sid, ts(1, 17))
    # 4 верных подряд повышают уровень
    for i in range(4):
        row = tr.next_question(sid, ts(1, 17, i))
        t = Task.load(row["task_json"])
        tr.s.run("UPDATE answers SET topic='percent' WHERE id=?", (row["id"],))
        t.topic = "percent"
        tr.s.run("UPDATE answers SET task_json=? WHERE id=?", (t.dump(), row["id"]))
        tr.submit(sid, t.correct_display().replace("−", "-"), ts(1, 17, i, 10))
    assert tr.s.one("SELECT level FROM topic_state WHERE topic='percent'")["level"] == 2
    # слабая тема выбирается чаще
    for _ in range(10):
        tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,given,correct,seconds) "
                 "VALUES(1,99,'ratio',1,'q','{}',1,2,'x',0,5)")
    tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at) VALUES(1,100,'equations',1,'z','{}',1)")
    rnd = random.Random(3)
    picks = [tr._pick_daily(1, rnd)[0] for _ in range(300)]
    assert picks.count("ratio") > picks.count("percent") * 2
