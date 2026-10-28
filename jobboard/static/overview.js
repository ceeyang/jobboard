/* Overview tab for the static sites: what is new today, where the jobs are, who sponsors visas.
   Numbers come from data/stats.json (computed once at build time); everything is drawn with plain HTML/CSS/SVG. */
(() => {
  const KV = "jb-pub-view";
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const read = k => { try { return localStorage.getItem(k) } catch (e) { return null } };
  const write = (k, v) => { try { localStorage.setItem(k, v) } catch (e) {} };

  const style = document.createElement("style");
  style.textContent = `
  #stats{display:none!important}
  body.view-overview main>:is(#tabs,.tools,#list,#sentinel,#filters){display:none!important}
  body:not(.view-overview) #overview{display:none}
  .vtabs{display:flex;gap:6px;margin:2px 0 14px}
  .vtabs button{border:1px solid var(--line);background:var(--panel);color:var(--mut);border-radius:9px;padding:6px 16px;font-size:14px;cursor:pointer}
  .vtabs button.on{background:var(--ac);border-color:var(--ac);color:var(--acfg);font-weight:600}
  body.view-overview .detail-panel,body.view-overview .dscrim{display:none!important}
  body.view-overview main{grid-column:2/-1}
  #overview{display:grid;gap:14px;max-width:1080px}
  .ov-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px}
  .ov-card h3{margin:0 0 12px;font-size:15px}.ov-card h3 small{color:var(--mut);font-weight:400;margin-left:8px;font-size:12.5px}
  .ov-nums{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  .ov-num b{display:block;font-size:28px;line-height:1.1}.ov-num span{color:var(--mut);font-size:13px}
  .ov-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .ov-bar{display:grid;grid-template-columns:104px 1fr 44px;gap:10px;align-items:center;margin:7px 0;font-size:13.5px}
  .ov-bar .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ov-bar a.nm,.ov-bar button.nm{color:var(--fg);text-decoration:none;background:none;border:0;padding:0;text-align:left;cursor:pointer;font:inherit}
  .ov-bar a.nm:hover,.ov-bar button.nm:hover{color:var(--ac);text-decoration:underline}
  .ov-bar i{display:block;height:9px;border-radius:5px;background:var(--ac);opacity:.85;min-width:3px}.ov-bar em{color:var(--mut);font-style:normal;text-align:right}
  .ov-chart{display:flex;align-items:flex-end;gap:5px;height:96px;margin-top:6px}
  .ov-chart div{flex:1;background:var(--ac);opacity:.8;border-radius:4px 4px 0 0;min-height:2px;position:relative}
  .ov-chart div.today{opacity:1}.ov-axis{display:flex;justify-content:space-between;color:var(--mut);font-size:11.5px;margin-top:4px}
  .ov-trend{margin-top:10px;font-size:13.5px;color:var(--mut)}.ov-up{color:var(--ok)}.ov-down{color:var(--warn)}
  .ov-picks{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
  a.ov-pick{display:block;border:1px solid var(--line);border-radius:10px;padding:11px 13px;color:var(--fg);text-decoration:none;background:var(--panel2,var(--panel))}
  a.ov-pick:hover{border-color:var(--ac)}a.ov-pick b{display:block;font-size:14.5px;line-height:1.35;margin-bottom:3px}
  .ov-pick span{display:block;color:var(--mut);font-size:12.5px}.ov-tags{margin-top:6px;display:flex;gap:5px;flex-wrap:wrap}
  .ov-tags i{font-style:normal;border:1px solid var(--line);border-radius:99px;padding:1px 8px;font-size:11.5px;color:var(--mut)}
  .ov-emp details{border-top:1px solid var(--line);padding:7px 0}.ov-emp details:first-of-type{border-top:0}
  .ov-emp summary{cursor:pointer;font-size:14px}.ov-emp summary em{color:var(--mut);font-style:normal;margin-left:6px;font-size:12.5px}
  .ov-emp ul{margin:6px 0 2px 18px;padding:0;font-size:13px}.ov-emp li{margin:2px 0}.ov-emp a{color:var(--ac)}
  .ov-btn{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:9px;padding:6px 12px;font-size:13px;cursor:pointer}.ov-btn:hover{border-color:var(--ac)}
  .ov-feeds{margin-top:8px}.ov-feeds summary{cursor:pointer;color:var(--ac)}.ov-feeds .ov-tags{margin-top:8px}a.ov-feed{border:1px solid var(--line);border-radius:8px;padding:1px 9px;font-size:12.5px;text-decoration:none;color:var(--fg)}a.ov-feed:hover{border-color:var(--ac);color:var(--ac)}
  .ov-note{color:var(--mut);font-size:12.5px;line-height:1.6}.ov-note a{color:var(--ac)}
  @media(max-width:760px){.ov-nums{grid-template-columns:repeat(2,1fr)}.ov-cols{grid-template-columns:1fr}.ov-bar{grid-template-columns:88px 1fr 40px}}`;
  document.head.appendChild(style);

  function show(view) {
    document.body.classList.toggle("view-overview", view === "overview");
    document.querySelectorAll(".vtabs button").forEach(b => b.classList.toggle("on", b.dataset.v === view));
    write(KV, view);
  }
  // S, change, ago and utc are the page script's globals (declared with let/const: reachable by name, not as window.X)
  const toList = (fn) => { show("list"); if (fn) { fn(); change(); } window.scrollTo({ top: 0 }); };
  const num = n => Number(n).toLocaleString("zh-CN");

  function render(st) {
    const t = st.totals, ov = document.querySelector("#overview");
    const delta = t.prev_7d ? Math.round(100 * (t.new_7d - t.prev_7d) / t.prev_7d) : null;
    const max = Math.max(1, ...st.distribution.items.map(i => i.count)), dmax = Math.max(1, ...st.daily.map(d => d.count));
    const bars = st.distribution.items.map(i => {
      const name = i.url ? `<a class="nm" href="${esc(i.url)}" target="_blank" rel="noopener" title="打开「${esc(i.label)}」专属站">${esc(i.label)} ↗</a>`
                         : `<button class="nm" data-role="${esc(i.key)}" title="只看这个方向">${esc(i.label)}</button>`;
      return `<div class="ov-bar">${name}<span><i style="width:${Math.max(2, Math.round(100 * i.count / max))}%"></i></span><em>${num(i.count)}</em></div>`;
    }).join("");
    const mmax = Math.max(1, ...(st.source_mix || []).map(m => m.count));
    const mix = (st.source_mix || []).map(m => `<div class="ov-bar"><span class="nm">${esc(m.label)}</span><span><i style="width:${Math.max(2, Math.round(100 * m.count / mmax))}%"></i></span><em>${num(m.count)}</em></div>`).join("");
    const chart = st.daily.map((d, k) => `<div class="${k === st.daily.length - 1 ? "today" : ""}" style="height:${Math.max(2, Math.round(96 * d.count / dmax))}px" title="${esc(d.date)}：${d.count} 条"></div>`).join("");
    const when = p => (typeof ago === "function" && typeof utc === "function") ? esc(ago(utc(p.posted_at))) : "";
    const picks = st.picks.length ? st.picks.map(p => `<a class="ov-pick" href="${esc(p.url)}" target="_blank" rel="noopener">
        <b>${esc(p.title)}</b><span>${esc([p.company, p.location].filter(Boolean).join(" · "))}</span>
        <div class="ov-tags">${p.remote ? "<i>🏠 远程</i>" : ""}${p.visa === "yes" ? "<i>🛂 担保签证</i>" : ""}${p.tags.map(x => `<i>${esc(x)}</i>`).join("")}<i>${when(p)}</i></div></a>`).join("")
      : '<div class="ov-note">最近几天没有新的高匹配岗位。</div>';
    const emp = st.visa_employers.length ? st.visa_employers.map(e => `<details><summary>${esc(e.company)}<em>${e.count} 个岗位</em></summary>
        <ul>${e.jobs.map(j => `<li><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a></li>`).join("")}</ul></details>`).join("")
      : '<div class="ov-note">目前没有岗位明确写明提供签证担保。</div>';
    ov.innerHTML = `
      <div class="ov-card"><div class="ov-nums">
        <div class="ov-num"><b>${num(t.jobs)}</b><span>在招岗位</span></div>
        <div class="ov-num"><b>${num(t.new_24h)}</b><span>24 小时内新增</span></div>
        <div class="ov-num"><b>${t.remote_pct}%</b><span>远程岗位占比</span></div>
        <div class="ov-num"><b>${num(t.visa_yes)}</b><span>明确担保签证</span></div></div></div>
      <div class="ov-card"><h3>今日新岗位精选<small>最近两天里匹配度最高、方向和公司尽量分散</small></h3><div class="ov-picks">${picks}</div></div>
      <div class="ov-cols">
        <div class="ov-card"><h3>${esc(st.distribution.title)}<small>${st.distribution.kind === "families" ? "点方向名打开专属站" : "点方向名只看这个方向"}</small></h3>${bars}</div>
        <div class="ov-card"><h3>近 14 天新增<small>按发布日期</small></h3><div class="ov-chart">${chart}</div>
          <div class="ov-axis"><span>${esc(st.daily[0].date.slice(5))}</span><span>${esc(st.daily[st.daily.length - 1].date.slice(5))}</span></div>
          <div class="ov-trend">最近 7 天 <b>${num(t.new_7d)}</b> 条，前 7 天 ${num(t.prev_7d)} 条${delta === null ? "" : `，<span class="${delta >= 0 ? "ov-up" : "ov-down"}">${delta >= 0 ? "▲" : "▼"} ${Math.abs(delta)}%</span>`}</div>
          <h3 style="margin-top:18px">来源构成<small>一个岗位可能同时出现在多个来源</small></h3>${mix}</div></div>
      <div class="ov-card ov-emp"><h3>愿意担保签证的雇主<small>岗位正文明确写了签证担保，或来源官方标明（如加拿大 LMIA）；申请前请向雇主确认</small></h3>${emp}
        <div style="margin-top:10px"><button class="ov-btn" data-act="visa">在岗位列表里只看担保签证 →</button></div></div>
      <div class="ov-card ov-note">${esc(st.note)}<br>
        订阅新岗位：<a href="feed.xml">全部（RSS）</a> · <a href="feed.json">JSON Feed</a>　·　<a href="about.html">数据来源与状态</a>
        ${(st.feeds || []).length ? `<details class="ov-feeds"><summary>按方向订阅（${st.feeds.length} 个）</summary><div class="ov-tags">${st.feeds.map(f => `<a class="ov-feed" href="feeds/${esc(f.key)}.xml">${esc(f.label)}</a>`).join("")}</div></details>` : ""}
        <div style="margin-top:10px"><button class="ov-btn" data-act="list">浏览全部 ${num(t.jobs)} 个岗位 →</button></div></div>`;
    ov.onclick = e => {
      const r = e.target.closest("[data-role]"), a = e.target.closest("[data-act]");
      if (r) toList(() => { S.roles = [r.dataset.role]; S.since = ""; });
      else if (a && a.dataset.act === "visa") toList(() => { S.visa = true; S.min = 1; S.roles = []; S.since = ""; });
      else if (a) toList();
    };
    const tab = document.querySelector('.vtabs button[data-v="list"]');
    if (tab) tab.textContent = `岗位列表（${num(t.jobs)}）`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector("main > .bar");
    if (!bar) return;
    const tabs = document.createElement("div");
    tabs.className = "vtabs";
    tabs.innerHTML = '<button data-v="overview">概览</button><button data-v="list">岗位列表</button>';
    const ov = document.createElement("section");
    ov.id = "overview"; ov.setAttribute("aria-label", "概览");
    bar.after(tabs); tabs.after(ov);
    const intro = document.querySelector(".intro");
    if (intro) ov.before(intro);
    tabs.onclick = e => { const b = e.target.closest("button"); if (b) { show(b.dataset.v); window.scrollTo({ top: 0 }); } };
    show(read(KV) === "list" ? "list" : "overview");
    ov.innerHTML = '<div class="ov-card ov-note">正在加载概览…</div>';
    fetch(new URL("data/stats.json", document.baseURI), { cache: "no-cache" }).then(r => r.json()).then(render)
      .catch(() => { ov.innerHTML = '<div class="ov-card ov-note">概览暂时加载不出来，请切换到「岗位列表」。</div>'; });
  });
})();
