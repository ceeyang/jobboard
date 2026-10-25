import argparse
import json
import sys

from jobboard import db, roles


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
    sub.add_parser("reanalyze", help="re-score stored jobs after editing profile.py")
    s = sub.add_parser("serve", help="run the web board")
    s.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)

    try:
        if a.cmd in ("fetch", "digest", "reanalyze"):
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
                    out["scrubbed"] = publish.scrub_db(db.connect())
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
            roles.activate(con)
            print(json.dumps(publish.export(con, a.out, window_days=a.days), ensure_ascii=False, indent=1))
        elif a.cmd == "discover":
            from jobboard.sources import discover
            ch = ["db", "seed"] + ([] if a.no_commoncrawl else ["commoncrawl"]) + (["file"] if a.file else [])
            print(json.dumps(discover.run(channels=tuple(ch), file=a.file, max_probe=a.max), ensure_ascii=False, indent=1))
        elif a.cmd == "reanalyze":
            print(f"reanalyzed {db.reanalyze(db.connect())} jobs")
        elif a.cmd == "serve":
            import uvicorn
            uvicorn.run("jobboard.web:app", host="127.0.0.1", port=a.port)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
