#!/usr/bin/env python3
"""Regenerate the data-driven parts of dkumbhar.github.io.

The site is plain static HTML served by GitHub Pages with no build step on
GitHub's side. This script only runs on your machine. It reads:

  content/site.json          site settings, topics, and the list of articles
  articles/<slug>/index.html each article's own title, description, date,
                             chapter headings and word count

and rewrites these generated files:

  index.html                 the sections between <!-- build:NAME --> markers
  writing/index.html         all articles, newest first
  topics/index.html          every topic with matching articles and chapters
  feed.xml                   RSS 2.0 feed
  sitemap.xml                every page

It also refreshes the "Continue reading" block at the end of each article
(between <!-- build:continue --> markers) and never touches article prose.

Usage (Python 3.8+, no packages needed):
  python3 scripts/build.py           write the files
  python3 scripts/build.py --check   report problems, write nothing
"""

import datetime as dt
import html
import json
import os
import re
import sys
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = "--check" in sys.argv
problems = []


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def write(rel, text):
    path = os.path.join(ROOT, rel)
    if CHECK:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def esc(s):
    return html.escape(s, quote=True)


INLINE = r"</?(?:a|b|i|em|strong|code|span|sup|sub|abbr|small|mark|time)\b[^>]*>"


def strip_tags(s):
    """Plain text: inline tags vanish, block tags become spaces."""
    s = re.sub(INLINE, "", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def meta(doc, prop, attr="property"):
    m = re.search(r'<meta %s="%s" content="([^"]*)"' % (attr, re.escape(prop)), doc)
    return html.unescape(m.group(1)) if m else ""


# ---------------------------------------------------------------- articles

def load_article(entry, cfg):
    rel = entry["path"].strip("/") + "/index.html"
    doc = read(rel)
    a = dict(entry)
    a["url"] = "/" + entry["path"].strip("/") + "/"
    a["abs"] = cfg["site"]["url"] + a["url"]

    # Description: the article's meta description.
    a["description"] = meta(doc, "description", "name")
    if not a["description"]:
        problems.append("%s has no meta description" % rel)

    # Date: JSON-LD datePublished, then article:published_time.
    m = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', doc) or \
        re.search(r'article:published_time" content="(\d{4}-\d{2}-\d{2})', doc)
    if not m:
        problems.append("%s has no datePublished" % rel)
        a["date"] = dt.date.today()
    else:
        a["date"] = dt.date.fromisoformat(m.group(1))

    # Image: og:image (absolute) -> card image looked up in /assets by slug.
    a["og_image"] = meta(doc, "og:image")
    slug = entry["path"].strip("/").split("/")[-1]
    a["slug"] = slug
    for ext in ("webp", "png", "jpg"):
        if os.path.exists(os.path.join(ROOT, "assets", "cover-%s.%s" % (slug, ext))):
            a["cover"] = "/assets/cover-%s.%s" % (slug, ext)
            break
    else:
        a["cover"] = ""

    # Body text for reading time: <main> without svg, script, style or code blocks.
    body = doc[doc.find("<main"):doc.find("</main>")] if "<main" in doc else doc
    body = re.sub(r"<!-- build:continue -->.*?<!-- /build:continue -->", " ", body, flags=re.S)
    for tag in ("svg", "script", "style", "pre"):
        body = re.sub(r"<%s\b.*?</%s>" % (tag, tag), " ", body, flags=re.S)
    words = len(strip_tags(body).split())
    a["words"] = words
    a["minutes"] = max(1, round(words / cfg["site"]["words_per_minute"]))

    # Chapters: <h2 id="..."> headings, with the first paragraph after each as an excerpt.
    chapters = []
    for m in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>(.*?)(?=<h2[\s>]|</main>)', doc, flags=re.S):
        cid, head, rest = m.group(1), m.group(2), m.group(3)
        num = re.search(r'<span class="num">([^<]*)</span>', head)
        title = strip_tags(re.sub(r'<span class="num">[^<]*</span>', "", head)).strip()
        p = re.search(r"<p(?: class=\"[^\"]*\")?>(.*?)</p>", rest, flags=re.S)
        excerpt = " ".join(strip_tags(p.group(1)).split()) if p else ""
        sec_body = rest
        for tag in ("svg", "script", "style", "pre"):
            sec_body = re.sub(r"<%s\b.*?</%s>" % (tag, tag), " ", sec_body, flags=re.S)
        sw = len(strip_tags(sec_body).split())
        chapters.append({
            "id": cid,
            "num": num.group(1).strip() if num else "",
            "title": title,
            "excerpt": excerpt,
            "minutes": max(1, round(sw / cfg["site"]["words_per_minute"])),
        })
    a["chapters"] = {c["id"]: c for c in chapters}
    a["chapter_order"] = [c["id"] for c in chapters]

    for tslug, ids in entry.get("topics", {}).items():
        for cid in ids:
            if cid not in a["chapters"]:
                problems.append("%s: topic %s lists missing section #%s" % (rel, tslug, cid))
    for cid in entry.get("featured_sections", []):
        if cid not in a["chapters"]:
            problems.append("%s: featured section #%s not found" % (rel, cid))
    return a


def topic_of(a, cfg):
    for t in cfg["topics"]:
        if t["slug"] == a["category"]:
            return t
    problems.append("%s: category %s is not in topics" % (a["path"], a["category"]))
    return {"slug": a["category"], "name": a["category"], "description": ""}


def fmt_date(d):
    return d.strftime("%B %-d, %Y") if os.name != "nt" else d.strftime("%B %d, %Y").replace(" 0", " ")


def meta_line(a, extra_minutes=None):
    mins = extra_minutes if extra_minutes is not None else a["minutes"]
    return ('<p class="dk-meta"><time datetime="%s">%s</time><span>%d min read</span></p>'
            % (a["date"].isoformat(), fmt_date(a["date"]), mins))


# ---------------------------------------------------------------- blocks

def block_featured(arts, cfg):
    by_path = {a["path"]: a for a in arts}
    picks = [by_path[p] for p in cfg["featured"] if p in by_path] or arts[:1]
    lead, rest = picks[0], picks[1:4]
    t = topic_of(lead, cfg)
    out = []
    out.append('<article class="dk-lead" aria-labelledby="lead-title">')
    if lead["cover"]:
        out.append('  <a class="dk-lead-media" href="%s" tabindex="-1" aria-hidden="true">'
                   '<img src="%s" width="1280" height="720" alt="" fetchpriority="high" decoding="async"></a>'
                   % (lead["url"], lead["cover"]))
    out.append('  <div class="dk-lead-body">')
    out.append('    <p class="dk-cat"><a href="/topics/#%s">%s</a></p>' % (t["slug"], esc(t["name"])))
    out.append('    <h3 class="dk-lead-title" id="lead-title"><a class="dk-title-link" href="%s">%s</a></h3>'
               % (lead["url"], esc(lead["title"])))
    out.append('    <p class="dk-excerpt">%s</p>' % esc(lead["description"]))
    out.append('    ' + meta_line(lead))
    out.append('    <a class="dk-more" href="%s">Read the field guide<span class="dk-sr">: %s</span></a>'
               % (lead["url"], esc(lead["title"])) if lead.get("format") == "Field guide" else
               '    <a class="dk-more" href="%s">Read article<span class="dk-sr">: %s</span></a>'
               % (lead["url"], esc(lead["title"])))
    out.append('  </div>')
    out.append('</article>')

    # Secondary slots: other featured articles first, then chapters of the lead article.
    cards = []
    for a in rest:
        ta = topic_of(a, cfg)
        cards.append((ta, a["title"], a["url"], a["description"], meta_line(a), "Read article", None))
    for cid in lead.get("featured_sections", []):
        if len(cards) >= 4:
            break
        c = lead["chapters"].get(cid)
        if not c:
            continue
        tc = next((tt for tt in cfg["topics"] if cid in lead.get("topics", {}).get(tt["slug"], [])), t)
        cards.append((tc, c["title"], lead["url"] + "#" + cid, c["excerpt"],
                      '<p class="dk-meta"><span>Section %s</span><span>%d min read</span></p>'
                      % (str(int(c["num"])) if c["num"].isdigit() else c["num"], c["minutes"]),
                      "Read section", c["num"]))
    if cards:
        only_sections = all(x[6] is not None for x in cards)
        out.append('<div class="dk-inside">')
        if only_sections:
            out.append('  <h3 class="dk-inside-h">Start with a section</h3>')
            out.append('  <p class="dk-inside-note">Each section of the field guide stands on its own.</p>')
        else:
            out.append('  <h3 class="dk-inside-h">More featured writing</h3>')
        out.append('  <ul class="dk-chapters">')
        for tc, title, url, excerpt, mline, verb, num in cards:
            if len(excerpt) > 190:
                excerpt = excerpt[:190].rsplit(" ", 1)[0].rstrip(",;:") + "…"
            out.append('    <li>')
            out.append('      <p class="dk-cat"><a href="/topics/#%s">%s</a></p>' % (tc["slug"], esc(tc["name"])))
            out.append('      <h4 class="dk-ch-title"><a class="dk-title-link" href="%s">%s</a></h4>' % (url, esc(title)))
            out.append('      <p class="dk-ch-excerpt">%s</p>' % esc(excerpt))
            out.append('      ' + mline)
            out.append('      <a class="dk-more" href="%s">%s<span class="dk-sr">: %s</span></a>' % (url, verb, esc(title)))
            out.append('    </li>')
        out.append('  </ul>')
        out.append('</div>')
    return "\n".join(out)


def row(a, cfg, heading="h3"):
    t = topic_of(a, cfg)
    return "\n".join([
        '  <li class="dk-row">',
        '    <p class="dk-row-date"><time datetime="%s">%s</time></p>' % (a["date"].isoformat(), fmt_date(a["date"])),
        '    <div>',
        '      <%s class="dk-row-title"><a class="dk-title-link" href="%s">%s</a></%s>' % (heading, a["url"], esc(a["title"]), heading),
        '      <p class="dk-row-excerpt">%s</p>' % esc(a["description"]),
        '    </div>',
        '    <p class="dk-row-side"><a href="/topics/#%s">%s</a><span>%d min read</span>'
        '<a class="dk-more" href="%s">Read<span class="dk-sr">: %s</span></a></p>'
        % (t["slug"], esc(t["name"]), a["minutes"], a["url"], esc(a["title"])),
        '  </li>',
    ])


def block_latest(arts, cfg):
    n = cfg["site"]["latest_count"]
    out = ['<ul class="dk-list">'] + [row(a, cfg) for a in arts[:n]] + ['</ul>']
    if len(arts) > n:
        out.append('<p class="dk-viewall"><a class="dk-more" href="/writing/">View all %d articles</a></p>' % len(arts))
    else:
        out.append('<p class="dk-viewall"><a class="dk-more" href="/writing/">Browse the writing index</a></p>')
    return "\n".join(out)


def topic_counts(arts, cfg):
    counts = []
    for t in cfg["topics"]:
        n_art = sum(1 for a in arts if a["category"] == t["slug"] or a.get("topics", {}).get(t["slug"]))
        n_sec = sum(len(a.get("topics", {}).get(t["slug"], [])) for a in arts)
        if n_art:
            counts.append((t, n_art, n_sec))
    return counts


def block_topics(arts, cfg):
    out = ['<ul class="dk-topics">']
    for t, n_art, n_sec in topic_counts(arts, cfg):
        label = ("%d section%s" % (n_sec, "" if n_sec == 1 else "s")) if len(arts) == 1 else \
                ("%d article%s" % (n_art, "" if n_art == 1 else "s"))
        out.append('  <li><a href="/topics/#%s">%s <span class="dk-count">%s</span></a></li>'
                   % (t["slug"], esc(t["name"]), label))
    out.append('</ul>')
    if len(arts) == 1:
        out.append('<p class="dk-topics-note">Topics are drawn from the sections of the published field guide. '
                   'New articles are added to the topics they cover.</p>')
    return "\n".join(out)


def block_jsonld(arts, cfg):
    s = cfg["site"]
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Person", "@id": s["url"] + "/#person", "name": s["author"], "url": s["url"] + "/",
             "jobTitle": "Associate Partner",
             "description": "Enterprise architect and Hyperscaler AI Leader writing about enterprise AI, agentic systems, architecture and technology transformation.",
             "knowsAbout": ["Enterprise AI", "Agentic AI", "Enterprise architecture", "Application modernization",
                            "Cloud platforms", "AI governance", "Technology transformation"],
             "sameAs": [s["linkedin"], s["github"]]},
            {"@type": "WebSite", "@id": s["url"] + "/#website", "name": s["name"], "url": s["url"] + "/",
             "description": s["tagline"], "inLanguage": "en-US", "publisher": {"@id": s["url"] + "/#person"}},
            {"@type": "Blog", "@id": s["url"] + "/#blog", "name": s["name"], "url": s["url"] + "/",
             "description": s["tagline"], "author": {"@id": s["url"] + "/#person"},
             "blogPost": [{"@type": "BlogPosting", "headline": a["title"], "url": a["abs"],
                           "datePublished": a["date"].isoformat(), "description": a["description"],
                           "author": {"@id": s["url"] + "/#person"}} for a in arts]},
        ],
    }
    return '<script type="application/ld+json">%s</script>' % json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def replace_block(doc, name, content, rel):
    pat = re.compile(r"(<!-- build:%s -->)(.*?)(<!-- /build:%s -->)" % (name, name), re.S)
    if not pat.search(doc):
        problems.append("%s: missing <!-- build:%s --> markers" % (rel, name))
        return doc
    return pat.sub(lambda m: m.group(1) + "\n" + content + "\n" + m.group(3), doc)


