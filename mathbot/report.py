"""Отчёты для родителя: текст (для Телеграма) и HTML (для почты)."""
from __future__ import annotations

import html
import time
from datetime import timedelta

from .engine import DIAG_PER_TOPIC, IDLE_CAP, Trainer
from collections import Counter

from .engine import BOOKS
from .profile import P
from .tasks import ALL_TOPICS, TAGS, TOPICS, Task

STATUS = {
    "done": "✅ Занятие выполнено",
    "partial": "⚠️ Начал(а), но не закончил(а) — бросил(а) посреди занятия",
    "missed": "❌ Не приступал(а): не отреагировал(а) на приглашение и напоминания",
    "declined": "🙅 Отказался(лась): нажал(а) «Не сегодня»",
}


SHORT = {"done": "выполнено", "partial": "не закончено", "missed": "не приступал(а)", "declined": "отказ"}


def mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def label(acc: float) -> str:
    return "хорошо" if acc >= 0.85 else "надо повторить" if acc >= 0.6 else "западает"


def session_stats(t: Trainer, sid: int) -> dict:
    s = t.session(sid)
    rows = t.s.q("SELECT * FROM answers WHERE session_id=? AND answered_at IS NOT NULL ORDER BY seq", (sid,))
    n = len(rows)
    ok = sum(r["correct"] for r in rows)
    active = sum(min(r["seconds"], IDLE_CAP) for r in rows)
    by_topic: dict[str, dict] = {}
    for r in rows:
        d = by_topic.setdefault(r["topic"], {"n": 0, "ok": 0, "sec": 0.0})
        d["n"] += 1
        d["ok"] += r["correct"]
        d["sec"] += min(r["seconds"], IDLE_CAP)
    errors = [r for r in rows if not r["correct"]]
    slow = [r for r in rows if r["seconds"] > 60]
    reaction = (s["started_at"] - s["invited_at"]) if s["started_at"] and s["invited_at"] else None
    wall = (s["finished_at"] - s["started_at"]) if s["finished_at"] and s["started_at"] else None
    tags = Counter(r["error_tag"] for r in errors if r["error_tag"])
    rev = [r for r in rows if r["tag"] == "review"]
    xp = sum((10 + (5 if r["seconds"] <= 15 else 0)) for r in rows if r["correct"])
    return {"tags": tags, "rev_n": len(rev), "rev_ok": sum(r["correct"] for r in rev), "xp": xp,
            "s": s, "rows": rows, "n": n, "ok": ok, "wrong": n - ok, "active": active,
            "avg": active / n if n else 0, "by_topic": by_topic, "errors": errors, "slow": slow,
            "reaction": reaction, "wall": wall}


def streak_days(t: Trainer, upto_day: str) -> int:
    return t.streak(upto_day)


def diagnostic_summary(t: Trainer) -> list[dict]:
    res = []
    for topic, name in TOPICS.items():
        rows = t.s.q(
            "SELECT a.level, a.correct, a.seconds FROM answers a JOIN sessions s ON s.id=a.session_id "
            "WHERE s.kind='diagnostic' AND a.topic=? AND a.answered_at IS NOT NULL ORDER BY a.id", (topic,))
        if rows:
            ok = sum(r["correct"] for r in rows)
            res.append({"topic": topic, "name": name, "n": len(rows), "ok": ok, "acc": ok / len(rows),
                        "avg": sum(min(r["seconds"], IDLE_CAP) for r in rows) / len(rows)})
        else:
            res.append({"topic": topic, "name": name, "n": 0, "ok": 0, "acc": 0.0, "avg": 0})
    return res


def topic_overview(t: Trainer) -> list[dict]:
    """Текущая картина по темам: точность на последних ответах и уровень."""
    out = []
    for topic, name in ALL_TOPICS.items():
        if topic not in TOPICS and not t.s.one("SELECT 1 FROM prereq WHERE topic=? AND state IN ('active','done')", (topic,)):
            continue
        rows = t.s.q("SELECT correct FROM answers WHERE topic=? AND answered_at IS NOT NULL ORDER BY id DESC LIMIT 15", (topic,))
        st = t.s.one("SELECT level FROM topic_state WHERE topic=?", (topic,))
        n = len(rows)
        acc = sum(r["correct"] for r in rows) / n if n else 0
        out.append({"topic": topic, "name": name, "n": n, "acc": acc, "level": st["level"] if st else None})
    return out


