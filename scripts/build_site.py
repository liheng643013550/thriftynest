"""Build the ThriftyNest static site from content/posts/*.md.

Usage: python scripts/build_site.py

Outputs to site/ (deployed to GitHub Pages by the CI workflow):
    index.html
    category/<cat>/index.html
    posts/<slug>/index.html
    sitemap.xml, robots.txt, feed.xml, 404.html
    static/... (copied from static/)
"""
import html
import json
import re
import shutil
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from string import Template

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"
TEMPLATE_PATH = ROOT / "templates" / "base.html"
STATIC_DIR = ROOT / "static"
OUT_DIR = ROOT / "site"

CATEGORY_NAMES = {
    "kitchen": "Kitchen & Cooking",
    "organization": "Organization & Storage",
    "cleaning": "Cleaning",
    "home-office": "Home Office",
    "pet": "Pets",
    "garden": "Garden & Outdoors",
    "energy": "Energy & Savings",
    "tools": "Tools & DIY",
}

AMAZON_LINK_RE = re.compile(r'<a href="(https://www\.amazon\.com/[^"]*)"')


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_posts():
    posts = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            continue
        parts = raw.split("---", 2)
        if len(parts) < 3:
            continue
        meta = yaml.safe_load(parts[1]) or {}
        meta["date"] = str(meta.get("date") or "")
        body = parts[2].strip()
        posts.append({"meta": meta, "body": body, "path": path})
    posts.sort(key=lambda p: (p["meta"].get("date", ""), p["path"].name), reverse=True)
    return posts


def slug_of(post):
    return post["meta"].get("slug") or post["path"].stem


def category_name(cat):
    return CATEGORY_NAMES.get(cat, cat.replace("-", " ").title())


def excerpt(html_text, limit=160):
    plain = re.sub(r"<[^>]+>", " ", html_text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit].rstrip() + ("..." if len(plain) > limit else "")


def amazon_links(html_text, tag):
    """Inject the affiliate tag into amazon links and mark them as sponsored."""
    def _href(match):
        href = match.group(1)
        if "__AMAZON_TAG__" in href:
            href = href.replace("?tag=__AMAZON_TAG__", "")
            if tag:
                href += "?tag=" + tag
        return '<a href="%s" rel="nofollow sponsored" target="_blank"' % href
    return AMAZON_LINK_RE.sub(_href, html_text)