# ---------------------------------------------------------------- pages

def chrome(cfg, active):
    """Shared navigation used by every page."""
    s = cfg["site"]
    cur = lambda k: ' aria-current="page"' if k == active else ""
    return """<a class="dk-skip" href="#main">Skip to content</a>
<header class="dk-nav">
  <div class="dk-nav-inner">
    <a class="dk-brand" href="/">Deepak Kumbhar</a>
    <nav aria-label="Main">
      <ul>
        <li><a href="/writing/"%s>Writing</a></li>
        <li><a href="/topics/"%s>Topics</a></li>
        <li><a href="/#about"%s>About</a></li>
        <li class="dk-push"><a class="dk-icon-link" href="%s" aria-label="Deepak Kumbhar on LinkedIn"><svg class="dk-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z"/></svg></a></li>
        <li><a class="dk-icon-link" href="%s" aria-label="Deepak Kumbhar on GitHub"><svg class="dk-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 .3a12 12 0 0 0-3.8 23.38c.6.12.83-.26.83-.57L9 21.07c-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.08-.74.09-.73.09-.73 1.2.09 1.83 1.24 1.83 1.24 1.07 1.83 2.81 1.3 3.5 1 .1-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.14-.3-.54-1.52.1-3.18 0 0 1-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.28-1.55 3.29-1.23 3.29-1.23.64 1.66.24 2.88.12 3.18a4.65 4.65 0 0 1 1.23 3.22c0 4.61-2.8 5.63-5.48 5.92.42.36.81 1.1.81 2.22l-.01 3.29c0 .31.2.69.82.57A12 12 0 0 0 12 .3"/></svg></a></li>
      </ul>
    </nav>
  </div>
</header>""" % (cur("writing"), cur("topics"), cur("about"), s["linkedin"], s["github"])


