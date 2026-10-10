"""Проверка подключения к iiko и подсказки для настройки config.json.

    python probe.py              # связь, поля OLAP, список точек
    python probe.py --groups bh  # группы блюд и топ-позиции точки за 7 дней

Что делает:
  1. логинится в iikoServer API;
  2. проверяет, что все поля из config.json -> fields есть в OLAP;
  3. показывает похожие поля (кассир, чек, группа), если какое-то не нашлось;
  4. выводит подразделения и группы блюд, чтобы заполнить rules.
"""
import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

from iiko_client import IikoError, IikoServer, date_filter, include

HINTS = ["cashier", "waiter", "user", "order", "session", "num", "group", "category", "dish", "department", "sum", "amount"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--groups", metavar="POINT", help="показать группы блюд точки (bh / mo)")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()

    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    srv, f = cfg["server"], cfg["fields"]
    try:
        with IikoServer(srv["url"], srv["login"], srv["password"], verify_ssl=srv.get("verify_ssl", True)) as api:
            print(f"✓ Вход выполнен: {api.base}")
            cols = api.olap_columns("SALES")
            print(f"✓ Полей в OLAP «Продажи»: {len(cols)}\n")

            need = [v for k, v in f.items() if not k.startswith("_")]
            need = [x for v in need for x in (v if isinstance(v, list) else [v])]
            missing = [x for x in need if x not in cols]
            for x in need:
                print(("  ✓ " if x in cols else "  ✗ ") + x + (f" — {cols[x].get('name', '')}" if x in cols else ""))
            if missing:
                print("\nНе найдены поля. Похожие варианты (id — название):")
                for cid, c in sorted(cols.items()):
                    if any(h in cid.lower() for h in HINTS):
                        print(f"  {cid} — {c.get('name', '')}")

            to = dt.date.today() + dt.timedelta(days=1)
            frm = to - dt.timedelta(days=a.days)
            period = {f["date"]: date_filter(frm.isoformat(), to.isoformat())}

            rows = api.olap([f["department"]], [f["revenue"]], period)
            print(f"\nПодразделения с продажами за {a.days} дн. (впиши в points.*.department):")
            for r in rows:
                print(f"  {r.get(f['department'])!r}: {round(r.get(f['revenue']) or 0):,} ₽".replace(",", " "))

            if a.groups:
                dep = cfg["points"][a.groups]["department"]
                flt = dict(period, **{f["department"]: include([dep])})
                rows = api.olap([f["group"], f["dish"]], [f["amount"]], flt)
                by_group = Counter()
                print(f"\nГруппы и позиции «{dep}»:")
                for r in rows:
                    by_group[r.get(f["group"])] += r.get(f["amount"]) or 0
                for g, n in by_group.most_common():
                    print(f"\n  [{g}] — {round(n)} шт")
                    top = sorted((r for r in rows if r.get(f["group"]) == g), key=lambda r: -(r.get(f["amount"]) or 0))
                    for r in top[:8]:
                        print(f"      {r.get(f['dish'])}: {round(r.get(f['amount']) or 0)}")
    except IikoError as e:
        sys.exit(f"✗ {e}")


if __name__ == "__main__":
    main()
