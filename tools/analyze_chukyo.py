# -*- coding: utf-8 -*-
"""
中京競馬 コース別攻略データベース生成スクリプト（夏競馬フォーカス）

出力:
    chukyo_report.md   … コースごとの攻略データ（通年 + 夏競馬比較）
    chukyo_stats.csv    … 集計値の一覧

使い方:
    python analyze_chukyo.py --db chukyo.db
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

MIN_RACES_WARN = 30  # このレース数未満のコースには注意書きを付ける


def load(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM entries", conn)
    conn.close()
    return df


def classify_style(row):
    for col in ("corner4", "corner3", "corner2"):
        pos = row[col]
        if pd.notna(pos) and pd.notna(row["field_size"]) and row["field_size"] > 0:
            ratio = pos / row["field_size"]
            if ratio <= 0.2:
                return "逃げ"
            elif ratio <= 0.45:
                return "先行"
            elif ratio <= 0.7:
                return "差し"
            else:
                return "追込"
    return None


def bucket_agari(rank):
    """レース内の上がり3F順位(1=最速)を区分する"""
    if rank is None or pd.isna(rank):
        return None
    r = int(rank)
    if r == 1:
        return "上がり1位"
    if r == 2:
        return "上がり2位"
    if r == 3:
        return "上がり3位"
    if r <= 6:
        return "上がり4-6位"
    return "上がり7位以下"


def bucket_corner4(pos):
    """4角の通過順位(絶対的な番手)を区分する"""
    if pos is None or pd.isna(pos):
        return None
    p = int(pos)
    if p <= 3:
        return "4角1-3番手"
    if p <= 6:
        return "4角4-6番手"
    if p <= 9:
        return "4角7-9番手"
    return "4角10番手以下"


AGARI_CATEGORIES = ["上がり1位", "上がり2位", "上がり3位", "上がり4-6位", "上がり7位以下"]
CORNER4_CATEGORIES = ["4角1-3番手", "4角4-6番手", "4角7-9番手", "4角10番手以下"]
KAISAI_DAY_LETTERS = {c: 10 + i for i, c in enumerate("ABCDEFGHIJ")}  # A=10日目, B=11日目...
KAISAI_BAND_CATEGORIES = ["開催前半(1-4日目)", "開催中盤(5-8日目)", "開催後半(9日目以降)"]
SEX_CATEGORIES = ["牡", "牝", "セ"]
AGE_CATEGORIES = ["2歳", "3歳", "4歳", "5歳以上"]
DISTANCE_CHANGE_CATEGORIES = ["延長(201m以上)", "延長(1-200m)", "同距離", "短縮(1-200m)", "短縮(201m以上)"]
INTERVAL_CATEGORIES = ["連闘(中0週)", "中1-2週", "中3-5週", "中6-9週", "中10週以上(休み明け)"]
CHUKYO_EXPERIENCE_CATEGORIES = ["中京経験あり", "中京初挑戦"]
PREV_AGARI_CATEGORIES = ["上がり速いタイプ(上位33%)", "平均的", "上がり遅いタイプ(下位33%)"]


def bucket_distance_change(cur_dist, prev_dist):
    if cur_dist is None or prev_dist is None or pd.isna(cur_dist) or pd.isna(prev_dist):
        return None
    diff = cur_dist - prev_dist
    if diff >= 201:
        return "延長(201m以上)"
    if diff >= 1:
        return "延長(1-200m)"
    if diff == 0:
        return "同距離"
    if diff >= -200:
        return "短縮(1-200m)"
    return "短縮(201m以上)"


def bucket_interval(weeks):
    if weeks is None or pd.isna(weeks):
        return None
    w = int(weeks)
    if w <= 0:
        return "連闘(中0週)"
    if w <= 2:
        return "中1-2週"
    if w <= 5:
        return "中3-5週"
    if w <= 9:
        return "中6-9週"
    return "中10週以上(休み明け)"


def bucket_chukyo_experience(prev_venue):
    if prev_venue is None or (isinstance(prev_venue, float) and pd.isna(prev_venue)):
        return None  # 初出走(前走なし)は集計対象外にする
    return "中京経験あり" if prev_venue == "中京" else "中京初挑戦"


def parse_kaisai_day(kaisai):
    """開催コード(例:'3名8','1名A')から開催日目(何日目)を取り出す"""
    if not kaisai:
        return None
    last = kaisai[-1]
    if last.isdigit():
        return int(last)
    return KAISAI_DAY_LETTERS.get(last)


def bucket_kaisai_day(day):
    if day is None:
        return None
    if day <= 4:
        return "開催前半(1-4日目)"
    if day <= 8:
        return "開催中盤(5-8日目)"
    return "開催後半(9日目以降)"


def bucket_age(age):
    if age is None or pd.isna(age):
        return None
    a = int(age)
    if a <= 2:
        return "2歳"
    if a == 3:
        return "3歳"
    if a == 4:
        return "4歳"
    return "5歳以上"


def prev_agari_bucket_and_bins(dfc: pd.DataFrame):
    """dfc(1コース分)のprev_last3f(前走の上がり3Fタイム)を3分位に分割する。
    ※これは「前走時点で既に分かっている値」なので出走前の特徴量として使ってよい。
    　当該レース結果から作るagari_bucket(上がり1位/2位…)とは違いデータリークにならない。
    サンプル不足(有効値30件未満)やタイムがほぼ同一で分位点が作れない場合は
    (全てNoneのSeries, None)を返し、呼び出し側で「この項目は使わない」扱いにする。
    bin_edges(3分位の境界値、2個)はcourses.jsonに保存し、predict_race.py側で
    新しい馬のprev_last3fを同じ基準で分類するのに使う。"""
    valid = dfc["prev_last3f"].dropna()
    if len(valid) < 30:
        return pd.Series([None] * len(dfc), index=dfc.index, dtype=object), None
    try:
        cats, bins = pd.qcut(
            dfc["prev_last3f"], 3, labels=PREV_AGARI_CATEGORIES, retbins=True, duplicates="drop"
        )
    except ValueError:
        return pd.Series([None] * len(dfc), index=dfc.index, dtype=object), None
    if len(bins) - 1 != 3:
        # duplicatesで分位点が潰れて3分割できなかった場合は無効値扱いにする
        return pd.Series([None] * len(dfc), index=dfc.index, dtype=object), None
    cats = cats.astype(object)
    cats[dfc["prev_last3f"].isna()] = None
    return cats, [round(float(b), 2) for b in bins]


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """脚質・上がり3F順位・4角通過順位帯・開催日程・年齢区分をまとめて付与する"""
    df = df.copy()
    df["running_style"] = df.apply(classify_style, axis=1)
    df["agari_rank"] = df.groupby("race_id")["last3f"].rank(method="min", ascending=True)
    df["agari_bucket"] = df["agari_rank"].apply(bucket_agari)
    df["corner4_band"] = df["corner4"].apply(bucket_corner4)
    df["kaisai_day"] = df["kaisai"].apply(parse_kaisai_day)
    df["kaisai_band"] = df["kaisai_day"].apply(bucket_kaisai_day)
    df["age_bucket"] = df["age"].apply(bucket_age)
    df["distance_change"] = df.apply(lambda r: bucket_distance_change(r["distance"], r["prev_distance"]), axis=1)
    df["interval_bucket"] = df["interval_weeks"].apply(bucket_interval)
    df["chukyo_experience"] = df["prev_venue"].apply(bucket_chukyo_experience)
    return df


def pct(numerator, denominator):
    if not denominator:
        return None
    return round(100 * numerator / denominator, 1)


def valid_starters(df: pd.DataFrame) -> pd.DataFrame:
    """取消・除外(出走していない=賭けが成立しない)を除いた実際の出走分を返す"""
    return df[~df["finish_status"].isin(["取消", "除外"])]


def group_stats(sub: pd.DataFrame):
    """1グループ分の 勝率/複勝率/単勝回収率/複勝回収率/N を計算する"""
    n = len(sub)
    if not n:
        return {"n": 0, "win_pct": None, "place_pct": None, "win_roi": None, "place_roi": None}
    win = (sub["finish_pos"] == 1).sum()
    place = (sub["finish_pos"] <= 3).sum()
    win_roi = round(sub["tansho_payout"].fillna(0).sum() / (n * 100) * 100, 1)
    place_roi = round(sub["fukusho_payout"].fillna(0).sum() / (n * 100) * 100, 1)
    return {"n": n, "win_pct": pct(win, n), "place_pct": pct(place, n), "win_roi": win_roi, "place_roi": place_roi}


def rate_table(df: pd.DataFrame, group_col: str, categories):
    """group_colのカテゴリ別 勝率/複勝率/回収率(N=出走数)を返す"""
    return {cat: group_stats(df[df[group_col] == cat]) for cat in categories}


def entity_ranking(df: pd.DataFrame, group_col: str, min_n: int = 5):
    """騎手・調教師など、値の種類が多い列向けのランキング(カテゴリ固定なし)"""
    out = []
    for val, sub in df.groupby(group_col):
        if not val:
            continue
        stats = group_stats(sub)
        if stats["n"] < min_n:
            continue
        stats["name"] = val
        out.append(stats)
    return out


def bucket_popularity(pop):
    if pop is None or (isinstance(pop, float) and pd.isna(pop)):
        return None
    if pop == 1:
        return "1番人気"
    if pop <= 3:
        return "2-3番人気"
    if pop <= 6:
        return "4-6番人気"
    return "7番人気以下"


# クロス集計する2要素の組み合わせ定義
CROSS_PAIRS = [
    ("馬番ゾーン", "post_zone", ["内", "中", "外"], "脚質", "running_style", ["逃げ", "先行", "差し", "追込"]),
    ("脚質", "running_style", ["逃げ", "先行", "差し", "追込"], "馬場状態", "track_condition", ["良", "稍重", "重", "不良"]),
    ("馬番ゾーン", "post_zone", ["内", "中", "外"], "馬場状態", "track_condition", ["良", "稍重", "重", "不良"]),
    ("脚質", "running_style", ["逃げ", "先行", "差し", "追込"], "人気", "pop_bucket",
     ["1番人気", "2-3番人気", "4-6番人気", "7番人気以下"]),
    ("馬番ゾーン", "post_zone", ["内", "中", "外"], "人気", "pop_bucket",
     ["1番人気", "2-3番人気", "4-6番人気", "7番人気以下"]),
    ("脚質", "running_style", ["逃げ", "先行", "差し", "追込"], "季節", "season", ["夏(6-8月)", "夏以外"]),
    ("馬番ゾーン", "post_zone", ["内", "中", "外"], "季節", "season", ["夏(6-8月)", "夏以外"]),
    ("上がり3F順位", "agari_bucket", AGARI_CATEGORIES, "脚質", "running_style", ["逃げ", "先行", "差し", "追込"]),
    ("4角通過", "corner4_band", CORNER4_CATEGORIES, "上がり3F順位", "agari_bucket", AGARI_CATEGORIES),
    ("4角通過", "corner4_band", CORNER4_CATEGORIES, "馬場状態", "track_condition", ["良", "稍重", "重", "不良"]),
    ("馬番ゾーン", "post_zone", ["内", "中", "外"], "開催日程", "kaisai_band", KAISAI_BAND_CATEGORIES),
    ("脚質", "running_style", ["逃げ", "先行", "差し", "追込"], "開催日程", "kaisai_band", KAISAI_BAND_CATEGORIES),
    ("距離変化", "distance_change", DISTANCE_CHANGE_CATEGORIES, "脚質", "running_style", ["逃げ", "先行", "差し", "追込"]),
    ("間隔", "interval_bucket", INTERVAL_CATEGORIES, "脚質", "running_style", ["逃げ", "先行", "差し", "追込"]),
    ("中京経験", "chukyo_experience", CHUKYO_EXPERIENCE_CATEGORIES, "脚質", "running_style", ["逃げ", "先行", "差し", "追込"]),
    ("距離変化", "distance_change", DISTANCE_CHANGE_CATEGORIES, "馬番ゾーン", "post_zone", ["内", "中", "外"]),
]


def build_crosses(df: pd.DataFrame):
    """馬番ゾーン×脚質、脚質×馬場状態…など2要素の掛け合わせを総当たりで集計する"""
    df = valid_starters(df).copy()
    df["pop_bucket"] = df["popularity"].apply(bucket_popularity)
    df["season"] = df["is_summer"].map({1: "夏(6-8月)", 0: "夏以外"})

    out = []
    for name1, col1, vals1, name2, col2, vals2 in CROSS_PAIRS:
        for v1 in vals1:
            for v2 in vals2:
                sub = df[(df[col1] == v1) & (df[col2] == v2)]
                stats = group_stats(sub)
                if stats["n"] == 0:
                    continue
                stats["label"] = f"{v1}×{v2}"
                stats["factor1"] = name1
                stats["value1"] = v1
                stats["factor2"] = name2
                stats["value2"] = v2
                out.append(stats)
    return out


def avg_rpci_per_race(df: pd.DataFrame):
    per_race = df.dropna(subset=["rpci"]).groupby("race_id")["rpci"].first()
    if per_race.empty:
        return None, 0
    return round(per_race.mean(), 1), len(per_race)


def winner_time_by_condition(df: pd.DataFrame):
    winners = df[(df["finish_pos"] == 1) & df["time_sec"].notna()]
    out = {}
    for cond, sub in winners.groupby("track_condition"):
        out[cond] = {"avg_time": round(sub["time_sec"].mean(), 2), "n": len(sub)}
    return out


def summarize(df: pd.DataFrame):
    """1つの(サブセット)DataFrameから主要指標をまとめて返す"""
    df = valid_starters(df)
    n_races = df["race_id"].nunique()
    n_runs = len(df)
    post_zone = rate_table(df, "post_zone", ["内", "中", "外"])
    style = rate_table(df, "running_style", ["逃げ", "先行", "差し", "追込"])
    umaban = rate_table(df, "umaban", sorted([u for u in df["umaban"].dropna().unique()]))
    agari = rate_table(df, "agari_bucket", AGARI_CATEGORIES)
    corner4_band = rate_table(df, "corner4_band", CORNER4_CATEGORIES)
    kaisai_band = rate_table(df, "kaisai_band", KAISAI_BAND_CATEGORIES)
    sex = rate_table(df, "sex", SEX_CATEGORIES)
    age = rate_table(df, "age_bucket", AGE_CATEGORIES)
    distance_change = rate_table(df, "distance_change", DISTANCE_CHANGE_CATEGORIES)
    interval = rate_table(df, "interval_bucket", INTERVAL_CATEGORIES)
    chukyo_experience = rate_table(df, "chukyo_experience", CHUKYO_EXPERIENCE_CATEGORIES)
    rpci_avg, rpci_n = avg_rpci_per_race(df)
    time_by_cond = winner_time_by_condition(df)

    blinker_on = df[df["blinker"].notna() & (df["blinker"] != "")]
    blinker_off = df[df["blinker"].isna() | (df["blinker"] == "")]
    blinker_stat = {
        "on": {"n": len(blinker_on), "win_pct": pct((blinker_on["finish_pos"] == 1).sum(), len(blinker_on))},
        "off": {"n": len(blinker_off), "win_pct": pct((blinker_off["finish_pos"] == 1).sum(), len(blinker_off))},
    }

    fav = df[df["popularity"] == 1]
    fav_win_pct = pct((fav["finish_pos"] == 1).sum(), len(fav)) if len(fav) else None
    fav_roi = group_stats(fav)["win_roi"] if len(fav) else None

    return {
        "n_races": n_races,
        "n_runs": n_runs,
        "post_zone": post_zone,
        "style": style,
        "umaban": umaban,
        "agari": agari,
        "corner4_band": corner4_band,
        "kaisai_band": kaisai_band,
        "sex": sex,
        "age": age,
        "distance_change": distance_change,
        "interval": interval,
        "chukyo_experience": chukyo_experience,
        "rpci_avg": rpci_avg,
        "rpci_n": rpci_n,
        "time_by_cond": time_by_cond,
        "blinker": blinker_stat,
        "fav_win_pct": fav_win_pct,
        "fav_roi": fav_roi,
    }


def fmt_rate_rows(d: dict, label_col: str):
    lines = [f"| {label_col} | 勝率 | 複勝率 | 単勝回収率 | 複勝回収率 | N |", "|---|---|---|---|---|---|"]
    for cat, v in d.items():
        lines.append(f"| {cat} | {v['win_pct']}% | {v['place_pct']}% | {v['win_roi']}% | {v['place_roi']}% | {v['n']} |")
    return lines


def course_title(surface, distance, variant):
    base = f"中京 {surface}{distance}m"
    if variant:
        base += f"（{variant}コース）"
    return base


def build_course_section(surface, distance, variant, df_all: pd.DataFrame):
    mask = (df_all["surface"] == surface) & (df_all["distance"] == distance)
    if variant:
        mask &= df_all["course_variant"] == variant
    else:
        mask &= df_all["course_variant"].isna()
    df_course = df_all[mask]
    df_summer = df_course[df_course["is_summer"] == 1]
    df_other = df_course[df_course["is_summer"] == 0]

    overall = summarize(df_course)
    summer = summarize(df_summer)
    other = summarize(df_other)

    lines = [f"\n## {course_title(surface, distance, variant)}", ""]
    lines.append(f"（全体サンプル: {overall['n_races']}レース / うち夏(6-8月): {summer['n_races']}レース）")
    if overall["n_races"] < MIN_RACES_WARN:
        lines.append("> ⚠ サンプル数が少ないため参考値です。")
    if summer["n_races"] < MIN_RACES_WARN:
        lines.append("> ⚠ 夏競馬のサンプル数が少ないため、夏の傾向は参考値としてご覧ください。")

    lines.append("\n### 通年：馬番ゾーン別成績（内=下位1/3, 中=中位1/3, 外=上位1/3）")
    lines += fmt_rate_rows(overall["post_zone"], "ゾーン")

    lines.append("\n### 通年：脚質別・勝率（コーナー通過順位から推定）")
    lines += fmt_rate_rows(overall["style"], "脚質")

    lines.append("\n### 通年：上がり3F順位別・成績（そのレースでの速さ順）")
    lines += fmt_rate_rows(overall["agari"], "上がり順位")

    lines.append("\n### 通年：4角通過順位帯別・成績（絶対的な番手）")
    lines += fmt_rate_rows(overall["corner4_band"], "4角番手")

    if overall["rpci_avg"] is not None:
        pace_note = "スロー寄り（瞬発力勝負になりやすい）" if overall["rpci_avg"] > 50 else "ハイペース寄り（消耗戦になりやすい）"
        lines.append(f"\n### 通年：平均RPCI = {overall['rpci_avg']}（N={overall['rpci_n']}レース） → {pace_note}")

    if overall["time_by_cond"]:
        lines.append("\n### 通年：馬場状態別・勝ち時計平均")
        lines.append("| 馬場 | 平均勝ち時計(秒) | N |")
        lines.append("|---|---|---|")
        for cond, v in overall["time_by_cond"].items():
            lines.append(f"| {cond} | {v['avg_time']} | {v['n']} |")

    if overall["fav_win_pct"] is not None:
        lines.append(f"\n### 通年：単勝1番人気の信頼度 = 勝率 {overall['fav_win_pct']}%")

    # ---- 夏競馬 特化セクション ----
    lines.append("\n### ▶ 夏競馬(6〜8月)フォーカス：他季節との比較")
    lines.append("| 指標 | 夏(6-8月) | それ以外の季節 |")
    lines.append("|---|---|---|")

    for zone in ["内", "中", "外"]:
        s = summer["post_zone"][zone]
        o = other["post_zone"][zone]
        lines.append(f"| {zone}枠 勝率(N) | {s['win_pct']}%({s['n']}) | {o['win_pct']}%({o['n']}) |")

    for style in ["逃げ", "先行", "差し", "追込"]:
        s = summer["style"][style]
        o = other["style"][style]
        lines.append(f"| {style} 勝率(N) | {s['win_pct']}%({s['n']}) | {o['win_pct']}%({o['n']}) |")

    if summer["rpci_avg"] is not None and other["rpci_avg"] is not None:
        lines.append(f"| 平均RPCI | {summer['rpci_avg']} | {other['rpci_avg']} |")

    if summer["time_by_cond"].get("良") and other["time_by_cond"].get("良"):
        lines.append(
            f"| 良馬場・平均勝ち時計 | {summer['time_by_cond']['良']['avg_time']}秒 "
            f"| {other['time_by_cond']['良']['avg_time']}秒 |"
        )

    return "\n".join(lines), {
        "surface": surface, "distance": distance, "variant": variant,
        "n_races_all": overall["n_races"], "n_races_summer": summer["n_races"],
        "rpci_all": overall["rpci_avg"], "rpci_summer": summer["rpci_avg"], "rpci_other": other["rpci_avg"],
    }


def main():
    ap = argparse.ArgumentParser(description="中京コース別攻略データを集計する（夏競馬フォーカス）")
    ap.add_argument("--db", type=Path, default=Path("chukyo.db"))
    ap.add_argument("--out-md", type=Path, default=Path("chukyo_report.md"))
    ap.add_argument("--out-csv", type=Path, default=Path("chukyo_stats.csv"))
    args = ap.parse_args()

    df = load(args.db)
    df = enrich(df)

    courses = (
        df[["surface", "distance", "course_variant"]]
        .drop_duplicates()
        .sort_values(["surface", "distance", "course_variant"])
        .values.tolist()
    )

    md_parts = ["# 中京競馬 コース別攻略データベース（夏競馬フォーカス）\n",
                "対象: 2016〜2025年の中京開催データ（芝1200/1400/1600/2000/2200m、ダート1200/1400/1800/1900m）\n",
                "「夏(6-8月)」と「それ以外の季節」を比較し、夏競馬特有の傾向を確認できるようにしています。\n"]
    summary_rows = []
    for surface, distance, variant in courses:
        section, summary = build_course_section(surface, distance, variant if pd.notna(variant) else None, df)
        md_parts.append(section)
        summary_rows.append(summary)

    Path(args.out_md).write_text("\n".join(md_parts), encoding="utf-8")
    pd.DataFrame(summary_rows).to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    print(f"レポート出力: {args.out_md}")
    print(f"CSV出力: {args.out_csv}")


if __name__ == "__main__":
    main()
