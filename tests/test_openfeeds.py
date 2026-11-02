"""The open feeds added on 2026-10-28 and the extra GitHub issue boards. No network."""
from jobboard import mode
from jobboard.sources import github, openfeeds, run

HN = {"hits": [
    {"objectID": "1", "title": "Acme (YC W24) Is Hiring Software Engineers", "url": "https://acme.com/jobs", "created_at": "2026-10-20T10:00:00Z"},
    {"objectID": "2", "title": "9 Mothers (YC P26) Is Hiring in Austin, TX", "url": None, "created_at": "2026-10-21T10:00:00Z"},
    {"objectID": "3", "title": "", "created_at": "2026-10-21T10:00:00Z"}]}


def test_hn_jobs():
    a, b = openfeeds.hn_jobs(lambda u: HN)
    assert (a["company"], a["title"], a["tags"]) == ("Acme", "Software Engineers", ["YC"]) and a["url"] == "https://acme.com/jobs"
    assert b["company"] == "9 Mothers" and b["location"] == "Austin, TX" and b["url"].endswith("item?id=2") and "Hiring" in b["title"]


PY = '''<rss><channel><item><title>Senior Python Engineer (Remote), Manychat</title><link>https://www.python.org/jobs/1/</link>
<description>Barcelona, Spain &lt;p&gt;Build things&lt;/p&gt;</description></item><item><title></title><link>x</link></item></channel></rss>'''


def test_python_org_has_no_dates_so_first_sight_counts():
    (j,) = openfeeds.python_org(lambda u: PY)
    assert (j["title"], j["company"], j["location"]) == ("Senior Python Engineer (Remote)", "Manychat", "Barcelona, Spain")
    assert j["date"] and j["tags"] == ["Python"]


JP = '''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>Backend Engineer</title><link>https://jobspresso.co/job/b/</link>
<dc:creator><![CDATA[Docker<br>⚲&nbsp;United States, Canada]]></dc:creator><pubDate>Tue, 27 Oct 2026 12:22:04 +0000</pubDate>
<description><![CDATA[<p>Hello</p>]]></description></item></channel></rss>'''


def test_jobspresso():
    (j,) = openfeeds.jobspresso(lambda u: JP)
    assert (j["company"], j["location"], j["date"]) == ("Docker", "United States, Canada", "2026-10-27T12:22:04+00:00") and j["remote"]


LJ = '''<rss xmlns:job="https://larajobs.com/ns"><channel><item><title>Laravel Dev</title><link>https://larajobs.com/job/1</link>
<pubDate>Sun, 25 Oct 2026 18:21:28 +0000</pubDate><job:location>Remote/USA</job:location><job:salary>$160k</job:salary>
<job:company>Evo</job:company><job:tags>Full Time,Laravel</job:tags><description>text</description></item></channel></rss>'''


def test_larajobs():
    (j,) = openfeeds.larajobs(lambda u: LJ)
    assert (j["company"], j["salary"], j["tags"], j["remote"]) == ("Evo", "$160k", ["Full Time", "Laravel"], True)


def test_new_sources_are_public_and_ids_unique():
    names = {s[0] for s in run.all_sources("public")}
    assert {"HN Jobs", "Python.org Jobs", "Jobspresso", "LaraJobs"} <= names
    ids = [s[1] for s in run.all_sources()]
    assert len(set(ids)) == len(ids) and all(-100 < i < 0 for i in ids)
    assert {"GitHub: CocoaHeadsBrasil/vagas", "GitHub: phpdevbr/vagas"} <= names
    assert all(mode.kind(n, i) in ('site', 'gov', 'github') for n, i, *_ in run.all_sources('public'))


def test_v2ex_topics_become_jobs():
    rows = [{"id": 11, "title": "[远程/全职] 招聘后端工程师", "url": "https://www.v2ex.com/t/11", "content": "职责：后端开发", "content_rendered": "<p>职责：后端开发</p>", "created": 1793000000},
            {"id": 12, "title": "", "created": 1793000000}, {"id": 13, "title": "x", "deleted": True, "created": 1793000000}]
    (j,) = openfeeds.v2ex("jobs", lambda u: rows)
    assert j["id"] == "v211" and j["remote"] and j["text"] == "职责：后端开发" and j["date"].startswith("2026-")
    assert openfeeds.v2ex("remote", lambda u: rows[:1])[0]["remote"]


VUE = '''<rss><channel><item><title>Frontend Engineer II</title><link>https://vuejobs.com/jobs/openly-fe?utm_source=rssfeed</link>
<guid>https://vuejobs.com/jobs/openly-fe</guid><description>&lt;p&gt;&lt;strong&gt;Employer:&lt;/strong&gt; Openly&lt;/p&gt;&lt;p&gt;&lt;strong&gt;Location:&lt;/strong&gt; Remote&lt;/p&gt;&amp;nbsp;</description>
<pubDate>Mon, 02 Nov 2026 14:39:38 +0000</pubDate></item></channel></rss>'''


def test_vuejobs_reads_employer_and_location_and_survives_html_entities():
    (j,) = openfeeds.vuejobs(lambda u: VUE.replace("&amp;nbsp;", "&nbsp;"))
    assert (j["company"], j["location"], j["remote"], j["id"]) == ("Openly", "Remote", True, "https://vuejobs.com/jobs/openly-fe")


CJL = '''<rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title>Backend Engineer</title>
<link>https://cryptojobslist.com/jobs/be-at-acme</link><dc:creator>Acme</dc:creator><location>Remote (Worldwide)</location>
<description>&lt;p&gt;Build&lt;/p&gt;</description></item></channel></rss>'''


def test_cryptojobslist():
    (j,) = openfeeds.cryptojobslist(lambda u: CJL)
    assert (j["company"], j["remote"], j["tags"]) == ("Acme", True, ["Web3"]) and j["date"]