def footer(cfg):
    s = cfg["site"]
    year = dt.date.today().year
    return """<footer class="dk-footer">
  <div class="dk-footer-inner">
    <p><strong>Deepak Kumbhar.</strong> %s This is a personal publication; views are my own and do not represent my employer.</p>
    <ul>
      <li><a href="/writing/">Writing</a></li>
      <li><a href="/topics/">Topics</a></li>
      <li><a href="/feed.xml">RSS</a></li>
      <li><a href="%s">LinkedIn</a></li>
      <li><a href="%s">GitHub</a></li>
    </ul>
    <p>&copy; %d Deepak Kumbhar</p>
  </div>
</footer>""" % (esc(s["tagline"]), s["linkedin"], s["github"], year)


def head(cfg, title, description, path, og_image=None, og_type="website"):
    s = cfg["site"]
    url = s["url"] + path
    img = og_image or s["url"] + "/assets/og-agent-is-the-easy-part.png"
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{d}">
<meta name="author" content="Deepak Kumbhar">
<link rel="canonical" href="{u}">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="alternate" type="application/rss+xml" title="Deepak Kumbhar" href="/feed.xml">
<meta name="theme-color" content="#F7F7F3" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#121417" media="(prefers-color-scheme: dark)">
<meta property="og:type" content="{ot}">
<meta property="og:site_name" content="Deepak Kumbhar">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{u}">
<meta property="og:image" content="{i}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{d}">
<meta name="twitter:image" content="{i}">
<link rel="stylesheet" href="/assets/site.css">
</head>""".format(t=esc(title), d=esc(description), u=url, i=img, ot=og_type)


def page_writing(arts, cfg):
    rows = "\n".join(row(a, cfg, heading="h2") for a in arts)
    n = len(arts)
    return """{head}
