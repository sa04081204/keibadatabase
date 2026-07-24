# -*- coding: utf-8 -*-
"""
TARGET実出力形式の中京データCSV（複数ファイル）をSQLiteに取り込む。

使い方:
    python import_chukyo.py *.csv --db chukyo.db
    または
    python import_chukyo.py 中京芝1600m.csv 中京ダート1200m.csv --db chukyo.db
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

ZEN_TO_HAN = str.maketrans("０１２３４５６７８９", "0123456789")
# 丸数字①〜⑳ (降着があった場合に使われる。数値としては元の着順とみなす)
CIRCLED = {chr(0x2460 + i): str(i + 1) for i in range(20)}

TRACK_COND_MAP = {"良": "良", "稍": "稍重", "重": "重", "不": "不良"}
FINISH_STATUS_MAP = {"止": "中止", "外": "除外", "消": "取消"}


def zen2han(s: str) -> str:
    s = s.translate(ZEN_TO_HAN)
    for circled, han in CIRCLED.items():
        s = s.replace(circled, han)
    return s


def parse_finish(raw: str):
    raw = raw.strip()
    if not raw:
        return None, None
    if raw in FINISH_STATUS_MAP:
        return None, FINISH_STATUS_MAP[raw]
    is_demoted = any(c in raw for c in CIRCLED)
    han = zen2han(raw)
    if han.isdigit():
        return int(han), ("降着" if is_demoted else "完了")
    return None, raw  # 想定外の値はそのまま記録


def parse_time_sec(raw: str):
    """4桁コード 'MSSs' (分1桁+秒2桁+10分の1秒1桁) を秒に変換"""
    raw = raw.strip()
    if not raw or not raw.isdigit():
        return None
    if len(raw) == 4:
        m, ss, s = raw[0], raw[1:3], raw[3]
        return int(m) * 60 + int(ss) + int(s) / 10
    if len(raw) == 3:  # 分が0のケースは無いはずだが保険
        ss, s = raw[0:2], raw[2]
        return int(ss) + int(s) / 10
    return None


def parse_int(raw, signed=False):
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_float(raw):
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_payout(raw):
    """括弧付き '(2.2)' は参考オッズなので配当としては扱わずNoneにする"""
    raw = raw.strip()
    if not raw or raw.startswith("("):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def post_zone(umaban, field_size):
    if not umaban or not field_size:
        return None
    ratio = umaban / field_size
    if ratio <= 1 / 3:
        return "内"
    elif ratio <= 2 / 3:
        return "中"
    else:
        return "外"


def make_race_id(date_yyyymmdd, kaisai, race_no):
    return f"{date_yyyymmdd}_{kaisai}_{race_no}R"


def parse_date(raw_yymmdd: str):
    raw_yymmdd = raw_yymmdd.strip()
    if len(raw_yymmdd) != 6:
        return None, None, None
    yy, mm, dd = raw_yymmdd[0:2], raw_yymmdd[2:4], raw_yymmdd[4:6]
    year = 2000 + int(yy)
    month = int(mm)
    date_str = f"{year:04d}-{mm}-{dd}"
    return date_str, year, month


def parse_stable(raw: str):
    raw = raw.strip()
    if raw == "(栗)":
        return "栗東"
    if raw == "(美)":
        return "美浦"
    return raw.strip("()") or None


def import_file(csv_path: Path, cur: sqlite3.Cursor):
    with open(csv_path, encoding="cp932", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # 父馬名(種牡馬)の列名は出力設定により異なることがあるので候補を順に探す
    sire_col = None
    for cand in ("父", "父馬名", "父馬"):
        if reader.fieldnames and cand in reader.fieldnames:
            sire_col = cand
            break

    inserted = 0
    for row in rows:
        date_str, year, month = parse_date(row["日付"])
        if date_str is None:
            continue
        is_summer = 1 if month in (6, 7, 8) else 0
        kaisai = row["開催"].strip()
        race_no = parse_int(row["Ｒ"])
        race_id = make_race_id(row["日付"].strip(), kaisai, race_no)

        field_size = parse_int(row["頭数"])
        umaban = parse_int(row["馬番"])
        finish_pos, finish_status = parse_finish(row["着順"])
        sire = (row.get(sire_col, "").strip() or None) if sire_col else None

        cur.execute(
            """INSERT OR REPLACE INTO entries (
                race_id, date, year, month, is_summer, kaisai, race_no, race_name,
                horse_name, sire, sex, age, jockey, weight_carried, field_size, umaban,
                post_zone, popularity, finish_pos, finish_status, surface, distance,
                course_variant, track_condition, prize, stable_area, trainer,
                time_sec, margin, corner2, corner3, corner4, last3f, pci, pci3,
                rpci, last3f_diff, body_weight, body_weight_diff, blinker,
                tansho_payout, fukusho_payout
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                race_id, date_str, year, month, is_summer, kaisai, race_no, row["レース名"].strip(),
                row["馬名"].strip(), sire, row["性別"].strip(), parse_int(row["年齢"]),
                row["騎手"].strip(), parse_float(row["斤量"]), field_size, umaban,
                post_zone(umaban, field_size), parse_int(row["人気"]), finish_pos, finish_status,
                row["芝・ダ"].strip(), parse_int(row["距離"]), row["コース区分"].strip() or None,
                TRACK_COND_MAP.get(row["馬場状態"].strip(), row["馬場状態"].strip()),
                parse_float(row["賞金"]), parse_stable(row["所属"]), row["調教師"].strip(),
                parse_time_sec(row["走破タイム"]), row["着差"].strip(),
                parse_int(row["2角"]), parse_int(row["3角"]), parse_int(row["4角"]),
                parse_float(row["上り3F"]), parse_float(row["PCI"]), parse_float(row["PCI3"]),
                parse_float(row["RPCI"]), parse_float(row["上3F地点差"]),
                parse_int(row["馬体重"]), parse_int(row["馬体重増減"]), row["ブリンカー"].strip() or None,
                parse_payout(row["単勝配当"]), parse_payout(row["複勝配当"]),
            ),
        )
        inserted += 1
    return inserted


def main():
    ap = argparse.ArgumentParser(description="中京データCSVをSQLiteに取込む")
    ap.add_argument("csv_paths", nargs="+", type=Path)
    ap.add_argument("--db", type=Path, default=Path("chukyo.db"))
    args = ap.parse_args()

    schema_path = Path(__file__).parent / "schema_chukyo.sql"
    conn = sqlite3.connect(args.db)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    cur = conn.cursor()

    total = 0
    for p in args.csv_paths:
        if not p.exists():
            print(f"見つかりません: {p}", file=sys.stderr)
            continue
        n = import_file(p, cur)
        print(f"{p.name}: {n}件 取込")
        total += n

    conn.commit()
    conn.close()
    print(f"合計 {total}件 -> {args.db}")


if __name__ == "__main__":
    main()
