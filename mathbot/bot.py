"""Телеграм-бот «Математика 6 класс»: ежедневная тренировка + отчёты маме."""
from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import date, timedelta

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import ai, mailer, packs, report
from . import profile as profile_mod
from .config import load_env
from .engine import BOOKS, DIAG_CHUNK, Settings, Store, Trainer
from .lessons import LESSONS
from .profile import P
from .tasks import ALL_TOPICS, TOPICS, Task

log = logging.getLogger("mathbot")

PRAISE = ["Верно! 👍", "Точно! ✅", "Отлично! 🔥", "Правильно! 💪", "Так держать! ⭐", "Супер! 🎯"]
KEYWORDS = {"делим": "divisibility", "нод": "divisibility", "нок": "divisibility", "обыкновен": "fractions_add", "сокращ": "fractions_add",
            "умножение дроб": "fractions_muldiv", "деление дроб": "fractions_muldiv", "отношен": "ratio", "пропорц": "ratio",
            "процент": "percent", "отрицат": "negatives", "модул": "negatives", "рациональн": "rational_add",
            "координат": "coordinates", "уравнен": "equations", "скобк": "equations",
            "окружност": "circle", "круг": "circle", "длина окруж": "circle", "текстов": "word_problems", "движен": "word_problems",
            "работ": "word_problems", "устн": "mental"}


def parent_id(tr: Trainer) -> int | None:
    v = os.environ.get("PARENT_TELEGRAM_ID") or tr.s.get("parent_chat_id")
    return int(v) if v and v.strip().lstrip("-").isdigit() else None


def child_id(tr: Trainer) -> int | None:
    v = tr.s.get("child_chat_id")
    return int(v) if v else None


def is_parent(tr: Trainer, uid: int) -> bool:
    p = parent_id(tr)
    return uid == p if p else (child_id(tr) is not None and uid != child_id(tr))


def T(ctx) -> Trainer:
    return ctx.application.bot_data["tr"]


# ------------------------------------------------------------------ клавиатуры и вопросы
def invite_markup() -> Markup:
    return Markup([[Btn("▶️ Начать", callback_data="go"), Btn("⏱ Только 3 минуты", callback_data="min")],
                   [Btn("🙅 Не сегодня", callback_data="skip")]])


def question_markup(row, task: Task, diagnostic: bool) -> Markup:
    rows = []
    if task.buttons:
        rows.append([Btn(b, callback_data=f"ans:{row['id']}:{b}") for b in task.buttons])
    extra = [Btn("🤷 Не знаю", callback_data=f"idk:{row['id']}")]
    if not diagnostic:
        extra.append(Btn("📖 Правило", callback_data=f"rule:{row['id']}"))
    rows.append(extra)
    return Markup(rows)


async def send_question(ctx, chat: int, sid: int, row) -> None:
    tr = T(ctx)
    task = Task.load(row["task_json"])
    diag = tr.session(sid)["kind"] == "diagnostic"
    head = f"Задание {row['seq']}" + (f" из {DIAG_CHUNK}" if diag else "") + ("  🔁 повторение" if row["tag"] == "review" else "")
    await ctx.bot.send_message(chat, f"{head}\n\n{task.text}", reply_markup=question_markup(row, task, diag))


async def send_next(ctx, chat: int, sid: int) -> None:
    tr = T(ctx)
    now = time.time()
    if tr.should_finish(sid, now):
        await finish_session(ctx, chat, sid)
        return
    row = tr.next_question(sid, now)
    if row is None:
        await finish_session(ctx, chat, sid)
        return
    if tr.session(sid)["kind"] == "daily" and row["tag"] is None and tr.take_lesson(row["topic"]):
        await ctx.bot.send_message(chat, "Новая тема! Сначала короткое напоминание правила.\n\n" + LESSONS[row["topic"]])
        tr.s.run("UPDATE answers SET sent_at=? WHERE id=?", (time.time(), row["id"]))   # время на чтение правила не считаем
    await send_question(ctx, chat, sid, row)


