import argparse
import json
import os
import sys
from pathlib import Path

from jobboard import db, profile_config, roles


def discover_run(con, channels):
    from jobboard.sources import discover
    r = discover.run(con, channels=channels)
    return {"probed": r["probed"], "added": r["added"], "registry": r["registry"]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="pull new messages from all groups/channels")
    f.add_argument("--backfill-days", type=int, default=2)
    f.add_argument("--no-telegram", action="store_true", help="skip Telegram channels")
    f.add_argument("--no-web", action="store_true", help="skip job websites")
    f.add_argument("--force", action="store_true", help="ignore per-site minimum intervals")
    f.add_argument("--only", help="comma-separated site names, e.g. RemoteOK,Jobicy")
    f.add_argument("--mode", choices=["private", "public"], default="private",
                   help="public = only sources allowed on the public site, no Telegram, database scrubbed afterwards")
    ex = sub.add_parser("export", help="write the static, public-safe site (GitHub Pages) to a folder")
    ex.add_argument("--out", default="site")
    ex.add_argument("--days", type=int, default=45, help="only jobs posted within this many days")
    d = sub.add_parser("digest", help="send the daily digest to your Saved Messages")
    d.add_argument("--dry-run", action="store_true", help="print instead of sending")
    dc = sub.add_parser("discover", help="find company job boards (Greenhouse/Lever/Ashby/...) to follow")
    dc.add_argument("--file", help="text file with ATS links, `ats:slug` lines or career-page URLs")
    dc.add_argument("--no-commoncrawl", action="store_true", help="skip the Common Crawl index (slow, often overloaded)")
    dc.add_argument("--max", type=int, default=2000, help="probe at most this many new candidates")
    bd = sub.add_parser("build", help="build a site from another site's feed, re-scored with this profile (no scraping)")
    bd.add_argument("--feed", required=True, help="URL of the feed's data folder (https://<org>.github.io/jobboard/data) or a local folder")
    bd.add_argument("--profile", default=None, help="profile file (default config/profile.json)")
    bd.add_argument("--out", default="site")
    og = sub.add_parser("org-render", help="render the repositories of a GitHub organisation (aggregate engine + one per profile + landing page)")
    og.add_argument("--org", required=True, help="the GitHub organisation name")
    og.add_argument("--out", required=True, help="folder to write into")
    og.add_argument("--engine-repo", default="jobboard")
    og.add_argument("--presets", nargs="*", help="presets that get their own repository (default: all common tech roles)")
    sub.add_parser("presets", help="list the ready-made profiles (front-end, back-end, mobile, data/AI, ...)")
    ini = sub.add_parser("init", help="create config/profile.json from a preset, then edit the keywords")
    ini.add_argument("--preset", required=True, help="see `jobboard presets`")
    ini.add_argument("--title", help="site title (default: the preset's)")
    ini.add_argument("--out", default=None, help="where to write (default config/profile.json)")
    ini.add_argument("--force", action="store_true", help="overwrite an existing profile")
    pr = sub.add_parser("profile", help="check config/profile.json and try job titles against it")
    pr.add_argument("--try", dest="titles", nargs="*", metavar="TITLE", help="job titles to score, e.g. --try \"Senior Go Engineer\"")
    pr.add_argument("--path", default=None)
    sub.add_parser("reanalyze", help="re-score stored jobs after editing profile.py")
    sub.add_parser("health", help="check every source; exit code 1 if one is broken (used by the workflow to raise an alert)")
    s = sub.add_parser("serve", help="run the web board")
    s.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "build":
            from jobboard import publish
            cfg = profile_config.load(a.profile)
            if not cfg:
                raise RuntimeError("no profile found: run `jobboard init --preset <name>` or pass --profile")
            profile_config.apply(cfg)
            print(json.dumps(publish.build_from_feed(a.feed, a.out, cfg), ensure_ascii=False, indent=1))
            return
        if a.cmd == "org-render":
            from jobboard import orgkit
            print(json.dumps(orgkit.render_all(Path(a.out), a.org, a.engine_repo, a.presets), ensure_ascii=False, indent=1))
            return
        if a.cmd == "presets":
            for n in profile_config.presets():
                c = profile_config.validate(profile_config.load_preset(n))
                print(f"{n:16} {len(c['roles']):2} roles  {c['site']['title']}")
            return
        if a.cmd == "init":
            target = Path(a.out) if a.out else profile_config.PATH
            if target.exists() and not a.force:
                raise RuntimeError(f"{target} already exists (use --force to overwrite)")
            cfg = profile_config.load_preset(a.preset)
            if a.title:
                cfg.setdefault("site", {})["title"] = a.title
            profile_config.validate(cfg)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"wrote {target}: {len(cfg['roles'])} roles. Edit the keywords there, then check them with:\n"
                  f"  jobboard profile --try \"Senior Backend Engineer\" \"Sales Manager\"")
            return
        if a.cmd == "profile":
            cfg = profile_config.activate(a.path)
            if not cfg:
                raise RuntimeError("no config/profile.json yet: run `jobboard init --preset <name>` first")
            print(f"{cfg['site']['title']}: {len(cfg['roles'])} roles")
            for r in cfg["roles"]:
                print(f"  {r['key']:14} w{r['weight']:<2} {', '.join(r['keywords'])}")
            for t in a.titles or []:
                s, tags = profile_config.classify(t)
                print(f"  {'KEEP' if s >= 5 else 'skip':4} score {s:<2} {t}  {tags}")
            return
        # sites built for the public (and anything that exports/builds one) take their roles from config/profile.json;
        # the personal board keeps managing roles in its database unless JOBBOARD_USE_PROFILE=1
        use_profile = (a.cmd == "export" or (a.cmd == "fetch" and a.mode == "public") or os.environ.get("JOBBOARD_USE_PROFILE") == "1")
        cfg = profile_config.activate() if use_profile else None
        if a.cmd in ("fetch", "digest", "reanalyze") and not cfg:
            roles.activate(db.connect())   # score with the roles configured in the web UI
        if a.cmd == "fetch":
            out = {}
            public = a.mode == "public"
            if public and db.HOME == db.DEFAULT_HOME:   # this mode rewrites the database down to excerpts: never your real one
                raise RuntimeError("--mode public needs JOBBOARD_HOME pointing at a separate folder (it rewrites the database)")
            if not a.no_telegram and not public:
                from jobboard import fetch
                try:
                    out["telegram"] = fetch.run(a.backfill_days)
                except Exception as exc:  # e.g. not logged in: websites should still update
                    out["telegram"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
            if not a.no_web:
                from jobboard.sources import run as web
                if not a.only:      # keep the company registry growing: links found in stored jobs, seed list when empty
                    con = db.connect()
                    try:
                        empty = not con.execute("SELECT 1 FROM companies LIMIT 1").fetchone()
                        out["discover"] = discover_run(con, ("db", "seed") if empty else ("db",))
                    except Exception as exc:  # discovery is a bonus, never a reason to skip the fetch
                        con.rollback()
                        out["discover"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
                    finally:
                        con.close()      # no connection of this step may outlive it (an open one locks the database)
                if public and not a.only:   # public channels listed in config/telegram_channels.txt, via their web preview
                    from jobboard.sources import telegram_public
                    tcon = db.connect()
                    try:
                        out["telegram_public"] = telegram_public.run(tcon)
                    except Exception as exc:  # one broken step must not stop the sources after it
                        tcon.rollback()
                        out["telegram_public"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
                    finally:
                        tcon.close()
                out["web"] = web.run(only=set(a.only.split(",")) if a.only else None, force=a.force,
                                     mode="public" if public else None)
                if public:   # the database itself is committed to a public branch: keep only excerpts + links in it
                    from jobboard import publish
                    pcon = db.connect()
                    out["pruned"] = db.prune(pcon)
                    out["scrubbed"] = publish.scrub_db(pcon)
            print(json.dumps(out, ensure_ascii=False, indent=1))
        elif a.cmd == "digest":
            from jobboard import notify
            con = db.connect()
            if a.dry_run:
                print("\n".join(notify.format_digest(notify.pending(con), notify._names(con))) or "(nothing to send)")
            else:
                print(f"sent {notify.send(con)} jobs")
        elif a.cmd == "export":
            from jobboard import publish
            con = db.connect()
            if not cfg:
                roles.activate(con)
            print(json.dumps(publish.export(con, a.out, window_days=a.days), ensure_ascii=False, indent=1))
        elif a.cmd == "discover":
            from jobboard.sources import discover
            ch = ["db", "seed"] + ([] if a.no_commoncrawl else ["commoncrawl"]) + (["file"] if a.file else [])
            print(json.dumps(discover.run(channels=tuple(ch), file=a.file, max_probe=a.max), ensure_ascii=False, indent=1))
        elif a.cmd == "health":
            import time as _t
            from jobboard import health
            sm = health.summary(health.status_rows(db.connect(), _t.time()))
            print(json.dumps(sm, ensure_ascii=False, indent=1))
            if sm["alert"]:
                sys.exit(1)
        elif a.cmd == "reanalyze":
            print(f"reanalyzed {db.reanalyze(db.connect())} jobs")
        elif a.cmd == "serve":
            import uvicorn
            uvicorn.run("jobboard.web:app", host="127.0.0.1", port=a.port)
    except (RuntimeError, ValueError) as exc:     # bad input (unknown preset, invalid profile ...): one clear line, not a traceback
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
