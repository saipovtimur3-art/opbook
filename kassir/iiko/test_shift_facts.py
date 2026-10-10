"""python -m unittest test_shift_facts"""
import json
import unittest
from pathlib import Path

from shift_facts import build_facts

CFG = json.loads(Path(__file__).with_name("config.example.json").read_text(encoding="utf-8"))
F, RULES = CFG["fields"], CFG["points"]["bh"]["rules"]


def line(cashier, order, group, dish, rev, amount=1, session=1):
    return {"Cashier": cashier, "SessionNum": session, "OrderNum": order,
            "DishGroup": group, "DishName": dish, "DishDiscountSumInt": rev, "DishAmountInt": amount}


class BuildFactsTest(unittest.TestCase):
    def test_per_cashier_facts(self):
        rows = [
            # чек 1: бургер + напиток + соус
            line("Алина", 1, "Бургеры", "Чизбургер", 450),
            line("Алина", 1, "Напитки", "Кола 0,5", 180),
            line("Алина", 1, "Соусы", "Сырный соус", 60),
            # чек 2: комбо + 2 бургера месяца
            line("Алина", 2, "Комбо", "Комбо Классик", 690),
            line("Алина", 2, "Бургеры", "Бургер месяца", 1100, amount=2),
            # чек 3 у другого кассира
            line("Даниил", 3, "Напитки", "Кола L", 220),
            # возврат: нулевой/отрицательный чек не считается
            line("Даниил", 4, "Бургеры", "Чизбургер", -450),
        ]
        out = build_facts(rows, F, RULES)
        a, d = out["Алина"], out["Даниил"]
        self.assertEqual((a["rev"], a["checks"]), (2480, 2))
        self.assertEqual((a["drink"], a["sauce"], a["combo"], a["month"], a["dessert"]), (1, 1, 1, 2, 0))
        self.assertEqual((d["rev"], d["checks"], d["drink"], d["size"]), (220, 1, 1, 1))

    def test_same_order_num_in_different_sessions(self):
        rows = [line("Алина", 1, "Напитки", "Чай", 100, session=1),
                line("Алина", 1, "Напитки", "Чай", 100, session=2)]
        self.assertEqual(build_facts(rows, F, RULES)["Алина"]["checks"], 2)


if __name__ == "__main__":
    unittest.main()