async def finish_session(ctx, chat: int, sid: int) -> None:
    tr = T(ctx)
    tr.finish(sid, "done", time.time())
    st = report.session_stats(tr, sid)
    s = tr.session(sid)
    streak = tr.streak(s["day"])
    cfg = tr.money_cfg()
    if cfg["correct"] and not (s["minutes"] and s["minutes"] <= 3) and st["n"]:
        tr.add_bonus(sid, cfg["session"])          # бонус за полностью пройденное занятие
    msg = f"🎉 Готово! Решено заданий: {st['n']}, верно: {st['ok']}.\nВремя: {report.mmss(st['active'])} мин.\n⭐ +{st['xp']} очков (всего {tr.xp()}, звание «{tr.title(tr.xp())}»)"
    if cfg["correct"]:
        ans, bonus = tr.session_earned(sid)
        msg += f"\n💰 Заработано: {tr.fmt(ans + bonus)}" + (f" (в том числе бонус за полное занятие: {tr.fmt(bonus)})" if bonus else "") + f"\nВ кошельке: {tr.fmt(tr.balance())}. Покажи маме!"
    if streak > 1:
        msg += f"\n🔥 Занимаешься уже {streak} дн. подряд!"
    reward = tr.reward_due(sid)
    if reward:
        msg += f"\n\n🏆 {reward[1]} дней подряд! Твоя награда: {reward[0]}"
    msg += "\n\nДо завтра!" if s["kind"] == "daily" else "\n\nСпасибо! Завтра продолжим."
    await ctx.bot.send_message(chat, msg)
    await send_report(ctx, sid)


async def begin(ctx, chat: int, sid: int, minutes: int | None = None) -> None:
    tr = T(ctx)
    tr.start(sid, time.time(), minutes)
    s = tr.session(sid)
    if s["kind"] == "diagnostic":
        done = sum(1 for c in tr.diag_counts().values() if c >= 3)
        intro = (f"Сегодня небольшая проверка: посмотрим, что ты уже знаешь по темам {P.grade_gen} (тем проверено: {done} из {len(TOPICS)}). "
                 f"Ошибаться можно — это нужно только чтобы понять, с чего начать. Всё устно, калькулятор не нужен. Заданий: {DIAG_CHUNK}.")
    else:
        intro = f"Поехали! Примерно {tr.minutes_for(s)} мин. устного счёта."
        days = tr.days_to_test(time.time())
        if days is not None and days <= 7:
            intro += f"\n📌 Контрольная через {days} дн. — потренируем и школьную тему."
    intro += "\nПиши ответ числом (дроби — 3/4, десятичные — 0,5). Если не знаешь — жми «🤷 Не знаю», я объясню."
    await ctx.bot.send_message(chat, intro)
    await send_next(ctx, chat, sid)


# ------------------------------------------------------------------ школа: учебник, тема, контрольная
async def ask_school(ctx, chat: int, what: str) -> None:
    if what == "book":
        keys = list(BOOKS)
        rows = [[Btn(BOOKS[k], callback_data=f"bk:{k}") for k in keys[i:i + 2]] for i in range(0, len(keys), 2)]
        await ctx.bot.send_message(chat, f"Расскажи про школу 🙂 Какой у тебя учебник по {P.subject_dat}? (посмотри на обложку)", reply_markup=Markup(rows))
    elif what == "topic":
        rows = [[Btn(name, callback_data=f"st:{k}")] for k, name in TOPICS.items()] + [[Btn("Не знаю / другая тема", callback_data="st:none")]]
        await ctx.bot.send_message(chat, f"Какую тему сейчас проходите на уроках {P.subject_gen}?", reply_markup=Markup(rows))
    else:
        rows = [[Btn("На этой неделе", callback_data="tt:w1"), Btn("Через 1–2 недели", callback_data="tt:w2")],
                [Btn("Через месяц или позже", callback_data="tt:m1"), Btn("Пока нет", callback_data="tt:none")]]
        await ctx.bot.send_message(chat, f"Когда ближайшая контрольная по {P.subject_dat}?", reply_markup=Markup(rows))


