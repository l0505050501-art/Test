import random
from fractions import Fraction

from mathbot import report
from mathbot.engine import REVIEW_GAPS, Settings, Store, Trainer
from mathbot.tasks import Task, TOPICS

from .test_flow import make, ts


def daily(tr, day=1):
    tr.s.run("INSERT INTO sessions(day,kind,status,invited_at,started_at,last_activity) VALUES(?, 'daily','active',?,?,?)",
             (f"2026-11-{day:02d}", ts(day, 17), ts(day, 17), ts(day, 17)))
    return tr.s.one("SELECT MAX(id) m FROM sessions")["m"]


def force(tr, sid, now, topic, level, text, task):
    aid = tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at) VALUES(?,?,?,?,?,?,?)",
                   (sid, 1, topic, level, text, task.dump(), now))
    return aid


def test_typical_mistake_gives_specific_hint():
    tr = make()
    sid = daily(tr)
    t = Task("fractions_add", 3, "Вычисли: 1/2 + 1/3", "num", Fraction(5, 6), "x")
    t.traps = [["2/5", "add_num_den"]]
    force(tr, sid, ts(1, 17), "fractions_add", 3, t.text, t)
    res = tr.submit(sid, "2/5", ts(1, 17, 0, 20))
    assert res.ok is False and res.tag == "add_num_den" and "общему знаменателю" in res.hint
    tr.finish(sid, "done", ts(1, 17, 5))
    assert report.build_session_report(tr, sid)[1].count("складывает/вычитает отдельно") == 1


def test_wrong_answers_come_back_on_schedule():
    tr = make()
    sid = daily(tr)
    t = Task("percent", 2, "Найди 15% от 60.", "num", Fraction(9), "x")
    force(tr, sid, ts(1, 17), "percent", 2, t.text, t)
    tr.submit(sid, "5", ts(1, 17))                       # ошибка → повтор через 4 минуты
    rv = tr.s.one("SELECT * FROM reviews")
    assert rv["stage"] == 0 and abs(rv["due_at"] - (ts(1, 17) + REVIEW_GAPS[0])) < 1
    assert tr._due_review(sid, ts(1, 17, 2)) is None
    row = tr.next_question(sid, ts(1, 17, 5), random.Random(1))
    assert row["tag"] == "review" and row["topic"] == "percent"
    ok_ans = Task.load(row["task_json"]).correct_display().replace("−", "-")
    tr.submit(sid, ok_ans, ts(1, 17, 5, 10))
    rv = tr.s.one("SELECT * FROM reviews")
    assert rv["stage"] == 1 and abs(rv["due_at"] - (ts(1, 17, 5, 10) + REVIEW_GAPS[1])) < 1   # следующий раз ~через сутки
    # ошибка при повторе сбрасывает расписание на начало
    tr.s.run("UPDATE reviews SET due_at=0")
    tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,correct,seconds,tag) VALUES(?,9,'ratio',1,'n','{}',1,2,1,5,NULL)", (sid,))
    row = tr.next_question(sid, ts(1, 17, 6), random.Random(2))
    assert row["tag"] == "review"
    tr.submit(sid, "99999", ts(1, 17, 6, 10))
    rv = tr.s.one("SELECT * FROM reviews")
    assert rv["stage"] == 0 and rv["active"] == 1
    # четыре успешных повтора подряд закрывают ошибку
    for i in range(4):
        sid_i = daily(tr, 10 + i)                       # каждый повтор — на другом занятии
        tr.s.run("UPDATE reviews SET due_at=0")
        tr.s.run("INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,answered_at,correct,seconds,tag) VALUES(?,9,'ratio',1,'n','{}',1,2,1,5,NULL)", (sid_i,))
        row = tr.next_question(sid_i, ts(10 + i, 17, 7), random.Random(i))
        assert row["tag"] == "review"
        tr.submit(sid_i, Task.load(row["task_json"]).correct_display().replace("−", "-"), ts(10 + i, 17, 7, 30))
    assert tr.s.one("SELECT active FROM reviews")["active"] == 0


def test_two_errors_in_row_trigger_lesson_once():
    tr = make()
    sid = daily(tr)
    out = []
    for i in range(3):
        t = Task("ratio", 1, f"q{i}", "num", Fraction(1), "x")
        force(tr, sid, ts(1, 17, i), "ratio", 1, t.text, t)
        out.append(tr.submit(sid, "99", ts(1, 17, i, 10)).lesson_topic)
    assert out == [None, "ratio", None]


