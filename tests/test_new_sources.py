"""Company registry, ATS adapters, discovery, LinkedIn guest parsing, JSON-LD, extra APIs and the time-range filter.
No test touches the network: http.get_json/get_text are replaced by routes of canned responses."""
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from jobboard import db, parse, profile, web
from jobboard.sources import ats, discover, http, jsonld, linkedin, more, run


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    monkeypatch.setattr(ats, "GAP", 0)
    monkeypatch.setattr(linkedin, "GAP", 0)
    profile.set_rules(None)           # default roles (flutter/ios/android/...)
    yield db.connect()
    profile.set_rules(None)


def fake(monkeypatch, routes):
    """routes: {url substring: payload | Exception}. First match wins; unknown URL fails the test."""
    def pick(url, *a, **kw):
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")
    monkeypatch.setattr(http, "get_json", lambda url, timeout=25, headers=None: pick(url))
    monkeypatch.setattr(http, "get_text", lambda url, timeout=25, headers=None: pick(url))


def add(con, ats_, slug, name=None):
    db.company_add(con, ats_, slug, name)
    con.commit()


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def iso(days_ago=0, tz=timezone.utc):
    return (datetime.now(tz) - timedelta(days=days_ago)).isoformat()


def job(title, date, **kw):
    t = f"{title}\nAcme · Remote"
    a = parse.analyze(t, structured=True)
    a.update(title=title, company="Acme", fingerprint=parse.fingerprint(kw.get("fp") or title + str(kw.get("n", ""))))
    return a, t, {"chat": "S", "chat_id": -1, "msg_id": kw.get("n", 1), "date": date, "url": "https://x"}


# ---- time range ----------------------------------------------------------------------------------------
def test_since_filters_by_posted_at_even_with_timezone_offsets(isolated):
    con = isolated
    for n, (title, d) in enumerate([("Flutter Dev A", iso(0.2)), ("Flutter Dev B", iso(2, timezone(timedelta(hours=8)))),
                                    ("Flutter Dev C", iso(5)), ("Flutter Dev D", iso(20)), ("Flutter Dev E", iso(90))], 1):
        db.upsert(con, *job(title, d, n=n))
    con.commit()
    names = lambda since: {r["title"][-1] for r in db.list_jobs(con, since=since)}
    assert names("1d") == {"A"} and names("3d") == {"A", "B"} and names("7d") == {"A", "B", "C"}
    assert names("30d") == {"A", "B", "C", "D"} and names(None) == names("") == {"A", "B", "C", "D", "E"}
    with pytest.raises(ValueError):
        db.list_jobs(con, since="2y")


def test_jobs_api_since_param(isolated):
    db.upsert(isolated, *job("Flutter Dev New", iso(0.1), n=1))
    db.upsert(isolated, *job("Flutter Dev Old", iso(10), n=2))
    isolated.commit()
    c = TestClient(web.app)
    assert len(c.get("/api/jobs").json()["jobs"]) == 2
    assert [j["title"] for j in c.get("/api/jobs?since=3d").json()["jobs"]] == ["Flutter Dev New"]
    assert c.get("/api/jobs?since=bogus").status_code == 400


def test_merged_job_is_as_old_as_its_earliest_source(isolated):
    a, t, s = job("Flutter Dev", iso(0.1), n=1, fp="same")
    assert db.upsert(isolated, a, t, s)
    a2, t2, s2 = job("Flutter Dev", iso(6), n=2, fp="same")
    s2["chat_id"] = -2
    assert not db.upsert(isolated, a2, t2, s2)          # same fingerprint -> merged
    assert db.list_jobs(isolated, since="1d") == []     # not "last day" any more
    assert len(db.list_jobs(isolated, since="7d")) == 1


