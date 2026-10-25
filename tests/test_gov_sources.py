"""Government sources (Germany, Sweden, EURES, Canada) and visa-sponsorship detection. No network."""
import pytest

from jobboard import db, parse, profile
from jobboard.sources import gov, run


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path)
    profile.set_rules(None)
    yield db.connect()
    profile.set_rules(None)


@pytest.mark.parametrize("text,expected", [
    ("We offer visa sponsorship and relocation support", "yes"),
    ("Visa sponsorship available for the right candidate", "yes"),
    ("We will sponsor your work permit", "yes"),
    ("Wir unterstützen bei Visum und Blue Card", "yes"),
    ("公司提供签证担保，可协助办理工作签证", "yes"),
    ("Employer has applied for an LMIA", "yes"),
    ("Relocation package included", "maybe"),
    ("We do not provide visa assistance, and our cooperation model does not include benefits", ""),
    ("Sorry, we don't offer visa support or relocation", ""),
    ("We cannot provide visa sponsorship", ""),
    ("no worries, visa sponsorship available", "yes"),
    ("Visa assistance provided for the right candidate", "yes"),
    ("No visa sponsorship is available for this role", ""),
    ("Unfortunately we are unable to sponsor visas", ""),
    ("Candidates must have the right to work in the UK. Relocation not offered", ""),
    ("We do not offer visa sponsorship, but relocation is possible", ""),   # refusal beats everything
    ("不提供签证担保", ""),
    ("Senior Flutter developer, remote", ""),
    ("", ""),
])
def test_visa_flag(text, expected):
    assert parse.visa_flag(text) == expected


def test_visa_flows_to_db_filter_and_survives_rescoring(isolated):
    item = {"id": 1, "url": "https://x/1", "title": "Flutter Developer", "company": "Acme", "location": "Berlin", "tags": [],
            "salary": None, "text": "Visa sponsorship available.", "date": "2026-10-20T00:00:00+00:00", "remote": False}
    a, t, s = run.to_job(item, "X", -9)
    assert a["visa"] == "yes"
    official = dict(item, id=2, title="Android Developer", text="no text hint", visa="yes")   # e.g. Canada LMIA
    a2, t2, s2 = run.to_job(official, "Canada Job Bank", -24)
    assert a2["visa"] == "yes"
    for a_, t_, s_ in ((a, t, s), (a2, t2, s2)):
        db.upsert(isolated, a_, t_, s_)
    isolated.commit()
    db.reanalyze(isolated)
    assert {r["title"]: r["visa"] for r in isolated.execute("SELECT title, visa FROM jobs")} == {"Flutter Developer": "yes", "Android Developer": "yes"}


def test_visa_yes_from_any_source_wins_on_merge(isolated):
    base = {"url": "https://x", "title": "iOS Engineer", "company": "Acme", "location": "Oslo", "tags": [], "salary": None,
            "date": "2026-10-20T00:00:00+00:00", "remote": False}
    a1, t1, s1 = run.to_job(dict(base, id=1, text="plain text"), "A", -9)
    a2, t2, s2 = run.to_job(dict(base, id=2, text="plain text", visa="yes"), "B", -10)
    assert db.upsert(isolated, a1, t1, s1) and not db.upsert(isolated, a2, t2, s2)
    assert isolated.execute("SELECT visa FROM jobs").fetchone()[0] == "yes"


GERMANY = {"ergebnisliste": [
    {"referenznummer": "1-A", "stellenangebotsTitel": "Flutter Entwickler (m/w/d)", "firma": "X GmbH", "hauptberuf": "Softwareentwickler/in",
     "stellenlokationen": [{"adresse": {"ort": "Berlin"}}, {"adresse": {"ort": "Berlin"}}, {"adresse": {"ort": "Jena"}}],
     "datumErsteVeroeffentlichung": "2026-10-20"},
    {"referenznummer": "2-B", "stellenangebotsTitel": "Buchhalter", "firma": "Y", "stellenlokationen": []}]}
GERMANY_DETAIL = {"stellenangebotsBeschreibung": "Wir bieten Blue Card Unterstützung", "homeofficemoeglich": True}


