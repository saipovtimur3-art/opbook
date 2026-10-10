"""Telegram-бот: принимает выгрузку OLAP-отчёта iiko и присылает итоги по кассирам.

Запуск:  python bot.py        (config.json рядом)

Команды:
  /start                 — помощь и ID чата
  /tochka bh | mo        — точка по умолчанию для чата (если в отчёте нет колонки «Торговое предприятие»)
  /day [ГГГГ-ММ-ДД]      — итоги дня ещё раз (по умолчанию последний загруженный день — вчера/сегодня)
  /week                  — рейтинг кассиров за 7 дней
  /month [ГГГГ-ММ]       — премии кассиров за месяц (для расчёта ЗП)
  /pax 74350 [ГГГГ-ММ-ДД]— пассажиропоток МВЛ за неделю (по умолчанию прошлая неделя), сразу считает СПП
  /spp                   — СПП за прошлую неделю
Файл .xlsx/.csv        — загрузить отчёт за день (повторная загрузка того же дня заменяет цифры)
"""
import asyncio
import datetime as dt
import io
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import daily
from iiko_export import ExportError, parse_export

BASE = Path(__file__).parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
DB = daily.DB(str(BASE / CFG.get("db", "kassir.db")))
ALLOWED = set(CFG["telegram"].get("allowed_chats") or [])
dp = Dispatcher()


def allowed(m: Message):
    return m.chat.id in ALLOWED


def chat_points(m: Message):
    p = DB.chat_point(m.chat.id)
    return [p] if p else list(CFG["points"])


@dp.message(Command("start", "help"))
async def start(m: Message):
    text = __doc__.split("Команды:")[1]
    note = "" if allowed(m) else (f"\n\n⚠️ Этот чат не в списке разрешённых. Добавь <code>{m.chat.id}</code> "
                                  f"в config.json → telegram.allowed_chats и перезапусти бота.")
    await m.answer("Привет! Кидай сюда выгрузку OLAP-отчёта «Смены кассиров» из iikoOffice после закрытия дня.\n"
                   f"<pre>{text.strip()}</pre>{note}")


@dp.message(Command("tochka"))
async def tochka(m: Message, command: CommandObject):
    if not allowed(m):
        return
    p = (command.args or "").strip().lower()
    if p not in CFG["points"]:
        return await m.answer("Укажи точку: " + ", ".join(f"/tochka {k} — {v['name']}" for k, v in CFG["points"].items()))
    DB.set_chat_point(m.chat.id, p)
    await m.answer(f"Ок, отчёты из этого чата без колонки точки считаю как {CFG['points'][p]['name']}.")


@dp.message(F.document)
async def upload(m: Message, bot: Bot):
    if not allowed(m):
        return
    doc = m.document
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        return await m.answer("Файл больше 20 МБ, Telegram не даст его скачать. Сузь период отчёта до одного дня.")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        rows = parse_export(buf.getvalue(), doc.file_name or "")
    except ExportError as e:
        return await m.answer(f"❌ {e}")
    except Exception as e:   # битый файл и т.п.
        logging.exception("parse failed")
        return await m.answer(f"❌ Не смог прочитать файл: {e}")

    fallback = DB.chat_point(m.chat.id)
    caption = (m.caption or "").lower()
    for key, p in CFG["points"].items():
        if key in caption.split() or any(x in caption for x in p.get("match", [])):
            fallback = key
    groups = daily.split_rows(CFG, rows, fallback)
    unknown = sorted({r["department"] or "(пусто)" for (d, p), rs in groups.items() if p is None for r in rs})
    if unknown:
        await m.answer("⚠️ Не понял, к какой точке относятся строки: " + ", ".join(unknown) +
                       ".\nДобавь название в config.json → points.*.match, или задай точку чата: /tochka bh")
    for (day, point), rs in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1] or "")):
        if point is None:
            continue
        facts = daily.facts_for(CFG, point, rs)
        DB.save_day(day, point, facts)
        await m.answer(daily.day_summary(CFG, point, day, facts))


@dp.message(Command("day"))
async def day(m: Message, command: CommandObject):
    if not allowed(m):
        return
    d = (command.args or "").strip()
    for p in chat_points(m):
        if not d:
            r = DB.c.execute("SELECT max(day) FROM facts WHERE point=?", (p,)).fetchone()
            dd = r[0]
        else:
            dd = d
        rows = DB.facts(p, dd, dd) if dd else []
        if rows:
            await m.answer(daily.day_summary(CFG, p, dd, {c: f for _, c, f in rows}))
        else:
            await m.answer(f"{CFG['points'][p]['name']}: нет данных{' за ' + dd if dd else ''}.")


@dp.message(Command("week"))
async def week(m: Message):
    if not allowed(m):
        return
    to = dt.date.today()
    frm = to - dt.timedelta(days=6)
    for p in chat_points(m):
        await m.answer(daily.period_summary(CFG, DB, p, frm.isoformat(), to.isoformat(), "7 дней"))


@dp.message(Command("month"))
async def month(m: Message, command: CommandObject):
    if not allowed(m):
        return
    try:
        first = dt.date.fromisoformat((command.args or "").strip() + "-01") if command.args else dt.date.today().replace(day=1)
    except ValueError:
        return await m.answer("Формат: /month 2026-10")
    last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    title = first.strftime("%m.%Y")
    for p in chat_points(m):
        await m.answer(daily.period_summary(CFG, DB, p, first.isoformat(), last.isoformat(), f"месяц {title}"))


@dp.message(Command("pax"))
async def pax(m: Message, command: CommandObject):
    if not allowed(m):
        return
    args = (command.args or "").replace(" ", " ").split()
    nums = "".join(a for a in args if a.isdigit())
    try:
        n = int(nums) if nums else 0
        ref = dt.date.fromisoformat(next(a for a in args if "-" in a)) if any("-" in a for a in args) else dt.date.today() - dt.timedelta(days=7)
    except ValueError:
        n = 0
    if not n:
        return await m.answer("Формат: /pax 74350  — за прошлую неделю\nили /pax 74350 2026-09-28 — за неделю с этой даты")
    monday = daily.week_start(ref)
    DB.set_pax(monday.isoformat(), n)
    await m.answer(daily.spp_summary(CFG, DB, monday))


@dp.message(Command("spp"))
async def spp(m: Message):
    if not allowed(m):
        return
    await m.answer(daily.spp_summary(CFG, DB, daily.week_start(dt.date.today() - dt.timedelta(days=7))))


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(CFG["telegram"]["token"], default=DefaultBotProperties(parse_mode="HTML"))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