def _bar(acc: float, width: int = 10) -> str:
    k = round(acc * width)
    return "█" * k + "░" * (width - k)


def school_lines(t: Trainer, now: float) -> list[str]:
    book = t.s.get("book")
    topic = t.s.get("school_topic")
    days = t.days_to_test(now)
    out = []
    if book or topic or days is not None:
        out.append("Школа: " + "; ".join(x for x in [
            f"учебник — {BOOKS.get(book, book)}" if book else "",
            f"сейчас проходят — {TOPICS[topic]}" if topic in TOPICS else "",
            (f"контрольная через {days} дн." if days else "контрольная сегодня") if days is not None else ""] if x))
        if topic in TOPICS:
            rows = t._last(topic, 12)
            if rows:
                acc = sum(r["correct"] for r in rows) / len(rows)
                out.append(f"Готовность по этой теме: {round(acc * 100)}% — {label(acc)}")
    return out


def gap_lines(t: Trainer) -> list[str]:
    out = []
    for p in t.s.q("SELECT * FROM prereq ORDER BY ts"):
        parent = TOPICS.get(p["parent"], p["parent"])
        name = ALL_TOPICS.get(p["topic"], p["topic"])
        st = {"probe": "будет проверено на ближайших занятиях", "active": "пробел подтверждён, бот тренирует эту тему",
              "ok": "проверка прошла — пробела нет", "done": "пробел закрыт ✅"}[p["state"]]
        out.append(f"  • {name} (нужна для темы «{parent}») — {st}")
    return out


def extras_text(t: Trainer, st: dict) -> list[str]:
    s = st["s"]
    lines: list[str] = []
    if st["n"]:
        if s["minutes"] and s["minutes"] <= 3:
            lines.append("Режим «минимум»: короткая версия занятия (3 минуты).")
        if st["tags"]:
            lines += ["", "Типичные ошибки (по ответам):"]
            for tag, n in st["tags"].most_common():
                lines.append(f"  • {TAGS[tag][1]} — {n}")
        if st["rev_n"]:
            lines.append(f"Повторение прошлых ошибок: решено верно {st['rev_ok']} из {st['rev_n']}")
        xp = t.xp()
        lines += ["", f"Очки за занятие: +{st['xp']}, всего {xp} — звание «{t.title(xp)}»"]
        if t.money_cfg()["correct"]:
            a, b = t.session_earned(s["id"])
            lines.append(f"💰 Заработано за занятие: {t.fmt(a + b)}" + (f" (бонус за полное занятие и освоение: {t.fmt(b)})" if b else "")
                         + f". К выплате сейчас: {t.fmt(t.balance())} (отметить выплату: /payout)")
    note = t.s.get(f"reward_note_{s['id']}")
    if note:
        lines += ["", note]
    gaps = gap_lines(t)
    if gaps:
        lines += ["", f"Пробелы за {P.foundation_label} (спуск вниз):"] + gaps
    sc = school_lines(t, s["finished_at"] or s["invited_at"])
    if sc:
        lines += [""] + sc
    return lines


def advice(t: Trainer) -> str:
    """Совет маме: на чём сосредоточиться на этой неделе."""
    ov = [o for o in topic_overview(t) if o["n"] >= 3]
    if not ov:
        return "Пока мало данных для совета."
    weak = min(ov, key=lambda o: o["acc"])
    tags = Counter(r["error_tag"] for r in t.s.q(
        "SELECT error_tag FROM answers WHERE topic=? AND correct=0 AND error_tag IS NOT NULL AND error_tag!='idk' ORDER BY id DESC LIMIT 20", (weak["topic"],)))
    msg = f"Совет на неделю: тема «{weak['name']}» ({round(weak['acc'] * 100)}%)."
    if tags:
        msg += f" Чаще всего ребёнок {TAGS[tags.most_common(1)[0][0]][1]}. Уделите этому 5 минут вместе: попросите объяснить решение вслух."
    else:
        msg += " Посидите 5 минут вместе и попросите ребёнка объяснить решение вслух."
    return msg


