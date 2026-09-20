#!/usr/bin/env python3
"""Build verification (smoke test) for ThriftyNest.

Runs AFTER scripts/build_site.py. Exits non-zero if anything looks wrong, so
GitHub Actions turns red and you get an email instead of silently deploying a
broken site.

Why this exists: build_site.py rewrites 130+ files and deploys them
unattended every day. Without a gate, a single regression ships to production
and nobody notices.

Usage:
    python scripts/verify_site.py
    python scripts/verify_site.py --sample 15      # check more post pages
    python scripts/verify_site.py --warn-only      # never fail (report only)

Exit codes: 0 = all checks passed, 1 = at least one check failed.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"
SITE_DIR = ROOT / "site"

# Extra non-post URLs the builder emits: home + categories index + 3 trust
# pages + 8 category pages. Keep in sync with build_site.CATEGORY_NAMES.
NON_POST_URLS = 13

# A page smaller than this is almost certainly a failed render.
MIN_INDEX_BYTES = 5_000
MIN_POST_BYTES = 3_000


class Report:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warned = []

    def ok(self, msg):
        self.passed.append(msg)
        print("  [ok]   %s" % msg)

    def fail(self, msg, detail=None):
        self.failed.append((msg, detail))
        print("  [FAIL] %s" % msg)
        if detail:
            for line in str(detail).splitlines():
                print("         %s" % line)

    def warn(self, msg, detail=None):
        self.warned.append((msg, detail))
        print("  [warn] %s" % msg)
        if detail:
            for line in str(detail).splitlines():
                print("         %s" % line)

    def check(self, cond, msg, detail=None):
        if cond:
            self.ok(msg)
        else:
            self.fail(msg, detail)
        return bool(cond)


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit("config.yaml not found at %s" % CONFIG_PATH)
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_post_slugs():
    slugs = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            continue
        parts = raw.split("---", 2)
        if len(parts) < 3:
            continue
        meta = yaml.safe_load(parts[1]) or {}
        slugs.append((meta.get("slug") or path.stem, meta, path.name))
    return slugs


def main():
    ap = argparse.ArgumentParser(description="Verify the built ThriftyNest site")
    ap.add_argument("--sample", type=int, default=10,
                    help="how many post pages to deep-inspect (default 10, 0 = all)")
    ap.add_argument("--warn-only", action="store_true",
                    help="never exit non-zero, just report")
    args = ap.parse_args()

    cfg = load_config()
    site = cfg.get("site", {})
    base = str(site.get("url", "")).rstrip("/")
    rep = Report()

    print("=" * 68)
    print("ThriftyNest build verification")
    print("  site.url  : %s" % (base or "(unset!)"))
    print("  site dir  : %s" % SITE_DIR)
    print("=" * 68)

    # ---------------------------------------------------------------- 0. inputs
    print("\n[1/7] config & inputs")
    rep.check(bool(base), "site.url is configured")
    if not base:
        rep.fail("site.url is empty - canonical/sitemap would be broken")
    rep.check("yourusername" not in base,
              "site.url is not the template placeholder",
              "Edit config.yaml -> site.url before deploying.")

    posts = load_post_slugs()
    rep.check(len(posts) > 0, "found %d markdown posts" % len(posts))
    if not posts:
        return finish(rep, args)

    # ----------------------------------------------------------- 1. site exists
    print("\n[2/7] build output exists")
    if not rep.check(SITE_DIR.is_dir(), "site/ directory exists"):
        return finish(rep, args)
    rep.check((SITE_DIR / "index.html").is_file(), "site/index.html exists")
    rep.check((SITE_DIR / "sitemap.xml").is_file(), "site/sitemap.xml exists")
    rep.check((SITE_DIR / "robots.txt").is_file(), "site/robots.txt exists")
    rep.check((SITE_DIR / "feed.xml").is_file(), "site/feed.xml exists")
    rep.check((SITE_DIR / "404.html").is_file(), "site/404.html exists")
    rep.check((SITE_DIR / "static" / "style.css").is_file(),
              "site/static/style.css exists")

    idx = SITE_DIR / "index.html"
    if idx.is_file():
        n = idx.stat().st_size
        rep.check(n >= MIN_INDEX_BYTES,
                  "index.html is %d bytes (>= %d)" % (n, MIN_INDEX_BYTES),
                  "Home page looks truncated - did the render fail?")

    # ------------------------------------------------------- 2. page count 1:1
    print("\n[3/7] page count matches content count")
    built = sorted(p.parent.name for p in (SITE_DIR / "posts").glob("*/index.html")) \
        if (SITE_DIR / "posts").is_dir() else []
    # 跳转页不是文章，必须从计数里排除。
    # 合并重复选题后，旧地址会生成一个跳转页（见 Site.build_redirects），
    # 它位于 site/posts/<旧slug>/index.html —— 不排除的话这里会永远报
    # "built=207 expected=205"，看起来像构建坏了，其实是正常的。
    redirects = set()
    try:
        _cfg = load_config()
        for _old in (_cfg.get("redirects") or {}):
            redirects.add(str(_old).strip("/").split("/")[-1])
    except Exception:
        pass
    built = [b for b in built if b not in redirects]
    expected = sorted(s for s, _, _ in posts)
    rep.check(len(built) == len(posts),
              "site/posts/*/index.html count == %d markdown posts" % len(posts),
              "built=%d expected=%d (excluding %d redirect page(s))"
              % (len(built), len(posts), len(redirects)))
    missing = [s for s in expected if s not in built]
    extra = [s for s in built if s not in expected]
    if missing:
        rep.fail("%d post page(s) missing" % len(missing),
                 ", ".join(missing[:15]) + ("..." if len(missing) > 15 else ""))
    else:
        rep.ok("no missing post pages")
    if extra:
        rep.warn("%d stale post page(s) in site/ (deleted posts leave orphans)"
                 % len(extra), ", ".join(extra[:15]))
    else:
        rep.ok("no orphan post pages")

    # ------------------------------------------------------------- 3. sitemap
    print("\n[4/7] sitemap / robots / feed consistency")
    sm = (SITE_DIR / "sitemap.xml")
    if sm.is_file():
        sm_xml = sm.read_text(encoding="utf-8")
        locs = re.findall(r"<loc>(.*?)</loc>", sm_xml)
        want = len(posts) + NON_POST_URLS
        rep.check(len(locs) == want,
                  "sitemap has %d URLs (expected %d = %d posts + %d pages)"
                  % (len(locs), want, len(posts), NON_POST_URLS),
                  "Update NON_POST_URLS if you added/removed category or trust pages.")
        bad_loc = [u for u in locs if base and not u.startswith(base)]
        if bad_loc:
            rep.fail("%d sitemap URL(s) do not start with site.url" % len(bad_loc),
                     ", ".join(bad_loc[:5]))
        else:
            rep.ok("all sitemap URLs share the configured host")
        dupe = {u for u in locs if locs.count(u) > 1}
        rep.check(not dupe, "no duplicate URLs in sitemap",
                  ", ".join(sorted(dupe)[:5]) if dupe else None)

        # GitHub Pages 301-redirects the slash-less form of a directory URL to
        # the slash form. A sitemap full of slash-less URLs is therefore a
        # sitemap full of redirects, which the sitemap spec forbids and which
        # Search Console reports as "Page with redirect" rather than "Indexed".
        # This was a real defect in this repo: 132/145 URLs were redirects.
        def _is_dir_url(u):
            return "." not in u.rstrip("/").rsplit("/", 1)[-1]

        slashless = [u for u in locs
                     if _is_dir_url(u) and not u.endswith("/")]
        rep.check(not slashless,
                  "every sitemap URL is in canonical (trailing-slash) form",
                  "%d URL(s) point at a 301 redirect instead of the real page, "
                  "e.g. %s" % (len(slashless), ", ".join(slashless[:4]))
                  if slashless else None)

    rb = SITE_DIR / "robots.txt"
    if rb.is_file():
        rb_txt = rb.read_text(encoding="utf-8")
        rep.check("Sitemap: %s/sitemap.xml" % base in rb_txt,
                  "robots.txt points at the configured sitemap URL",
                  rb_txt.strip())

    fd = SITE_DIR / "feed.xml"
    if fd.is_file():
        fd_xml = fd.read_text(encoding="utf-8")
        items = re.findall(r"<item>", fd_xml)
        rep.check(len(items) == 10, "feed.xml has %d items (builder emits 10)"
                  % len(items))

    # indexnow key file
    key = str(site.get("indexnow_key") or "").strip()
    if key:
        rep.check((SITE_DIR / (key + ".txt")).is_file(),
                  "IndexNow key file %s.txt is present" % key)
    else:
        rep.warn("no site.indexnow_key configured - Bing instant indexing is off")

    # --------------------------------------------------- 4. per-page deep check
    print("\n[5/7] deep-inspect pages")
    slugs = [s for s, _, _ in posts]
    if args.sample and args.sample < len(slugs):
        step = max(1, len(slugs) // args.sample)
        sample = slugs[::step][:args.sample]
    else:
        sample = slugs

    problems = {"title": [], "canonical": [], "jsonld": [], "small": [],
                "unclosed": [], "placeholder": [],
                # SEO 长度闸门（2026-09 新增）。长度一律按【html.unescape 之后】算：
                # HTML 会把 ' 转义成 &#x27;（1 字符变 6 字符），按原始 HTML 数长度
                # 会把 55 字符的标题算成 70+，造成大量误报 —— 体检脚本踩过这个坑。
                "title_long": [], "desc_len": []}
    titles_seen = {}
    for slug in sample:
        page = SITE_DIR / "posts" / slug / "index.html"
        if not page.is_file():
            continue
        raw = page.read_text(encoding="utf-8")

        if len(raw) < MIN_POST_BYTES:
            problems["small"].append("%s (%d B)" % (slug, len(raw)))
        if "</html>" not in raw:
            problems["unclosed"].append(slug)

        m = re.search(r"<title>(.*?)</title>", raw, re.S)
        if not m or not m.group(1).strip():
            problems["title"].append(slug)
        else:
            titles_seen.setdefault(m.group(1).strip(), []).append(slug)

        # SEO 长度：先反转义再数，否则 &#x27; 这类实体会把长度撑大 6 倍
        t_txt = html.unescape(m.group(1)).strip() if m else ""
        if t_txt and len(t_txt) > 65:
            problems["title_long"].append("%s (%d)" % (slug, len(t_txt)))
        d_m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', raw, re.I)
        if d_m:
            d_txt = html.unescape(d_m.group(1)).strip()
            if len(d_txt) > 165:
                problems["desc_len"].append("%s (desc %d)" % (slug, len(d_txt)))
            elif len(d_txt) < 50:
                problems["desc_len"].append("%s (desc %d, too short)" % (slug, len(d_txt)))

        c = re.search(r'<link rel="canonical" href="([^"]+)"', raw)
        # 严格比对（含尾斜杠）：canonical 必须指向真实提供 200 的那个地址。
        # 旧版用 rstrip("/") 比较，把"canonical 指向 301 重定向"这个真实缺陷
        # 掩盖掉了 —— 审计脚本犯过同样的错，所以这里刻意不去尾斜杠。
        want_canon = "%s/posts/%s/" % (base, slug)
        if not c:
            problems["canonical"].append("%s (no canonical)" % slug)
        elif c.group(1) != want_canon:
            problems["canonical"].append("%s -> %s" % (slug, c.group(1)))

        if "application/ld+json" not in raw:
            problems["jsonld"].append(slug)

        ph = raw.count("PLACEHOLDER-ASIN")
        if ph:
            problems["placeholder"].append("%s (%d)" % (slug, ph))

    def bucket(name, label, severity="fail"):
        vals = problems[name]
        if not vals:
            rep.ok(label)
        elif severity == "fail":
            rep.fail("%s -> %d page(s)" % (label, len(vals)),
                     ", ".join(vals[:10]))
        else:
            rep.warn("%s -> %d page(s)" % (label, len(vals)),
                     ", ".join(vals[:10]))

    rep.check(len(sample) > 0, "inspected %d post page(s)" % len(sample))
    bucket("title", "every inspected page has a non-empty <title>")
    bucket("title_long", "post titles stay <=65 chars (else search results truncate)",
           severity="warn")
    bucket("desc_len", "post descriptions stay 50-165 chars", severity="warn")
    bucket("canonical", "every inspected page has the right canonical URL")
    bucket("jsonld", "every inspected page has JSON-LD structured data")
    bucket("small", "no truncated pages (all >= %d B)" % MIN_POST_BYTES)
    bucket("unclosed", "every inspected page ends with </html>")

    # duplicate titles (SEO risk)
    dup_titles = {t: s for t, s in titles_seen.items() if len(s) > 1}
    if dup_titles:
        rep.warn("%d duplicate <title> across pages (duplicate-content risk)"
                 % len(dup_titles),
                 "\n".join("%s -> %s" % (t[:60], ", ".join(v))
                           for t, v in list(dup_titles.items())[:5]))
    else:
        rep.ok("all inspected pages have unique <title>")

    # ------------------------------------------------------------------
    # GLOBAL scan - deliberately NOT sampled.
    # A leaked __AMAZON_TAG__ means every affiliate link on the affected
    # pages is dead, so this is the one check that must always cover the
    # whole site. ~150 small files, cheap to read.
    # ------------------------------------------------------------------
    all_html = sorted(p for p in SITE_DIR.rglob("*.html")
                      if "static" not in p.relative_to(SITE_DIR).parts)
    leak_pages = []
    ph_total = 0
    ph_pages = 0
    for p in all_html:
        raw = p.read_text(encoding="utf-8", errors="replace")
        n = raw.count("PLACEHOLDER-ASIN")
        ph_total += n
        if n:
            ph_pages += 1
        if "__AMAZON_TAG__" in raw:
            leak_pages.append(str(p.relative_to(SITE_DIR)).replace("\\", "/"))
    rep.check(not leak_pages,
              "no page leaks the literal __AMAZON_TAG__ placeholder "
              "(%d pages scanned site-wide)" % len(all_html),
              "Affiliate tag substitution FAILED on %d page(s): %s"
              % (len(leak_pages), ", ".join(leak_pages[:10]))
              if leak_pages else None)

    # -------------------------------------------------- 5. affiliate link health
    print("\n[6/7] affiliate link health (report only)")
    if ph_total:
        rep.warn("PLACEHOLDER-ASIN dead links still present: %d site-wide "
                 "across %d page(s)" % (ph_total, ph_pages),
                 "These 404 for users. Run inventory/fix_asin.ps1 to replace them.")
    else:
        rep.ok("no PLACEHOLDER-ASIN dead links anywhere on the site")

    amazon_tag = str((cfg.get("monetization") or {}).get("amazon_tag") or "").strip()
    if amazon_tag:
        rep.ok("monetization.amazon_tag is set (%s)" % amazon_tag)
    else:
        rep.warn("monetization.amazon_tag is EMPTY - affiliate revenue is 0",
                 "Fill it in config.yaml once Amazon Associates approves you.")
    adsense = str((cfg.get("monetization") or {}).get("adsense_client") or "").strip()
    if not adsense:
        rep.warn("monetization.adsense_client is EMPTY - no ad revenue")

    # ------------------------------------------------------ 6. content hygiene
    print("\n[7/7] content hygiene")
    no_faq = 0
    for slug, meta, _ in posts:
        raw = (POSTS_DIR / (slug + ".md")).read_text(encoding="utf-8")
        if not re.search(r"(?im)^#{2,3}\s+.*(FAQ|Frequently Asked)", raw):
            no_faq += 1
    if no_faq:
        rep.warn("%d/%d posts have no FAQ section (lose FAQ rich results)"
                 % (no_faq, len(posts)))
    else:
        rep.ok("every post has an FAQ section")

    # Template placeholders that leaked into the prose as real content.
    # Real case found in this repo: the prompt's own example
    # "[Product Name](...)" got copied verbatim, so three live pages render a
    # product link whose visible text is literally "Product Name".
    residue_pat = (r"\[(?:Product Name|Brand|Product|Name|Link|URL|Your [^\]]{1,20})\]"
                   r"|\[X\]|\{\{?[a-z_]+\}?\}")
    residue = []
    for slug, _, _ in posts:
        raw = (POSTS_DIR / (slug + ".md")).read_text(encoding="utf-8")
        found = re.findall(residue_pat, raw)
        if found:
            residue.append("%s (%d: %s)" % (slug, len(found),
                                            ",".join(sorted(set(found))[:3])))
    rep.check(not residue,
              "no template placeholders leaked into article text",
              "%d post(s) contain literal placeholders as content:\n%s"
              % (len(residue), "\n".join(residue[:10])) if residue else None)

    # Titles must not imply the site tested products.
    # These words print straight into search-result titles - worse than a stray
    # sentence in the body, and the body check above cannot see them because it
    # only scans the body. Real cases found live:
    #   "The 7 Best Air Fryers Under $50 in 2026 (Tested & Compared)"  x2
    #   "The 5 Best Coffee Makers Under $100 in 2026 (Honest Review)" x2
    # The generator now strips them via clean_title(); this is the backstop so a
    # manual edit cannot reintroduce one.
    title_claim = re.compile(
        r"(?i)\b(?:tested|tested\s*(?:&|and)\s*compared|honest\s+review|"
        r"reviewed|hands[- ]on|lab[- ]tested|we\s+tried)\b")
    tainted = []
    for slug, _, _ in posts:
        raw = (POSTS_DIR / (slug + ".md")).read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
        if not m:
            continue
        tm = re.search(r"(?m)^title:\s*(.*(?:\n[ \t]+\S.*)*)", m.group(1))
        if not tm:
            continue
        val = " ".join(x.strip() for x in tm.group(1).splitlines())
        if title_claim.search(val):
            tainted.append("%s (%r)" % (slug, val[:60]))
    rep.check(not tainted, "no title claims that products were tested",
              "%d title(s) imply testing:\n%s"
              % (len(tainted), "\n".join(tainted[:8])) if tainted else None)

    # Pinterest pin coverage.
    # scripts/make_pins.py must run BEFORE build_site.py, and it was missing from
    # the CI workflow for the entire life of the project: no pin image was ever
    # generated, /static/pins/ 404'd on the live site, and nothing complained.
    # Gate it so a silent regression cannot happen again.
    pins_dir = SITE_DIR / "static" / "pins"
    if not pins_dir.is_dir():
        rep.fail("site/static/pins/ is missing - make_pins.py never ran",
                 "Add `python scripts/make_pins.py` before build_site.py "
                 "in .github/workflows/daily-publish.yml (see PINTEREST.md).")
    else:
        missing_pins = [s for s, _, _ in posts
                        if not (pins_dir / (s + ".png")).is_file()]
        rep.check(not missing_pins,
                  "every post has a Pinterest pin image (1000x1500)",
                  "%d/%d post(s) have no pin image, e.g. %s"
                  % (len(missing_pins), len(posts), ", ".join(missing_pins[:5]))
                  if missing_pins else None)

    return finish(rep, args)


def finish(rep, args):
    print("\n" + "=" * 68)
    print("RESULT: %d passed, %d warnings, %d failed"
          % (len(rep.passed), len(rep.warned), len(rep.failed)))
    if rep.failed:
        print("\nFAILED CHECKS:")
        for msg, _ in rep.failed:
            print("  - %s" % msg)
    print("=" * 68)
    if rep.failed and not args.warn_only:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    main()

def check_title_desc_length(site_dir):
    """标题 <=65、描述 50~165 —— 必须按【反转义后】的真实长度算。

    踩过的坑：早期按原始 HTML 数长度，' 被写成 &#x27;（1 字符变 6 字符），
    于是 68 字符的标题被算成 74，误报"超长"。所有长度判定一律先 html.unescape。
    """
    import html as _html
    import re as _re
    from html.parser import HTMLParser as _HP

    class _Head(_HP):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.title = ""
            self.desc = None
            self._t = False
            self._in = True

        def handle_starttag(self, tag, attrs):
            if tag == "body":
                self._in = False
            if not self._in:
                return
            if tag == "title":
                self._t = True
            if tag == "meta":
                a = {k.lower(): v for k, v in attrs}
                if (a.get("name") or "").lower() == "description" and self.desc is None:
                    self.desc = a.get("content") or ""

        def handle_endtag(self, tag):
            if tag == "title":
                self._t = False

        def handle_data(self, data):
            if self._t:
                self.title += data

    problems = []
    pages = list(Path(site_dir).rglob("index.html"))
    for f in pages:
        if "page" in f.parts and "posts" not in f.parts:
            continue
        try:
            h = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        p = _Head()
        try:
            p.feed(h)
        except Exception:
            continue
        rel = "/".join(f.relative_to(site_dir).parts[:-1]) or "/"
        t = _html.unescape(p.title).strip()
        d = _html.unescape(p.desc or "").strip()
        if t and len(t) > 65:
            problems.append("%s 标题 %d 字符 (>65)" % (rel, len(t)))
        if d:
            if len(d) > 165:
                problems.append("%s 描述 %d 字符 (>165)" % (rel, len(d)))
            elif len(d) < 50 and not rel.startswith("posts/"):
                problems.append("%s 描述 %d 字符 (<50)" % (rel, len(d)))
    return problems
