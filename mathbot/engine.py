"""Хранилище (SQLite) и логика занятий — без привязки к Телеграму, чтобы её можно было тестировать."""
from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .profile import P
from .tasks import ALL_TOPICS, PREREQ, TAGS, TOPICS, TOPICS5, BadFormat, Task, check, make_task, match_trap

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, kind TEXT, status TEXT,
  invited_at REAL, started_at REAL, finished_at REAL, last_activity REAL,
  reminders INTEGER DEFAULT 0, reported INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, seq INTEGER, topic TEXT, level INTEGER,
  text TEXT, task_json TEXT, sent_at REAL, answered_at REAL, given TEXT, correct INTEGER, seconds REAL);
CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, level INTEGER, stage INTEGER DEFAULT 0, due_at REAL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS prereq (topic TEXT PRIMARY KEY, parent TEXT, state TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS topic_state (topic TEXT PRIMARY KEY, level INTEGER DEFAULT 1, streak INTEGER DEFAULT 0);
"""

DIAG_PER_TOPIC = 3
DIAG_CHUNK = 15            # вопросов диагностики за одно занятие
REVIEW_GAPS = [240, 22 * 3600, 70 * 3600, 6 * 86400]   # через сколько повторять ошибку: 4 мин, ~1 день, ~3 дня, ~неделя
MASTER_ACC, MASTER_SECS = 7 / 8, 30                      # «освоено»: ≥7 из последних 8 верно и ≤30 с в среднем
TITLES = [(0, "Новичок"), (150, "Счётчик"), (500, "Знаток"), (1200, "Эксперт"), (2500, "Мастер"), (5000, "Профессор")]
BOOKS = {"vilenkin": "Виленкин", "merzlyak": "Мерзляк", "nikolsky": "Никольский", "dorofeev": "Дорофеев", "unknown": "не знаю / другой"}
IDLE_CAP = 180             # секунд: дольше этого на один вопрос в «чистое время» не засчитываем


class Store:
    def __init__(self, path: str = "mathbot.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for table, col, typ in [("answers", "tag", "TEXT"), ("answers", "error_tag", "TEXT"), ("answers", "ref", "INTEGER"), ("answers", "reward", "INTEGER"), ("sessions", "bonus", "INTEGER"),
                                ("sessions", "minutes", "INTEGER")]:
            try:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        self.db.commit()

    # --- настройки
    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.db.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            self.db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        return self.db.execute(sql, args).fetchone()

    def run(self, sql: str, args=()) -> int:
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.lastrowid


@dataclass
class Settings:
    tz: str = "Europe/Moscow"
    session_minutes: int = 10
    max_tasks: int = 30
    remind1_min: int = 30
    remind2_min: int = 60
    give_up_min: int = 120
    idle_finish_min: int = 20
    invite_until: str = "21:00"


class Trainer:
    def __init__(self, store: Store, cfg: Settings | None = None):
        self.s = store
        self.cfg = cfg or Settings()
        self.tz = ZoneInfo(self.cfg.tz)

    # ------------------------------------------------------------ время
    def local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def day_of(self, ts: float) -> str:
        return self.local(ts).date().isoformat()

    def start_time(self) -> str:
        return self.s.get("start_time", "17:00")

    # ------------------------------------------------------------ сессии
    def today_session(self, now: float):
        return self.s.one("SELECT * FROM sessions WHERE day=? ORDER BY id DESC LIMIT 1", (self.day_of(now),))

    def session(self, sid: int):
        return self.s.one("SELECT * FROM sessions WHERE id=?", (sid,))

    def diag_counts(self) -> dict[str, int]:
        rows = self.s.q(
            "SELECT a.topic, COUNT(*) c FROM answers a JOIN sessions s ON s.id=a.session_id "
            "WHERE s.kind='diagnostic' AND a.answered_at IS NOT NULL GROUP BY a.topic")
        res = {t: 0 for t in TOPICS}
        res.update({r["topic"]: r["c"] for r in rows})
        return res

    def diagnostic_done(self) -> bool:
        return all(c >= DIAG_PER_TOPIC for c in self.diag_counts().values())

    def due_for_invite(self, now: float) -> bool:
        if self.today_session(now):
            return False
        t = self.local(now)
        hh, mm = map(int, self.start_time().split(":"))
        uh, um = map(int, self.cfg.invite_until.split(":"))
        return (hh, mm) <= (t.hour, t.minute) < (uh, um)

    def create_session(self, now: float, status: str = "invited") -> int:
        kind = "daily" if self.diagnostic_done() else "diagnostic"
        return self.s.run("INSERT INTO sessions(day,kind,status,invited_at,last_activity) VALUES(?,?,?,?,?)",
                          (self.day_of(now), kind, status, now, now))

    def start(self, sid: int, now: float, minutes: int | None = None) -> None:
        self.s.run("UPDATE sessions SET status='active', started_at=?, last_activity=?, minutes=? WHERE id=?", (now, now, minutes, sid))

    def decline(self, sid: int, now: float) -> None:
        self.s.run("UPDATE sessions SET status='declined', finished_at=? WHERE id=?", (now, sid))

    def finish(self, sid: int, status: str, now: float) -> None:
        self.s.run("UPDATE sessions SET status=?, finished_at=? WHERE id=? AND status IN ('invited','active')", (status, now, sid))
        if status in ("done", "partial"):
            self.detect_gaps(now)

    def mark_reported(self, sid: int) -> None:
        self.s.run("UPDATE sessions SET reported=1 WHERE id=?", (sid,))

    # ------------------------------------------------------------ тик планировщика
    def tick(self, now: float) -> list[tuple[str, int]]:
        """Возвращает действия для Телеграм-слоя: ('invite'|'remind1'|'remind2'|'report', session_id)."""
        if not self.s.get("child_chat_id"):
            return []
        actions: list[tuple[str, int]] = []
        if self.due_for_invite(now):
            actions.append(("invite", self.create_session(now)))
        # незавершённые сессии (в том числе вчерашние, если бот был выключен)
        for s in self.s.q("SELECT * FROM sessions WHERE status IN ('invited','active')"):
            sid = s["id"]
            if s["status"] == "invited":
                waited = (now - s["invited_at"]) / 60
                if waited >= self.cfg.give_up_min or s["day"] != self.day_of(now):
                    self.finish(sid, "missed", now)
                    actions.append(("report", sid))
                elif waited >= self.cfg.remind2_min and s["reminders"] < 2:
                    self.s.run("UPDATE sessions SET reminders=2 WHERE id=?", (sid,))
                    actions.append(("remind2", sid))
                elif waited >= self.cfg.remind1_min and s["reminders"] < 1:
                    self.s.run("UPDATE sessions SET reminders=1 WHERE id=?", (sid,))
                    actions.append(("remind1", sid))
            else:  # active
                if (now - s["last_activity"]) / 60 >= self.cfg.idle_finish_min:
                    n = self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=? AND answered_at IS NOT NULL", (sid,))["c"]
                    self.finish(sid, "partial" if n else "missed", s["last_activity"])
                    actions.append(("report", sid))
        for s in self.s.q("SELECT id FROM sessions WHERE reported=0 AND status IN ('done','partial','missed','declined')"):
            if ("report", s["id"]) not in actions:
                actions.append(("report", s["id"]))
        return actions

    # ------------------------------------------------------------ кошелёк: «деньги» за верные ответы
    def currency(self) -> str:
        return self.s.get("currency") or P.currency

    def price(self) -> int:
        v = self.s.get("price_correct")
        return int(v) if v is not None else P.price_correct

    def money_cfg(self) -> dict:
        """Цены. Всё считается от цены верного ответа, если мама не задала иначе."""
        p = self.price()
        g = lambda k, d: int(self.s.get(k)) if self.s.get(k) is not None else d   # noqa: E731
        return {"correct": p, "fast": g("price_fast", round(p * 0.4)), "fast_secs": g("fast_secs", 15),
                "session": g("bonus_session", p * 6), "master": g("bonus_master", p * 10), "cap": g("daily_cap", 0)}

    def set_money(self, currency: str | None = None, price: int | None = None) -> None:
        if currency:
            self.s.set("currency", currency[:12])
        if price is not None:
            self.s.set("price_correct", str(price))
            for k in ("price_fast", "bonus_session", "bonus_master"):
                self.s.set(k, None)         # пересчитать пропорционально новой цене

    def fmt(self, n: int | float) -> str:
        n = int(n) if float(n).is_integer() else n
        return f"{n} {self.currency()}"

    def earned_on(self, day: str) -> int:
        a = self.s.one("SELECT COALESCE(SUM(a.reward),0) r FROM answers a JOIN sessions s ON s.id=a.session_id WHERE s.day=?", (day,))["r"]
        b = self.s.one("SELECT COALESCE(SUM(bonus),0) b FROM sessions WHERE day=?", (day,))["b"]
        return int(a + b)

    def session_earned(self, sid: int) -> tuple[int, int]:
        """(за ответы, бонусы)."""
        a = self.s.one("SELECT COALESCE(SUM(reward),0) r FROM answers WHERE session_id=?", (sid,))["r"]
        b = self.s.one("SELECT COALESCE(bonus,0) b FROM sessions WHERE id=?", (sid,))["b"]
        return int(a), int(b)

    def total_earned(self) -> int:
        a = self.s.one("SELECT COALESCE(SUM(reward),0) r FROM answers")["r"]
        b = self.s.one("SELECT COALESCE(SUM(bonus),0) b FROM sessions")["b"]
        return int(a + b)

    def balance(self) -> int:
        return self.total_earned() - int(self.s.get("paid_total", "0"))

    def pay(self, amount: int | None = None) -> int:
        """Мама выдала деньги ребёнку: списать из «к выплате». Возвращает выплаченную сумму."""
        amount = self.balance() if amount is None else min(amount, self.balance())
        if amount > 0:
            self.s.set("paid_total", str(int(self.s.get("paid_total", "0")) + amount))
        return max(amount, 0)

    def add_bonus(self, sid: int, amount: int) -> int:
        if amount > 0:
            self.s.run("UPDATE sessions SET bonus=COALESCE(bonus,0)+? WHERE id=?", (amount, sid))
        return max(amount, 0)

    def reward_for(self, ok: bool, secs: float, sid: int) -> int:
        if not ok:
            return 0
        c = self.money_cfg()
        r = c["correct"] + (c["fast"] if 1.5 <= secs <= c["fast_secs"] else 0)   # быстрее 1,5 с — подозрительно, бонуса нет
        if c["cap"]:
            day = self.session(sid)["day"]
            r = max(0, min(r, c["cap"] - self.earned_on(day)))
        return r

    # ------------------------------------------------------------ школа: учебник, тема, контрольная
    def school_question(self, now: float) -> str | None:
        """Что бот должен спросить у ученика сейчас: book | topic | test | None."""
        week = "%d-W%02d" % self.local(now).isocalendar()[:2]
        if not self.s.get("book"):
            return "book"
        if self.s.get("school_topic_week") != week:
            return "topic"
        if self.s.get("test_week") != week and self.days_to_test(now) is None:
            return "test"
        return None

    def set_book(self, key: str) -> None:
        self.s.set("book", key)

    def set_school_topic(self, key: str, now: float) -> None:
        self.s.set("school_topic", key if key in TOPICS else None)
        self.s.set("school_topic_week", "%d-W%02d" % self.local(now).isocalendar()[:2])

    def set_test_date(self, iso: str | None, now: float) -> None:
        self.s.set("test_date", iso)
        self.s.set("test_week", "%d-W%02d" % self.local(now).isocalendar()[:2])

    def days_to_test(self, now: float) -> int | None:
        d = self.s.get("test_date")
        if not d:
            return None
        n = (date.fromisoformat(d) - self.local(now).date()).days
        return n if n >= 0 else None

    # ------------------------------------------------------------ очки, звание, серия, награда
    def xp(self) -> int:
        return int(self.s.get("xp", "0"))

    def add_xp(self, n: int) -> None:
        if n:
            self.s.set("xp", str(self.xp() + n))

    @staticmethod
    def title(xp: int) -> str:
        return [t for lim, t in TITLES if xp >= lim][-1]

    def streak(self, upto_day: str) -> int:
        days = {r["day"] for r in self.s.q("SELECT day FROM sessions WHERE status IN ('done','partial') AND day<=?", (upto_day,))}
        d = date.fromisoformat(upto_day)
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= timedelta(days=1)
        return n

    def reward_due(self, sid: int) -> tuple[str, int] | None:
        """Если серия дней достигла порога — вернуть (текст награды, длина серии), один раз за день."""
        text = self.s.get("reward_text")
        if not text:
            return None
        day = self.session(sid)["day"]
        n = max(1, int(self.s.get("reward_days", "7")))
        st = self.streak(day)
        if st and st % n == 0 and not self.s.get(f"reward_given_{day}"):
            self.s.set(f"reward_given_{day}", "1")
            self.s.set(f"reward_note_{sid}", f"🏆 Серия {st} дн.! Пора выдать награду: {text}")
            return text, st
        return None

    # ------------------------------------------------------------ освоение тем и подбор задач
    def _topic_level(self, topic: str) -> int:
        r = self.s.one("SELECT level FROM topic_state WHERE topic=?", (topic,))
        return r["level"] if r else 1

    def _last(self, topic: str, n: int):
        return self.s.q("SELECT correct, seconds FROM answers WHERE topic=? AND answered_at IS NOT NULL ORDER BY id DESC LIMIT ?", (topic, n))

    def _accuracy(self, topic: str) -> float:
        rows = self._last(topic, 12)
        return sum(r["correct"] for r in rows) / len(rows) if rows else 0.5

    def is_mastered(self, topic: str) -> bool:
        rows = self._last(topic, 8)
        if len(rows) < 8 or self._topic_level(topic) < 3:
            return False
        return sum(r["correct"] for r in rows) / 8 >= MASTER_ACC and sum(min(r["seconds"], IDLE_CAP) for r in rows) / 8 <= MASTER_SECS

    def topic_pool(self) -> list[str]:
        return list(TOPICS) + [r["topic"] for r in self.s.q("SELECT topic FROM prereq WHERE state='active'")]

    def _weights(self, topics: list[str], now: float) -> list[float]:
        school = self.s.get("school_topic")
        days = self.days_to_test(now)
        boost = 4.0 if days is not None and days <= 7 else 2.5
        out = []
        for t in topics:
            w = 0.15 + (1 - self._accuracy(t))
            if t in TOPICS5:
                w += 0.5            # пробел за 5 класс — в приоритете
            if self.is_mastered(t):
                w *= 0.3
            if t == school:
                w *= boost
            out.append(w)
        return out

    def _pick_daily(self, sid: int, rnd, now: float | None = None) -> tuple[str, int]:
        now = now or time.time()
        recent = [r["topic"] for r in self.s.q("SELECT topic FROM answers WHERE session_id=? ORDER BY id DESC LIMIT 2", (sid,))]
        topics = self.topic_pool()
        weights = self._weights(topics, now)
        for i, t in enumerate(topics):
            if len(recent) == 2 and recent[0] == recent[1] == t:
                weights[i] = 0.0   # не три подряд из одной темы
        topic = rnd.choices(topics, weights)[0]
        return topic, self._topic_level(topic)

    def _pick_diag(self, sid: int) -> tuple[str, int] | None:
        in_session = self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=?", (sid,))["c"]
        if in_session >= DIAG_CHUNK:
            return None
        for topic, n in self.diag_counts().items():
            if n < DIAG_PER_TOPIC:
                return topic, n + 1
        return None

    def _probe_next(self, sid: int) -> tuple[str, int] | None:
        """Пробные вопросы по теме 5 класса (3 штуки: от простого к сложному)."""
        if self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=? AND tag='probe'", (sid,))["c"] >= 6:
            return None
        for p in self.s.q("SELECT topic FROM prereq WHERE state='probe' ORDER BY ts"):
            c = self.s.one("SELECT COUNT(*) c FROM answers WHERE tag='probe' AND topic=?", (p["topic"],))["c"]
            if c < 3:
                return p["topic"], c + 1
        return None

    def _due_review(self, sid: int, now: float):
        if self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=? AND tag='review'", (sid,))["c"] >= 5:
            return None
        last = self.s.one("SELECT tag FROM answers WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,))
        if last and last["tag"] == "review":
            return None            # повторы перемежаем с обычными заданиями
        return self.s.one("SELECT * FROM reviews WHERE active=1 AND due_at<=? ORDER BY due_at LIMIT 1", (now,))

    def minutes_for(self, s) -> int:
        return s["minutes"] or self.cfg.session_minutes

    def _time_up(self, s, done: int, now: float) -> bool:
        floor = 3 if s["minutes"] and s["minutes"] <= 3 else 5
        return done >= self.cfg.max_tasks or ((now - (s["started_at"] or now)) >= self.minutes_for(s) * 60 and done >= floor)

    def next_question(self, sid: int, now: float, rnd=None):
        """Создаёт и возвращает следующую задачу (строка answers) или None, если занятие пора закончить."""
        rnd = rnd or random
        s = self.session(sid)
        pending = self.pending(sid)
        if pending:
            return pending
        done = self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=?", (sid,))["c"]
        tag = ref = None
        if s["kind"] == "diagnostic":
            pick = self._pick_diag(sid)
        elif self._time_up(s, done, now):
            pick = None
        else:
            pick = self._probe_next(sid) if self.minutes_for(s) > 3 else None
            if pick:
                tag = "probe"
            else:
                rv = self._due_review(sid, now)
                if rv:
                    pick, tag, ref = (rv["topic"], rv["level"]), "review", rv["id"]
                else:
                    pick = self._pick_daily(sid, rnd, now)
        if not pick:
            return None
        avoid = {r["text"] for r in self.s.q("SELECT text FROM answers WHERE session_id=?", (sid,))}
        task = make_task(pick[0], pick[1], rnd, avoid)
        aid = self.s.run(
            "INSERT INTO answers(session_id,seq,topic,level,text,task_json,sent_at,tag,ref) VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, done + 1, task.topic, task.level, task.text, task.dump(), now, tag, ref))
        return self.s.one("SELECT * FROM answers WHERE id=?", (aid,))

    def pending(self, sid: int):
        return self.s.one("SELECT * FROM answers WHERE session_id=? AND answered_at IS NULL ORDER BY id LIMIT 1", (sid,))

    def active_session(self):
        return self.s.one("SELECT * FROM sessions WHERE status='active' ORDER BY id DESC LIMIT 1")

    def take_lesson(self, topic: str) -> bool:
        """True один раз для каждой темы: перед первым заданием показываем мини-урок."""
        key = f"lesson_seen_{topic}"
        if self.s.get(key):
            return False
        self.s.set(key, "1")
        return True

    # ------------------------------------------------------------ ответ
    def submit(self, sid: int, text: str, now: float) -> "Result":
        row = self.pending(sid)
        if not row:
            return Result(None, None)
        task = Task.load(row["task_json"])
        try:
            ok = check(task, text)
        except BadFormat as e:
            return Result(None, task, str(e))
        return self._record(sid, row, task, ok, text, now)

    def give_up(self, sid: int, now: float) -> "Result":
        """Кнопка «Не знаю»: считаем ошибкой, показываем разбор."""
        row = self.pending(sid)
        if not row:
            return Result(None, None)
        return self._record(sid, row, Task.load(row["task_json"]), False, "не знаю", now, idk=True)

    def _record(self, sid: int, row, task: Task, ok: bool, text: str, now: float, idk: bool = False) -> "Result":
        secs = max(0.0, now - row["sent_at"])
        err = None if ok else ("idk" if idk else match_trap(task, text))
        self.s.run("UPDATE answers SET answered_at=?, given=?, correct=?, seconds=?, error_tag=? WHERE id=?",
                   (now, text[:200], int(ok), secs, err, row["id"]))
        self.s.run("UPDATE sessions SET last_activity=? WHERE id=?", (now, sid))
        kind = self.session(sid)["kind"]
        self._update_level(task, ok, sid, row["tag"])
        self._review_update(row, task, ok, now, kind)
        reward = self.reward_for(ok, secs, sid)
        self.s.run("UPDATE answers SET reward=? WHERE id=?", (reward, row["id"]))
        res = Result(ok, task, tag=err, aid=row["id"])
        res.money = reward
        res.hint = TAGS[err][0] if err and err != "idk" else None
        res.xp = (10 + (5 if secs <= 15 else 0)) if ok else 0
        self.add_xp(res.xp)
        if not ok and kind == "daily":
            last2 = self.s.q("SELECT correct FROM answers WHERE session_id=? AND topic=? AND answered_at IS NOT NULL ORDER BY id DESC LIMIT 2", (sid, task.topic))
            key = f"lesson_sess_{sid}_{task.topic}"
            if len(last2) == 2 and not any(r["correct"] for r in last2) and not self.s.get(key):
                self.s.set(key, "1")
                res.lesson_topic = task.topic
        flag = f"mastered_{task.topic}"
        if self.is_mastered(task.topic):
            if not self.s.get(flag):
                self.s.set(flag, "1")
                res.mastered = task.topic
                res.money += self.add_bonus(sid, self.money_cfg()["master"])
        elif self.s.get(flag):
            self.s.set(flag, None)
        return res

    def _review_update(self, row, task: Task, ok: bool, now: float, kind: str) -> None:
        if row["tag"] == "review" and row["ref"]:
            rv = self.s.one("SELECT * FROM reviews WHERE id=?", (row["ref"],))
            if not rv:
                return
            if ok:
                stage = rv["stage"] + 1
                if stage >= len(REVIEW_GAPS):
                    self.s.run("UPDATE reviews SET stage=?, active=0 WHERE id=?", (stage, rv["id"]))
                else:
                    self.s.run("UPDATE reviews SET stage=?, due_at=? WHERE id=?", (stage, now + REVIEW_GAPS[stage], rv["id"]))
            else:
                self.s.run("UPDATE reviews SET stage=0, due_at=? WHERE id=?", (now + REVIEW_GAPS[0], rv["id"]))
            return
        if ok or row["tag"] == "probe":
            return
        gap = REVIEW_GAPS[0] if kind == "daily" else 20 * 3600   # после среза знаний повторяем уже завтра
        ex = self.s.one("SELECT id FROM reviews WHERE active=1 AND topic=?", (task.topic,))
        if ex:
            self.s.run("UPDATE reviews SET stage=0, due_at=?, level=? WHERE id=?", (now + gap, task.level, ex["id"]))
        else:
            self.s.run("INSERT INTO reviews(topic,level,stage,due_at) VALUES(?,?,0,?)", (task.topic, task.level, now + gap))

    def _update_level(self, task: Task, ok: bool, sid: int, tag: str | None = None) -> None:
        s = self.session(sid)
        if tag == "probe":
            rows = self.s.q("SELECT correct FROM answers WHERE tag='probe' AND topic=? AND answered_at IS NOT NULL", (task.topic,))
            if len(rows) >= 3:
                good = sum(r["correct"] for r in rows) >= 2
                self.s.run("UPDATE prereq SET state=? WHERE topic=?", ("ok" if good else "active", task.topic))
                if not good:
                    self.s.run("INSERT INTO topic_state(topic,level,streak) VALUES(?,1,0) ON CONFLICT(topic) DO UPDATE SET level=1, streak=0", (task.topic,))
            return
        if s["kind"] == "diagnostic":
            rows = self.s.q(
                "SELECT a.correct FROM answers a JOIN sessions s ON s.id=a.session_id "
                "WHERE s.kind='diagnostic' AND a.topic=? AND a.answered_at IS NOT NULL", (task.topic,))
            if len(rows) >= DIAG_PER_TOPIC:
                c = sum(r["correct"] for r in rows)
                lvl = 1 if c <= 1 else 2 if c == 2 else 3
                self.s.run("INSERT INTO topic_state(topic,level,streak) VALUES(?,?,0) ON CONFLICT(topic) DO UPDATE SET level=?, streak=0",
                           (task.topic, lvl, lvl))
            return
        r = self.s.one("SELECT level, streak FROM topic_state WHERE topic=?", (task.topic,))
        level, streak = (r["level"], r["streak"]) if r else (task.level, 0)
        if ok:
            streak = max(streak, 0) + 1
            if streak >= 4 and level < 3:
                level, streak = level + 1, 0
        else:
            streak = min(streak, 0) - 1
            if streak <= -2 and level > 1:
                level, streak = level - 1, 0
        self.s.run("INSERT INTO topic_state(topic,level,streak) VALUES(?,?,?) ON CONFLICT(topic) DO UPDATE SET level=?, streak=?",
                   (task.topic, level, streak, level, streak))
        if task.topic in TOPICS5 and self.is_mastered(task.topic):
            self.s.run("UPDATE prereq SET state='done' WHERE topic=? AND state='active'", (task.topic,))

    def detect_gaps(self, now: float) -> list[tuple[str, str]]:
        """Тема 6 класса «западает» → проверяем, нет ли пробела в теме 5 класса."""
        new = []
        for topic in TOPICS:
            rows = [r for r in self._last(topic, 12)]
            if len(rows) < 3 or sum(r["correct"] for r in rows) / len(rows) >= 0.55:
                continue
            for p in PREREQ[topic]:
                if not self.s.one("SELECT 1 FROM prereq WHERE topic=?", (p,)):
                    self.s.run("INSERT INTO prereq(topic,parent,state,ts) VALUES(?,?,'probe',?)", (p, topic, now))
                    new.append((p, topic))
        return new

    def should_finish(self, sid: int, now: float) -> bool:
        s = self.session(sid)
        done = self.s.one("SELECT COUNT(*) c FROM answers WHERE session_id=? AND answered_at IS NOT NULL", (sid,))["c"]
        if s["kind"] == "diagnostic":
            return done >= DIAG_CHUNK or self._pick_diag(sid) is None
        return self._time_up(s, done, now)

    def reset_diagnostic(self) -> None:
        """Начать срез знаний заново: старые ответы остаются в истории, но в расчёт среза не идут."""
        self.s.run("UPDATE sessions SET kind='diagnostic_old' WHERE kind='diagnostic'")
        self.s.run("DELETE FROM topic_state")


@dataclass
class Result:
    ok: bool | None            # None — ответ не разобран, переспросить
    task: Task | None
    bad: str | None = None
    hint: str | None = None    # подсказка по типу ошибки
    tag: str | None = None
    xp: int = 0
    lesson_topic: str | None = None
    mastered: str | None = None
    aid: int | None = None     # id строки answers
    money: int = 0             # заработано за этот ответ

    def __iter__(self):        # совместимость: ok, task, bad = tr.submit(...)
        return iter((self.ok, self.task, self.bad))