def build_session_report(t: Trainer, sid: int, child_name: str = "") -> tuple[str, str, str]:
    """Возвращает (тема письма, текст, html)."""
    st = session_stats(t, sid)
    s = st["s"]
    name = child_name or "Ученик"
    kind = "Срез знаний" if s["kind"] == "diagnostic" else "Ежедневная тренировка"
    title = f"{kind} · {name} · {s['day']}"
    if st["n"]:
        subj = f"{P.subject_nom.capitalize()}, {name}: {st['ok']} из {st['n']} верно, {mmss(st['active'])} мин ({SHORT[s['status']]})"
    else:
        subj = f"{P.subject_nom.capitalize()}, {name}: {SHORT[s['status']]}"

    lines: list[str] = [title, "", STATUS[s["status"]], ""]
    if st["reaction"] is not None:
        lines.append(f"Откликнулся(лась) на приглашение через: {mmss(st['reaction'])} мин")
    if st["n"]:
        lines += [
            f"Заданий выдано и решено: {st['n']}",
            f"Верно: {st['ok']}   Ошибок: {st['wrong']}   Точность: {round(100 * st['ok'] / st['n'])}%",
            f"Время решения (без пауз): {mmss(st['active'])} мин; в среднем {mmss(st['avg'])} на задание",
        ]
        if st["wall"]:
            lines.append(f"От старта до конца занятия: {mmss(st['wall'])} мин")
        if st["slow"]:
            lines.append(f"Дольше минуты думал(а) над заданиями: {len(st['slow'])}")
        if s["status"] == "done" or s["status"] == "partial":
            lines.append(f"Занимается подряд дней: {streak_days(t, s['day'])}")
        lines += ["", "По темам:"]
        for topic, d in st["by_topic"].items():
            lines.append(f"  • {ALL_TOPICS[topic]}: {d['ok']}/{d['n']} верно, в среднем {mmss(d['sec'] / d['n'])}")
        if st["errors"]:
            lines += ["", "Ошибки:"]
            for r in st["errors"]:
                task = Task.load(r["task_json"])
                lines.append(f"  ✗ {r['text']}\n      ответ: «{r['given']}», правильно: {task.correct_display()}")
    lines += extras_text(t, st)
    if s["kind"] == "diagnostic":
        lines += ["", "СРЕЗ ЗНАНИЙ (накопительно по темам):"]
        for d in diagnostic_summary(t):
            if d["n"]:
                lines.append(f"  {_bar(d['acc'])} {round(d['acc'] * 100):>3}%  {d['name']} ({d['ok']}/{d['n']}) — {label(d['acc'])}")
            else:
                lines.append(f"  ░░░░░░░░░░  --  {d['name']} — ещё не проверяли")
        if t.diagnostic_done():
            weak = [d['name'] for d in diagnostic_summary(t) if d['acc'] < 0.6]
            lines.append("")
            lines.append("Срез завершён. Начиная со следующего занятия бот чаще даёт слабые темы." +
                         (f" Западают: {'; '.join(weak)}." if weak else " Серьёзных провалов нет."))
        else:
            lines.append("")
            lines.append("Срез ещё не закончен — остальные темы бот проверит на следующих занятиях.")
    text = "\n".join(lines)
    return subj, text, _html(title, text, st, s, t)


