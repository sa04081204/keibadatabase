# -*- coding: utf-8 -*-
"""
netkeibaの出馬表(race_id)を取得し、data/courses.json・data/rankings/*.json の
実データ（枠順/脚質/騎手/調教師の勝率・回収率）と掛け合わせて、
中京コース攻略ボードの流儀に沿った「ルールベースAI予想」を作るスクリプト。

【設計方針】
- 機械学習ではなくルールベースにしている。中京はコース別に見るとサンプル数が
  数百件程度で、モデルを学習させるとすぐ過学習するため。courses.json自体が
  「勝率・回収率が高い条件」を集計したものなので、それをそのままスコアの
  重みとして使うのが素直で説明可能。
- 出馬表の時点でわかる情報（枠番・性齢・斤量・騎手・調教師）に加えて、
  取得できれば前走情報（間隔・距離変化）も使う。前走情報は取得できない
  場合は自動的にその項目のスコアを0扱い（無視）にして続行する。

使い方:
    pip install requests beautifulsoup4 --break-system-packages
    python tools/predict_race.py 202607020101
    python tools/predict_race.py 202607020101 --variant B   # 芝コースでBコース施行と分かっている場合
    python tools/predict_race.py 202607020101 --out-dir data --no-past

出力:
    data/predictions/<race_id>.json   … サイト表示用の予想結果
    data/predictions/index.json       … 予想済みレースの一覧（サイトのプルダウン用）
    標準出力にも順位表を表示する
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("requests / beautifulsoup4 が必要です。\n"
          "  pip install requests beautifulsoup4 --break-system-packages\n"
          "を実行してから再度お試しください。", file=sys.stderr)
    sys.exit(1)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
JOCKEY_MARKS = "★☆◇▲△"  # 見習い減量マークなど、騎手名の前に付くことがある記号

SURFACE_SLUG = {"芝": "shiba", "ダ": "da", "ダート": "da"}

# ---------------------------------------------------------------------------
# スコアの重み。0を大きくするほどその要素を重視する。
# courses.jsonのどのブロックに対応するかをコメントで示す。
# ---------------------------------------------------------------------------
WEIGHTS = {
    "post_zone": 18,     # 馬番ゾーン(内/中/外)の勝率
    "style_hint": 10,    # 前走の脚質推定 × このコースの脚質別勝率（推定できた場合のみ）
    "sex": 6,            # 性別の勝率
    "age": 6,            # 年齢の勝率
    "jockey": 30,        # 騎手のコース別勝率・回収率
    "trainer": 18,       # 調教師のコース別勝率・回収率
    "interval": 6,       # 間隔（休み明けかどうか）
    "distance_change": 6,  # 前走からの距離変化
    "sire": 8,           # 種牡馬のコース別勝率・回収率(新規)
    "prev_agari": 8,     # 前走の上がり3Fタイム(3分位バケット) × このコースの上がり3F傾向(新規、リークなし)
}
# 注: sire/prev_agariを足したことで合計が100→118になっている。
# 既存8項目の相対的な重みは自動的に少し下がる(スコア計算はtotal_weightで正規化されるため)。
# 全体の重み再配分(実測ベースへの見直し)はまだ未着手・別途要判断。


# ---------------------------------------------------------------------------
# ネット取得
# ---------------------------------------------------------------------------
def fetch_html(url):
    res = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    res.encoding = res.apparent_encoding or "euc-jp"
    res.raise_for_status()
    return res.text


def find_shutuba_table(soup):
    """馬番/馬名/騎手などの見出しを含むtableを探す。class名の変更に強くするため
    厳密なclass指定ではなく見出しテキストで判定する。"""
    for table in soup.find_all("table"):
        header_text = table.get_text()
        if "馬番" in header_text and "馬名" in header_text and "騎手" in header_text:
            return table
    return None


def clean_text(td):
    return unicodedata.normalize("NFKC", td.get_text(strip=True)) if td else ""


def strip_jockey_marks(name):
    return name.lstrip(JOCKEY_MARKS).strip()


def parse_race_meta(soup, page_text):
    """レース名・距離・馬場・発走時刻などをページ全体のテキストから正規表現で拾う。"""
    meta = {}
    title_tag = soup.find("h1") or soup.find(class_=re.compile("RaceName"))
    if title_tag:
        meta["race_name"] = clean_text(title_tag)

    # 例: "ダ1200m (左)" や "芝1600m (外)" のようなパターンを探す
    m = re.search(r"(芝|ダ)(\d{3,4})m\s*[\(（]?([^\)）\s]*)", page_text)
    if m:
        meta["surface"] = "芝" if m.group(1) == "芝" else "ダ"
        meta["distance"] = int(m.group(2))
        meta["course_note"] = m.group(3)
    else:
        meta["surface"] = None
        meta["distance"] = None
        meta["course_note"] = ""

    m2 = re.search(r"(\d+)回\s*中京\s*(\d+)日目", page_text)
    if m2:
        meta["kaisai_kai"] = int(m2.group(1))
        meta["kaisai_day"] = int(m2.group(2))

    m3 = re.search(r"(\d{1,2}:\d{2})発走", page_text)
    if m3:
        meta["start_time"] = m3.group(1)

    return meta


def fetch_shutuba(race_id):
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    page_text = re.sub(r"\s+", "", soup.get_text())
    meta = parse_race_meta(soup, soup.get_text())

    table = find_shutuba_table(soup)
    if table is None:
        raise RuntimeError("出馬表テーブルが見つかりませんでした。netkeibaのHTML構造が変わっている可能性があります。")

    horses = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        row_text = clean_text(tr)
        if "馬番" in row_text or not row_text:
            continue

        # 馬名リンク(db.netkeiba.com/horse/xxxx)を含むtdを探す
        horse_link = tr.find("a", href=re.compile(r"/horse/\d+"))
        if not horse_link:
            continue
        horse_name = clean_text(horse_link)
        horse_id_m = re.search(r"/horse/(\d+)", horse_link["href"])
        horse_id = horse_id_m.group(1) if horse_id_m else None

        jockey_link = tr.find("a", href=re.compile(r"/jockey/"))
        jockey = strip_jockey_marks(clean_text(jockey_link)) if jockey_link else ""

        trainer_link = tr.find("a", href=re.compile(r"/trainer/"))
        trainer_raw = clean_text(trainer_link) if trainer_link else ""

        # 性齢・斤量は数字/性別の文字を含む短いtdから拾う
        sex, age, kinryo = None, None, None
        for td in tds:
            t = clean_text(td)
            m = re.match(r"^(牡|牝|セ)(\d{1,2})$", t)
            if m:
                sex, age = m.group(1), int(m.group(2))
            elif re.match(r"^\d{2}\.\d$", t) and kinryo is None:
                kinryo = float(t)

        # 枠番・馬番は先頭2つの数字tdであることが多い
        waku, umaban = None, None
        nums = []
        for td in tds[:3]:
            t = clean_text(td)
            if re.match(r"^\d{1,2}$", t):
                nums.append(int(t))
        if len(nums) >= 2:
            waku, umaban = nums[0], nums[1]
        elif len(nums) == 1:
            umaban = nums[0]

        horses.append({
            "horse_id": horse_id,
            "horse_name": horse_name,
            "waku": waku,
            "umaban": umaban,
            "sex": sex,
            "age": age,
            "kinryo": kinryo,
            "jockey": jockey,
            "trainer": trainer_raw,
        })

    if not horses:
        raise RuntimeError("出走馬を1頭も取得できませんでした。race_idを確認してください。")

    return meta, horses


PAST_RUNNING_STYLE_MAP = [
    (range(1, 3), "逃げ"),
    (range(3, 6), "先行"),
    (range(6, 9), "差し"),
]


def guess_style_from_corner(corner_pos, field_size):
    """4角通過順位から脚質を大まかに推定する（簡易版）。取得できなければNone。"""
    if corner_pos is None or field_size is None or field_size <= 0:
        return None
    ratio = corner_pos / field_size
    if ratio <= 0.2:
        return "逃げ"
    if ratio <= 0.45:
        return "先行"
    if ratio <= 0.75:
        return "差し"
    return "追込"


def parse_corner_and_field_size(text_cells):
    """通過欄("2-2-3-4"のような文字列)から最終コーナーの通過順位を、
    頭数っぽい単独の整数セルから頭数を推測する（ベストエフォート・簡易版）。
    ※netkeibaのHTML構造は要確認・要検証（このサンドボックスからは
    db.netkeiba.comへ実際にアクセスして確認できないため、実レースIDで
    一度動作確認してから使うことを推奨）。
    """
    corner_pos, field_size = None, None
    corner_cell = next((c for c in text_cells if re.match(r"^\d{1,2}(-\d{1,2}){1,3}$", c)), None)
    if corner_cell:
        parts = corner_cell.split("-")
        try:
            corner_pos = int(parts[-1])  # 最後の数字=最終コーナー通過順位
        except ValueError:
            corner_pos = None
    # 頭数: 2桁までの単独整数セルのうち、通過欄の各数字より大きいものを候補にする
    int_cells = [int(c) for c in text_cells if re.match(r"^\d{1,2}$", c)]
    if int_cells and corner_pos is not None:
        candidates = [v for v in int_cells if v >= corner_pos]
        if candidates:
            field_size = max(candidates)
    return corner_pos, field_size


def parse_last3f(text_cells):
    """上がり3Fタイム(前走時点で既に分かっている値。33.0〜45.9秒くらいのレンジで
    斤量やオッズと混同しないようにする簡易ヒューリスティック)を拾う。"""
    for c in text_cells:
        if re.match(r"^(3[3-9]|4[0-5])\.\d$", c):
            try:
                return float(c)
            except ValueError:
                pass
    return None


def fetch_sire(soup):
    """血統表(class名に'blood'を含むtable)の中で最初に出てくる馬リンクを父(種牡馬)とみなす。
    ※これも簡易ヒューリスティック。netkeibaのHTML構造変更やレイアウト差異で
    取れないことがあるが、その場合はNoneを返し、呼び出し側でsire評価をスキップするだけなので安全。"""
    table = soup.find("table", class_=re.compile("blood", re.IGNORECASE))
    if table is None:
        return None
    link = table.find("a", href=re.compile(r"/horse/"))
    return clean_text(link) if link else None


def fetch_horse_hint(horse_id):
    """馬ごとのベストエフォート取得: ①直近1走の距離・日付・4角通過順・頭数・上がり3F、
    ②血統表からの父(種牡馬)名。取得できない・パースできない項目はNoneのまま返し、
    呼び出し側でその項目だけスコア計算をスキップする(エラーにはしない)。
    """
    try:
        url = f"https://db.netkeiba.com/horse/{horse_id}/"
        html = fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        result = {"sire": fetch_sire(soup)}

        table = soup.find("table", class_=re.compile("db_h_race"))
        if table is None:
            return result
        rows = table.find_all("tr")[1:2]  # 直近1走のみ使う（簡易版）
        if not rows:
            return result
        tds = rows[0].find_all("td")
        text_cells = [clean_text(td) for td in tds]
        result["raw"] = text_cells

        # 日付(先頭セル想定)
        date_cell = next((c for c in text_cells if re.match(r"\d{4}/\d{2}/\d{2}", c)), None)
        if date_cell:
            result["date"] = date_cell

        # 距離: "ダ1200" のようなセル
        dist_cell = next((c for c in text_cells if re.match(r"^(芝|ダ)\d{3,4}", c)), None)
        if dist_cell:
            dm = re.match(r"^(芝|ダ)(\d{3,4})", dist_cell)
            result["surface"] = dm.group(1)
            result["distance"] = int(dm.group(2))

        corner_pos, field_size = parse_corner_and_field_size(text_cells)
        result["corner_pos"] = corner_pos
        result["field_size"] = field_size
        result["last3f"] = parse_last3f(text_cells)

        return result
    except Exception:
        return None


# ---------------------------------------------------------------------------
# コースデータの読み込み・マッチング
# ---------------------------------------------------------------------------
def course_key(surface, distance, variant):
    v = f"_{variant}" if variant else ""
    return f"{SURFACE_SLUG.get(surface, surface)}{distance}{v}"


def load_course(data_dir, surface, distance, variant):
    courses = json.loads((data_dir / "courses.json").read_text(encoding="utf-8"))["courses"]
    by_key = {c["key"]: c for c in courses}

    if surface == "芝" and variant is None:
        # A/Bどちらか不明な場合は両方あればサンプル数で加重平均したものを作る
        ka, kb = course_key(surface, distance, "A"), course_key(surface, distance, "B")
        ca, cb = by_key.get(ka), by_key.get(kb)
        if ca and cb:
            return blend_courses(ca, cb), f"{ka}+{kb}(加重平均・要確認)"
        key = ka if ca else kb
        c = by_key.get(key)
        return c, key

    key = course_key(surface, distance, variant)
    return by_key.get(key), key


def blend_courses(ca, cb):
    """variant不明時にA/Bコースをnで加重平均する簡易ブレンド。"""
    def blend_dict(da, db):
        out = {}
        keys = set(da) | set(db)
        for k in keys:
            va, vb = da.get(k), db.get(k)
            if va and vb and va.get("n") is not None and vb.get("n") is not None:
                n = va["n"] + vb["n"]
                if n == 0:
                    out[k] = va
                    continue
                blended = {"n": n}
                for field in ("win_pct", "place_pct", "win_roi", "place_roi"):
                    if va.get(field) is not None and vb.get(field) is not None:
                        blended[field] = round((va[field] * va["n"] + vb[field] * vb["n"]) / n, 1)
                out[k] = blended
            else:
                out[k] = va or vb
        return out

    bins_a, bins_b = ca.get("prev_agari_bins"), cb.get("prev_agari_bins")
    if bins_a and bins_b:
        na, nb = ca["n_races"], cb["n_races"]
        blended_bins = [round((a * na + b * nb) / (na + nb), 2) for a, b in zip(bins_a, bins_b)]
    else:
        blended_bins = bins_a or bins_b

    return {
        "key": f"{ca['key']}+{cb['key']}",
        "label": ca["label"].replace("(A)", "") + "(A/B加重平均)",
        "surface": ca["surface"],
        "distance": ca["distance"],
        "n_races": ca["n_races"] + cb["n_races"],
        "post_zone": blend_dict(ca["post_zone"], cb["post_zone"]),
        "style": blend_dict(ca["style"], cb["style"]),
        "sex": blend_dict(ca["sex"], cb["sex"]),
        "age": blend_dict(ca["age"], cb["age"]),
        "interval": blend_dict(ca["interval"], cb["interval"]),
        "distance_change": blend_dict(ca["distance_change"], cb["distance_change"]),
        "prev_agari": blend_dict(ca.get("prev_agari", {}), cb.get("prev_agari", {})),
        "prev_agari_bins": blended_bins,
    }


def load_ranking(data_dir, key):
    # keyが "shiba1400_A+shiba1400_B(...)" のようなブレンド表記の場合は先頭要素で代表させる
    real_key = key.split("+")[0].split("(")[0]
    path = data_dir / "rankings" / f"{real_key}.json"
    if not path.exists():
        path = data_dir / "rankings" / "ALL.json"
    return json.loads(path.read_text(encoding="utf-8"))


def zone_for_umaban(umaban, field_size):
    """courses.jsonのpost_zoneは「内=下位1/3, 中=中位1/3, 外=上位1/3」という定義。"""
    if umaban is None or not field_size:
        return None
    ratio = (umaban - 1) / max(field_size - 1, 1)
    if ratio <= 1 / 3:
        return "内"
    if ratio <= 2 / 3:
        return "中"
    return "外"


def find_ranking_entry(name_surname, entries):
    """出馬表の姓（+見習いマーク除去済み）から、rankings側のフルネームを探す。
    完全一致 → 部分一致(startswith/末尾一致) の順で探し、複数該当時はnが多い方を採用。"""
    if not name_surname:
        return None
    exact = [e for e in entries if e["name"] == name_surname]
    if exact:
        return exact[0]
    candidates = [e for e in entries if name_surname in e["name"] or e["name"] in name_surname]
    if not candidates:
        candidates = [e for e in entries if e["name"].startswith(name_surname[:2])]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.get("n", 0), reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# スコアリング
# ---------------------------------------------------------------------------
def pct_score(dict_, key_, min_n=5):
    """dict_[key_]のwin_pctを0-100スコアとして返す。サンプル不足ならNone。"""
    entry = (dict_ or {}).get(key_)
    if not entry or entry.get("n", 0) < min_n or entry.get("win_pct") is None:
        return None
    return entry["win_pct"]


PREV_AGARI_CATEGORIES = ["上がり速いタイプ(上位33%)", "平均的", "上がり遅いタイプ(下位33%)"]


def bucket_prev_agari(last3f, bins):
    """courses.jsonに保存されたbin境界値(analyze_chukyo.pyのprev_agari_bucket_and_binsと
    同じ基準)を使って、新しい馬の前走上がり3Fタイムを3分位カテゴリに分類する。
    binsが無い(そのコースはサンプル不足で分位点を作れなかった)場合はNoneを返す。"""
    if last3f is None or not bins or len(bins) != 4:
        return None
    if last3f <= bins[1]:
        return PREV_AGARI_CATEGORIES[0]
    if last3f <= bins[2]:
        return PREV_AGARI_CATEGORIES[1]
    return PREV_AGARI_CATEGORIES[2]


def rank_normalize(raw_scores):
    """{horse_index: raw_score(None可)} を 0-100の相対スコアへ変換する。
    Noneは「中央値」扱いにして、情報が無い項目のせいで極端に順位が振れないようにする。"""
    vals = [v for v in raw_scores.values() if v is not None]
    if not vals:
        return {k: 50.0 for k in raw_scores}
    lo, hi = min(vals), max(vals)
    med = sorted(vals)[len(vals) // 2]
    out = {}
    for k, v in raw_scores.items():
        if v is None:
            out[k] = 50.0
            continue
        if hi == lo:
            out[k] = 50.0
        else:
            out[k] = round((v - lo) / (hi - lo) * 100, 1)
    return out


def build_prediction(meta, horses, course, ranking, past_hints):
    field_size = len(horses)
    factor_raw = {f: {} for f in WEIGHTS}
    reasoning = {i: [] for i in range(field_size)}

    for i, h in enumerate(horses):
        zone = zone_for_umaban(h["umaban"], field_size)
        v = pct_score(course.get("post_zone"), zone) if course else None
        factor_raw["post_zone"][i] = v
        if v is not None:
            reasoning[i].append(f"馬番ゾーン「{zone}」の当コース勝率 {v}%")

        hint = past_hints.get(h["horse_id"])
        style_guess = None
        if hint and hint.get("corner_pos") is not None:
            style_guess = guess_style_from_corner(hint["corner_pos"], hint.get("field_size"))
        v = pct_score(course.get("style"), style_guess) if (course and style_guess) else None
        factor_raw["style_hint"][i] = v
        if v is not None:
            reasoning[i].append(f"前走の脚質傾向「{style_guess}」の当コース勝率 {v}%")

        v = pct_score(course.get("sex"), h["sex"]) if course else None
        factor_raw["sex"][i] = v

        age_band = None
        if h["age"] is not None:
            age_band = "2歳" if h["age"] == 2 else "3歳" if h["age"] == 3 else "4歳" if h["age"] == 4 else "5歳以上"
        v = pct_score(course.get("age"), age_band) if course else None
        factor_raw["age"][i] = v

        jockey_entry = find_ranking_entry(h["jockey"], ranking.get("jockey", []))
        v = None
        if jockey_entry and jockey_entry.get("n", 0) >= 5:
            v = jockey_entry["win_pct"]
            reasoning[i].append(
                f"騎手{jockey_entry['name']}は当コース勝率{jockey_entry['win_pct']}%"
                f"（複勝率{jockey_entry['place_pct']}%, 単勝回収率{jockey_entry['win_roi']}%, N={jockey_entry['n']}）"
            )
        factor_raw["jockey"][i] = v

        trainer_surname = re.sub(r"^(栗東|美浦)", "", h["trainer"]).strip()
        trainer_entry = find_ranking_entry(trainer_surname, ranking.get("trainer", []))
        v = None
        if trainer_entry and trainer_entry.get("n", 0) >= 5:
            v = trainer_entry["win_pct"]
            reasoning[i].append(
                f"調教師{trainer_entry['name']}は当コース勝率{trainer_entry['win_pct']}%"
                f"（単勝回収率{trainer_entry['win_roi']}%, N={trainer_entry['n']}）"
            )
        factor_raw["trainer"][i] = v

        interval_band, dist_change_band = None, None
        if hint and hint.get("date"):
            try:
                last_date = datetime.strptime(hint["date"], "%Y/%m/%d")
                weeks = (datetime.now() - last_date).days / 7
                if weeks <= 2:
                    interval_band = "中1-2週"
                elif weeks <= 5:
                    interval_band = "中3-5週"
                elif weeks <= 9:
                    interval_band = "中6-9週"
                else:
                    interval_band = "中10週以上(休み明け)"
            except ValueError:
                pass
        v = pct_score(course.get("interval"), interval_band) if course else None
        factor_raw["interval"][i] = v

        if hint and hint.get("distance") and meta.get("distance"):
            diff = meta["distance"] - hint["distance"]
            if diff >= 201:
                dist_change_band = "延長(201m以上)"
            elif diff >= 1:
                dist_change_band = "延長(1-200m)"
            elif diff == 0:
                dist_change_band = "同距離"
            elif diff >= -200:
                dist_change_band = "短縮(1-200m)"
            else:
                dist_change_band = "短縮(201m以上)"
        v = pct_score(course.get("distance_change"), dist_change_band) if course else None
        factor_raw["distance_change"][i] = v

        sire_name = hint.get("sire") if hint else None
        sire_entry = find_ranking_entry(sire_name, ranking.get("sire", [])) if sire_name else None
        v = None
        if sire_entry and sire_entry.get("n", 0) >= 5:
            v = sire_entry["win_pct"]
            reasoning[i].append(
                f"種牡馬{sire_entry['name']}は当コース勝率{sire_entry['win_pct']}%"
                f"（単勝回収率{sire_entry['win_roi']}%, N={sire_entry['n']}）"
            )
        factor_raw["sire"][i] = v

        prev_last3f = hint.get("last3f") if hint else None
        agari_band = bucket_prev_agari(prev_last3f, course.get("prev_agari_bins")) if course else None
        v = pct_score(course.get("prev_agari"), agari_band) if (course and agari_band) else None
        factor_raw["prev_agari"][i] = v
        if v is not None:
            reasoning[i].append(f"前走の上がり3F傾向「{agari_band}」の当コース勝率 {v}%")

    factor_norm = {f: rank_normalize(raw) for f, raw in factor_raw.items()}

    total_weight = sum(WEIGHTS.values())
    results = []
    for i, h in enumerate(horses):
        score = sum(factor_norm[f][i] * WEIGHTS[f] for f in WEIGHTS) / total_weight
        results.append({
            "umaban": h["umaban"],
            "waku": h["waku"],
            "horse_name": h["horse_name"],
            "sex": h["sex"],
            "age": h["age"],
            "kinryo": h["kinryo"],
            "jockey": h["jockey"],
            "trainer": h["trainer"],
            "score": round(score, 1),
            "factor_scores": {f: factor_norm[f][i] for f in WEIGHTS},
            "reasoning": reasoning[i][:5],
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    for rank, r in enumerate(results, start=1):
        r["rank"] = rank
    return results


# ---------------------------------------------------------------------------
# 1レース分の処理（バッチ実行でも使い回せるように関数化）
# ---------------------------------------------------------------------------
def process_one_race(race_id, data_dir, variant, no_past):
    """1レース分を取得〜スコア計算〜保存まで行う。成功時はoutput dictを返す。"""
    out_dir = data_dir / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] netkeiba 出馬表を取得中... race_id={race_id}")
    meta, horses = fetch_shutuba(race_id)
    print(f"      レース: {meta.get('race_name')} / {meta.get('surface')}{meta.get('distance')}m / {len(horses)}頭")

    if meta.get("surface") is None or meta.get("distance") is None:
        raise RuntimeError("コース情報(芝/ダ・距離)を取得できませんでした。中京以外のレースの可能性があります。")

    past_hints = {}
    if not no_past:
        print("[2/4] 前走情報を取得中(馬ごと)... ※対応表がない場合はスキップされます")
        for h in horses:
            if h["horse_id"]:
                hint = fetch_horse_hint(h["horse_id"])
                if hint:
                    past_hints[h["horse_id"]] = hint
                time.sleep(0.4)  # db.netkeiba.comへの連続アクセスを避ける
    else:
        print("[2/4] --no-past 指定のため前走情報の取得をスキップ")

    print("[3/4] コースデータと照合してスコア計算中...")
    course, key = load_course(data_dir, meta["surface"], meta["distance"], variant)
    if course is None:
        raise RuntimeError(f"コースデータが見つかりません(key={key})。data/courses.jsonの中身を確認してください。")
    ranking = load_ranking(data_dir, key)

    results = build_prediction(meta, horses, course, ranking, past_hints)

    print("[4/4] 結果を保存中...")
    output = {
        "race_id": race_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "race_name": meta.get("race_name"),
        "surface": meta.get("surface"),
        "distance": meta.get("distance"),
        "course_key": key,
        "start_time": meta.get("start_time"),
        "field_size": len(horses),
        "disclaimer": "過去データの傾向をルールベースで点数化した参考情報です。実際の着順を保証するものではありません。",
        "results": results,
    }
    (out_dir / f"{race_id}.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== {meta.get('race_name')} ({meta.get('surface')}{meta.get('distance')}m) AI予想スコア ===")
    print(f"{'順位':>4} {'馬番':>4} {'馬名':<14} {'騎手':<8} {'score':>6}")
    for r in results:
        print(f"{r['rank']:>4} {r['umaban'] or '-':>4} {r['horse_name']:<14} {r['jockey']:<8} {r['score']:>6}")
    print()
    return output


def update_index(data_dir, outputs):
    """複数レース分のoutputをまとめてdata/predictions/index.jsonへ反映する。"""
    out_dir = data_dir / "predictions"
    index_path = out_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"races": []}
    done_ids = {o["race_id"] for o in outputs}
    index["races"] = [r for r in index["races"] if r["race_id"] not in done_ids]
    for o in outputs:
        index["races"].append({
            "race_id": o["race_id"],
            "race_name": o.get("race_name"),
            "surface": o.get("surface"),
            "distance": o.get("distance"),
            "start_time": o.get("start_time"),
            "generated_at": o.get("generated_at"),
        })
    index["races"].sort(key=lambda r: r["race_id"])
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_race_numbers(spec):
    """'1-12' や '1,3,5' や '1-5,9,11-12' を [1,2,...] のリストに展開する。"""
    nums = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            nums.extend(range(int(a), int(b) + 1))
        else:
            nums.append(int(part))
    return nums


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="netkeiba出馬表 × 中京コースデータ でルールベースAI予想を作る")
    ap.add_argument("race_ids", nargs="*", help="race_id（複数指定可）例: 202607020101 202607020102 ...")
    ap.add_argument("--day", help="開催日コード(先頭10桁 例:2026070201)を指定すると--racesで指定したR数を一括処理する")
    ap.add_argument("--races", default="1-12", help="--day指定時に処理するレース番号 例:1-12 / 1,3,5 / 1-6,9 (既定:1-12)")
    ap.add_argument("--data-dir", default="data", help="courses.json/rankingsのあるディレクトリ (既定: data)")
    ap.add_argument("--variant", choices=["A", "B"], default=None, help="芝コースのA/Bが分かっている場合に指定")
    ap.add_argument("--no-past", action="store_true", help="前走情報の取得をスキップ（速いが精度は落ちる）")
    args = ap.parse_args()

    # 処理対象のrace_idリストを組み立てる
    race_id_list = list(args.race_ids)
    if args.day:
        for n in parse_race_numbers(args.races):
            race_id_list.append(f"{args.day}{n:02d}")

    if not race_id_list:
        print("エラー: race_idを1つ以上指定するか、--day で開催日を指定してください。\n"
              "  例1) python tools/predict_race.py 202607020101\n"
              "  例2) python tools/predict_race.py --day 2026070201 --races 1-12", file=sys.stderr)
        sys.exit(1)

    data_dir = Path(args.data_dir)
    outputs = []
    failed = []
    is_batch = len(race_id_list) > 1

    for idx, race_id in enumerate(race_id_list):
        print(f"\n########## ({idx + 1}/{len(race_id_list)}) race_id={race_id} ##########")
        try:
            output = process_one_race(race_id, data_dir, args.variant, args.no_past)
            outputs.append(output)
        except Exception as e:  # 1レース失敗しても他のレースは続行する
            print(f"エラー: race_id={race_id} の処理に失敗しました: {e}", file=sys.stderr)
            failed.append((race_id, str(e)))
        if is_batch and idx < len(race_id_list) - 1:
            time.sleep(1.5)  # netkeibaへの連続アクセスを避けるインターバル

    if outputs:
        update_index(data_dir, outputs)

    print("\n================ 完了 ================")
    print(f"成功: {len(outputs)}件 / 失敗: {len(failed)}件")
    if failed:
        print("失敗したrace_id:")
        for rid, err in failed:
            print(f"  - {rid}: {err}")
    if outputs:
        print(f"\n保存先: {data_dir / 'predictions'}/ （各race_id.json + index.json）")
        print("サイトに反映するには data/predictions を git add / commit / push してください。")


if __name__ == "__main__":
    main()