def test_germany(isolated):
    calls = []

    def gj(url):
        calls.append(url)
        return GERMANY_DETAIL if "jobdetails" in url else GERMANY
    items = gov.germany(gj)
    assert len(items) == 1                                   # same job found by several search terms is kept once
    it = items[0]
    assert it["url"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/1-A" and it["company"] == "X GmbH"
    assert it["location"] == "Berlin, Jena, Germany" and it["remote"] and it["date"].startswith("2026-10-20")
    assert sum("jobdetails" in u for u in calls) == 1        # details only for the title that matched
    assert run.to_job(it, "Germany (BA)", -21)[0]["visa"] == "yes"


def test_sweden(isolated):
    hit = {"id": "9", "headline": "Android Developer", "employer": {"name": "Acme AB"}, "webpage_url": "https://arbetsformedlingen.se/x/9",
           "workplace_address": {"municipality": "Malmö", "region": "Skåne län", "country": "Sverige"}, "occupation": {"label": "Mjukvaruutvecklare"},
           "description": {"text": "Distansarbete möjligt"}, "publication_date": "2026-10-23T10:00:00", "salary_description": None}
    items = gov.sweden(lambda url: {"hits": [hit, dict(hit, id="10", headline="Lärare")]})
    assert [i["id"] for i in items] == ["se9"] and items[0]["remote"] and items[0]["location"] == "Malmö, Skåne län, Sverige"


def test_eures(isolated):
    jv = {"id": "ABC", "title": "Mobile App / Flutter Developer", "description": "<p>Work from home</p>", "creationDate": 1792611926673,
          "employer": {"name": "KST"}, "locationMap": {"AT": ["AT130"], "DE": []}}
    bodies = []

    def post(url, body):
        bodies.append(body)
        return {"jvs": [jv, dict(jv, id="Z", title="Zerspanungstechniker")]}
    items = gov.eures(post)
    assert [i["id"] for i in items] == ["euABC"] and items[0]["location"] == "Austria, Germany" and items[0]["remote"]
    assert items[0]["url"].endswith("/jv-details/ABC?lang=en") and items[0]["date"].startswith("2026-10-")
    assert bodies[0]["keywords"] == [{"keyword": "flutter", "specificSearchCode": "TITLE"}] and bodies[0]["publicationPeriod"] == "LAST_MONTH"


CA_CARD = '''<article id="article-%s" class="action-buttons"><a href="/x"><h3 class="title"><span class="flag"><span class="telework">Remote</span></span>
<span class="noctitle"> %s </span></h3><ul><li class="date">October 22, 2026 </li><li class="business">Acme &amp; Co</li>
<li class="location"><span class="wb-inv">Location</span> Calgary (AB) </li><li class="salary"><span class="fa"></span> Salary $90,000.00 annually</li></ul></a></article>'''


def test_canada_marks_lmia_jobs(isolated):
    plain_page = CA_CARD % ("1", "android developer") + CA_CARD % ("2", "ios developer")
    lmia_page = CA_CARD % ("2", "ios developer")

    def get(url):
        return lmia_page if "fskl=101010" in url else plain_page
    items = {i["id"]: i for i in gov.canada(get, sleep=lambda s: None)}
    assert set(items) == {"ca1", "ca2"} and items["ca2"]["visa"] == "yes" and items["ca1"]["visa"] == ""
    it = items["ca1"]
    assert it["company"] == "Acme & Co" and it["location"] == "Calgary (AB), Canada" and it["remote"] and it["date"].startswith("2026-10-22")
    assert it["salary"] == "$90,000.00 annually" and it["url"].endswith("/jobposting/1")


def test_gov_sources_registered_with_unique_ids():
    ids = [s[1] for s in run.all_sources()]
    assert len(set(ids)) == len(ids) and {"Germany (BA)", "Sweden (AF)", "EURES (EU)", "Canada Job Bank"} <= {s[0] for s in run.all_sources()}


def test_visa_flags_are_rebuilt_once_when_the_rules_change(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "HOME", tmp_path / "h2")
    con = db.connect()
    item = {"id": 1, "url": "https://x/1", "title": "Flutter Developer", "company": "Acme", "location": "Berlin", "tags": [], "salary": None,
            "text": "We do not provide visa assistance.", "date": "2026-10-20T00:00:00+00:00", "remote": False}
    a, t, s = run.to_job(item, "X", -9)
    assert a["visa"] == ""
    db.upsert(con, a, t, s)
    con.execute("UPDATE jobs SET visa='yes'")                                      # an old wrong flag, written by the previous rules
    con.execute("INSERT INTO source_state(name,last_run,last_count) VALUES('X', 12345, 1)")
    con.execute("DELETE FROM settings WHERE key='visa_rules'")
    con.commit()
    assert db.connect().execute("SELECT visa FROM jobs").fetchone()[0] == ""       # cleared by the migration ...
    assert db.connect().execute("SELECT last_run FROM source_state WHERE name='X'").fetchone()[0] == 0   # ... and every source is due again
    db.upsert(con, *run.to_job(dict(item, id=2, text="Relocation package included."), "Y", -10))
    con.commit()
    assert db.connect().execute("SELECT visa FROM jobs").fetchone()[0] == "maybe"  # ... and a later sighting can set it again
    db.upsert(con, *run.to_job(dict(item, id=3, text="Visa sponsorship available."), "Z", -11))
    con.commit()
    assert db.connect().execute("SELECT visa FROM jobs").fetchone()[0] == "yes"
    db.upsert(con, *run.to_job(dict(item, id=4, text="Relocation package included."), "W", -12))
    con.commit()
    assert db.connect().execute("SELECT visa FROM jobs").fetchone()[0] == "yes"   # a "maybe" never downgrades a "yes"
    db.connect()
    assert db.connect().execute("SELECT visa FROM jobs").fetchone()[0] == "yes"   # the migration runs only once