def _html(title: str, text: str, st: dict, s, t: Trainer) -> str:
    e = html.escape
    parts = [f"<h2>{e(title)}</h2><p style='font-size:16px'><b>{e(STATUS[s['status']])}</b></p>"]
    if st["n"]:
        acc = round(100 * st["ok"] / st["n"])
        parts.append(
            "<table cellpadding='6' style='border-collapse:collapse;border:1px solid #ccc'>"
            f"<tr><td>Заданий решено</td><td><b>{st['n']}</b></td></tr>"
            f"<tr><td>Верно / ошибок</td><td><b>{st['ok']}</b> / <b style='color:#c00'>{st['wrong']}</b> ({acc}%)</td></tr>"
            f"<tr><td>Время решения</td><td><b>{mmss(st['active'])}</b> мин (в среднем {mmss(st['avg'])} на задание)</td></tr>"
            + (f"<tr><td>Откликнулся(лась) через</td><td>{mmss(st['reaction'])} мин</td></tr>" if st["reaction"] is not None else "")
            + "</table>")
        parts.append("<h3>По темам</h3><table cellpadding='5' style='border-collapse:collapse;border:1px solid #ccc'>"
                     "<tr style='background:#f3f3f3'><th align=left>Тема</th><th>Верно</th><th>Среднее время</th></tr>")
        for topic, d in st["by_topic"].items():
            parts.append(f"<tr><td>{e(ALL_TOPICS[topic])}</td><td align=center>{d['ok']}/{d['n']}</td><td align=center>{mmss(d['sec'] / d['n'])}</td></tr>")
        parts.append("</table>")
        if st["errors"]:
            parts.append("<h3>Ошибки</h3><ul>")
            for r in st["errors"]:
                task = Task.load(r["task_json"])
                parts.append(f"<li>{e(r['text'])}<br>ответ: <b>{e(r['given'] or '')}</b>, правильно: <b>{e(task.correct_display())}</b></li>")
            parts.append("</ul>")
    else:
        parts.append("<p>Заданий не решалось.</p>")
    ex = extras_text(t, st)
    if ex:
        parts.append("<pre style='font-family:Arial,sans-serif;font-size:14px;white-space:pre-wrap'>" + e("\n".join(ex)) + "</pre>")
    if s["kind"] == "diagnostic":
        parts.append("<h3>Срез знаний по темам</h3><table cellpadding='5' style='border-collapse:collapse;border:1px solid #ccc'>")
        for d in diagnostic_summary(t):
            if d["n"]:
                color = "#2a8" if d["acc"] >= 0.85 else "#e90" if d["acc"] >= 0.6 else "#c33"
                parts.append(f"<tr><td>{e(d['name'])}</td><td style='color:{color}'><b>{round(d['acc'] * 100)}%</b> ({d['ok']}/{d['n']})</td><td>{label(d['acc'])}</td></tr>")
            else:
                parts.append(f"<tr><td>{e(d['name'])}</td><td colspan=2 style='color:#888'>ещё не проверяли</td></tr>")
        parts.append("</table>")
    return "<html><body style='font-family:Arial,sans-serif'>" + "".join(parts) + "</body></html>"


def build_weekly(t: Trainer, today: str, child_name: str = "") -> tuple[str, str, str]:
    from datetime import date
    d0 = date.fromisoformat(today)
    days = [(d0 - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    sess = t.s.q("SELECT * FROM sessions WHERE day>=? AND day<=? ORDER BY id", (days[0], days[-1]))
    by_day = {x["day"]: x for x in sess}
    lines = [f"Итоги недели · {child_name or 'Ученик'} · {days[0]} — {days[-1]}", ""]
    tot_n = tot_ok = 0
    tot_sec = 0.0
    done_days = 0
    for day in days:
        s = by_day.get(day)
        if not s:
            lines.append(f"  {day}: —")
            continue
        st = session_stats(t, s["id"])
        tot_n += st["n"]
        tot_ok += st["ok"]
        tot_sec += st["active"]
        done_days += s["status"] in ("done", "partial")
        short = SHORT[s["status"]]
        lines.append(f"  {day}: {short}" + (f" · {st['ok']}/{st['n']} верно · {mmss(st['active'])}" if st["n"] else ""))
    lines += ["", f"Занимался(лась) дней: {done_days} из 7", f"Всего решено: {tot_n}, верно {tot_ok}" + (f" ({round(100 * tot_ok / tot_n)}%)" if tot_n else ""),
              f"Общее время: {mmss(tot_sec)} мин", "", "Темы сейчас:"]
    for o in topic_overview(t):
        if o["n"]:
            lines.append(f"  {_bar(o['acc'])} {round(o['acc'] * 100):>3}%  {o['name']} — {label(o['acc'])}, уровень {o['level'] or 1}/3")
        else:
            lines.append(f"  ░░░░░░░░░░  --  {o['name']} — нет данных")
    gaps = gap_lines(t)
    if gaps:
        lines += ["", f"Пробелы за {P.foundation_label}:"] + gaps
    sc = school_lines(t, time.time())
    if sc:
        lines += [""] + sc
    done_topics = [ALL_TOPICS[x] for x in ALL_TOPICS if t.is_mastered(x)]
    if done_topics:
        lines += ["", "Освоено ✅: " + "; ".join(done_topics)]
    if t.money_cfg()["correct"]:
        wk = sum(t.earned_on(d) for d in days)
        lines += ["", f"💰 За неделю заработано: {t.fmt(wk)}. К выплате сейчас: {t.fmt(t.balance())}"]
    lines += ["", advice(t)]
    text = "\n".join(lines)
    body = "<html><body style='font-family:Arial,sans-serif'><pre style='font-size:14px'>" + html.escape(text) + "</pre></body></html>"
    return f"{P.subject_nom.capitalize()}, {child_name or 'ученик'}: итоги недели", text, body