<body class="dk">
{chrome}
<main id="main">
  <div class="dk-wrap">
    <div class="dk-page-head">
      <h1>Writing</h1>
      <p>{count} on enterprise AI, agentic systems and architecture, newest first. Subscribe with the <a href="/feed.xml">RSS feed</a>.</p>
    </div>
    <div class="dk-page-body">
      <ul class="dk-list">
{rows}
      </ul>
    </div>
  </div>
</main>
{footer}
</body>
</html>
""".format(head=head(cfg, "Writing | Deepak Kumbhar", "All articles by Deepak Kumbhar on enterprise AI, agentic systems, architecture and technology transformation.", "/writing/"),
           chrome=chrome(cfg, "writing"), rows=rows, footer=footer(cfg),
           count="%d article%s" % (n, "" if n == 1 else "s"))


def page_topics(arts, cfg):
    blocks = []
    for t, n_art, n_sec in topic_counts(arts, cfg):
        lines = ['<section class="dk-topic" id="%s" aria-labelledby="h-%s">' % (t["slug"], t["slug"]),
                 '  <h2 id="h-%s">%s</h2>' % (t["slug"], esc(t["name"])),
                 '  <p class="dk-count">%s</p>' % esc(t["description"])]
        for a in arts:
            ids = a.get("topics", {}).get(t["slug"], [])
            if not ids and a["category"] != t["slug"]:
                continue
            lines.append('  <h3><a class="dk-title-link" href="%s">%s</a></h3>' % (a["url"], esc(a["title"])))
            if ids:
                lines.append('  <ul class="dk-sections">')
                for cid in sorted(ids, key=a["chapter_order"].index):
                    c = a["chapters"][cid]
                    lines.append('    <li><span class="dk-sec-num">Section %s</span><a href="%s#%s">%s</a></li>'
                                 % (str(int(c["num"])) if c["num"].isdigit() else c["num"], a["url"], cid, esc(c["title"])))
                lines.append('  </ul>')
        lines.append('</section>')
        blocks.append("\n".join(lines))
    return """{head}