class Site:
    def __init__(self, config):
        self.cfg = config
        self.site = config.get("site", {})
        self.money = config.get("monetization", {})
        self.base = self.site.get("url", "").rstrip("/")
        self.name = self.site.get("name", "ThriftyNest")
        self.tagline = self.site.get("tagline", "")
        self.tag = (self.money.get("amazon_tag") or "").strip()
        self.adsense = (self.money.get("adsense_client") or "").strip()
        self.adsense_slot = (self.money.get("adsense_slot") or "").strip()
        self.base_tpl = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))

    def path(self, *parts):
        return self.base + "/" + "/".join(str(p).strip("/") for p in parts if str(p))

    # ---------- rendering helpers ----------

    def render_page(self, title, description, body_html, canonical, jsonld=None,
                    og_type="website"):
        adsense_script = ""
        if self.adsense:
            adsense_script = (
                '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
                'adsbygoogle.js?client=%s" crossorigin="anonymous"></script>'
                % self.adsense
            )
        ctx = {
            "site_name": self.name,
            "tagline": self.tagline,
            "lang": self.site.get("lang", "en"),
            "base": self.base,
            "page_title": html.escape(title),
            "description": html.escape(description),
            "canonical": html.escape(canonical),
            "og_url": html.escape(canonical),
            "og_type": og_type,
            "jsonld": jsonld or "",
            "adsense_script": adsense_script,
            "content": body_html,
            "footer_year": datetime.now().year,
        }
        page = self.base_tpl.substitute(ctx)
        if not jsonld:
            page = page.replace(
                '<script type="application/ld+json"></script>\n', "")
        return page

    def ad_unit(self):
        if not (self.adsense and self.adsense_slot):
            return ""
        return (
            '<div class="ad-slot"><ins class="adsbygoogle" style="display:block" '
            'data-ad-client="%s" data-ad-slot="%s" data-ad-format="auto" '
            'data-full-width-responsive="true"></ins>'
            '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script></div>'
            % (self.adsense, self.adsense_slot)
        )

    def post_card(self, post):
        title = html.escape(post["meta"].get("title") or "Untitled")
        link = self.path("posts", slug_of(post))
        date = post["meta"].get("date") or ""
        cat = post["meta"].get("category") or ""
        desc = html.escape(post["meta"].get("description") or "")
        return (
            '<article class="card"><h2 class="card-title"><a href="%s">%s</a></h2>'
            '<p class="card-meta">%s &middot; <a href="%s">%s</a></p>'
            '<p class="card-desc">%s</p></article>'
            % (link, title, html.escape(date),
               self.path("category", cat), html.escape(category_name(cat)), desc)
        )

    # ---------- page builders ----------

    def build_post(self, post, related):
        meta = post["meta"]
        slug = slug_of(post)
        cat = meta.get("category", "")
        date = meta.get("date", "")
        md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
        body_html = md.convert(post["body"])
        body_html = amazon_links(body_html, self.tag)
        ad = self.ad_unit()
        if ad:
            # insert an ad unit after the first paragraph
            m = re.search(r"</p>", body_html)
            if m:
                pos = m.end()
                body_html = body_html[:pos] + "\n" + ad + "\n" + body_html[pos:]

        canonical = self.path("posts", slug)
        crumbs = (
            '<nav class="breadcrumbs"><a href="%s">Home</a> &rsaquo; '
            '<a href="%s">%s</a> &rsaquo; %s</nav>'
            % (self.path(""), self.path("category", cat),
               html.escape(category_name(cat)), html.escape(meta.get("title", "")))
        )
        related_html = ""
        if related:
            items = "".join(
                '<li><a href="%s">%s</a></li>'
                % (self.path("posts", slug_of(p)), html.escape(p["meta"].get("title", "")))
                for p in related
            )
            related_html = '<section class="related"><h2>Related reads</h2><ul>%s</ul></section>' % items

        jsonld = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": meta.get("title", ""),
            "description": meta.get("description", ""),
            "datePublished": date,
            "author": {"@type": "Person", "name": self.site.get("author", self.name)},
            "publisher": {"@type": "Organization", "name": self.name},
            "mainEntityOfPage": canonical,
        }
        body = (
            "<header class=\"post-head\"><h1>%s</h1>"
            "<p class=\"post-meta\">%s &middot; %s</p></header>"
            "<div class=\"post-body\">%s</div>%s"
            % (html.escape(meta.get("title", "")), html.escape(date),
               html.escape(category_name(cat)), body_html, related_html)
        )
        page = self.render_page(
            title=meta.get("title", ""),
            description=meta.get("description", ""),
            body_html=crumbs + body,
            canonical=canonical,
            jsonld=json.dumps(jsonld),
            og_type="article",
        )
        out = OUT_DIR / "posts" / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")

    def build_index(self, posts):
        cards = "".join(self.post_card(p) for p in posts[:12])
        chips = "".join(
            '<a class="chip" href="%s">%s</a>' % (self.path("category", cat), name)
            for cat, name in sorted(CATEGORY_NAMES.items())
        )
        jsonld = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": self.name,
            "url": self.path(""),
        }
        body = (
            '<section class="hero"><h1>%s</h1><p class="hero-sub">%s</p></section>'
            '<section class="grid">%s</section>'
            '<section class="chips"><h2>Browse by topic</h2>%s</section>'
            % (html.escape(self.site.get("description", self.name)),
               html.escape(self.tagline), cards, chips)
        )
        page = self.render_page(
            title=self.name + " — " + self.tagline,
            description=self.site.get("description", ""),
            body_html=body,
            canonical=self.path(""),
            jsonld=json.dumps(jsonld),
        )
        (OUT_DIR / "index.html").write_text(page, encoding="utf-8")

    def build_category(self, cat, posts):
        cards = "".join(self.post_card(p) for p in posts)
        body = (
            '<nav class="breadcrumbs"><a href="%s">Home</a> &rsaquo; %s</nav>'
            '<header class="cat-head"><h1>%s</h1></header>'
            '<section class="grid">%s</section>'
            % (self.path(""), html.escape(category_name(cat)),
               html.escape(category_name(cat)), cards)
        )
        page = self.render_page(
            title="%s — %s" % (category_name(cat), self.name),
            description="All %s guides on %s." % (category_name(cat), self.name),
            body_html=body,
            canonical=self.path("category", cat),
        )
        out = OUT_DIR / "category" / cat / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")

    def build_seo_files(self, posts):
        urls = [self.path("")]
        urls += [self.path("category", c) for c in CATEGORY_NAMES]
        urls += [self.path("posts", slug_of(p)) for p in posts]
        lastmod = datetime.now().strftime("%Y-%m-%d")
        urls_xml = "".join(
            "<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (u, lastmod) for u in urls
        )
        (OUT_DIR / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n' % urls_xml,
            encoding="utf-8",
        )
        (OUT_DIR / "robots.txt").write_text(
            "User-agent: *\nAllow: /\nSitemap: %s\n" % self.path("sitemap.xml"),
            encoding="utf-8",
        )
        items = ""
        for p in posts[:10]:
            meta = p["meta"]
            md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
            desc = excerpt(md.convert(p["body"]), 300)
            items += (
                "<item><title>%s</title><link>%s</link>"
                "<guid isPermaLink=\"true\">%s</guid>"
                "<pubDate>%s</pubDate><description>%s</description></item>\n"
                % (html.escape(meta.get("title", "")),
                   self.path("posts", slug_of(p)),
                   self.path("posts", slug_of(p)),
                   format_datetime(datetime.strptime(meta.get("date", "2000-01-01"), "%Y-%m-%d")),
                   html.escape(desc))
            )
        (OUT_DIR / "feed.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">'
            "<channel><title>%s</title><link>%s</link><description>%s</description>"
            "<language>en-us</language>%s</channel></rss>\n"
            % (html.escape(self.name), self.path(""),
               html.escape(self.site.get("description", "")), items),
            encoding="utf-8",
        )
        not_found = self.render_page(
            title="Page not found — " + self.name,
            description="The page you're looking for doesn't exist.",
            body_html="<h1>404</h1><p>This page moved or never existed. "
                      '<a href="%s">Back to %s</a>.</p>' % (self.path(""), self.name),
            canonical=self.path(""),
        )
        (OUT_DIR / "404.html").write_text(not_found, encoding="utf-8")

    def build(self, posts):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.build_index(posts)
        by_cat = {}
        for p in posts:
            by_cat.setdefault(p["meta"].get("category", "misc"), []).append(p)
        for cat, cat_posts in by_cat.items():
            self.build_category(cat, cat_posts)
        for i, post in enumerate(posts):
            cat_posts = [p for p in posts if p["meta"].get("category") == post["meta"].get("category")
                         and p is not post]
            related = cat_posts[:3] if cat_posts else [p for p in posts if p is not post][:3]
            self.build_post(post, related)
        self.build_seo_files(posts)
        shutil.copytree(STATIC_DIR, OUT_DIR / "static", dirs_exist_ok=True)
        print("[build] done: %d posts, %d categories -> %s" %
              (len(posts), len(by_cat), OUT_DIR))


def main():
    config = load_config()
    site_url = (config.get("site", {}).get("url") or "")
    if "yourusername" in site_url:
        print("[build] WARNING: config.yaml site.url is still the placeholder "
              "'https://yourusername.github.io'. Set it to your real URL before "
              "deploying (see SETUP.md).")
    posts = load_posts()
    site = Site(config)
    site.build(posts)


if __name__ == "__main__":
    main()