# ---- company registry ----------------------------------------------------------------------------------
def test_registry_add_dedup_deactivate_and_revive(isolated):
    con = isolated
    assert db.company_add(con, "greenhouse", "Stripe", "Stripe") is True
    assert db.company_add(con, "greenhouse", "stripe") is False        # slug is case-insensitive
    for _ in range(db.MAX_FAILS - 1):
        db.company_result(con, "greenhouse", "stripe", False)
    assert len(db.companies(con, "greenhouse")) == 1
    db.company_result(con, "greenhouse", "stripe", False)
    assert db.companies(con, "greenhouse") == []                        # given up after MAX_FAILS
    db.company_add(con, "greenhouse", "stripe")                         # found again -> revived
    assert len(db.companies(con, "greenhouse")) == 1
    assert db.company_add(con, "web", "https://Acme.com/Careers") is True  # career-page URLs keep their case


# ---- ATS adapters ----------------------------------------------------------------------------------------
GH_LIST = {"jobs": [
    {"id": 1, "title": "Senior Flutter Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
     "location": {"name": "Remote - Europe"}, "company_name": "Acme", "first_published": "2026-10-20T10:00:00-04:00"},
    {"id": 2, "title": "Office Manager", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2", "location": {"name": "NYC"}}]}
GH_DETAIL = {"id": 1, "content": "&lt;p&gt;Build &lt;b&gt;apps&lt;/b&gt;&lt;/p&gt;",
             "pay_input_ranges": [{"min_cents": 12000000, "max_cents": 16000000, "currency_type": "USD"}]}
LEVER = [{"id": "ab-1", "text": "Android Engineer", "hostedUrl": "https://jobs.lever.co/acme/ab-1", "createdAt": 1760000000000,
          "categories": {"team": "Mobile", "commitment": "Full-time", "allLocations": ["Berlin", "Remote"]},
          "descriptionPlain": "Kotlin all day", "workplaceType": "remote"},
         {"id": "ab-2", "text": "Sales Rep", "hostedUrl": "https://jobs.lever.co/acme/ab-2", "categories": {}}]
ASHBY = {"jobs": [{"id": "x1", "title": "iOS Engineer", "location": "Paris", "secondaryLocations": [{"location": "Remote (EU)"}],
                   "publishedAt": "2026-10-15T09:00:00.000+00:00", "isListed": True, "isRemote": True, "department": "Eng",
                   "jobUrl": "https://jobs.ashbyhq.com/acme/x1", "descriptionPlain": "Swift", "compensation": {"compensationTierSummary": "$150K - $200K"}},
                  {"id": "x2", "title": "iOS Engineer (hidden)", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/acme/x2"}]}
RECRUITEE = {"offers": [{"id": 7, "title": "Flutter Developer", "slug": "flutter-dev", "status": "published", "city": "Utrecht",
                         "country": "NL", "description": "<p>Dart</p>", "published_at": "2026-10-10 08:00:00 UTC", "remote": True}]}
PERSONIO = """<?xml version="1.0"?><workzag-jobs><position><id>55</id><subcompany>Acme GmbH</subcompany><office>Munich</office>
<department>Tech</department><name>Mobile Developer (iOS/Android)</name><jobDescriptions><jobDescription><name>Role</name>
<value><![CDATA[<p>Build apps</p>]]></value></jobDescription></jobDescriptions><createdAt>2026-10-12T10:00:00+00:00</createdAt></position>
<position><id>56</id><name>Accountant</name></position></workzag-jobs>"""


def test_greenhouse_prefilters_titles_and_decodes_content(isolated, monkeypatch):
    add(isolated, "greenhouse", "acme")
    fake(monkeypatch, {"/acme/jobs/1": GH_DETAIL, "/acme/jobs": GH_LIST})   # detail first: it is the longer path
    (item,) = dict((n, fn) for n, _, _, fn in ats.SOURCES)["Greenhouse"]()
    assert item["title"] == "Senior Flutter Engineer" and item["company"] == "Acme" and item["remote"]
    assert item["text"] == "Build apps" and item["salary"] == "$120k-160k/年" and item["date"].startswith("2026-10-20")


def test_lever_ashby_recruitee_personio(isolated, monkeypatch):
    for a in ("lever", "ashby", "recruitee", "personio"):
        add(isolated, a, "acme")
    fake(monkeypatch, {"api.lever.co": LEVER, "api.ashbyhq.com": ASHBY, "recruitee.com": RECRUITEE, "personio.de": PERSONIO})
    src = {n: fn for n, _, _, fn in ats.SOURCES}
    lv, ab, rc, pn = src["Lever"](), src["Ashby"](), src["Recruitee"](), src["Personio"]()
    assert [i["title"] for i in lv] == ["Android Engineer"] and lv[0]["remote"] and lv[0]["tags"] == ["Mobile", "Full-time"]
    assert lv[0]["company"] == "Acme" and lv[0]["date"].startswith("2025-")
    assert [i["title"] for i in ab] == ["iOS Engineer"] and ab[0]["salary"] == "$150K - $200K" and "Remote (EU)" in ab[0]["location"]
    assert rc[0]["title"] == "Flutter Developer" and rc[0]["location"] == "Utrecht, NL" and rc[0]["url"].endswith("/o/flutter-dev")
    assert pn[0]["company"] == "Acme GmbH" and pn[0]["url"] == "https://acme.jobs.personio.de/job/55" and pn[0]["text"] == "Build apps"


def test_items_flow_through_run_and_score(isolated, monkeypatch):
    add(isolated, "ashby", "acme")
    fake(monkeypatch, {"api.ashbyhq.com": ASHBY})
    stats = run.run(isolated, sources=[s for s in ats.SOURCES if s[0] == "Ashby"])
    assert stats["Ashby"] == {"fetched": 1, "matched": 1, "new": 1}
    row = isolated.execute("SELECT * FROM jobs").fetchone()
    assert row["company"] == "Acme" and "ios" in json.loads(row["tags"]) and row["remote"] == 1


def test_404_deactivates_but_network_errors_do_not(isolated, monkeypatch):
    for s in ("gone", "flaky", "good"):
        db.company_add(isolated, "lever", s)
    fake(monkeypatch, {"/gone": http_error(404), "/flaky": OSError("offline"), "/good": LEVER})
    for _ in range(db.MAX_FAILS):
        assert len(ats._walk("lever", ats._lever_board, isolated)) == 1
    assert {r["slug"] for r in db.companies(isolated, "lever")} == {"flaky", "good"}


def test_everything_failing_is_reported_not_silent(isolated, monkeypatch):
    db.company_add(isolated, "lever", "a")
    fake(monkeypatch, {"api.lever.co": OSError("offline")})
    with pytest.raises(RuntimeError, match="all 1 lever boards failed"):
        ats._walk("lever", ats._lever_board, isolated)
    assert len(db.companies(isolated, "lever")) == 1            # offline never wipes the registry


def test_rate_limit_is_surfaced(isolated, monkeypatch):
    db.company_add(isolated, "lever", "a")
    fake(monkeypatch, {"api.lever.co": http.RateLimited("429")})
    with pytest.raises(http.RateLimited):
        ats._walk("lever", ats._lever_board, isolated)


def test_pacer_spaces_requests():
    t, slept = [0.0], []
    p = http.Pacer(2.0, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    for _ in range(3):
        p.wait()
    assert slept == [2.0, 2.0]


# ---- discovery ----------------------------------------------------------------------------------------
def test_extract_slugs_from_links_and_pairs():
    text = """apply at https://boards.greenhouse.io/Acme/jobs/123 or https://job-boards.greenhouse.io/embed/job_board?for=widgets
    https://jobs.lever.co/foo-bar/uuid  https://jobs.ashbyhq.com/ramp  https://bunq.recruitee.com/o/x  https://acme.jobs.personio.de/
    https://boards.greenhouse.io/embed/job_app
    lever: manual-one"""
    assert discover.extract(text) == {("greenhouse", "acme"), ("greenhouse", "widgets"), ("lever", "foo-bar"), ("ashby", "ramp"),
                                      ("recruitee", "bunq"), ("personio", "acme"), ("lever", "manual-one")}


def test_discover_run_validates_candidates_before_storing(isolated, tmp_path):
    a, t, s = job("Flutter Dev", iso(1), n=1)
    t += "\nApply: https://jobs.lever.co/realco/abc and https://jobs.lever.co/ghostco/zzz"
    db.upsert(isolated, a, t, s)
    f = tmp_path / "extra.txt"
    f.write_text("greenhouse:filecorp\nhttps://acme.example/careers\n")
    probe = lambda ats_, slug: (slug in ("realco", "filecorp", "https://acme.example/careers"), "Real Co" if slug == "realco" else None)
    out = discover.run(isolated, channels=("db", "file"), file=str(f), probe_fn=probe, pace=0)
    assert out["added"] == 3 and out["invalid"] == 1 and out["registry"] == {"greenhouse": 1, "lever": 1, "web": 1}
    assert db.companies(isolated, "lever")[0]["name"] == "Real Co"
    again = discover.run(isolated, channels=("db", "file"), file=str(f), probe_fn=probe, pace=0)
    assert again["probed"] == 1 and again["added"] == 0         # known boards are not probed again; the ghost is retried


def test_probe_per_ats(monkeypatch):
    fake(monkeypatch, {"greenhouse.io/v1/boards/yes": {"name": "Yes Inc"}, "greenhouse.io/v1/boards/no": http_error(404),
                       "api.lever.co": [], "ashbyhq.com": {"jobs": []}, "recruitee.com": {"offers": []},
                       "personio.de": "<?xml version='1.0'?><workzag-jobs></workzag-jobs>"})
    assert discover.probe("greenhouse", "yes") == (True, "Yes Inc") and discover.probe("greenhouse", "no") == (False, None)
    assert all(discover.probe(a, "x")[0] for a in ("lever", "ashby", "recruitee", "personio"))


def test_commoncrawl_is_best_effort():
    lines = "\n".join(json.dumps({"url": u}) for u in ("https://boards.greenhouse.io/ccco/jobs/1", "https://boards.greenhouse.io/embed/x"))

    def get(u):
        return json.dumps([{"id": "CC-1"}]) if u.endswith("collinfo.json") else lines
    assert ("greenhouse", "ccco") in discover.commoncrawl(get, sleep=lambda s: None)
    assert discover.commoncrawl(lambda u: (_ for _ in ()).throw(OSError("504")), sleep=lambda s: None) == set()


# ---- LinkedIn guest ----------------------------------------------------------------------------------
LI_LIST = """
<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:111"><a class="base-card__full-link" href="https://sg.linkedin.com/jobs/view/x-111?trk=a"></a>
<h3 class="base-search-card__title">
  Senior Flutter Engineer </h3><h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="#"> Acme &amp; Co </a></h4>
<span class="job-search-card__location">Singapore, Singapore</span><time class="job-search-card__listdate" datetime="2026-10-23">1 day ago</time></div></li>
<li><div class="base-card" data-entity-urn="urn:li:jobPosting:222"><h3 class="base-search-card__title">Account Manager</h3>
<h4 class="base-search-card__subtitle"><a href="#">Foo</a></h4><span class="job-search-card__location">Remote</span>
<time datetime="2026-10-22">2 days ago</time></div></li>"""
LI_DETAIL = """<div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
<p>Build <b>Flutter</b> apps</p><ul><li>Dart</li></ul></div>
<ul><li><span class="description__job-criteria-text description__job-criteria-text--criteria"> Mid-Senior level </span></li>
<li><span class="description__job-criteria-text description__job-criteria-text--criteria">Full-time</span></li></ul>"""


def test_linkedin_parsers():
    cards = linkedin.parse_cards(LI_LIST)
    assert cards[0] == {"id": "111", "title": "Senior Flutter Engineer", "company": "Acme & Co",
                        "location": "Singapore, Singapore", "date": "2026-10-23"} and cards[1]["id"] == "222"
    d = linkedin.parse_detail(LI_DETAIL)
    assert d["text"] == "Build Flutter apps\n- Dart" and d["criteria"] == ["Mid-Senior level", "Full-time"]


def test_linkedin_run_filters_titles_remembers_seen_and_pages(isolated):
    seen_urls = []

    def get(url):
        seen_urls.append(url)
        if "/seeMoreJobPostings/" in url:
            return LI_LIST if "start=0" in url else ""          # second page is empty -> stop paging
        return LI_DETAIL
    items = linkedin.linkedin(get, sleep=lambda s: None, con=isolated)
    assert [i["id"] for i in items] == ["li111"]                # Account Manager never downloaded
    assert items[0]["url"] == "https://www.linkedin.com/jobs/view/111" and items[0]["company"] == "Acme & Co"
    assert items[0]["tags"] == ["Mid-Senior level", "Full-time"] and "f_TPR=r604800" in seen_urls[0]
    assert sum("/jobPosting/" in u for u in seen_urls) == 1     # 4 search terms returned the same job: fetched once
    before = len(seen_urls)
    assert linkedin.linkedin(get, sleep=lambda s: None, con=isolated) == []   # already seen -> no detail request
    assert not any("/jobPosting/" in u for u in seen_urls[before:])
    assert run.to_job(items[0], "LinkedIn", -19)[0]["score"] >= 10


def test_linkedin_stops_on_429_but_keeps_what_it_has(isolated):
    n = {"list": 0}

    def get(url):
        if "/seeMoreJobPostings/" in url:
            n["list"] += 1
            if n["list"] > 1:
                raise http.RateLimited("429")
            return LI_LIST
        return LI_DETAIL
    assert [i["id"] for i in linkedin.linkedin(get, sleep=lambda s: None, con=isolated)] == ["li111"]
    with pytest.raises(http.RateLimited):                       # nothing collected -> a real failure
        linkedin.linkedin(lambda u: (_ for _ in ()).throw(http.RateLimited("429")), sleep=lambda s: None, con=isolated)


def test_linkedin_locations_setting(isolated):
    isolated.execute("INSERT INTO settings(key,value) VALUES('linkedin_locations',?)", (json.dumps(["Singapore", "Remote"]),))
    isolated.commit()
    urls = []
    linkedin.linkedin(lambda u: (urls.append(u), "")[1], sleep=lambda s: None, con=isolated)
    assert any("location=Singapore" in u for u in urls) and any("location=Remote" in u for u in urls)


# ---- JSON-LD career pages ----------------------------------------------------------------------------
LD_PAGE = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebSite"},
{"@type":"JobPosting","title":"Flutter Engineer","description":"&lt;p&gt;Dart &amp;amp; Flutter&lt;/p&gt;","datePosted":"2026-10-21",
"identifier":{"@type":"PropertyValue","value":"J-9"},"hiringOrganization":{"@type":"Organization","name":"Initech"},
"jobLocationType":"TELECOMMUTE","jobLocation":{"@type":"Place","address":{"addressLocality":"Lisbon","addressCountry":"PT"}},
"baseSalary":{"@type":"MonetaryAmount","currency":"EUR","value":{"minValue":50000,"maxValue":70000,"unitText":"YEAR"}},
"url":"https://initech.example/jobs/j-9"}]}</script></html>"""


def test_jsonld_posting_parsing():
    (p,) = jsonld.extract_postings(LD_PAGE)
    it = jsonld.to_item(p, "https://initech.example/careers", "fallback")
    assert it["id"] == "J-9" and it["company"] == "Initech" and it["location"] == "Lisbon, PT" and it["remote"]
    assert it["salary"] == "€50k-70k/年" and it["date"].startswith("2026-10-21") and it["url"].endswith("/jobs/j-9")
    assert it["text"].startswith("Dart") and "<" not in it["text"]
    assert jsonld.extract_postings("<script type='application/ld+json'>{bad json</script>") == []


def test_jsonld_listing_page_follows_job_links(isolated, monkeypatch):
    listing = '<a href="/jobs/1">One</a><a href="/jobs/2?utm=x">Two</a><a href="https://other.com/jobs/3">x</a><a href="/about">About</a>'
    assert jsonld.job_links(listing, "https://initech.example/careers") == ["https://initech.example/jobs/1", "https://initech.example/jobs/2"]
    add(isolated, "web", "https://initech.example/careers", "Initech")
    pages = {"https://initech.example/careers": listing, "https://initech.example/jobs/1": LD_PAGE, "https://initech.example/jobs/2": "<html></html>"}
    monkeypatch.setattr(http, "get_text", lambda u, timeout=25, headers=None: pages[u])
    (item,) = jsonld.jsonld()
    assert item["title"] == "Flutter Engineer"


# ---- Adzuna / The Muse / Arbeitnow / USAJOBS ----------------------------------------------------------
def test_keyed_sources_skip_without_keys(monkeypatch, isolated):
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_KEY", "USAJOBS_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    stats = run.run(isolated, sources=[s for s in more.SOURCES if s[0] in ("Adzuna", "USAJOBS")], force=True)
    assert all("skipped" in v and "key" in v["skipped"] for v in stats.values())
    assert isolated.execute("SELECT COUNT(*) FROM source_state").fetchone()[0] == 0   # retried next time, no error recorded


def test_adzuna_and_usajobs_parse(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "id"), monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    monkeypatch.setenv("USAJOBS_KEY", "k"), monkeypatch.setenv("USAJOBS_EMAIL", "me@example.com")
    az = more.adzuna(lambda url: {"results": [{"id": "9", "title": "Flutter Developer", "redirect_url": "https://adzuna/x",
                                               "company": {"display_name": "Acme"}, "location": {"display_name": "London"},
                                               "salary_min": 50000, "salary_max": 60000, "created": "2026-10-22T08:00:00Z",
                                               "description": "Remote ok", "category": {"label": "IT Jobs"}}]})
    assert len(az) == 1 and az[0]["remote"] and az[0]["company"] == "Acme"      # same id across countries/queries: de-duplicated
    seen = {}
    us = more.usajobs(lambda url, headers=None: seen.update(h=headers) or {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
        "PositionID": "P1", "PositionTitle": "Mobile Developer (iOS)", "PositionURI": "https://usajobs.gov/p1", "OrganizationName": "NASA",
        "PositionLocationDisplay": "Houston", "PositionRemuneration": [{"MinimumRange": "100000", "MaximumRange": "150000"}],
        "PublicationStartDate": "2026-10-20", "UserArea": {"Details": {"JobSummary": "Build iOS apps"}}}}]}})
    assert us[0]["salary"] == "$100k-150k/年" and seen["h"]["Authorization-Key"] == "k" and seen["h"]["User-Agent"] == "me@example.com"


def test_muse_and_arbeitnow_parse():
    mu = more.themuse(2, lambda url: {"page_count": 1, "results": [{"id": 3, "name": "iOS Engineer", "company": {"name": "Muse Co"},
                                                                     "locations": [{"name": "Flexible / Remote"}], "levels": [{"name": "Senior"}],
                                                                     "publication_date": "2026-10-21T10:00:00Z", "contents": "<p>Swift</p>",
                                                                     "categories": [{"name": "Software Engineering"}],
                                                                     "refs": {"landing_page": "https://themuse.com/j/3"}}]})
    assert len(mu) == 1 and mu[0]["remote"] and mu[0]["tags"] == ["Software Engineering", "Senior"]
    an = more.arbeitnow(3, lambda url: {"data": [{"slug": "flutter-berlin", "company_name": "Berlin GmbH", "title": "Flutter Developer",
                                                  "description": "<p>Dart</p>", "remote": False, "url": "https://arbeitnow.com/j/1",
                                                  "tags": ["IT"], "job_types": ["full time"], "location": "Berlin", "created_at": 1760000000}],
                                        "links": {"next": None}})
    assert len(an) == 1 and an[0]["location"] == "Berlin" and an[0]["date"].startswith("2025-")


def test_all_sources_have_unique_ids_inside_the_site_range():
    names = [s[0] for s in run.all_sources()]
    ids = [s[1] for s in run.all_sources()]
    assert len(set(names)) == len(names) and len(set(ids)) == len(ids) and all(-100 < i < 0 for i in ids)
    assert {"Greenhouse", "Lever", "Ashby", "Recruitee", "Personio", "Adzuna", "The Muse", "Arbeitnow", "USAJOBS",
            "Career pages", "LinkedIn", "RemoteOK", "Himalayas"} <= set(names)


def test_search_terms_come_from_enabled_roles(isolated):
    from jobboard import roles
    terms = roles.search_terms(isolated, 6)
    assert terms[:2] == ["flutter", "dart"] and "ios" in terms and all(t.isascii() for t in terms)
    isolated.execute("UPDATE roles SET enabled=0 WHERE key='flutter'")
    assert "flutter" not in roles.search_terms(isolated, 6)


# ---- regressions: the "database is locked" crash that silently froze the public site ----------------------
def test_slugs_with_spaces_are_not_boards():
    found = discover.extract("see https://jobs.ashbyhq.com/solana%20foundation and https://jobs.lever.co/real-co/abc")
    assert ("ashby", "solana foundation") not in found and ("lever", "real-co") in found


def test_probe_never_raises(monkeypatch):
    import http.client as hc
    for exc in (hc.InvalidURL("bad url"), hc.IncompleteRead(b""), RuntimeError("x"), http.RateLimited("429")):
        def boom(url, *a, **kw):
            raise exc
        assert discover.probe("ashby", "x", get_json=boom, get_text=boom) == (False, None)


def test_failed_discovery_does_not_keep_the_database_locked(isolated):
    a, t, s = job("Flutter Dev", iso(1), n=1)
    t += "\nhttps://jobs.lever.co/realco/1"
    db.upsert(isolated, a, t, s)
    isolated.commit()

    def explode(ats_, slug):
        raise RuntimeError("probe blew up")
    with pytest.raises(RuntimeError):
        discover.run(isolated, channels=("db",), probe_fn=explode, pace=0)
    assert not isolated.in_transaction                                  # rolled back, nothing left open
    other = db.connect()                                                # a second connection can write at once
    other.execute("INSERT INTO settings(key,value) VALUES('probe','1')")
    other.commit()


def test_one_broken_fetch_step_does_not_stop_the_rest(isolated, monkeypatch, capsys):
    from jobboard import cli
    from jobboard.sources import run as run_mod, telegram_public
    monkeypatch.setattr(cli, "discover_run", lambda con, ch: (_ for _ in ()).throw(RuntimeError("discover broke")))
    monkeypatch.setattr(telegram_public, "run", lambda con: (_ for _ in ()).throw(RuntimeError("telegram broke")))
    monkeypatch.setattr(run_mod, "run", lambda **kw: {"RemoteOK": {"fetched": 1}})
    cli.main(["fetch", "--mode", "public", "--no-telegram"])
    out = json.loads(capsys.readouterr().out)
    assert "discover broke" in out["discover"]["error"] and "telegram broke" in out["telegram_public"]["error"]
    assert out["web"] == {"RemoteOK": {"fetched": 1}} and "scrubbed" in out                          # the sources and the scrub still ran
