"""Выгружает из iiko факты смены по каждому кассиру за учётный день.

    python pull.py --point bh                    # за сегодня
    python pull.py --point mo --date 2026-10-09  # за конкретный день
    python pull.py --point bh --out facts.json   # сохранить в файл

Формат вывода совпадает с полем `facts` смены в kassir/index.html:
    {"Иванова Алина": {"rev": 64200, "checks": 82, "drink": 56, "sauce": 19, ...}}
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from iiko_client import IikoError, IikoServer, date_filter, include
from shift_facts import build_facts


def pull(cfg, point, day):
    f, p, srv = cfg["fields"], cfg["points"][point], cfg["server"]
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    filters = dict(cfg.get("extra_filters", {}))
    filters[f["date"]] = date_filter(day, nxt)
    filters[f["department"]] = include([p["department"]])
    group_by = [f["cashier"], *f["check"], f["group"], f["dish"]]
    with IikoServer(srv["url"], srv["login"], srv["password"], verify_ssl=srv.get("verify_ssl", True)) as api:
        rows = api.olap(group_by, [f["revenue"], f["amount"]], filters)
    return build_facts(rows, f, p["rules"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--point", required=True, choices=["bh", "mo"])
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--out")
    a = ap.parse_args()

    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    try:
        facts = pull(cfg, a.point, a.date)
    except IikoError as e:
        sys.exit(f"✗ {e}")
    text = json.dumps(facts, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