<body class="dk">
{chrome}
<main id="main">
  <div class="dk-wrap">
    <div class="dk-page-head">
      <h1>Topics</h1>
      <p>Each topic links to the articles, and the sections within them, that cover it.</p>
    </div>
    <div class="dk-page-body">
{blocks}
    </div>
  </div>
</main>
{footer}
</body>
</html>
""".format(head=head(cfg, "Topics | Deepak Kumbhar", "Browse Deepak Kumbhar's writing by topic: agentic AI, AI architecture, context engineering, security and governance, and evaluation.", "/topics/"),
           chrome=chrome(cfg, "topics"), blocks="\n".join(blocks), footer=footer(cfg))


def feed(arts, cfg):
    s = cfg["site"]
    items = []
    for a in arts:
        when = dt.datetime.combine(a["date"], dt.time(12, 0), tzinfo=dt.timezone.utc)
        items.append("""  <item>
    <title>{t}</title>
    <link>{u}</link>
    <guid isPermaLink="true">{u}</guid>
    <pubDate>{d}</pubDate>
    <description>{x}</description>
  </item>""".format(t=esc(a["title"]), u=a["abs"], d=format_datetime(when), x=esc(a["description"])))
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Deepak Kumbhar</title>
  <link>{u}/</link>
  <atom:link href="{u}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>{d}</description>
  <language>en-us</language>
{items}
</channel>
</rss>
""".format(u=s["url"], d=esc(s["tagline"]), items="\n".join(items))