def test_gap_in_grade5_found_probed_and_trained():
    tr = make()
    sid = daily(tr)
    # много ошибок в процентах → нужны decimals_muldiv и mult_table
    for i in range(6):
        t = Task("percent", 1, f"q{i}", "num", Fraction(1), "x")
        force(tr, sid, ts(1, 17, i), "percent", 1, t.text, t)
        tr.submit(sid, "99", ts(1, 17, i, 5))
    tr.finish(sid, "done", ts(1, 18))
    states = {r["topic"]: r["state"] for r in tr.s.q("SELECT * FROM prereq")}
    assert states == {"decimals_muldiv": "probe", "mult_table": "probe"}
    # на следующем занятии идут 3 пробных вопроса по каждой теме 5 класса
    sid2 = daily(tr, 2)
    rnd = random.Random(1)
    seen = []
    for _ in range(6):
        row = tr.next_question(sid2, ts(2, 17), rnd)
        assert row["tag"] == "probe"
        seen.append((row["topic"], row["level"]))
        t = Task.load(row["task_json"])
        good = row["topic"] == "decimals_muldiv"          # десятичные знает, таблицу умножения — нет
        tr.submit(sid2, t.correct_display().replace("−", "-") if good else "99999", ts(2, 17, 1))
    assert seen[:3] == [("decimals_muldiv", 1), ("decimals_muldiv", 2), ("decimals_muldiv", 3)]
    states = {r["topic"]: r["state"] for r in tr.s.q("SELECT * FROM prereq")}
    assert states == {"decimals_muldiv": "ok", "mult_table": "active"}
    assert "mult_table" in tr.topic_pool() and "decimals_muldiv" not in tr.topic_pool()
    tr.finish(sid2, "done", ts(2, 18))
    text = report.build_session_report(tr, sid2)[1]
    assert "Пробелы за 5 класс" in text and "таблица умножения" in text


def test_mastery_and_school_topic_boost():
    tr = make()
    sid = daily(tr)
    tr.s.run("INSERT INTO topic_state(topic,level,streak) VALUES('equations',3,0)")
    for i in range(8):
        t = Task("equations", 3, f"q{i}", "num", Fraction(1), "x")
        force(tr, sid, ts(1, 17, i), "equations", 3, t.text, t)
        res = tr.submit(sid, "1", ts(1, 17, i, 10))
    assert tr.is_mastered("equations") and tr.s.get("mastered_equations") == "1"
    assert tr.title(0) == "Новичок" and tr.title(600) == "Знаток"
    # школьная тема получает больший вес, особенно перед контрольной
    tr2 = make()
    w0 = dict(zip(TOPICS, tr2._weights(list(TOPICS), ts(1, 17))))
    tr2.set_school_topic("percent", ts(1, 17))
    w1 = dict(zip(TOPICS, tr2._weights(list(TOPICS), ts(1, 17))))
    assert w1["percent"] == 2.5 * w0["percent"] and w1["ratio"] == w0["ratio"]
    tr2.set_test_date("2026-11-05", ts(1, 17))
    assert tr2.days_to_test(ts(1, 17)) == 4
    assert dict(zip(TOPICS, tr2._weights(list(TOPICS), ts(1, 17))))["percent"] == 4.0 * w0["percent"]


def test_school_questions_asked_in_order_and_weekly():
    tr = make()
    now = ts(2, 17)           # понедельник
    assert tr.school_question(now) == "book"
    tr.set_book("merzlyak")
    assert tr.school_question(now) == "topic"
    tr.set_school_topic("percent", now)
    assert tr.school_question(now) == "test"
    tr.set_test_date(None, now)
    assert tr.school_question(now) is None
    assert tr.school_question(ts(9, 17)) == "topic"      # на следующей неделе снова спросит тему


def test_streak_reward_once_per_day():
    tr2 = make()
    tr2.s.set("reward_text", "кино")
    tr2.s.set("reward_days", "2")
    for d in (1, 2):
        tr2.s.run("INSERT INTO sessions(day,kind,status) VALUES(?, 'daily','done')", (f"2026-11-{d:02d}",))
    sid = tr2.s.one("SELECT id FROM sessions WHERE day='2026-11-02'")["id"]
    assert tr2.reward_due(sid) == ("кино", 2) and tr2.reward_due(sid) is None
