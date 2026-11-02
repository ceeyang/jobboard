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
  .vtabs{display:inline-flex;gap:2px;margin:0 0 16px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:3px}
  .vtabs button{border:0;background:none;color:var(--mut);border-radius:7px;padding:0 14px;height:30px;font-size:13.5px;cursor:pointer;white-space:nowrap}
  .vtabs button:hover{color:var(--fg)}
  .vtabs button.on{background:var(--panel);color:var(--ac2);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08),0 0 0 1px var(--line)}
  body.view-overview .detail-panel,body.view-overview .dscrim{display:none!important}
  body.view-overview .app{grid-template-columns:var(--side) minmax(0,1fr)!important}
  #overview{display:grid;gap:16px;max-width:1120px}
  .ov-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;box-shadow:var(--shadow)}
  .ov-card h3{margin:0 0 14px;font-size:15px;font-weight:650;display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px}
  .ov-card h3 small{color:var(--mut);font-weight:400;font-size:12.5px}
  .ov-card:has(>.ov-nums){background:none;border:0;padding:0;box-shadow:none}
  .ov-nums{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  .ov-num{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;box-shadow:var(--shadow);display:flex;flex-direction:column-reverse;gap:6px}
  .ov-num b{display:block;font-size:28px;line-height:1.1;letter-spacing:-.02em;font-weight:700}.ov-num span{color:var(--mut);font-size:13px}
  .ov-num:nth-child(3) b{color:var(--ac2)}.ov-num:nth-child(4) b{color:var(--warn)}
  .ov-cols{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:16px;align-items:start}
  .ov-bar{display:grid;grid-template-columns:110px 1fr 48px;gap:12px;align-items:center;margin:10px 0;font-size:13.5px}
  .ov-bar .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ov-bar a.nm,.ov-bar button.nm{color:var(--fg);text-decoration:none;background:none;border:0;padding:0;text-align:left;cursor:pointer;font:inherit}
  .ov-bar a.nm:hover,.ov-bar button.nm:hover{color:var(--ac2)}
  .ov-bar>span:not(.nm){display:block;height:8px;border-radius:99px;background:var(--panel3,var(--panel2));overflow:hidden}
  .ov-bar i{display:block;height:100%;border-radius:99px;background:var(--ac);min-width:3px}.ov-bar em{color:var(--mut);font-style:normal;text-align:right;font-size:12.5px}
  .ov-chart{display:flex;align-items:flex-end;gap:5px;height:110px;margin-top:6px}
  .ov-chart div{flex:1;background:var(--panel3,var(--panel2));border-radius:5px 5px 2px 2px;min-height:3px}
  .ov-chart div:hover{background:var(--acline,var(--ac))}
  .ov-chart div.today{background:var(--ac)}.ov-axis{display:flex;justify-content:space-between;color:var(--mut);font-size:11.5px;margin-top:6px}
  .ov-trend{margin-top:12px;font-size:13px;color:var(--mut)}.ov-trend b{color:var(--fg)}.ov-up{color:var(--ok);font-weight:600}.ov-down{color:var(--warn);font-weight:600}
  .ov-picks{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
  a.ov-pick{display:flex;flex-direction:column;gap:4px;border:1px solid var(--line);border-radius:12px;padding:14px 16px;color:var(--fg);text-decoration:none;background:var(--panel);transition:border-color .12s,box-shadow .12s}
  a.ov-pick:hover{border-color:var(--line2);box-shadow:var(--pop)}a.ov-pick b{display:block;font-size:14.5px;line-height:1.4;font-weight:650}
  .ov-pick span{display:block;color:var(--mut);font-size:12.5px}.ov-tags{margin-top:auto;padding-top:8px;display:flex;gap:6px;flex-wrap:wrap}
  .ov-tags i{font-style:normal;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:0 7px;font-size:11.5px;line-height:20px;color:var(--fg2,var(--mut))}
  .ov-emp details{border-top:1px solid var(--line);padding:10px 0;margin:0}.ov-emp details:first-of-type{border-top:0}
  .ov-emp summary{cursor:pointer;font-size:14px;font-weight:600;list-style:none;display:flex;justify-content:space-between;width:auto;color:var(--fg)}
  .ov-emp summary::before{content:none}.ov-emp summary::after{content:"›";color:var(--mut);transition:transform .15s}.ov-emp details[open] summary::after{transform:rotate(90deg)}
  .ov-emp summary em{color:var(--mut);font-style:normal;margin-left:auto;margin-right:12px;font-size:12.5px;font-weight:400}
  .ov-emp ul{margin:8px 0 2px 18px;padding:0;font-size:13px}.ov-emp li{margin:3px 0}.ov-emp a{color:var(--ac2)}
  .ov-btn{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:10px;padding:0 14px;height:34px;font-size:13px;font-weight:600;cursor:pointer}.ov-btn:hover{border-color:var(--ac);color:var(--ac2)}
  .ov-card.ov-note .ov-btn:last-child{background:var(--ac);border-color:var(--ac);color:var(--acfg)}
  .ov-feeds{margin-top:8px}.ov-feeds summary{cursor:pointer;color:var(--ac2)}.ov-feeds .ov-tags{margin-top:8px}a.ov-feed{border:1px solid var(--line);border-radius:8px;padding:1px 9px;font-size:12.5px;text-decoration:none;color:var(--fg)}a.ov-feed:hover{border-color:var(--ac);color:var(--ac2)}
  .ov-note{color:var(--mut);font-size:13px;line-height:1.7}.ov-note a{color:var(--ac2)}
  @media(max-width:900px){.ov-cols{grid-template-columns:1fr}}
  @media(max-width:760px){.ov-nums{grid-template-columns:repeat(2,1fr)}.ov-bar{grid-template-columns:88px 1fr 40px}.ov-num b{font-size:24px}}`;
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
