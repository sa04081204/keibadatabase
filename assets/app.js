(() => {
  "use strict";

  const DATA_VERSION = "v3"; // データ更新のたびに数字を上げるとキャッシュを確実に回避できる

  const state = {
    courses: [],
    surfaceFilter: "all",
    keyword: "",
    entriesCache: {},   // key -> {columns, rows}
    searchRows: [],      // 現在のコースの明細(オブジェクト配列に変換済み)
    searchFiltered: [],
    page: 0,
    pageSize: 30,
    rankingCache: {},    // key -> {jockey:[...], trainer:[...]}
  };

  const el = (sel) => document.querySelector(sel);
  const els = (sel) => Array.from(document.querySelectorAll(sel));

  // ---------------- 初期化 ----------------
  async function init() {
    const res = await fetch(`data/courses.json?${DATA_VERSION}`);
    const data = await res.json();
    state.courses = data.courses;

    renderCourseList();
    buildCourseSelect();
    buildRankingCourseSelect();
    startTicker();
    bindTabs();
    bindFilters();
    bindSearchControls();
    bindRankingControls();
  }

  // ---------------- タブ切替 ----------------
  function bindTabs() {
    els(".tabbar button").forEach((btn) => {
      btn.addEventListener("click", () => {
        els(".tabbar button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const tab = btn.dataset.tab;
        ["overview", "ranking", "search"].forEach((t) => {
          el(`#tab-${t}`).hidden = t !== tab;
        });
      });
    });
  }

  // ---------------- ヒーローのティッカー ----------------
  function startTicker() {
    const facts = [];
    state.courses.forEach((c) => {
      c.takeaway.forEach((t) => facts.push(`<b>${c.label}</b> ${t}`));
    });
    if (!facts.length) return;
    let i = 0;
    const box = el("#ticker");
    const show = () => {
      box.innerHTML = `<span class="ticker-inner"><span class="dot"></span>${facts[i]}</span>`;
      i = (i + 1) % facts.length;
    };
    show();
    setInterval(show, 4200);
  }

  // ---------------- コース一覧 ----------------
  function bindFilters() {
    els(".chip-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        els(".chip-toggle").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        state.surfaceFilter = btn.dataset.surface;
        renderCourseList();
      });
    });
    el("#course-search").addEventListener("input", (e) => {
      state.keyword = e.target.value.trim();
      renderCourseList();
    });
  }

  function paceBadge(c) {
    const r = c.rpci.overall;
    if (r == null) return "";
    if (r >= 51) return `<span class="badge pace-slow">RPCI ${r} スロー寄り</span>`;
    if (r <= 49) return `<span class="badge pace-fast">RPCI ${r} ハイペース寄り</span>`;
    return `<span class="badge">RPCI ${r} 標準</span>`;
  }

  function bestBadge(dict, labelPrefix) {
    let bestK = null, bestV = null;
    Object.entries(dict).forEach(([k, v]) => {
      if (v.win_pct != null && v.n >= 20 && (bestV == null || v.win_pct > bestV.win_pct)) {
        bestK = k; bestV = v;
      }
    });
    if (!bestK) return "";
    return `<span class="badge">${labelPrefix}${bestK} ${bestV.win_pct}%</span>`;
  }

  function renderCourseList() {
    const wrap = el("#course-list");
    let list = state.courses;
    if (state.surfaceFilter !== "all") {
      list = list.filter((c) => c.surface === state.surfaceFilter);
    }
    if (state.keyword) {
      list = list.filter((c) => c.label.includes(state.keyword) || String(c.distance).includes(state.keyword));
    }
    if (!list.length) {
      wrap.innerHTML = `<div class="empty-note">該当するコースがありません</div>`;
      return;
    }
    wrap.innerHTML = list.map((c) => `
      <div class="course-card surface-${c.surface}" data-key="${c.key}">
        <div class="top-row">
          <h3>${c.label}</h3>
          <span class="n">${c.n_races}R${c.low_sample ? ' <span class="low-sample-flag">(少)</span>' : ""}</span>
        </div>
        <div class="badges">
          ${bestBadge(c.post_zone, "馬番:")}
          ${bestBadge(c.style, "脚質:")}
          ${paceBadge(c)}
        </div>
        <div class="note">${c.takeaway.slice(0, 2).map((t) => `<span>${t}</span>`).join("<br>")}</div>
        <button class="detail-btn" data-key="${c.key}">詳細データを見る</button>
      </div>
    `).join("");

    els(".detail-btn").forEach((btn) => {
      btn.addEventListener("click", () => openModal(btn.dataset.key));
    });
  }

  // ---------------- コース詳細モーダル ----------------
  function rateRows(dict) {
    return Object.entries(dict).map(([k, v]) => `
      <tr><td>${k}</td><td class="num">${v.win_pct ?? "-"}%</td><td>${v.place_pct ?? "-"}%</td><td>${v.win_roi ?? "-"}%</td><td>${v.place_roi ?? "-"}%</td><td>${v.n}</td></tr>
    `).join("");
  }

  function umabanRows(list) {
    return (list || []).map((v) => `
      <tr><td>${v.umaban}番</td><td class="num">${v.win_pct ?? "-"}%</td><td>${v.place_pct ?? "-"}%</td><td>${v.win_roi ?? "-"}%</td><td>${v.n}</td></tr>
    `).join("");
  }

  function openModal(key) {
    const c = state.courses.find((x) => x.key === key);
    if (!c) return;
    const modal = el("#modal");
    const sheet = el("#modal-sheet");

    const timeRows = Object.entries(c.time_by_cond || {}).map(([cond, v]) => `
      <tr><td>${cond}</td><td class="num">${v.avg_time}秒</td><td>${v.n}</td></tr>
    `).join("");

    sheet.innerHTML = `
      <div class="sheet-head">
        <h2>${c.label}</h2>
        <button class="close-btn" id="close-modal">閉じる</button>
      </div>
      <div class="n">全${c.n_races}レース ／ 夏(6-8月) ${c.n_races_summer}レース ${c.low_sample_summer ? '<span class="low-sample-flag">※夏は少サンプル</span>' : ""}</div>

      <div class="section-label">狙える条件まとめ</div>
      <ul class="takeaway-list">${c.takeaway.map((t) => `<li>${t}</li>`).join("")}</ul>

      <div class="section-label">馬番ゾーン別 成績（内=下位1/3, 中=中位1/3, 外=上位1/3）</div>
      <table class="stat-table">
        <tr><th>ゾーン</th><th class="num">勝率</th><th>複勝率</th><th>単勝回収率</th><th>複勝回収率</th><th>N</th></tr>
        ${rateRows(c.post_zone)}
      </table>

      <div class="section-label">脚質別 成績</div>
      <table class="stat-table">
        <tr><th>脚質</th><th class="num">勝率</th><th>複勝率</th><th>単勝回収率</th><th>複勝回収率</th><th>N</th></tr>
        ${rateRows(c.style)}
      </table>

      <div class="section-label">馬番別 成績（実際の馬番ごと）</div>
      <table class="stat-table">
        <tr><th>馬番</th><th class="num">勝率</th><th>複勝率</th><th>単勝回収率</th><th>N</th></tr>
        ${umabanRows(c.umaban)}
      </table>

      <div class="section-label">馬場状態別 勝ち時計</div>
      <table class="stat-table">
        <tr><th>馬場</th><th class="num">平均時計</th><th>N</th></tr>
        ${timeRows}
      </table>

      <div class="section-label">1番人気の信頼度</div>
      <table class="stat-table">
        <tr><td>単勝1番人気 勝率／回収率</td><td class="num">${c.fav_win_pct ?? "-"}% ／ ${c.fav_roi ?? "-"}%</td></tr>
      </table>

      <button class="detail-btn" id="go-search-btn" style="margin-top:6px;">このコースの明細を検索する</button>
      <button class="detail-btn" id="go-ranking-btn" style="margin-top:8px;">このコースの騎手・調教師ランキングを見る</button>
    `;

    el("#close-modal").addEventListener("click", closeModal);
    el("#go-search-btn").addEventListener("click", () => {
      closeModal();
      el('.tabbar button[data-tab="search"]').click();
      el("#sel-course").value = key;
      onCourseSelected(key);
    });
    el("#go-ranking-btn").addEventListener("click", () => {
      closeModal();
      el('.tabbar button[data-tab="ranking"]').click();
      el("#rk-course").value = key;
      loadAndRenderRanking();
    });

    modal.hidden = false;
  }

  function closeModal() {
    el("#modal").hidden = true;
  }
  el("#modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
  });

  // ---------------- ランキングタブ ----------------
  function buildRankingCourseSelect() {
    const sel = el("#rk-course");
    const bySurface = { "芝": [], "ダ": [] };
    state.courses.forEach((c) => bySurface[c.surface].push(c));
    ["芝", "ダ"].forEach((s) => {
      if (!bySurface[s].length) return;
      const og = document.createElement("optgroup");
      og.label = s === "芝" ? "芝コース" : "ダートコース";
      bySurface[s].forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.key;
        opt.textContent = c.label;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
  }

  function bindRankingControls() {
    ["#rk-course", "#rk-type", "#rk-sort", "#rk-minn"].forEach((id) => {
      el(id).addEventListener("change", loadAndRenderRanking);
    });
    loadAndRenderRanking();
  }

  async function loadAndRenderRanking() {
    const key = el("#rk-course").value;
    el("#rk-summary").textContent = "読み込み中…";
    el("#rk-results").innerHTML = "";

    if (!state.rankingCache[key]) {
      const res = await fetch(`data/rankings/${encodeURIComponent(key)}.json?${DATA_VERSION}`);
      state.rankingCache[key] = await res.json();
    }
    renderRanking(state.rankingCache[key]);
  }

  function renderRanking(data) {
    const type = el("#rk-type").value;
    const sortKey = el("#rk-sort").value;
    const minN = parseInt(el("#rk-minn").value, 10);

    let rows = (data[type] || []).filter((r) => r.n >= minN);
    rows.sort((a, b) => (b[sortKey] ?? -Infinity) - (a[sortKey] ?? -Infinity));

    el("#rk-summary").textContent = rows.length ? `${rows.length}名を表示（${minN}走以上）` : "該当するデータがありません";

    const roiClass = (v) => (v == null ? "" : v >= 100 ? "win" : "");
    el("#rk-results").innerHTML = rows.map((r, i) => `
      <div class="entry-row">
        <div class="line1"><span>#${i + 1}</span><span>N=${r.n}</span></div>
        <div class="line2">
          <span class="horse">${r.name}</span>
          <span class="finish ${roiClass(r.win_roi)}">単勝回収率 ${r.win_roi ?? "-"}%</span>
        </div>
        <div class="tags">
          <span class="tag">勝率 ${r.win_pct ?? "-"}%</span>
          <span class="tag">複勝率 ${r.place_pct ?? "-"}%</span>
          <span class="tag">複勝回収率 ${r.place_roi ?? "-"}%</span>
        </div>
      </div>
    `).join("") || `<div class="empty-note">該当するデータがありません</div>`;
  }

  // ---------------- 検索タブ ----------------
  function buildCourseSelect() {
    const sel = el("#sel-course");
    const bySurface = { "芝": [], "ダ": [] };
    state.courses.forEach((c) => bySurface[c.surface].push(c));
    ["芝", "ダ"].forEach((s) => {
      if (!bySurface[s].length) return;
      const og = document.createElement("optgroup");
      og.label = s === "芝" ? "芝コース" : "ダートコース";
      bySurface[s].forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.key;
        opt.textContent = `${c.label} (${c.n_races}R)`;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
  }

  function bindSearchControls() {
    el("#sel-course").addEventListener("change", (e) => onCourseSelected(e.target.value));
    ["#sel-season", "#sel-cond", "#sel-zone", "#sel-style"].forEach((id) => {
      el(id).addEventListener("change", applyFilters);
    });
    el("#sel-keyword").addEventListener("input", applyFilters);
    el("#more-btn").addEventListener("click", () => {
      state.page += 1;
      renderResults(true);
    });
  }

  async function onCourseSelected(key) {
    if (!key) return;
    const resultsBox = el("#search-results");
    el("#search-summary").textContent = "読み込み中…";
    resultsBox.innerHTML = "";
    el("#more-btn").hidden = true;

    if (!state.entriesCache[key]) {
      const res = await fetch(`data/entries/${encodeURIComponent(key)}.json?${DATA_VERSION}`);
      state.entriesCache[key] = await res.json();
    }
    const { columns, rows } = state.entriesCache[key];
    state.searchRows = rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
    applyFilters();
  }

  function applyFilters() {
    const season = el("#sel-season").value;
    const cond = el("#sel-cond").value;
    const zone = el("#sel-zone").value;
    const style = el("#sel-style").value;
    const kw = el("#sel-keyword").value.trim();

    let rows = state.searchRows;
    if (season === "summer") rows = rows.filter((r) => r.summer);
    if (season === "other") rows = rows.filter((r) => !r.summer);
    if (cond !== "all") rows = rows.filter((r) => r.cond === cond);
    if (zone !== "all") rows = rows.filter((r) => r.zone === zone);
    if (style !== "all") rows = rows.filter((r) => r.style === style);
    if (kw) {
      rows = rows.filter((r) =>
        (r.horse && r.horse.includes(kw)) ||
        (r.jockey && r.jockey.includes(kw)) ||
        (r.race_name && r.race_name.includes(kw))
      );
    }
    state.searchFiltered = rows;
    state.page = 0;
    renderResults(false);
  }

  function fmtDate(d) {
    if (!d) return "-";
    const s = String(d);
    if (s.includes("-")) return s.replaceAll("-", "/");
    return `${s.slice(0, 4)}/${s.slice(4, 6)}/${s.slice(6, 8)}`;
  }

  function renderResults(append) {
    const total = state.searchFiltered.length;
    const shown = Math.min(total, (state.page + 1) * state.pageSize);
    el("#search-summary").textContent = total ? `${total}件中 ${shown}件を表示` : "該当するデータがありません";

    const slice = state.searchFiltered.slice(append ? shown - state.pageSize : 0, shown);
    const html = slice.map((r) => `
      <div class="entry-row">
        <div class="line1"><span>${fmtDate(r.date)} ${r.race_no}R ${r.race_name || ""}</span><span>${r.field_size ?? "-"}頭</span></div>
        <div class="line2">
          <span class="horse">${r.horse ?? "-"}</span>
          <span class="finish ${r.finish === 1 ? "win" : ""}">${r.status && r.status !== "完了" ? r.status : (r.finish ?? "-") + "着"}</span>
        </div>
        <div class="tags">
          <span class="tag">${r.jockey ?? "-"}</span>
          <span class="tag">馬番${r.umaban ?? "-"}(${r.zone ?? "-"})</span>
          <span class="tag">${r.style ?? "-"}</span>
          <span class="tag">${r.cond ?? "-"}</span>
          ${r.summer ? '<span class="tag">夏開催</span>' : ""}
        </div>
      </div>
    `).join("");

    if (append) {
      el("#search-results").insertAdjacentHTML("beforeend", html);
    } else {
      el("#search-results").innerHTML = html || `<div class="empty-note">該当するデータがありません</div>`;
    }
    el("#more-btn").hidden = shown >= total;
  }

  init();
})();
