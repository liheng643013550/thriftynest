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

CATEGORY_ICONS = {
    "kitchen": "🍳",
    "organization": "🗂️",
    "cleaning": "🧽",
    "home-office": "💻",
    "pet": "🐾",
    "garden": "🌿",
    "energy": "💡",
    "tools": "🔧",
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
        # normalize date to a plain string: YAML may parse "2026-08-18" as a
        # date object, which breaks sorting and html escaping downstream
        meta["date"] = str(meta.get("date") or "")
        body = parts[2].strip()
        posts.append({"meta": meta, "body": body, "path": path})
    posts.sort(key=lambda p: (p["meta"].get("date", ""), p["path"].name), reverse=True)
    return posts


def slug_of(post):
    return post["meta"].get("slug") or post["path"].stem


def category_name(cat):
    return CATEGORY_NAMES.get(cat, cat.replace("-", " ").title())


def strip_tags(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def extract_faq(body_html):
    """Pull (question, answer) pairs from a trailing FAQ section (if present)."""
    m = re.search(
        r"<h2[^>]*>(?P<t>.*?(?:Frequently Asked Questions|FAQ).*?)</h2>",
        body_html, re.IGNORECASE,
    )
    if not m:
        return []
    region = body_html[m.end():]
    nxt = re.search(r"<h2", region)
    if nxt:
        region = region[:nxt.start()]
    pairs = re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", region, re.DOTALL)
    faqs = []
    for q, a in pairs:
        q = strip_tags(q)
        a = strip_tags(a)
        if q and a:
            faqs.append({
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            })
    return faqs


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


def insert_illustrations(base, body_html, slug, alt_text):
    """Insert generated article illustrations after selected paragraphs."""
    ends = [m.end() for m in re.finditer(r"</p>", body_html)]
    n = len(ends)
    if n < 2:
        return body_html
    inserts = []
    if n >= 3:
        inserts.append((ends[1], 1))
    if n >= 6:
        inserts.append((ends[n // 2], 2))
    if n >= 10:
        inserts.append((ends[-3], 3))
    out = body_html
    for pos, num in reversed(inserts):
        src = "%s/static/img/%s-%d.png" % (base, slug, num)
        fig = (
            '<figure class="illustration"><img src="%s" alt="%s" '
            'loading="lazy" width="1200" height="675"></figure>'
            % (src, html.escape(alt_text))
        )
        out = out[:pos] + "\n" + fig + "\n" + out[pos:]
    return out


def add_toc(body_html):
    """Add anchor ids to H2 headings and return a table-of-contents snippet."""
    toc_items = []
    counter = 0

    def _repl(m):
        nonlocal counter
        counter += 1
        sid = "section-%d" % counter
        text = m.group(1)
        toc_items.append((sid, strip_tags(text)))
        return '<h2 id="%s">%s</h2>' % (sid, text)

    out = re.sub(r"<h2>(.*?)</h2>", _repl, body_html, flags=re.DOTALL)
    if not toc_items:
        return "", body_html
    li = "".join(
        '<li><a href="#%s">%s</a></li>' % (sid, html.escape(t)) for sid, t in toc_items
    )
    toc = '<nav class="toc"><h2>In this article</h2><ol>%s</ol></nav>' % li
    return toc, out


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
                    og_type="website", og_image=""):
        adsense_script = ""
        if self.adsense:
            adsense_script = (
                '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
                'adsbygoogle.js?client=%s" crossorigin="anonymous"></script>'
                % self.adsense
            )
        og_image_line = ""
        if og_image:
            og_image_line = '<meta property="og:image" content="%s">' % html.escape(og_image)
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
            "og_image": og_image_line,
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
        cat = post["meta"].get("category") or "misc"
        desc = html.escape(post["meta"].get("description") or "")
        icon = CATEGORY_ICONS.get(cat, "🏠")
        return (
            '<article class="card cat-%s">'
            '<div class="card-thumb cat-%s"><span>%s</span></div>'
            '<div class="card-body">'
            '<h2 class="card-title"><a href="%s">%s</a></h2>'
            '<p class="card-meta">%s &middot; <a href="%s">%s</a></p>'
            '<p class="card-desc">%s</p></div></article>'
            % (cat, cat, icon, link, title, html.escape(date),
               self.path("category", cat), html.escape(category_name(cat)), desc)
        )

    # ---------- page builders ----------

    def inline_links(self, related, category=""):
        """Build a natural in-body paragraph linking to 1-2 related posts."""
        picks = related[:2] if related else []
        linked = []
        for p in picks:
            title = html.escape(p["meta"].get("title", "this guide"))
            linked.append('<a href="%s">%s</a>' % (self.path("posts", slug_of(p)), title))
        if not linked:
            return ""
        if len(linked) == 1:
            sentence = "While you're here, you might also like %s." % linked[0]
        else:
            sentence = "If this helped, you'll also want to read %s and %s." % (linked[0], linked[1])
        return '<p class="inline-links">%s</p>' % sentence

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

        body_html = insert_illustrations(
            self.base, body_html, slug, meta.get("title", slug))

        # in-body contextual links to related posts (after the 3rd paragraph)
        inline = self.inline_links(related, cat)
        if inline:
            ends = [m.end() for m in re.finditer(r"</p>", body_html)]
            if len(ends) >= 3:
                pos = ends[2]
                body_html = body_html[:pos] + "\n" + inline + "\n" + body_html[pos:]

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

        article = {
            "@type": "Article",
            "headline": meta.get("title", ""),
            "description": meta.get("description", ""),
            "datePublished": date,
            "author": {"@type": "Person", "name": self.site.get("author", self.name)},
            "publisher": {"@type": "Organization", "name": self.name},
            "mainEntityOfPage": canonical,
        }
        faqs = extract_faq(body_html)
        if faqs:
            jsonld = {
                "@context": "https://schema.org",
                "@graph": [
                    article,
                    {"@type": "FAQPage", "mainEntity": faqs},
                ],
            }
        else:
            jsonld = dict({"@context": "https://schema.org"}, **article)
        toc_html, body_html = add_toc(body_html)
        body = (
            "<header class=\"post-head\"><h1>%s</h1>"
            "<p class=\"post-meta\">%s &middot; %s</p></header>"
            "<div class=\"post-body\">%s%s</div>%s"
            % (html.escape(meta.get("title", "")), html.escape(date),
               html.escape(category_name(cat)), toc_html, body_html, related_html)
        )
        pin = OUT_DIR / "static" / "pins" / (slug + ".png")
        og_image = self.path("static", "pins", slug + ".png") if pin.exists() else ""
        page = self.render_page(
            title=meta.get("title", ""),
            description=meta.get("description", ""),
            body_html=crumbs + body,
            canonical=canonical,
            jsonld=json.dumps(jsonld),
            og_type="article",
            og_image=og_image,
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
        quick = "".join(
            '<a class="hero-btn" href="%s">%s</a>' % (self.path("category", cat), name)
            for cat, name in sorted(CATEGORY_NAMES.items())
        )
        jsonld = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": self.name,
            "url": self.path(""),
        }
        body = (
            '<section class="hero">'
            '<p class="hero-eyebrow">Budget home &amp; kitchen guides</p>'
            '<h1>%s</h1>'
            '<p class="hero-sub">%s</p>'
            '<div class="hero-btns">%s</div>'
            '</section>'
            '<section class="grid">%s</section>'
            '<section class="chips"><h2>Browse by topic</h2>%s</section>'
            % (html.escape(self.tagline), html.escape(self.site.get("description", "")),
               quick, cards, chips)
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

    def build_categories_index(self, posts):
        counts = {}
        for p in posts:
            cat = p["meta"].get("category", "misc")
            counts[cat] = counts.get(cat, 0) + 1
        items = ""
        for cat, name in CATEGORY_NAMES.items():
            n = counts.get(cat, 0)
            items += (
                '<article class="card cat-%s"><h2 class="card-title"><a href="%s">%s</a></h2>'
                '<p class="card-meta">%d guide%s</p></article>'
                % (cat, self.path("category", cat), html.escape(name), n,
                   "" if n == 1 else "s")
            )
        body = (
            '<header class="cat-head"><h1>Browse all guides</h1>'
            '<p class="hero-sub">Every category on %s, in one place.</p></header>'
            '<section class="grid">%s</section>' % (self.name, items)
        )
        page = self.render_page(
            title="All guides — %s" % self.name,
            description="Browse every buying guide and money-saving tip on %s by category." % self.name,
            body_html=body,
            canonical=self.path("categories"),
        )
        out = OUT_DIR / "categories" / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")

    def _trust_page(self, slug, title, body):
        page = self.render_page(
            title="%s — %s" % (title, self.name),
            description="%s for %s." % (title, self.name),
            body_html=body,
            canonical=self.path(slug),
        )
        out = OUT_DIR / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")

    def build_trust_pages(self):
        home = self.path("")
        # Privacy policy (required by Amazon Associates + AdSense)
        privacy = (
            "<h1>Privacy Policy</h1>"
            "<p>%s (\"we\", \"us\") values your privacy. This policy explains what "
            "data we collect and how it is used when you visit %s.</p>"
            "<h2>Information we collect</h2>"
            "<p>We collect limited, non-identifying information such as browser type, "
            "device, and pages visited. We do not ask for personal details beyond what "
            "you voluntarily provide (for example, if you contact us).</p>"
            "<h2>Cookies and advertising</h2>"
            "<p>We use cookies to improve your experience and to display relevant "
            "advertising. Third-party vendors, including Google, may use cookies to serve "
            "ads based on your prior visits to this website. You may opt out of "
            "personalized advertising at <a href=\"https://adssettings.google.com\">"
            "Google Ads Settings</a>.</p>"
            "<h2>Advertising partners</h2>"
            "<p>We use advertising and affiliate partners, including Amazon Associates "
            "and Google AdSense. These partners may use cookies or web beacons to "
            "measure the effectiveness of their ads. As an Amazon Associate we earn "
            "from qualifying purchases made through links on this site.</p>"
            "<h2>Affiliate disclosure</h2>"
            "<p>Some links on this site are affiliate links. If you click one and make a "
            "purchase, we may earn a small commission at no extra cost to you.</p>"
            "<h2>Your choices</h2>"
            "<p>You can disable cookies in your browser settings. Note that some parts "
            "of the site may not work as well without them.</p>"
            "<h2>Contact</h2>"
            "<p>Questions about this policy? See our <a href=\"%s/contact/\">contact page</a>.</p>"
            % (self.name, self.base, self.base)
        )
        self._trust_page("privacy-policy", "Privacy Policy", privacy)

        # About
        about = (
            "<h1>About %s</h1>"
            "<p>%s is a practical guide to saving money on your home. We research budget "
            "kitchen appliances, storage solutions, cleaning supplies, and every-day "
            "household buys so you can make smart, affordable choices.</p>"
            "<p>Every guide is written to be honest and easy to read: no hype, no fluff, "
            "just real recommendations that help you spend less without sacrificing "
            "quality.</p>"
            "<h2>How we work</h2>"
            "<p>We compare products across price points, weigh the pros and cons, and "
            "tell you what is genuinely worth your money. When you buy through links on "
            "this site, we may earn a commission - it does not change the price you pay.</p>"
            "<h2>Start exploring</h2>"
            "<p>Browse all of our buying guides on the <a href=\"%s\">home page</a> or by "
            "<a href=\"%s/categories/\">category</a>.</p>"
            % (self.name, self.name, self.base, self.base)
        )
        self._trust_page("about", "About", about)

        # Contact
        contact = (
            "<h1>Contact</h1>"
            "<p>Have a question, a suggestion, or found an issue on the site? We would "
            "love to hear from you.</p>"
            "<p>We read every message and reply as soon as we can.</p>"
            "<p>You can also browse our guides from the <a href=\"%s\">home page</a> or "
            "check our <a href=\"%s/categories/\">full list of categories</a>.</p>"
            % (self.base, self.base)
        )
        self._trust_page("contact", "Contact", contact)

    def build_seo_files(self, posts):
        urls = [self.path("")]
        urls += [self.path("categories")]
        urls += [self.path("privacy-policy"), self.path("about"), self.path("contact")]
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

        # IndexNow key file (Bing instant indexing): makes <key>.txt live at
        # the site root so Bing can verify the key. Configured in config.yaml.
        # The key may contain "/" (Bing's format), so create parent dirs.
        key = (self.site.get("indexnow_key") or "").strip()
        if key:
            key_file = OUT_DIR / (key + ".txt")
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(key, encoding="utf-8")

    def build(self, posts):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.build_index(posts)
        by_cat = {}
        for p in posts:
            by_cat.setdefault(p["meta"].get("category", "misc"), []).append(p)
        for cat, cat_posts in by_cat.items():
            self.build_category(cat, cat_posts)
        self.build_categories_index(posts)
        self.build_trust_pages()
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
