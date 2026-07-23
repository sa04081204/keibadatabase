# -*- coding: utf-8 -*-
"""
chukyo.db から、静的サイト用のJSONデータを書き出す。

出力:
    data/courses.json          … コース別の集計値＋狙い目テキスト
    data/entries/<key>.json    … コースごとの全レース明細（検索/閲覧用）

使い方:
    python tools/export_web_data.py --db chukyo.db --out-dir data
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from analyze_chukyo import (  # noqa: E402
    classify_style, summarize, entity_ranking, valid_starters, build_crosses, enrich, MIN_RACES_WARN,
)

RANKING_MIN_N = 3  # サーバー側では緩めに絞り、UI側でさらに絞れるようにする
CROSS_MIN_N = 20   # クロス条件の「狙い目/軽視」判定に使う最低サンプル数


SURFACE_SLUG = {"芝": "shiba", "ダ": "da"}


def course_key(surface, distance, variant):
    """ファイル名・URLに使うキー。日本語を含めるとzip解凍やアップロード時に
    文字化けするトラブルが起きやすいため、必ずASCII(英数字)のみにする。"""
    v = f"_{variant}" if variant else ""
    return f"{SURFACE_SLUG.get(surface, surface)}{distance}{v}"


def course_label(surface, distance, variant):
    label = f"中京{surface}{distance}m"
    if variant:
        label += f"({variant})"
    return label


def best_of(d: dict, exclude_none=True):
    """{カテゴリ: {win_pct, n, ...}} から最高勝率のカテゴリを返す"""
    items = [(k, v) for k, v in d.items() if v["win_pct"] is not None and v["n"] >= 20]
    if not items:
        return None, None
    k, v = max(items, key=lambda kv: kv[1]["win_pct"])
    return k, v


def build_takeaway(overall, summer, other, crosses):
    lines = []

    zone_k, zone_v = best_of(overall["post_zone"])
    style_k, style_v = best_of(overall["style"])
    agari_k, agari_v = best_of(overall["agari"])
    if zone_k:
        lines.append(f"馬番は「{zone_k}」枠が勝率{zone_v['win_pct']}%と最も高い。")
    if style_k:
        lines.append(f"脚質は「{style_k}」が勝率{style_v['win_pct']}%でもっとも決まりやすい。")
    if agari_k and agari_v["n"] >= 30:
        lines.append(f"上がり3Fは「{agari_k}」の馬が勝率{agari_v['win_pct']}%と信頼度が高い。")

    dist_k, dist_v = best_of(overall["distance_change"])
    if dist_k and dist_v["n"] >= 30:
        lines.append(f"距離変化は「{dist_k}」の馬が勝率{dist_v['win_pct']}%（複勝率{dist_v['place_pct']}%）と好走傾向。")

    # 開催日程による内外バイアスの変化(前半 vs 後半、クロス集計から抽出)
    zone_by_kaisai = {(c["value1"], c["value2"]): c for c in crosses
                       if c["factor1"] == "馬番ゾーン" and c["factor2"] == "開催日程"}
    in_early = zone_by_kaisai.get(("内", "開催前半(1-4日目)"))
    in_late = zone_by_kaisai.get(("内", "開催後半(9日目以降)"))
    out_early = zone_by_kaisai.get(("外", "開催前半(1-4日目)"))
    out_late = zone_by_kaisai.get(("外", "開催後半(9日目以降)"))
    if in_early and in_late and in_early["n"] >= 20 and in_late["n"] >= 20:
        if in_early["win_pct"] - in_late["win_pct"] >= 1.5:
            msg = f"開催が進むと内枠の勝率が{in_early['win_pct']}%→{in_late['win_pct']}%と低下"
            if out_early and out_late and out_early["n"] >= 20 and out_late["n"] >= 20:
                msg += f"、外枠は{out_early['win_pct']}%→{out_late['win_pct']}%"
            lines.append(msg + "。開催後半は外差しに注意。")

    # クロス条件(2要素の掛け合わせ)からベスト/ワーストを1件ずつ抽出
    valid_crosses = [c for c in crosses if c["n"] >= CROSS_MIN_N and c["win_roi"] is not None]
    if valid_crosses:
        best_cross = max(valid_crosses, key=lambda c: c["win_roi"])
        worst_cross = min(valid_crosses, key=lambda c: c["win_roi"])
        if best_cross["win_roi"] >= 100:
            lines.append(
                f"買い条件:「{best_cross['label']}」は単勝回収率{best_cross['win_roi']}%"
                f"（勝率{best_cross['win_pct']}%, N={best_cross['n']}）と好走域。"
            )
        if worst_cross["win_roi"] <= 60:
            lines.append(
                f"軽視条件:「{worst_cross['label']}」は単勝回収率{worst_cross['win_roi']}%"
                f"（勝率{worst_cross['win_pct']}%, N={worst_cross['n']}）と低調、狙いにくい。"
            )

    if overall["rpci_avg"] is not None:
        if overall["rpci_avg"] >= 51:
            lines.append(f"平均RPCI{overall['rpci_avg']}でスロー寄り、上がり勝負になりやすい。")
        elif overall["rpci_avg"] <= 49:
            lines.append(f"平均RPCI{overall['rpci_avg']}でハイペース寄り、先行馬に厳しい消耗戦になりやすい。")
        else:
            lines.append(f"平均RPCI{overall['rpci_avg']}でペースは標準的。")

    if overall["fav_win_pct"] is not None:
        if overall["fav_win_pct"] >= 35:
            lines.append(f"1番人気の勝率{overall['fav_win_pct']}%と信頼度は高め。")
        elif overall["fav_win_pct"] <= 25:
            lines.append(f"1番人気の勝率{overall['fav_win_pct']}%とやや荒れやすい。")

    # 夏の特徴（最も差が大きい要素を1つ抽出）
    diffs = []
    for cat, sdict, odict in (("zone", summer["post_zone"], other["post_zone"]),
                               ("style", summer["style"], other["style"])):
        for k in sdict:
            s, o = sdict[k], odict[k]
            if s["win_pct"] is not None and o["win_pct"] is not None and s["n"] >= 15 and o["n"] >= 30:
                diffs.append((abs(s["win_pct"] - o["win_pct"]), cat, k, s["win_pct"], o["win_pct"]))
    if diffs:
        diffs.sort(reverse=True)
        _, cat, k, sw, ow = diffs[0]
        cat_label = "枠" if cat == "zone" else "脚質"
        direction = "上がる" if sw > ow else "下がる"
        lines.append(f"夏(6-8月)は「{k}」{cat_label}の勝率が{direction}傾向（夏{sw}% / 他季{ow}%）。")

    if summer["rpci_avg"] is not None and other["rpci_avg"] is not None:
        d = round(summer["rpci_avg"] - other["rpci_avg"], 1)
        if abs(d) >= 0.5:
            trend = "ペースが速くなる(消耗戦寄り)" if d < 0 else "ペースが緩くなる(瞬発戦寄り)"
            lines.append(f"夏はRPCIが{d:+.1f}変化し、{trend}傾向。")

    return lines


# 明細データの列定義（順序が entries/*.json の各行(配列)の順序と一致する）
ENTRY_COLUMNS = [
    "date", "race_no", "race_name", "horse", "jockey", "trainer", "sex", "age",
    "umaban", "field_size", "zone", "style", "pop", "finish", "status", "cond",
    "time", "last3f", "rpci", "weight", "weight_diff", "blinker", "summer",
]


def clean(v):
    if pd.isna(v):
        return None
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def entry_to_row(row):
    return [
        clean(row["date"]), clean(row["race_no"]), clean(row["race_name"]),
        clean(row["horse_name"]), clean(row["jockey"]), clean(row["trainer"]),
        clean(row["sex"]), clean(row["age"]), clean(row["umaban"]), clean(row["field_size"]),
        clean(row["post_zone"]), clean(row["running_style"]), clean(row["popularity"]),
        clean(row["finish_pos"]), clean(row["finish_status"]), clean(row["track_condition"]),
        clean(row["time_sec"]), clean(row["last3f"]), clean(row["rpci"]),
        clean(row["body_weight"]), clean(row["body_weight_diff"]), clean(row["blinker"]),
        bool(row["is_summer"]),
    ]


def umaban_list(d: dict):
    """{umaban(float): stats} -> [{umaban:int, ...stats}] のリストに変換(JSONキー用に整形)"""
    out = []
    for u, v in d.items():
        rec = {"umaban": int(u)}
        rec.update(v)
        out.append(rec)
    out.sort(key=lambda r: r["umaban"])
    return out


def ranking_payload(df: pd.DataFrame):
    dfv = valid_starters(df)
    jockey = entity_ranking(dfv, "jockey", min_n=RANKING_MIN_N)
    trainer = entity_ranking(dfv, "trainer", min_n=RANKING_MIN_N)
    sire = entity_ranking(dfv, "sire", min_n=RANKING_MIN_N)
    jockey.sort(key=lambda r: (-(r["win_pct"] or 0)))
    trainer.sort(key=lambda r: (-(r["win_pct"] or 0)))
    sire.sort(key=lambda r: (-(r["win_pct"] or 0)))
    return {"jockey": jockey, "trainer": trainer, "sire": sire}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("chukyo.db"))
    ap.add_argument("--out-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    df = pd.read_sql_query("SELECT * FROM entries", conn)
    conn.close()

    df = enrich(df)

    courses_out = []
    entries_dir = args.out_dir / "entries"
    rankings_dir = args.out_dir / "rankings"
    entries_dir.mkdir(parents=True, exist_ok=True)
    rankings_dir.mkdir(parents=True, exist_ok=True)

    combos = (
        df[["surface", "distance", "course_variant"]]
        .drop_duplicates()
        .sort_values(["surface", "distance", "course_variant"])
        .values.tolist()
    )

    for surface, distance, variant in combos:
        variant = variant if pd.notna(variant) else None
        mask = (df["surface"] == surface) & (df["distance"] == distance)
        mask &= df["course_variant"].isna() if not variant else (df["course_variant"] == variant)
        dfc = df[mask]
        dfs = dfc[dfc["is_summer"] == 1]
        dfo = dfc[dfc["is_summer"] == 0]

        overall = summarize(dfc)
        summer = summarize(dfs)
        other = summarize(dfo)
        crosses = build_crosses(dfc)
        takeaway = build_takeaway(overall, summer, other, crosses)

        key = course_key(surface, distance, variant)
        label = course_label(surface, distance, variant)

        courses_out.append({
            "key": key,
            "label": label,
            "surface": surface,
            "distance": int(distance),
            "variant": variant,
            "n_races": overall["n_races"],
            "n_races_summer": summer["n_races"],
            "low_sample": overall["n_races"] < MIN_RACES_WARN,
            "low_sample_summer": summer["n_races"] < MIN_RACES_WARN,
            "takeaway": takeaway,
            "post_zone": overall["post_zone"],
            "style": overall["style"],
            "umaban": umaban_list(overall["umaban"]),
            "agari": overall["agari"],
            "corner4_band": overall["corner4_band"],
            "kaisai_band": overall["kaisai_band"],
            "sex": overall["sex"],
            "age": overall["age"],
            "distance_change": overall["distance_change"],
            "interval": overall["interval"],
            "chukyo_experience": overall["chukyo_experience"],
            "crosses": crosses,
            "rpci": {"overall": overall["rpci_avg"], "summer": summer["rpci_avg"], "other": other["rpci_avg"]},
            "time_by_cond": overall["time_by_cond"],
            "fav_win_pct": overall["fav_win_pct"],
            "fav_roi": overall["fav_roi"],
            "summer_post_zone": summer["post_zone"],
            "summer_style": summer["style"],
            "other_post_zone": other["post_zone"],
            "other_style": other["style"],
        })

        # 明細データ(検索・閲覧用)。新しい順に並べ、列配列形式で軽量化する
        dfc_sorted = dfc.sort_values("date", ascending=False)
        rows = [entry_to_row(r) for _, r in dfc_sorted.iterrows()]
        payload = {"columns": ENTRY_COLUMNS, "rows": rows}
        (entries_dir / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

        # 騎手・調教師ランキング(コース別)
        (rankings_dir / f"{key}.json").write_text(
            json.dumps(ranking_payload(dfc), ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    # 全コース通算ランキング
    (rankings_dir / "ALL.json").write_text(
        json.dumps(ranking_payload(df), ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    (args.out_dir / "courses.json").write_text(
        json.dumps({"generated_note": "中京競馬 コース別攻略データ", "courses": courses_out},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"courses.json / entries/*.json / rankings/*.json を {args.out_dir} に出力しました（{len(courses_out)}コース）")


if __name__ == "__main__":
    main()
