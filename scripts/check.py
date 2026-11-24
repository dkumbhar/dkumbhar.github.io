#!/usr/bin/env python3
"""Static checks for dkumbhar.github.io. Run after scripts/build.py.

  python3 scripts/check.py

Checks every page for broken internal links and anchors, heading order,
missing image alt text, duplicate ids and a single <h1>. Also confirms that
every URL that was public before still exists. Exits non-zero on problems.
"""
import html.parser
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# URLs that were already public. Never remove one of these.
PUBLISHED = [
    "/", "/404.html", "/robots.txt", "/sitemap.xml",
    "/articles/agent-is-the-easy-part/",
    "/assets/favicon.svg", "/assets/cover-agent-is-the-easy-part.png", "/assets/og-agent-is-the-easy-part.png",
    "/google9b0313b970246087.html",
]
# Anchors that were already linked from outside: the old nav and the article's chapters.
PUBLISHED_ANCHORS = {"/": ["articles", "about"],
                     "/articles/agent-is-the-easy-part/": ["s%d" % i for i in range(1, 16)]}


def file_for(url):
    path = url.split("#")[0]
    return "." + path + ("index.html" if path.endswith("/") else "")


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.heads, self.imgs_no_alt, self.ids, self.links = [], 0, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if re.fullmatch(r"h[1-6]", tag):
            self.heads.append(int(tag[1]))
        if tag == "img" and "alt" not in a:
            self.imgs_no_alt += 1
        if "id" in a:
            self.ids.append(a["id"])
        for k in ("href", "src"):
            if k in a:
                self.links.append(a[k])


def main():
    pages = sorted(p[2:] for p in
                   (os.path.join(d, f) for d, _, fs in os.walk(".") for f in fs if f.endswith(".html"))
                   if not p.startswith("./scripts") and "google" not in p)
    parsed, problems = {}, []
    for p in pages:
        x = Page()
        x.feed(open(p, encoding="utf-8").read())
        url = "/" + p.replace("index.html", "")
        parsed[url] = x

    for url, x in parsed.items():
        if x.heads.count(1) != 1:
            problems.append("%s has %d <h1> elements" % (url, x.heads.count(1)))
        jumps = [(a, b) for a, b in zip(x.heads, x.heads[1:]) if b > a + 1]
        if jumps:
            problems.append("%s skips heading levels %s" % (url, jumps))
        if x.imgs_no_alt:
            problems.append("%s has %d <img> without alt" % (url, x.imgs_no_alt))
        dup = sorted({i for i in x.ids if x.ids.count(i) > 1})
        if dup:
            problems.append("%s has duplicate ids %s" % (url, dup))
        for link in x.links:
            if link.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            target = link if not link.startswith("#") else url + link
            path, _, frag = target.partition("#")
            if not path.startswith("/"):
                path = url + path
            if not os.path.exists(file_for(path)):
                problems.append("%s links to missing %s" % (url, link))
            elif frag and path in parsed and frag not in parsed[path].ids:
                problems.append("%s links to missing anchor %s" % (url, link))

    for u in PUBLISHED:
        if not os.path.exists(file_for(u)):
            problems.append("previously published URL is gone: %s" % u)
    for u, anchors in PUBLISHED_ANCHORS.items():
        for a in anchors:
            if a not in parsed.get(u, Page()).ids:
                problems.append("previously published anchor is gone: %s#%s" % (u, a))

    print("Checked %d pages: %s" % (len(parsed), ", ".join(sorted(parsed))))
    if problems:
        print("\nProblems:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("No problems found.")


if __name__ == "__main__":
    main()
