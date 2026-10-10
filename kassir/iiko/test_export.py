"""python -m unittest test_export"""
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

import daily
from iiko_export import ExportError, parse_export

CFG = json.loads(Path(__file__).with_name("config.example.json").read_text(encoding="utf-8"))
HEAD = ["Торговое предприятие", "Учетный день", "Кассир", "Номер кассовой смены", "Номер чека",
        "Группа блюда", "Блюдо", "Количество блюд", "Сумма со скидкой, р."]


def iiko_xlsx(rows):
    """Excel как из iikoOffice: шапка, пустые повторы группировок, строки «Итого»."""
    wb = Workbook()
    ws = wb.active
    ws.append(["OLAP-отчёт по продажам"])
    ws.append(["Период: 09.10.2026 - 09.10.2026"])
    ws.append([])
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


ROWS = [
    ["Burger Heroes Внуково", dt.datetime(2026, 10, 9), "Иванова Алина", 12, 101, "Бургеры", "Чизбургер", 1, 450],
    [None, None, None, None, None, "Напитки", "Кола 0,5", 1, 180],
    [None, None, None, None, None, "Соусы", "Сырный соус", 1, 60],
    [None, None, None, None, 102, "Комбо", "Комбо Классик", 1, 690],
    [None, None, None, None, None, "Бургеры", "Бургер месяца", 2, 1100],
    [None, None, "Итого Иванова Алина", None, None, None, None, 6, 2480],
    [None, None, "Петров Даниил", 12, 103, "Напитки", "Кола L", 1, 220],
    ["Мо Бар Внуково", "09.10.2026", "Орлова Мария", 7, 5, "Кофе", "Капучино L", 1, "310,00"],
    [None, None, None, None, None, "Десерты", "Чизкейк", 1, "290,00"],
    ["Итого", None, None, None, None, None, None, 9, 3300],
]


class ExportTest(unittest.TestCase):
    def test_parse_and_facts(self):
        rows = parse_export(iiko_xlsx(ROWS), "olap.xlsx")
        self.assertEqual(len(rows), 8)
        groups = daily.split_rows(CFG, rows, None)
        self.assertEqual(set(groups), {("2026-10-09", "bh"), ("2026-10-09", "mo")})
        bh = daily.facts_for(CFG, "bh", groups[("2026-10-09", "bh")])
        a = bh["Иванова Алина"]
        self.assertEqual((a["rev"], a["checks"], a["drink"], a["sauce"], a["combo"], a["month"]), (2480, 2, 1, 1, 1, 2))
        self.assertEqual((bh["Петров Даниил"]["checks"], bh["Петров Даниил"]["size"]), (1, 1))
        mo = daily.facts_for(CFG, "mo", groups[("2026-10-09", "mo")])["Орлова Мария"]
        self.assertEqual((mo["rev"], mo["checks"], mo["size"], mo["dessert"]), (600, 1, 1, 1))

    def test_bonus(self):
        e = daily.evaluate(CFG, "bh", {"rev": 64200, "checks": 82, "drink": 56})
        self.assertTrue(e["plan_met"])
        self.assertEqual(e["bonus"], 1284)                       # 2%
        e = daily.evaluate(CFG, "bh", {"rev": 57300, "checks": 74})
        self.assertEqual((e["bonus"], e["short"], e["lost"]), (716, 2700, 484))   # 1,25%

    def test_csv_cp1251(self):
        text = ";".join(HEAD) + "\nBurger Heroes Внуково;09.10.2026;Иванова Алина;1;1;Напитки;Чай;1;150\n"
        rows = parse_export(text.encode("cp1251"), "report.csv")
        self.assertEqual((rows[0]["cashier"], rows[0]["revenue"]), ("Иванова Алина", 150.0))

    def test_missing_columns(self):
        with self.assertRaises(ExportError):
            parse_export(b"", "x.xls")
        bad = io.BytesIO(); wb = Workbook(); wb.active.append(["Блюдо", "Сумма"]); wb.save(bad)
        with self.assertRaises(ExportError) as c:
            parse_export(bad.getvalue(), "x.xlsx")
        self.assertIn("Кассир", str(c.exception))

    def test_db_and_texts(self):
        with tempfile.TemporaryDirectory() as d:
            db = daily.DB(str(Path(d) / "t.db"))
            rows = parse_export(iiko_xlsx(ROWS), "olap.xlsx")
            for (day, p), rs in daily.split_rows(CFG, rows, None).items():
                db.save_day(day, p, daily.facts_for(CFG, p, rs))
            db.set_pax("2026-10-05", 74350)
            txt = daily.day_summary(CFG, "bh", "2026-10-09", {c: f for _, c, f in db.facts("bh", "2026-10-09", "2026-10-09")})
            self.assertIn("Иванова Алина", txt)
            self.assertIn("СПП", daily.spp_summary(CFG, db, dt.date(2026, 10, 5)))
            self.assertIn("премия", daily.period_summary(CFG, db, "mo", "2026-10-01", "2026-10-31", "месяц"))
            print("\n" + txt + "\n\n" + daily.spp_summary(CFG, db, dt.date(2026, 10, 5)))


if __name__ == "__main__":
    unittest.main()
