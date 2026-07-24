-- 中京競馬 攻略データベース スキーマ（TARGET実データ形式対応）

CREATE TABLE IF NOT EXISTS entries (
    race_id         TEXT,        -- date + kaisai + race_no で一意化
    date            TEXT,        -- YYYY-MM-DD
    year            INTEGER,
    month           INTEGER,
    is_summer       INTEGER,     -- 6,7,8月なら1 (夏競馬フラグ)
    kaisai          TEXT,        -- 開催コード(例: 3名8)
    race_no         INTEGER,
    race_name       TEXT,
    horse_name      TEXT,
    sire            TEXT,        -- 父馬名（種牡馬）
    dam             TEXT,        -- 母馬名
    dam_sire        TEXT,        -- 母の父馬名(母父)
    corner1         INTEGER,     -- 1角 通過順位(前走距離.csv等から取得)
    interval_weeks  INTEGER,     -- 前走からの間隔(週)
    prev_date       TEXT,        -- 前走の日付
    prev_venue      TEXT,        -- 前走の競馬場
    prev_surface    TEXT,        -- 前走 芝/ダート
    prev_distance   INTEGER,     -- 前走の距離
    prev_track_condition TEXT,   -- 前走の馬場状態
    prev_class_name TEXT,        -- 前走のレース名(クラス相当)
    prev_finish_pos INTEGER,     -- 前走の着順
    prev_popularity INTEGER,     -- 前走の人気
    prev_corner4    INTEGER,     -- 前走4角の通過順位
    prev_last3f     REAL,        -- 前走の上がり3F
    prev_time_sec   REAL,        -- 前走の走破タイム(秒)
    prev_odds       REAL,        -- 前走の単勝オッズ
    sex             TEXT,
    age             INTEGER,
    jockey          TEXT,
    weight_carried  REAL,
    field_size      INTEGER,     -- 頭数
    umaban          INTEGER,     -- 馬番
    post_zone       TEXT,        -- 内/中/外 (馬番/頭数の比率による簡易区分)
    popularity      INTEGER,
    finish_pos      INTEGER,     -- 数値着順（中止/除外/取消はNULL）
    finish_status   TEXT,        -- 完了/中止/除外/取消
    surface         TEXT,        -- 芝/ダート
    distance        INTEGER,
    course_variant  TEXT,        -- A/B/C (芝の周回コース区分、ダートは空)
    track_condition TEXT,        -- 良/稍重/重/不良
    prize           REAL,
    stable_area     TEXT,        -- 栗東/美浦
    trainer         TEXT,
    time_sec        REAL,        -- 走破タイム(秒)
    margin          TEXT,        -- 着差
    corner2         INTEGER,
    corner3         INTEGER,
    corner4         INTEGER,
    last3f          REAL,        -- 上り3F
    pci             REAL,
    pci3            REAL,
    rpci            REAL,
    last3f_diff     REAL,        -- 上3F地点差
    body_weight     INTEGER,
    body_weight_diff INTEGER,
    blinker         TEXT,
    tansho_payout   REAL,        -- 単勝配当（実際に配当があった場合のみ数値、参考オッズは括弧付きなので除外）
    fukusho_payout  REAL,
    PRIMARY KEY (race_id, umaban)
);

CREATE INDEX IF NOT EXISTS idx_entries_course
    ON entries(surface, distance, course_variant);

CREATE INDEX IF NOT EXISTS idx_entries_summer
    ON entries(is_summer);