def parse_date(arg: str, today: date) -> date | None:
    m = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", arg.strip())
    if m:
        d, mo = int(m[1]), int(m[2])
        y = int(m[3]) if m[3] else today.year
        y += 2000 if y < 100 else 0
        try:
            res = date(y, mo, d)
        except ValueError:
            return None
        if not m[3] and res < today:
            res = res.replace(year=y + 1)
        return res
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", arg.strip())
    try:
        return date(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError:
        return None


# ------------------------------------------------------------------ отчёты
async def send_report(ctx, sid: int) -> None:
    """Отчёт уходит маме в Телеграм (если она нажала /start в боте) и на почту (если настроена)."""
    tr = T(ctx)
    name = tr.s.get("child_name", "") or ""
    subject, text, body = report.build_session_report(tr, sid, name)
    delivered = bool(tr.s.get(f"tg_reported_{sid}"))
    p = parent_id(tr)
    if p and not delivered:
        try:
            await ctx.bot.send_message(p, text[:4000])
            tr.s.set(f"tg_reported_{sid}", "1")
            delivered = True
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить отчёт родителю в Телеграм")
    if mailer.configured():
        delivered = await mailer.send_email(subject, text, body) or delivered
    if delivered:
        tr.mark_reported(sid)    # иначе повторим позже: например, когда мама нажмёт /start


async def maybe_weekly(ctx, tr: Trainer, now: float) -> None:
    t = tr.local(now)
    if t.weekday() != 6 or t.hour < 20:
        return
    key = "%d-W%02d" % t.isocalendar()[:2]
    if tr.s.get("last_weekly") == key:
        return
    tr.s.set("last_weekly", key)
    subject, text, body = report.build_weekly(tr, t.date().isoformat(), tr.s.get("child_name", "") or "")
    await mailer.send_email(subject, text, body)
    if parent_id(tr):
        try:
            await ctx.bot.send_message(parent_id(tr), text[:4000])
        except Exception:  # noqa: BLE001
            log.exception("weekly telegram")


# ------------------------------------------------------------------ планировщик
async def tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tr = T(ctx)
    now = time.time()
    chat = child_id(tr)
    try:
        for action, sid in tr.tick(now):
            if action == "invite":
                s = tr.session(sid)
                text = (f"Привет! 👋 Время {P.subject_gen}. Это займёт около 10 минут." if s["kind"] == "daily" else
                        f"Привет! 👋 Сегодня небольшая проверка знаний по {P.subject_dat} за {P.grade_gen}. Это займёт около 10–15 минут.")
                days = tr.days_to_test(now)
                if days is not None and days <= 3:
                    text += f"\n📌 Контрольная через {days} дн. — самое время повторить!"
                await ctx.bot.send_message(chat, text, reply_markup=invite_markup())
            elif action == "remind1":
                await ctx.bot.send_message(chat, f"⏰ Напоминаю про {P.subject_acc} — всего 10 минут (или 3, если совсем некогда). Нажми «Начать»!", reply_markup=invite_markup())
            elif action == "remind2":
                await ctx.bot.send_message(chat, "⏰ Последнее напоминание: сегодняшняя тренировка ещё не сделана. Давай хотя бы на 3 минуты?", reply_markup=invite_markup())
            elif action == "report":
                s = tr.session(sid)
                if s["status"] == "partial":
                    await ctx.bot.send_message(chat, "Занятие завершено — пришлось остановиться. Завтра продолжим! 🙂")
                await send_report(ctx, sid)
        await maybe_weekly(ctx, tr, now)
    except Exception:  # noqa: BLE001
        log.exception("tick failed")


# ------------------------------------------------------------------ команды
HELP_PARENT = (
    "Команды для родителя:\n"
    "/stats — итоги недели, уровень по темам и совет\n"
    "/settime 17:30 — во сколько присылать задания\n"
    "/name Маша — имя ученика в отчётах\n"
    "/book Виленкин — учебник (если ученик не ответил)\n"
    "/school проценты — какую тему проходят в школе\n"
    "/test 20.11 — дата контрольной (/test нет — убрать)\n"
    "/reward 7 Кино в выходные — награда за серию в 7 дней (/reward off — выключить)\n"
    "/money грн 10 — валюта и цена верного ответа (/money — показать все настройки)\n"
    "/wallet — сколько заработал ребёнок, /payout — отметить, что вы выдали деньги\n"
    "/diag — начать срез знаний заново\n"
    "/testmail — проверить, что письма доходят\n"
    "/status — проверка настроек\n"
    "/myid — ваш Telegram ID"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tr = T(ctx)
    uid = update.effective_chat.id
    c = child_id(tr)
    if c is None and not (parent_id(tr) == uid):
        tr.s.set("child_chat_id", str(uid))
        await update.message.reply_text(
            f"Привет! 👋 Я буду присылать тебе устные задачки по {P.subject_dat} каждый день в {tr.start_time()}. "
            "Первый раз — небольшая проверка знаний, потом короткие тренировки по 10 минут. За правильные ответы копятся очки и растёт звание ⭐\n\n"
            "Начнём прямо сейчас? Нажми /go")
        return
    if uid == c:
        await update.message.reply_text("Привет! Я здесь 🙂 Нажми /go, чтобы позаниматься прямо сейчас. /me — твои очки и серия.")
        return
    if not os.environ.get("PARENT_TELEGRAM_ID") and not tr.s.get("parent_chat_id"):
        tr.s.set("parent_chat_id", str(uid))
    await update.message.reply_text("Здравствуйте! Вы подключены как родитель: отчёты будут приходить сюда и на почту.\n\n" + HELP_PARENT)


async def cmd_myid(update: Update, ctx) -> None:
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


async def cmd_go(update: Update, ctx) -> None:
    if update.effective_chat.id != child_id(T(ctx)):
        await update.message.reply_text("Эта команда для ученика. /help — команды родителя.")
        return
    await go(ctx, update.effective_chat.id)


async def go(ctx, chat: int, minutes: int | None = None) -> None:
    tr = T(ctx)
    now = time.time()
    minutes = minutes or ctx.application.bot_data.pop("minutes", None)
    s = tr.today_session(now)
    if s and s["status"] == "active":
        row = tr.pending(s["id"])
        if row:
            await ctx.bot.send_message(chat, "Продолжаем 👇")
            await send_question(ctx, chat, s["id"], row)
        else:
            await send_next(ctx, chat, s["id"])
        return
    if s and s["status"] in ("done", "partial"):
        await ctx.bot.send_message(chat, "Сегодня ты уже позанимался(ась) 👍 Следующая тренировка — завтра.")
        return
    what = tr.school_question(now)
    if what:   # сначала коротко расспросим про школу
        ctx.application.bot_data["chain"] = True
        ctx.application.bot_data["minutes"] = minutes
        await ask_school(ctx, chat, what)
        return
    sid = s["id"] if s and s["status"] == "invited" else tr.create_session(now)
    await begin(ctx, chat, sid, minutes)


async def on_callback(update: Update, ctx) -> None:
    tr = T(ctx)
    q = update.callback_query
    chat = q.message.chat_id
    await q.answer()
    if chat != child_id(tr):
        return
    data = q.data or ""
    now = time.time()
    if data in ("go", "min"):
        await q.edit_message_reply_markup(None)
        await go(ctx, chat, 3 if data == "min" else None)
    elif data == "skip":
        s = tr.today_session(now)
        await q.edit_message_reply_markup(None)
        if s and s["status"] == "invited":
            tr.decline(s["id"], now)
            await ctx.bot.send_message(chat, "Хорошо, сегодня пропускаем. Но завтра — обязательно! Если передумаешь, нажми /go.")
            await send_report(ctx, s["id"])
    elif data[:3] in ("bk:", "st:", "tt:"):
        key = data[3:]
        if data.startswith("bk:"):
            tr.set_book(key)
        elif data.startswith("st:"):
            tr.set_school_topic(key, now)
        else:
            days = {"w1": 3, "w2": 10, "m1": 30}.get(key)
            tr.set_test_date((tr.local(now).date() + timedelta(days=days)).isoformat() if days else None, now)
        await q.edit_message_reply_markup(None)
        await ctx.bot.send_message(chat, "Записал(а) 👍")
        if ctx.application.bot_data.pop("chain", False):
            await go(ctx, chat)
    elif data.startswith(("ans:", "idk:", "rule:", "ai:")):
        kind, aid, *rest = data.split(":", 2)
        if kind == "ai":
            row = tr.s.one("SELECT * FROM answers WHERE id=?", (int(aid),))
            if not row:
                return
            await q.edit_message_reply_markup(None)
            task = Task.load(row["task_json"])
            txt = await ai.explain(tr.s, ALL_TOPICS[row["topic"]], row["text"], row["given"] or "", task.correct_display(), BOOKS.get(tr.s.get("book"), ""))
            await ctx.bot.send_message(chat, ("🤖 " + txt) if txt else "ИИ-учитель сейчас недоступен. Нажми «📖 Правило» в следующем задании.")
            return
        s = tr.active_session()
        row = tr.pending(s["id"]) if s else None
        if not row or str(row["id"]) != aid:
            return
        if kind == "rule":
            await ctx.bot.send_message(chat, LESSONS[row["topic"]])
            return
        await q.edit_message_reply_markup(None)
        await handle_answer(ctx, chat, s["id"], rest[0] if rest else "", now, idk=(kind == "idk"))


async def handle_answer(ctx, chat: int, sid: int, text: str, now: float, idk: bool = False) -> None:
    tr = T(ctx)
    res = tr.give_up(sid, now) if idk else tr.submit(sid, text, now)
    if res.task is None:
        return
    if res.ok is None:
        await ctx.bot.send_message(chat, f"🤔 {res.bad}")
        return
    if res.ok:
        msg = random.choice(PRAISE) + (f"  +{tr.fmt(res.money)}" if res.money else f"  +{res.xp}")
        if res.mastered:
            msg += f"\n🏅 Тема «{ALL_TOPICS[res.mastered]}» освоена!" + (f" Бонус: {tr.fmt(tr.money_cfg()['master'])}" if tr.money_cfg()["correct"] else "")
        await ctx.bot.send_message(chat, msg)
    else:
        msg = ("Ничего страшного, разберём. " if idk else "❌ Не совсем. ") + f"Правильный ответ: {res.task.correct_display()}\n💡 {res.task.explanation}"
        if res.hint:
            msg += f"\n⚠️ {res.hint}"
        markup = Markup([[Btn("🤖 Объясни по-другому", callback_data=f"ai:{res.aid}")]]) if ai.enabled() else None
        await ctx.bot.send_message(chat, msg, reply_markup=markup)
        if res.lesson_topic:
            await ctx.bot.send_message(chat, "Две ошибки подряд по этой теме — давай вспомним правило.\n\n" + LESSONS[res.lesson_topic])
    await send_next(ctx, chat, sid)


async def on_text(update: Update, ctx) -> None:
    tr = T(ctx)
    chat = update.effective_chat.id
    if chat != child_id(tr):
        return
    s = tr.active_session()
    if not s:
        if ai.enabled():
            txt = await ai.answer_question(tr.s, update.message.text or "", BOOKS.get(tr.s.get("book"), ""))
            await update.message.reply_text(("🤖 " + txt) if txt else "Нажми /go, чтобы позаниматься.")
        else:
            await update.message.reply_text("Сейчас заданий нет. Нажми /go, чтобы позаниматься.")
        return
    await handle_answer(ctx, chat, s["id"], update.message.text or "", time.time())


async def cmd_me(update: Update, ctx) -> None:
    tr = T(ctx)
    if update.effective_chat.id != child_id(tr):
        return
    x = tr.xp()
    day = tr.local(time.time()).date().isoformat()
    ov = [o for o in report.topic_overview(tr) if o["n"]]
    lines = [f"⭐ Очки: {x} — звание «{tr.title(x)}»", f"🔥 Серия: {tr.streak(day)} дн."]
    lines += [f"{report._bar(o['acc'])} {round(o['acc'] * 100)}%  {o['name']}" for o in ov]
    await update.message.reply_text("\n".join(lines))


async def cmd_ask(update: Update, ctx) -> None:
    tr = T(ctx)
    if update.effective_chat.id != child_id(tr):
        return
    q = " ".join(ctx.args).strip()
    if not ai.enabled():
        await update.message.reply_text("ИИ-учитель не подключён. Попроси родителей или нажми «📖 Правило» в задании.")
    elif not q:
        await update.message.reply_text("Напиши вопрос после команды, например: /ask как сократить дробь 12/18?")
    else:
        txt = await ai.answer_question(tr.s, q, BOOKS.get(tr.s.get("book"), ""))
        await update.message.reply_text(("🤖 " + txt) if txt else "Сейчас не получилось, попробуй позже.")


def parent_only(fn):
    async def wrapper(update: Update, ctx):
        if not is_parent(T(ctx), update.effective_chat.id):
            await update.message.reply_text("Эта команда только для родителя.")
            return
        await fn(update, ctx)
    return wrapper


@parent_only
async def cmd_stats(update: Update, ctx) -> None:
    tr = T(ctx)
    _, text, _ = report.build_weekly(tr, tr.local(time.time()).date().isoformat(), tr.s.get("child_name", "") or "")
    await update.message.reply_text(text)


@parent_only
async def cmd_settime(update: Update, ctx) -> None:
    tr = T(ctx)
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", (ctx.args[0] if ctx.args else ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await update.message.reply_text("Формат: /settime 17:30")
        return
    t = f"{int(m.group(1)):02d}:{m.group(2)}"
    tr.s.set("start_time", t)
    await update.message.reply_text(f"Готово: задания будут приходить каждый день в {t} ({tr.cfg.tz}).")


@parent_only
async def cmd_name(update: Update, ctx) -> None:
    name = " ".join(ctx.args).strip()
    if not name:
        await update.message.reply_text("Формат: /name Маша")
        return
    T(ctx).s.set("child_name", name[:40])
    await update.message.reply_text(f"Теперь в отчётах: {name}")


@parent_only
async def cmd_diag(update: Update, ctx) -> None:
    T(ctx).reset_diagnostic()
    await update.message.reply_text("Срез знаний запущен заново: со следующего занятия снова проверяем все темы.")


@parent_only
async def cmd_status(update: Update, ctx) -> None:
    tr = T(ctx)
    done = sum(1 for c in tr.diag_counts().values() if c >= 3)
    await update.message.reply_text(
        f"Ученик подключён: {'да' if child_id(tr) else 'нет'}\n"
        f"Время занятий: {tr.start_time()} ({tr.cfg.tz})\n"
        f"Отчёты: в этот чат ✅{', и на почту ✅' if mailer.configured() else ' (почта не нужна, подключается по желанию)'}\n"
        f"ИИ-учитель на Claude: {'подключён' if ai.enabled() else 'не подключён (необязательно)'}\n"
        f"Срез знаний: проверено тем {done} из {len(TOPICS)}")


async def cmd_wallet(update: Update, ctx) -> None:
    tr = T(ctx)
    day = tr.local(time.time()).date().isoformat()
    if not tr.money_cfg()["correct"]:
        await update.message.reply_text("Кошелёк выключен. Родитель может включить его командой /money.")
        return
    await update.message.reply_text(
        f"💰 В кошельке (к выплате): {tr.fmt(tr.balance())}\nСегодня заработано: {tr.fmt(tr.earned_on(day))}\n"
        f"Всего заработано: {tr.fmt(tr.total_earned())}")


@parent_only
async def cmd_money(update: Update, ctx) -> None:
    tr = T(ctx)
    args = list(ctx.args)
    cur, price, extra, i = None, None, {}, 0
    while i < len(args):
        a = args[i]
        key = {"бонус": "bonus_session", "быстро": "price_fast", "лимит": "daily_cap", "освоение": "bonus_master"}.get(a.lower())
        if key and i + 1 < len(args) and args[i + 1].isdigit():
            extra[key] = args[i + 1]
            i += 2
            continue
        if a.isdigit() and price is None:
            price = int(a)
        elif cur is None and not a.isdigit():
            cur = a
        i += 1
    if cur or price is not None or extra:
        tr.set_money(cur, price)
        for k, v in extra.items():
            tr.s.set(k, v)
    c = tr.money_cfg()
    best = 25 * (c["correct"] + c["fast"]) + c["session"]
    await update.message.reply_text(
        f"💰 Валюта: {tr.currency()}\n"
        f"За верный ответ: {tr.fmt(c['correct'])}\nЗа быстрый (до {c['fast_secs']} с): ещё +{tr.fmt(c['fast'])}\n"
        f"За полностью пройденное занятие: +{tr.fmt(c['session'])}\nЗа освоенную тему: +{tr.fmt(c['master'])}\n"
        f"Лимит в день: {tr.fmt(c['cap']) if c['cap'] else 'нет'}\n\n"
        f"При идеальной работе (около 25 заданий, все верные и быстрые): примерно {tr.fmt(best)} за занятие.\n\n"
        "Изменить: /money грн 10  (валюта и цена верного ответа)\n"
        "Тонкая настройка: /money бонус 60 быстро 4 лимит 300 освоение 100\n"
        "Выключить деньги: /money 0\n"
        "Деньги виртуальные: вы сами выдаёте их ребёнку и отмечаете командой /payout.")


@parent_only
async def cmd_payout(update: Update, ctx) -> None:
    tr = T(ctx)
    amount = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else None
    paid = tr.pay(amount)
    await update.message.reply_text(f"✅ Отмечено как выплачено: {tr.fmt(paid)}. Осталось к выплате: {tr.fmt(tr.balance())}.")


@parent_only
async def cmd_testmail(update: Update, ctx) -> None:
    ok = await mailer.send_email(f"{P.subject_nom.capitalize()}: проверка почты", "Если вы видите это письмо, отчёты будут приходить сюда.",
                                 "<p>Если вы видите это письмо, отчёты будут приходить сюда.</p>")
    if ok:
        await update.message.reply_text(f"✅ Тестовое письмо отправлено на {os.environ.get('MAIL_TO')}. Проверьте ящик (и папку «Спам»).")
    else:
        await update.message.reply_text(f"❌ Письмо не отправилось.\nПричина: {mailer.last_error}\n"
                                        "Частые причины: обычный пароль вместо «пароля приложения», неверный SMTP_HOST/SMTP_PORT, опечатка в адресе.")


async def cmd_book(update: Update, ctx) -> None:
    tr = T(ctx)
    arg = " ".join(ctx.args).strip().lower()
    if not arg:
        if update.effective_chat.id == child_id(tr):
            await ask_school(ctx, update.effective_chat.id, "book")
        else:
            await update.message.reply_text("Формат: /book Виленкин")
        return
    key = next((k for k, v in BOOKS.items() if arg in v.lower() or arg == k), "unknown")
    tr.set_book(key)
    await update.message.reply_text(f"Учебник: {BOOKS[key]}")


async def cmd_school(update: Update, ctx) -> None:
    tr = T(ctx)
    arg = " ".join(ctx.args).strip().lower()
    if not arg:
        if update.effective_chat.id == child_id(tr):
            await ask_school(ctx, update.effective_chat.id, "topic")
        else:
            await update.message.reply_text("Формат: /school проценты  (темы: " + "; ".join(TOPICS.values()) + ")")
        return
    key = next((v for k, v in KEYWORDS.items() if k in arg and v in TOPICS), None) or next((k for k, n in TOPICS.items() if arg in n.lower()), None)
    if not key:
        await update.message.reply_text("Не поняла тему. Темы: " + "; ".join(TOPICS.values()))
        return
    tr.set_school_topic(key, time.time())
    await update.message.reply_text(f"В школе сейчас: {TOPICS[key]}. Бот будет чаще давать эту тему.")


async def cmd_test(update: Update, ctx) -> None:
    tr = T(ctx)
    arg = " ".join(ctx.args).strip().lower()
    now = time.time()
    if not arg:
        if update.effective_chat.id == child_id(tr):
            await ask_school(ctx, update.effective_chat.id, "test")
        else:
            await update.message.reply_text("Формат: /test 20.11  (или /test нет)")
        return
    if arg in ("нет", "off", "-"):
        tr.set_test_date(None, now)
        await update.message.reply_text("Контрольная убрана.")
        return
    d = parse_date(arg, tr.local(now).date())
    if not d:
        await update.message.reply_text("Не поняла дату. Формат: /test 20.11")
        return
    tr.set_test_date(d.isoformat(), now)
    await update.message.reply_text(f"Контрольная: {d.strftime('%d.%m.%Y')}. За неделю бот усилит эту тему.")


@parent_only
async def cmd_reward(update: Update, ctx) -> None:
    tr = T(ctx)
    if ctx.args and ctx.args[0].lower() in ("off", "нет"):
        tr.s.set("reward_text", None)
        await update.message.reply_text("Награда выключена.")
        return
    if len(ctx.args) < 2 or not ctx.args[0].isdigit():
        await update.message.reply_text("Формат: /reward 7 Кино в выходные  (каждые 7 дней подряд)")
        return
    tr.s.set("reward_days", ctx.args[0])
    tr.s.set("reward_text", " ".join(ctx.args[1:])[:100])
    await update.message.reply_text(f"Договорились: за каждые {ctx.args[0]} дн. подряд — «{' '.join(ctx.args[1:])}». Ребёнок увидит это в конце занятия, а вам придёт напоминание.")


async def cmd_help(update: Update, ctx) -> None:
    tr = T(ctx)
    if is_parent(tr, update.effective_chat.id):
        await update.message.reply_text(HELP_PARENT)
    else:
        await update.message.reply_text("/go — позаниматься сейчас\n/me — очки и серия\n/wallet — кошелёк\n/ask вопрос — спросить ИИ-учителя (если подключён)\n"
                                        "/school — какая тема в школе\n/test — когда контрольная\n/book — какой учебник")


def build_app() -> Application:
    load_env()
    profile_mod.load()
    if os.environ.get("PACK") or P.pack:
        packs.load_file(os.environ.get("PACK") or P.pack)
    if P.books:
        BOOKS.clear(); BOOKS.update(P.books); BOOKS.setdefault("unknown", "не знаю / другой")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN (см. README, шаг 1)")
    cfg = Settings(tz=os.environ.get("TZ_NAME") or P.tz or "Europe/Moscow",
                   session_minutes=int(os.environ.get("SESSION_MINUTES") or P.session_minutes or 10))
    db_path = os.environ.get("DB_PATH", "data/mathbot.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    store = Store(db_path)
    tr = Trainer(store, cfg)
    if (os.environ.get("START_TIME") or P.start_time) and not store.get("start_time"):
        store.set("start_time", os.environ.get("START_TIME") or P.start_time)
    if P.child_name and not store.get("child_name"):
        store.set("child_name", P.child_name)
    app = Application.builder().token(token).build()
    app.bot_data["tr"] = tr
    for name, fn in [("start", cmd_start), ("go", cmd_go), ("myid", cmd_myid), ("help", cmd_help), ("stats", cmd_stats),
                     ("settime", cmd_settime), ("name", cmd_name), ("diag", cmd_diag), ("status", cmd_status), ("me", cmd_me),
                     ("ask", cmd_ask), ("wallet", cmd_wallet), ("money", cmd_money), ("payout", cmd_payout), ("testmail", cmd_testmail), ("book", cmd_book), ("school", cmd_school), ("test", cmd_test), ("reward", cmd_reward)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(tick, interval=30, first=5)
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    for noisy in ("httpx", "httpcore"):          # их журнал содержит адреса запросов с токеном бота
        logging.getLogger(noisy).setLevel(logging.WARNING)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
