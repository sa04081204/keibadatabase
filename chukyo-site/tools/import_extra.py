# -*- coding: utf-8 -*-
"""
TARGETの詳細出力(父馬名・母馬名・前走情報などを含む105列形式、
例:「中京芝1600m前走距離.csv」)を読み込み、既存のchukyo.dbに
血統・前走情報をマージ(UPDATE)するスクリプト。

前提: 先に import_chukyo.py で基本データ(chukyo.db)を作成済みであること。
このスクリプトは (日付, レース番号, 馬番) をキーにして追加列をUPDATEする。

使い方:
    python tools/import_extra.py 中京*前走距離.csv --db chukyo.db
"""
import argparse
import csv
import sqlite3
from pathlib import Path

TRACK_COND_MAP = {"良": "良", "稍": "稍重", "重": "重", "不": "不良"}

EXTRA_COLUMNS = [
    "sire", "dam", "dam_sire", "corner1", "interval_weeks",
    "prev_date", "prev_venue", "prev_surface", "prev_distance",
    "prev_track_condition", "prev_class_name", "prev_finish_pos",
    "prev_popularity", "prev_corner4", "prev_last3f", "prev_time_sec", "prev_odds",
]


def ensure_columns(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(entries)")
    existing = {row[1] for row in cur.fetchall()}
    type_map = {
        "corner1": "INTEGER", "interval_weeks": "INTEGER", "prev_distance": "INTEGER",
        "prev_finish_pos": "INTEGER", "prev_popularity": "INTEGER", "prev_corner4": "INTEGER",
        "prev_last3f": "REAL", "prev_time_sec": "REAL", "prev_odds": "REAL",
    }
    for col in EXTRA_COLUMNS:
        if col not in existing:
            coltype = type_map.get(col, "TEXT")
            cur.execute(f"ALTER TABLE entries ADD COLUMN {col} {coltype}")
    conn.commit()


def parse_int(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_float(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def make_date(y2, m, d):
    y2, m, d = (y2 or "").strip(), (m or "").strip(), (d or "").strip()
    if not (y2 and m and d):
        return None
    return f"20{y2}-{m}-{d}"


def import_file(csv_path: Path, cur: sqlite3.Cursor):
    with open(csv_path, encoding="cp932", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0
    for row in rows:
        date_str = make_date(row["年"], row["月"], row["日"])
        race_no = parse_int(row["レース番号"])
        umaban = parse_int(row["馬番"])
        if date_str is None or race_no is None or umaban is None:
            continue

        prev_date = make_date(row.get("前走年"), row.get("前走月"), row.get("前走日"))

        values = {
            "sire": (row.get("父馬名") or "").strip() or None,
            "dam": (row.get("母馬名") or "").strip() or None,
            "dam_sire": (row.get("母の父馬名") or "").strip() or None,
            "corner1": parse_int(row.get("通過順1")) or None,
            "interval_weeks": parse_int(row.get("間隔")),
            "prev_date": prev_date,
            "prev_venue": (row.get("前走場所") or "").strip() or None,
            "prev_surface": (row.get("前走芝・ダ") or "").strip() or None,
            "prev_distance": parse_int(row.get("前走距離")),
            "prev_track_condition": TRACK_COND_MAP.get(
                (row.get("前走馬場状態") or "").strip(), (row.get("前走馬場状態") or "").strip() or None
            ),
            "prev_class_name": (row.get("前走レース名") or "").strip() or None,
            "prev_finish_pos": parse_int(row.get("前走確定着順")) or None,
            "prev_popularity": parse_int(row.get("前走人気順")),
            "prev_corner4": parse_int(row.get("前走通過順4")),
            "prev_last3f": parse_float(row.get("前走上がり3Fタイム")),
            "prev_time_sec": parse_float(row.get("前走走破タイム")),
            "prev_odds": parse_float(row.get("前走単勝オッズ")),
        }

        set_clause = ", ".join(f"{k}=?" for k in values)
        cur.execute(
            f"UPDATE entries SET {set_clause} WHERE date=? AND race_no=? AND umaban=?",
            (*values.values(), date_str, race_no, umaban),
        )
        if cur.rowcount > 0:
            updated += 1
    return updated, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_paths", nargs="+", type=Path)
    ap.add_argument("--db", type=Path, default=Path("chukyo.db"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_columns(conn)
    cur = conn.cursor()

    total_updated = total_rows = 0
    for p in args.csv_paths:
        updated, n = import_file(p, cur)
        print(f"{p.name}: {n}行中 {updated}件を更新")
        total_updated += updated
        total_rows += n

    conn.commit()
    conn.close()
    print(f"合計 {total_updated}/{total_rows} 件を更新 -> {args.db}")


if __name__ == "__main__":
    main()
