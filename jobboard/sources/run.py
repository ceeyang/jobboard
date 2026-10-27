"""Fetch every enabled job site, keep postings that match the profile, merge with Telegram jobs."""
import json
import time
import zlib

from jobboard import db, parse
from jobboard.sources import adapters, http


def to_job(item: dict, name: str, site_id: int) -> tuple[dict, str, dict]:
    tags = [t for t in item["tags"] if t]
    header = f"{item['title']}\n{item.get('company') or ''} · {item.get('location') or ''}"
    if item.get("salary"):
        header += f" · {item['salary']}"
    text = f"{header}\n{', '.join(tags)}\n{item['text']}".strip()
    a = parse.analyze(text, " ".join(tags), structured=True)
    a["title"] = item["title"].strip()[:90]
    a["company"] = (item.get("company") or "").strip() or None
    a["location"] = (item.get("location") or "").strip() or None
    a["geo"] = parse.geo_flag(item.get("location"))
    if a["geo"] == "unknown":
        a["geo"] = parse.geo_from_text(text)    # "US only" written in the description
    a["remote"] = bool(item.get("remote"))
    a["salary"] = item.get("salary") or None  # never regex salary out of prose ("10-50 employees")
    a["contacts"]["links"] = list(dict.fromkeys([item["url"]] + a["contacts"]["links"]))[:5]
    a["visa"] = "yes" if item.get("visa") == "yes" else a["visa"]   # the source's own flag (Canada LMIA, ...) beats text guessing
    # same company+title on different sites collapses into one job with several sources
    a["fingerprint"] = parse.fingerprint(f"{a['company'] or ''}{a['title']}")
    src = {"chat": name, "chat_id": site_id, "msg_id": zlib.crc32(str(item["id"]).encode()),
           "date": item["date"], "url": item["url"]}
    return a, text, src


def all_sources(mode: str | None = None) -> list:
    """Every enabled job source, in the order they are fetched (cheap public feeds first, LinkedIn last).
    mode='public' keeps only the sources allowed on the public site (see jobboard/mode.py)."""
    from jobboard import mode as _mode
    from jobboard.sources import ats, github, gov, jsonld, linkedin, more, openfeeds
    every = (adapters.SOURCES + more.SOURCES + openfeeds.SOURCES + gov.SOURCES + github.sources() + ats.SOURCES + jsonld.SOURCES
             + linkedin.SOURCES)
    return [s for s in every if _mode.allowed(s[0], mode)]


def run(con=None, only: set[str] | None = None, force: bool = False, min_score: int = 1,
        sources=None, now=time.time, mode: str | None = None) -> dict:
    con = con or db.connect()
    stats = {}
    for name, site_id, min_gap, fn in (sources or all_sources(mode)):
        if only and name not in only:
            continue
        st = con.execute("SELECT last_run FROM source_state WHERE name=?", (name,)).fetchone()
        if st and not force and now() - st["last_run"] < min_gap:
            stats[name] = {"skipped": "ran recently"}
            continue
        try:
            items = fn()
            new = kept = 0
            for it in items:
                a, text, src = to_job(it, name, site_id)
                if a["score"] < min_score:
                    continue
                kept += 1
                new += db.upsert(con, a, text, src)
            db.record_source(con, name, len(items), now=now())
            stats[name] = {"fetched": len(items), "matched": kept, "new": new}
        except http.Skip as why:  # e.g. API key not configured: not an error, try again next time
            stats[name] = {"skipped": str(why)}
        except Exception as exc:  # one broken site must never stop the others
            db.record_source(con, name, 0, error=f"{type(exc).__name__}: {exc}")
            stats[name] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
        con.commit()
    return stats
