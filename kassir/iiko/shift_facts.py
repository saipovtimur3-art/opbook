"""Превращает строки OLAP-отчёта iiko в факты смены по каждому кассиру.

На выходе для каждого кассира — словарь с теми же ключами, что и показатели
в приложении kassir/index.html: rev, checks, drink, sauce, combo, month, ...
  * pct-показатели  -> кол-во чеков, где есть хотя бы одна подходящая позиция
  * count-показатели -> кол-во проданных подходящих позиций (шт)
"""
from collections import defaultdict


def line_matches(rule, group, dish):
    group = (group or "").lower()
    dish = (dish or "").lower()
    if any(g.lower() == group for g in rule.get("groups", [])):
        return True
    if any(d.lower() == dish for d in rule.get("dishes", [])):
        return True
    if any(s.lower() in dish for s in rule.get("dish_contains", [])):
        return True
    return False


def build_facts(rows, fields, rules):
    """rows   — data из iiko OLAP (список словарей).
    fields — имена полей OLAP (см. config.example.json -> fields).
    rules  — правила показателей точки: {metric_id: {type, groups, dishes, dish_contains}}.
    """
    f = fields
    checks = defaultdict(lambda: {"rev": 0.0, "hits": set()})   # (кассир, чек) -> данные
    counts = defaultdict(lambda: defaultdict(float))              # кассир -> metric -> шт

    for r in rows:
        cashier = r.get(f["cashier"]) or "Без кассира"
        check_id = tuple(str(r.get(k)) for k in f["check"])
        group, dish = r.get(f["group"]), r.get(f["dish"])
        amount = float(r.get(f["amount"]) or 0)
        c = checks[(cashier, check_id)]
        c["rev"] += float(r.get(f["revenue"]) or 0)
        for mid, rule in rules.items():
            if not line_matches(rule, group, dish):
                continue
            if rule["type"] == "pct" and amount > 0:
                c["hits"].add(mid)
            elif rule["type"] == "count":
                counts[cashier][mid] += amount

    out = defaultdict(lambda: {"rev": 0, "checks": 0})
    for (cashier, _), c in checks.items():
        if c["rev"] <= 0:          # чеки возврата / нулевые не считаем
            continue
        o = out[cashier]
        o["rev"] += c["rev"]
        o["checks"] += 1
        for mid in c["hits"]:
            o[mid] = o.get(mid, 0) + 1

    result = {}
    for cashier, o in out.items():
        o["rev"] = round(o["rev"])
        for mid, rule in rules.items():
            if rule["type"] == "count":
                o[mid] = int(round(counts[cashier].get(mid, 0)))
            else:
                o.setdefault(mid, 0)
        result[cashier] = dict(o)
    return result
