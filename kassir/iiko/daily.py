"""Итоги дня из выгрузки iiko: расчёт по кассирам, хранение в SQLite, тексты для Telegram."""
import datetime as dt
import html
import json
import sqlite3
from collections import defaultdict

from shift_facts import build_facts

FIELDS = {"cashier": "cashier", "check": ["session", "check"], "group": "group",
          "dish": "dish", "revenue": "revenue", "amount": "amount"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def rub(x):
    return f"{round(x or 0):,}".replace(",", " ") + " ₽"


def pct(x):
    return f"{x:g}".replace(".", ",") + "%"


def day_ru(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]}"


# ---------- хранилище ----------
class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path)
        self.c.executescript("""
            CREATE TABLE IF NOT EXISTS facts(
                day TEXT, point TEXT, cashier TEXT, facts TEXT, loaded_at TEXT,
                PRIMARY KEY(day, point, cashier));
            CREATE TABLE IF NOT EXISTS pax(week TEXT PRIMARY KEY, pax INTEGER);
            CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, point TEXT);
        """)

    def save_day(self, day, point, facts_by_cashier):
        """Перезаливка дня целиком: повторная выгрузка заменяет старые цифры."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        self.c.execute("DELETE FROM facts WHERE day=? AND point=?", (day, point))
        self.c.executemany("INSERT INTO facts VALUES(?,?,?,?,?)",
                           [(day, point, k, json.dumps(v, ensure_ascii=False), now) for k, v in facts_by_cashier.items()])
        self.c.commit()

    def facts(self, point, day_from, day_to):
        cur = self.c.execute("SELECT day, cashier, facts FROM facts WHERE point=? AND day>=? AND day<=? ORDER BY day",
                             (point, day_from, day_to))
        return [(d, c, json.loads(f)) for d, c, f in cur]

    def set_pax(self, week, pax):
        self.c.execute("INSERT OR REPLACE INTO pax VALUES(?,?)", (week, pax))
        self.c.commit()

    def pax(self, week):
        r = self.c.execute("SELECT pax FROM pax WHERE week=?", (week,)).fetchone()
        return r[0] if r else None

    def set_chat_point(self, chat_id, point):
        self.c.execute("INSERT OR REPLACE INTO chats VALUES(?,?)", (chat_id, point))
        self.c.commit()

    def chat_point(self, chat_id):
        r = self.c.execute("SELECT point FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        return r[0] if r else None


# ---------- расчёт ----------
def detect_point(cfg, department, fallback):
    d = (department or "").lower()
    for key, p in cfg["points"].items():
        for m in [p.get("department", "")] + p.get("match", []):
            if m and (m.lower() in d or (d and d in m.lower())):
                return key
    return fallback


def split_rows(cfg, rows, fallback_point):
    """{(day, point): [rows]}; строки неизвестной точки -> ключ point=None."""
    out = defaultdict(list)
    for r in rows:
        out[(r["day"], detect_point(cfg, r["department"], fallback_point))].append(r)
    return out


def facts_for(cfg, point, rows):
    return build_facts(rows, FIELDS, cfg["points"][point]["rules"])


def evaluate(cfg, point, f):
    """Премия и выполнение плана для фактов одного кассира за день."""
    P = cfg["points"][point]
    plans, bonus = P["plans"], P["bonus"]
    rev, checks = f.get("rev", 0), f.get("checks", 0)
    plan_met = rev >= plans["rev"]
    rate = bonus["plan"] if plan_met else bonus["base"]
    metrics = []
    if "avg" in plans:
        avg = rev / checks if checks else 0
        metrics.append(("🧾", "ср. чек", rub(avg), avg >= plans["avg"]))
    for mid, rule in P["rules"].items():
        plan = rule.get("plan")
        if plan is None:
            continue
        if rule["type"] == "pct":
            v = f.get(mid, 0) / checks * 100 if checks else 0
            metrics.append((rule.get("icon", "•"), rule.get("label", mid), f"{round(v)}%", v >= plan))
        else:
            v = f.get(mid, 0)
            metrics.append((rule.get("icon", "•"), rule.get("label", mid), f"{v} шт", v >= plan))
    return {
        "rev": rev, "checks": checks, "avg": rev / checks if checks else 0,
        "plan_met": plan_met, "rate": rate, "bonus": round(rev * rate / 100),
        "lost": 0 if plan_met else round(plans["rev"] * bonus["plan"] / 100 - rev * bonus["base"] / 100),
        "short": max(0, plans["rev"] - rev), "metrics": metrics,
        "done": sum(1 for m in metrics if m[3]) + plan_met, "total": len(metrics) + 1,
    }


# ---------- тексты ----------
def day_summary(cfg, point, day, facts_by_cashier):
    P = cfg["points"][point]
    ev = sorted(((c, evaluate(cfg, point, f)) for c, f in facts_by_cashier.items()), key=lambda x: -x[1]["rev"])
    rev = sum(e["rev"] for _, e in ev)
    checks = sum(e["checks"] for _, e in ev)
    lines = [f"📊 <b>{P['name']} · {day_ru(day)}</b>",
             f"Выручка {rub(rev)} · чеков {checks} · ср. чек {rub(rev / checks if checks else 0)}",
             f"План на кассира {rub(P['plans']['rev'])}", ""]
    for i, (c, e) in enumerate(ev, 1):
        head = f"{i}. <b>{html.escape(c)}</b> — {rub(e['rev'])} " + ("✅" if e["plan_met"] else "❌")
        lines.append(head)
        lines.append(f"   премия {pct(e['rate'])} = <b>{rub(e['bonus'])}</b> · чеков {e['checks']} · ср. чек {rub(e['avg'])}")
        if not e["plan_met"]:
            lines.append(f"   до плана −{rub(e['short'])}, недополучено {rub(e['lost'])}")
        lines.append("   " + "  ".join(f"{ic}{v}{'✓' if ok else '✗'}" for ic, _, v, ok in e["metrics"]))
    lines += ["", "✓/✗ — выполнен ли план: " + ", ".join(f"{ic} {lab}" for ic, lab, _, _ in ev[0][1]["metrics"]) if ev else ""]
    return "\n".join(lines)


def period_summary(cfg, db, point, day_from, day_to, title):
    P = cfg["points"][point]
    rows = db.facts(point, day_from, day_to)
    if not rows:
        return f"{P['name']}: нет данных за {title}."
    agg = defaultdict(lambda: {"rev": 0, "checks": 0, "bonus": 0, "days": 0, "plan": 0})
    for _, c, f in rows:
        e = evaluate(cfg, point, f)
        a = agg[c]
        a["rev"] += e["rev"]; a["checks"] += e["checks"]; a["bonus"] += e["bonus"]
        a["days"] += 1; a["plan"] += e["plan_met"]
    total = sum(a["rev"] for a in agg.values())
    lines = [f"🏆 <b>{P['name']} · {title}</b>", f"Выручка {rub(total)} · премии {rub(sum(a['bonus'] for a in agg.values()))}", ""]
    for i, (c, a) in enumerate(sorted(agg.items(), key=lambda x: -x[1]["rev"]), 1):
        lines.append(f"{i}. <b>{html.escape(c)}</b> — {rub(a['rev'])}, смен {a['days']}, план {a['plan']}/{a['days']}, "
                     f"ср. чек {rub(a['rev'] / a['checks'] if a['checks'] else 0)}, премия <b>{rub(a['bonus'])}</b>")
    return "\n".join(lines)


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def spp_summary(cfg, db, week_monday):
    pax = db.pax(week_monday.isoformat())
    end = week_monday + dt.timedelta(days=6)
    head = f"✈️ <b>СПП · {day_ru(week_monday.isoformat())} – {day_ru(end.isoformat())}</b>"
    if not pax:
        return head + "\nПассажиропоток не внесён. Команда: /pax 74350"
    lines = [head, f"Пассажиропоток МВЛ: {pax:,}".replace(",", " ")]
    tot = 0
    for key, P in cfg["points"].items():
        rows = db.facts(key, week_monday.isoformat(), end.isoformat())
        rev = sum(f.get("rev", 0) for _, _, f in rows)
        checks = sum(f.get("checks", 0) for _, _, f in rows)
        tot += rev
        spp = rev / pax
        plan = P.get("plans", {}).get("spp")
        mark = "" if plan is None else (" ✅" if spp >= plan else f" ❌ (план {plan:g})")
        lines.append(f"{P['name']}: выручка {rub(rev)}, СПП <b>{spp:.2f} ₽</b>{mark}, конверсия {checks / pax * 100:.1f}%".replace(".", ","))
    lines.append(f"Всего: {rub(tot)}, СПП <b>{tot / pax:.2f} ₽</b>".replace(".", ","))
    days = {d for k in cfg["points"] for d, _, _ in db.facts(k, week_monday.isoformat(), end.isoformat())}
    if len(days) < 7:
        lines.append(f"⚠️ Загружено дней: {len(days)} из 7 — СПП занижен.")
    return "\n".join(lines)
