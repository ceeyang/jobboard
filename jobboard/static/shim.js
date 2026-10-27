/* Static edition of the board: answers the page's /api/* calls from data/*.json and keeps personal state
   (statuses, saved searches) in this browser only. Loaded before the page script. */
(() => {
  const real = window.fetch.bind(window);
  const BASE = new URL(".", document.baseURI).href;
  const KS = "jb-pub-status", KQ = "jb-pub-searches";
  const read = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch (e) { return d } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch (e) {} };
  const cache = {};
  const data = name => (cache[name] ??= real(BASE + "data/" + name + ".json", { cache: "no-cache" }).then(r => r.json()));
  const reply = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const READONLY = "公开站点是只读的：请在本地版里修改（岗位方向、自动刷新、每日摘要）";
  const DAYS = { "1d": 1, "3d": 3, "7d": 7, "30d": 30 };
  let generated = null, all = null, total = 0, loadedChunks = 0, chunks = 1, loading = null;

  window.JB_STATIC = {
    label() {
      if (!generated) return "公开站点 · 正在加载…";
      if (all && all.length < total) return `正在加载全部岗位 ${all.length} / ${total} …`;
      const h = Math.max(0, Math.round((Date.now() - new Date(generated)) / 36e5));
      return `${h < 1 ? "刚刚更新" : h + " 小时前更新"} · 自动更新（GitHub 定时不保证准点）`;
    },
  };
  // Read-only edition: no manual refresh / settings (data is rebuilt hourly), and a short, dismissible explanation.
  const style = document.createElement("style");
  style.textContent = "#refresh,#settingsBtn{display:none!important}" +
    ".intro{display:flex;gap:12px;align-items:flex-start;margin:4px 0 14px;padding:12px 14px;border:1px solid var(--line);" +
    "background:var(--panel);border-radius:var(--r);font-size:13px;line-height:1.55;color:var(--mut)}" +
    ".intro a{color:var(--ac)}.intro b{color:var(--fg)}.intro button{margin-left:auto;flex:none;border:1px solid var(--line);background:transparent;" +
    "color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer;font-size:12.5px}";
  document.head.appendChild(style);
  document.addEventListener("DOMContentLoaded", () => {
    const theme = document.querySelector("#theme");   // always available: where the data comes from, how fresh it is, how to ask for removal
    if (theme) theme.insertAdjacentHTML("beforebegin", '<a class="btn ico" href="about.html" title="数据来源、更新频率、下架申请">关于</a>');
    if (read("jb-pub-intro", false)) return;
    const box = document.createElement("div");
    box.className = "intro";
    const show = (site) => {
      box.innerHTML = `<div><b>${site.title}</b>：${site.description || "公开岗位的聚合看板。"}` +
        "这里只显示摘要和<b>原帖链接</b>，申请请点开原帖。数据由 GitHub Actions <b>自动更新</b>，但 GitHub 的定时触发<b>并不准点</b>" +
        "（实测近期通常每 4–8 小时一次），页面右上角会显示上次更新时间。想找<b>雇主担保签证</b>的岗位，打开左侧「只看雇主担保签证」" +
        "（根据岗位正文判断，申请前请向雇主确认）。感兴趣/已投等标记只保存在你当前的浏览器里。<a href=\"about.html\">数据来源与状态</a></div><button>知道了</button>";
      box.querySelector("button").onclick = () => { write("jb-pub-intro", true); box.remove(); };
      const anchor = document.querySelector("#overview") || document.querySelector("#stats");   // right under the tabs
      anchor?.parentNode.insertBefore(box, anchor);
    };
    data("meta").then(m => show(m.site || { title: "公开版岗位看板", description: "" })).catch(() => show({ title: "公开版岗位看板", description: "" }));
  });
  try {   // first visit: show the Telegram channel group expanded (the page folds it by default)
    if (!localStorage.getItem("jb2")) localStorage.setItem("jb2", JSON.stringify({ closed: { telegram: false } }));
  } catch (e) {}
  // Paged loading: the first chunk (best matches) is enough to show the page; the rest arrives in the background and the
  // page re-reads /api/jobs as chunks land, so search and filters become complete without blocking the first paint.
  async function firstChunk() {
    if (all) return;
    const d = await data("jobs");
    generated = d.generated_at; total = d.total ?? d.jobs.length; chunks = d.chunks ?? 1; all = d.jobs.slice(); loadedChunks = 1;
    if (chunks > 1) loading = loadRest();
  }
  async function loadRest() {
    let last = 0;
    for (let i = 1; i < chunks; i++) {
      try {
        const c = await real(BASE + `data/jobs-${i}.json`, { cache: "no-cache" }).then(r => r.json());
        all.push(...c.jobs); loadedChunks++;
      } catch (e) { break; }
      if (Date.now() - last > 600 || i === chunks - 1) { last = Date.now(); if (window.load) window.load().catch(() => {}); }
    }
  }
  const settings = () => ({ refresh_minutes: 60, digest_enabled: false, digest_time: "09:00", saved_searches: read(KQ, []),
                            next_fetch_at: null, min_refresh: 5, readonly: true });

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const m = url.pathname.match(/\/api\/(.*)$/);
    if (!m) return real(input, init);
    const path = m[1], method = (init.method || "GET").toUpperCase();
    if (method === "GET" && path === "jobs") {
      await firstChunk();
      const min = +url.searchParams.get("min_score") || 1, limit = +url.searchParams.get("limit") || 500;
      const since = DAYS[url.searchParams.get("since")], st = read(KS, {});
      const cut = since ? Date.now() - since * 864e5 : 0;
      const jobs = all.filter(j => j.score >= min && (!cut || new Date(j.posted_at) >= cut))
        .slice(0, limit).map(j => ({ ...j, status: st[j.id] || "new" }));
      return reply({ jobs, sync: { running: false, failed: false, last_job_seen: generated, refresh_minutes: 60, next_fetch_at: null } });
    }
    if (method === "GET" && path === "chats") return reply(await data("channels"));
    if (method === "GET" && path === "roles") return reply(await data("roles"));
    if (method === "GET" && path === "sync") return reply({ running: false, failed: false, last_job_seen: generated, refresh_minutes: 60, next_fetch_at: null });
    if (method === "GET" && path === "settings") return reply(settings());
    if (method === "PUT" && path === "settings") {
      const body = JSON.parse(init.body || "{}");
      if (Object.keys(body).some(k => k !== "saved_searches")) return reply({ detail: READONLY }, 400);
      write(KQ, body.saved_searches || []);
      return reply(settings());
    }
    const s = path.match(/^jobs\/(\d+)\/status$/);
    if (method === "POST" && s) {
      const status = JSON.parse(init.body || "{}").status, st = read(KS, {});
      if (status === "new") delete st[s[1]]; else st[s[1]] = status;
      write(KS, st);
      return reply({ id: +s[1], status });
    }
    if (method === "POST" && path === "refresh")
      return reply({ started: false, reason: "数据由 GitHub Actions 自动更新（定时不保证准点），无需手动刷新" });
    return reply({ detail: READONLY }, 400);   // role edits and everything else
  };
})();