def sitemap(arts, cfg):
    s = cfg["site"]
    newest = arts[0]["date"].isoformat() if arts else dt.date.today().isoformat()
    urls = [(s["url"] + "/", newest), (s["url"] + "/writing/", newest), (s["url"] + "/topics/", newest)]
    urls += [(a["abs"], a["date"].isoformat()) for a in arts]
    body = "\n".join("  <url><loc>%s</loc><lastmod>%s</lastmod></url>" % u for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n' % body)


def continue_block(a, arts, cfg):
    others = [o for o in arts if o["path"] != a["path"]][:3]
    out = ['<section class="dk-continue" aria-labelledby="continue-h">']
    if others:
        out.append('  <h2 id="continue-h">Continue reading</h2>')
        out.append('  <ul>')
        for o in others:
            out.append('    <li><a href="%s">%s</a><p>%s</p></li>' % (o["url"], esc(o["title"]), esc(o["description"])))
        out.append('  </ul>')
    else:
        out.append('  <h2 id="continue-h">Explore by topic</h2>')
        links = ['<a href="/topics/#%s">%s</a>' % (t["slug"], esc(t["name"]))
                 for t, _, _ in topic_counts(arts, cfg) if a.get("topics", {}).get(t["slug"]) or a["category"] == t["slug"]]
        out.append('  <p class="dk-topic-links">%s</p>' % "".join(links))
        out.append('  <p>More articles are on the way. <a href="/feed.xml">Follow the RSS feed</a> or '
                   '<a href="%s">connect on LinkedIn</a> to hear when they are published.</p>' % cfg["site"]["linkedin"])
    out.append('</section>')
    return "\n".join(out)


# ---------------------------------------------------------------- main

def main():
    cfg = json.loads(read("content/site.json"))
    arts = [load_article(e, cfg) for e in cfg["articles"]]
    arts.sort(key=lambda a: a["date"], reverse=True)

    home = read("index.html")
    home = replace_block(home, "featured", block_featured(arts, cfg), "index.html")
    home = replace_block(home, "latest", block_latest(arts, cfg), "index.html")
    home = replace_block(home, "topics", block_topics(arts, cfg), "index.html")
    home = replace_block(home, "jsonld", block_jsonld(arts, cfg), "index.html")
    write("index.html", home)

    write("writing/index.html", page_writing(arts, cfg))
    write("topics/index.html", page_topics(arts, cfg))
    write("feed.xml", feed(arts, cfg))
    write("sitemap.xml", sitemap(arts, cfg))

    for a in arts:
        rel = a["path"].strip("/") + "/index.html"
        doc = read(rel)
        if "<!-- build:continue -->" in doc:
            doc = replace_block(doc, "continue", continue_block(a, arts, cfg), rel)
        # Keep the reading time in the article's byline in step with the word count.
        doc = re.sub(r"About \d+ minutes to read", "About %d minutes to read" % a["minutes"], doc, count=1)
        write(rel, doc)

    for a in arts:
        print("  %-48s %s  %5d words  %2d min" % (a["url"], a["date"], a["words"], a["minutes"]))
    if problems:
        print("\nProblems:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("\n%s %d article(s)." % ("Checked" if CHECK else "Built", len(arts)))


if __name__ == "__main__":
    main()
