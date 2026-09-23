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
        # 公开联系邮箱（config.yaml 的 site.email）。留空则相关字段一律不输出，
        # 页面与改动前完全一致 —— 保持"配置驱动"，可随时撤回。
        self.email = (self.site.get("email") or "").strip()
        self.tag = (self.money.get("amazon_tag") or "").strip()
        self.adsense = (self.money.get("adsense_client") or "").strip()
        self.adsense_slot = (self.money.get("adsense_slot") or "").strip()
        # Pinterest 网站认领：填了才输出 meta，空着就不输出。
        # 这个占位曾经存在过（PINTEREST.md 里写着），但 base.html 与 config.yaml
        # 都没有实现它 —— 所以认领一直没法完成，pin 图带来的曝光也就没归因到站点。
        self.pinterest_verify = (self.site.get("pinterest_verify") or "").strip()
        # 访问统计配置。为什么不是 GA4：这个站面向英文读者，GA4 对【访问者】
        # 没问题，但站长自己连不上 Google —— 统计装了看不见等于没装。
        # 所以只接【免费 + 后台能从国内打开】的两家：Clarity / Umami。
        self.analytics = config.get("analytics", {}) or {}
        self.base_tpl = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))

    def analytics_snippet(self):
        """按 config.yaml 的 analytics: 生成 <head> 里的统计脚本。

        两个键，都不填就返回空串，页面与现在完全一致：
          clarity_id : Microsoft Clarity（免费无限量，带会话录像；
                       后台 login.microsoftonline.com，国内可开）
          umami_id   : Umami Cloud（轻量隐私友好，免费 10 万事件/月）
          umami_host : 自建 Umami 时改这里（默认官方云）
        """
        a = self.analytics
        parts = []

        clarity = (a.get("clarity_id") or "").strip()
        if clarity:
            parts.append(
                "<script type=\"text/javascript\">\n"
                "(function(c,l,a,r,i,t,y){\n"
                "c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};\n"
                "t=l.createElement(r);t.async=1;t.src=\"https://www.clarity.ms/tag/\"+i;\n"
                "y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);\n"
                "})(window, document, \"clarity\", \"script\", \"%s\");\n"
                "</script>" % html.escape(clarity))

        umami = (a.get("umami_id") or "").strip()
        if umami:
            host = (a.get("umami_host") or "https://cloud.umami.is").strip().rstrip("/")
            # ★ 为什么不用官方的 <script defer src=...> 写法（2026-09-21 改）
            # ----------------------------------------------------------
            # 官方写法是静态标签，任何执行 JS 的访客（包括 headless 浏览器、
            # 会渲染的爬虫、SEO 扫描器）都会把统计脚本拉下来并上报。
            # 实测后果：Umami 后台 24 小时显示 9 访客 / 23 浏览，看起来像有流量，
            # 但证据全指向爬虫：
            #   · 域名是 2 天前刚注册的，不可能有历史流量；
            #   · 访问记录里有 /imprint/ 和 /impressum/ —— 本站没有这两个页面，
            #     它们是德语站的法律页路径，只有扫描器会去试；这两个请求实际 404。
            #   · 浏览器 UA 出现 "Chrome (webview)"（headless 特征）；
            #   · 来源全部是"直接访问"，真实搜索流量一定带 referrer。
            # 数字误导决策，比没有数据更糟 —— 所以改成 JS 动态加载 + UA 过滤，
            # 与 hits_pixel() 用【同一套判据】。
            #
            # 注意顺序：data-website-id 必须在 appendChild 之前 setAttribute，
            # 否则 Umami 脚本读取时属性还不存在，上报会被服务端拒绝。
            parts.append(
                "<script>\n"
                "(function () {\n"
                "  var ua = navigator.userAgent || '';\n"
                "  if (/bot|crawl|spider|slurp|bingpreview|facebookexternalhit|headless"
                "|phantom|python-requests|python-urllib|curl\\/|wget|httpx|axios|monitor"
                "|uptime|lighthouse|pingdom/i.test(ua)) return;\n"
                "  if (navigator.webdriver) return;\n"
                "  var s = document.createElement('script');\n"
                "  s.defer = true;\n"
                "  s.src = '%s/script.js';\n"
                "  s.setAttribute('data-website-id', '%s');\n"
                "  (document.head || document.documentElement).appendChild(s);\n"
                "})();\n"
                "</script>" % (html.escape(host), html.escape(umami)))

        # GoatCounter：免费 + 【自带爬虫过滤】+ 有来源/页面/国家数据。
        # 为什么优先推荐它：hits.sh 只有浏览量，且爬虫照样计数 ——
        # 实测全站被爬一遍后 208 篇每篇恰好 3 次，数字完全没法用于决策。
        # GoatCounter 在服务端就把已知爬虫剔除了，是国内可达的免费方案里最合适的。
        gc = (a.get("goatcounter_code") or "").strip()
        if gc:
            endp = (a.get("goatcounter_host") or "https://%s.goatcounter.com" % gc).strip().rstrip("/")
            parts.append(
                '<script data-goatcounter="%s/count" async src="%s/count.js"></script>'
                % (html.escape(endp),
                   html.escape((a.get("goatcounter_js_host") or "https://gc.zgo.at").strip().rstrip("/"))))

        return "\n".join(parts)

    def hits_pixel(self, canonical):
        """hits.sh 计数像素：JS 动态加载 + 爬虫过滤。零注册，1x1 隐形图。

        为什么用它：这台机器的站长只有 Gmail（Web 打不开）且没有微软账号，
        任何"要邮箱验证"的统计服务都注册不了。hits.sh 不需要任何账号。

        计数键 = URL 去掉协议。于是【每篇文章一个独立计数器】——
        既能汇总站点总量，又能看出哪篇文章有人看（做内容决策要的就是这个）。

        必须知道的坑：只有 .svg 端点会计数。读取数据要用只读 API
        https://hits.sh/api/urns/<key>  —— 直接 GET .svg 会把数字刷高。

        ★ 为什么改成 JS 加载（2026-09-20 修）
        ------------------------------------
        原来是写死的 <img src="...svg"> 标签。实测后果：全站被爬一遍后，
        208 篇文章【每一篇都恰好 3 次】，共 622 次 —— 这是爬虫的分布，
        真人访问必然是长尾（少数几篇被反复读、多数接近 0）。
        数字完全无法用来判断"有没有真实读者"，比没有数据更糟，因为它误导决策。

        改成 JS 注入 + UA 粗筛后：
          · 不执行 JS 的爬虫 -> 一次都不计
          · navigator.webdriver（无头浏览器、自动化工具）-> 不计
          · 已知爬虫 UA -> 不发请求
          · 真人浏览器 -> 正常计数
        这不是完美方案（会渲染 JS 的爬虫仍会计数），但把噪声去掉了绝大部分，
        而且不需要任何账号。要彻底解决就填 analytics.goatcounter_code 或 umami_id。
        """
        if not self.analytics.get("hits_sh"):
            return ""
        key = re.sub(r"^https?://", "", (canonical or "").strip()).rstrip("/")
        if not key:
            return ""
        # 两个计数：① 页面浏览 ② 出站点击（读者点联盟链接时打一发）
        # 为什么要追踪出站：页面浏览量只说明"有人看"，点出去多少才说明
        # "哪篇文章真的能带货" —— 这是做内容决策唯一有用的数字。
        # 计数键加 /outbound 后缀，和浏览计数互不混淆。
        return (
            "<script>\n"
            "// 浏览计数：JS 动态加载 + 爬虫过滤（详见 build_site.hits_pixel 注释）\n"
            "(function () {\n"
            "  var ua = navigator.userAgent || '';\n"
            "  if (/bot|crawl|spider|slurp|bingpreview|facebookexternalhit|"
            "headless|phantom|puppeteer|playwright|python-requests|python-urllib|"
            "curl\\/|wget|httpx|scrapy|monitor|uptime|pingdom/i.test(ua)) return;\n"
            "  if (navigator.webdriver) return;\n"
            "  function fire() {\n"
            "    try {\n"
            "      var i = new Image(1, 1);\n"
            "      i.alt = '';\n"
            "      i.setAttribute('aria-hidden', 'true');\n"
            "      i.style.cssText = 'position:absolute;left:-9999px;top:0;border:0';\n"
            "      i.src = 'https://hits.sh/' + %s + '.svg';\n"
            "      var p = document.body || document.documentElement;\n"
            "      if (p) p.appendChild(i);\n"
            "    } catch (e) {}\n"
            "  }\n"
            "  if (document.body) { fire(); }\n"
            "  else { document.addEventListener('DOMContentLoaded', fire, { once: true }); }\n"
            "})();\n"
            "</script>\n"
            "<script>\n"
            "// 出站点击追踪（无 cookie、不阻塞跳转、只上报「这一页发出了点击」）\n"
            "document.addEventListener('click', function (e) {\n"
            "  var a = e.target && e.target.closest ? "
            "e.target.closest('a[href*=\"amazon.com\"]') : null;\n"
            "  if (!a) return;\n"
            "  try {\n"
            "    var k = %s + '/outbound';\n"
            "    var i = new Image(1, 1);\n"
            "    i.src = 'https://hits.sh/' + k + '.svg';\n"
            "  } catch (err) {}\n"
            "}, true);\n"
            "</script>"
            % (json.dumps(key), json.dumps(key))
        )

    def path(self, *parts):
        """Build a canonical site URL.

        GitHub Pages serves directory-style URLs **with** a trailing slash and
        301-redirects the slash-less form. The old version of this helper always
        emitted the slash-less form, which meant every <link rel=canonical> and
        every sitemap <loc> pointed at a redirect:

            /posts/foo      -> 301 -> /posts/foo/
            canonical       -> https://…/posts/foo        (a redirect)
            sitemap <loc>   -> https://…/posts/foo        (a redirect)

        The sitemap spec requires canonical, 200-OK URLs, and Search Console
        reports redirecting URLs as "Page with redirect" instead of "Indexed".
        So: directory-style URLs get the trailing slash, file URLs (style.css,
        xxx-1.png) do not.
        """
        clean = [str(p).strip("/") for p in parts if str(p).strip("/")]
        if not clean:
            return self.base + "/"
        url = self.base + "/" + "/".join(clean)
        last_segment = clean[-1].rsplit("/", 1)[-1]
        if "." in last_segment:
            return url            # 文件：static/style.css、static/img/x-1.png
        return url + "/"          # 目录式页面：GitHub Pages 的规范形式

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
        og_twitter_line = ""
        if og_image:
            og_image_line = '<meta property="og:image" content="%s">' % html.escape(og_image)
            og_twitter_line = '<meta name="twitter:image" content="%s">' % html.escape(og_image)

        # Pinterest 网站认领标签（config.yaml -> site.pinterest_verify）。
        # 未填写时输出空行，页面与现在完全一致。
        pinterest_meta = ""
        if self.pinterest_verify:
            pinterest_meta = ('<meta name="p:domain_verify" content="%s">'
                              % html.escape(self.pinterest_verify))
        ctx = {
            "pinterest_verify_meta": pinterest_meta,
            "analytics_head": self.analytics_snippet(),
            "hits_pixel": self.hits_pixel(canonical),
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
            "og_image_twitter": og_twitter_line,
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

    def affiliate_note(self):
        """正文顶部的联盟披露（放在首个联盟链接之前）。

        FTC 16 CFR 255 与 Amazon 运营协议都要求披露"清晰且显著"。
        页脚那条是站级别的，够合规；但最佳实践是在读者【即将看到联盟链接】
        的位置再明示一次 —— 这也是过审时审核员最容易看到的地方。
        可用 monetization.affiliate_note: false 关掉。
        """
        # 注意：属性名是 self.money（见 __init__），不是 self.monetization
        if not self.money.get("affiliate_note", True):
            return ""
        return ('<p class="affiliate-note"><em>This post contains affiliate links. '
                'If you buy through them, we may earn a small commission at no extra '
                'cost to you. We only link to products we could verify exist.</em></p>')

    def inline_links(self, related, category=""):
        """Build a natural in-body paragraph linking to 3 related posts.

        从 2 条提到 3 条：正文内链比文末列表权重更高，多一条就多一条通路。
        """
        picks = related[:3] if related else []
        linked = []
        for p in picks:
            title = html.escape(p["meta"].get("title", "this guide"))
            linked.append('<a href="%s">%s</a>' % (self.path("posts", slug_of(p)), title))
        if not linked:
            return ""
        if len(linked) == 1:
            sentence = "While you're here, you might also like %s." % linked[0]
        elif len(linked) == 2:
            sentence = "If this helped, you'll also want to read %s and %s." % (linked[0], linked[1])
        else:
            sentence = ("If this helped, you'll also want to read %s, %s and %s."
                        % (linked[0], linked[1], linked[2]))
        return '<p class="inline-links">%s</p>' % sentence

    def build_post(self, post, related):
        meta = post["meta"]
        slug = slug_of(post)
        cat = meta.get("category", "")
        date = meta.get("date", "")
        md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
        body_html = md.convert(post["body"])
        body_html = amazon_links(body_html, self.tag)
        # 披露语插在第一个段落之后（首个联盟链接之前），保证"先披露后链接"
        note = self.affiliate_note()
        if note:
            m0 = re.search(r"</p>", body_html)
            if m0:
                body_html = body_html[:m0.end()] + "\n" + note + "\n" + body_html[m0.end():]
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
            # dateModified 必须诚实：优先用 front matter 的 updated: 字段，
            # 没有就退回 date（= 这篇从未更新过，这是实话）。
            # ★ 绝不能用文件 mtime —— CI 每天检出会把 mtime 刷成"现在"，
            #   那等于声称 229 篇全部每天更新，是纯粹的假数据。
            "dateModified": meta.get("updated") or date,
            # reviewedBy：本项目没有人类编辑，不能编一个假名字。
            # 真正做审核的是自动质检流水线，指向说明页如实交代。
            "reviewedBy": {"@type": "Organization", "name": self.name,
                           "url": self.path("editorial-policy")},
            "author": {"@type": "Person", "name": self.site.get("author", self.name)},
            "publisher": {"@type": "Organization", "name": self.name},
            "mainEntityOfPage": canonical,
        }
        faqs = extract_faq(body_html)
        # BreadcrumbList: the page already renders a visual breadcrumb, but it
        # had no structured data, so Google could not show the breadcrumb trail.
        breadcrumb = {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home",
                 "item": self.path("")},
                {"@type": "ListItem", "position": 2, "name": category_name(cat),
                 "item": self.path("category", cat)},
                {"@type": "ListItem", "position": 3, "name": meta.get("title", ""),
                 "item": canonical},
            ],
        }
        graph = [article, breadcrumb]
        if faqs:
            graph.append({"@type": "FAQPage", "mainEntity": faqs})
        jsonld = {"@context": "https://schema.org", "@graph": graph}
        toc_html, body_html = add_toc(body_html)
        body = (
            "<header class=\"post-head\"><h1>%s</h1>"
            "<p class=\"post-meta\">%s &middot; %s</p></header>"
            "<div class=\"post-body\">%s%s</div>%s"
            % (html.escape(meta.get("title", "")), html.escape(date),
               html.escape(category_name(cat)), toc_html, body_html, related_html)
        )
        # og:image. The original code only looked for static/pins/<slug>.png,
        # a directory nothing ever generates, so every page shipped with no
        # social preview image. Fall back to the first generated illustration.
        pin = OUT_DIR / "static" / "pins" / (slug + ".png")
        if pin.exists():
            og_image = self.path("static", "pins", slug + ".png")
        else:
            og_image = self.path("static", "img", slug + "-1.png")
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
        # WebSite + Organization 一起给。
        # 为什么补 Organization：联盟与 AdSense 审核都要判断"这是不是一个
        # 真实可联系的实体"。只有 WebSite 节点时，站点在结构化数据里没有
        # 任何主体信息；补上 Organization + contactPoint 才能明确表达主体是谁、
        # 怎么联系。用 @graph 组织两个节点，避免再插一个 script 块。
        org = {
            "@type": "Organization",
            "name": self.name,
            "url": self.path(""),
        }
        if self.email:
            org["email"] = self.email
            org["contactPoint"] = {
                "@type": "ContactPoint",
                "contactType": "customer support",
                "email": self.email,
            }
        desc = self.site.get("description", "")
        if desc:
            org["description"] = desc
        jsonld = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebSite", "name": self.name, "url": self.path("")},
                org,
            ],
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
            description=(
                    "Browse every %s guide on %s: honest budget picks, real prices, "
                    "and money-saving tips for a thrifty home. Updated every week."
                    % (category_name(cat), self.name)
                ),
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

    def _trust_page(self, slug, title, body, desc=""):
        # desc 必须显式传入：原来的默认值 "%s for %s." 只有 20 来个字符，
        # 会让 about / contact / privacy 三页的 meta description 短得毫无信息量
        # （Google 会自己抓正文片段，等于放弃了这段可控的展示位）。
        page = self.render_page(
            title="%s — %s" % (title, self.name),
            description=desc or ("%s for %s." % (title, self.name)),
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
            "<p>Questions about this policy? Email us at "
            "<a href=\"mailto:liheng643013550@gmail.com\">liheng643013550&#64;gmail&#46;com</a>, or see our "
            "<a href=\"%s/contact/\">contact page</a>.</p>"
            % (self.name, self.base, self.base)
        )
        self._trust_page(
            "privacy-policy", "Privacy Policy", privacy,
            desc=("A plain-English privacy policy for %s: what data we collect, how "
                  "cookies and analytics are used, and how affiliate links work."
                  % self.name),
        )

        # About
        about = (
            '<h1>About %s</h1>'
            '<p>%s is a practical buying guide for people who want a comfortable home '
            'without overspending. We cover budget kitchen appliances, storage, '
            'cleaning, home office, pets, garden and small energy savings - the '
            'everyday purchases where a bad choice quietly costs you money for years.</p>'
            '<h2>How we choose what to recommend</h2>'
            '<p>We do not have a test lab, and we do not pretend to. Instead we work '
            'from published manufacturer specifications, owner reviews, and one check '
            'that most deal sites skip: <strong>every product we link to is verified to '
            'actually exist on Amazon</strong>. We look the item up, match it against '
            'the brand and model we intend to recommend, and discard it if the match '
            'turns out to be a look-alike, an accessory, a bundle, or a different '
            'capacity. A guide that sends you to a part instead of the product is worse '
            'than no guide at all.</p>'
            '<h2>What we will not do</h2>'
            '<p>We will not claim we tested, bought, measured or owned a product we '
            'have not. We will not invent a model number, a price, or a statistic to '
            'make a paragraph look more authoritative. If we cannot verify something, '
            'we leave it out - a shorter honest guide beats a longer confident one.</p>'
            '<h2>How the site is funded</h2>'
            '<p>%s is free to read. We earn a small commission when you buy through '
            'some of our links, and we display advertising. Neither changes the price '
            'you pay, and neither decides what we recommend - our picks are chosen '
            'before we know whether a link will earn anything. See our '
            '<a href="%s/privacy-policy/">privacy policy</a> for the full affiliate '
            'disclosure.</p>'
            '<h2>Corrections</h2>'
            '<p>Prices, stock and model numbers change constantly. If you spot '
            'something out of date or simply wrong, please '
            '<a href="%s/contact/">tell us</a> and we will fix it.</p>'
            '<h2>Start exploring</h2>'
            '<p>Browse every guide on the <a href="%s">home page</a>, or jump straight '
            'to a <a href="%s/categories/">category</a>.</p>'
            % (self.name, self.name, self.name,
               self.base, self.base, self.base, self.base)
        )
        self._trust_page(
            "about", "About", about,
            desc=("What %s is, how our budget buying guides are put together, how we "
                  "handle affiliate links, and how to tell us about a mistake."
                  % self.name),
        )

        # Contact
        contact = (
            "<h1>Contact</h1>"
            "<p>Have a question, a suggestion, or found an issue on the site? We would "
            "love to hear from you.</p>"
            "<p>Email is the best way to reach us: "
            "<a href=\"mailto:liheng643013550@gmail.com\"><strong>liheng643013550&#64;gmail&#46;com</strong></a></p>"
            "<p>We read every message and reply as soon as we can - usually within a "
            "few days. If you are reporting a broken link, a wrong price, or a product "
            "that no longer matches our description, please include the page address so "
            "we can fix it faster.</p>"
            "<p>You can also browse our guides from the <a href=\"%s\">home page</a> or "
            "check our <a href=\"%s/categories/\">full list of categories</a>.</p>"
            % (self.base, self.base)
        )
        # ★ GEO：编辑政策页。AI 判断"这条内容能不能引用"时会看有没有审核说明。
        # 如实写：没有人类编辑，审核由自动流水线完成 —— 编一个假编辑名反而危险。
        ep = (
            "<h1>Editorial Policy</h1>"
            "<p>This page explains how the guides on %s are produced and checked, "
            "so you can judge how much to rely on them.</p>"
            "<h2>How articles are produced</h2>"
            "<p>Articles are drafted with AI assistance, then passed through an "
            "automated quality gate before they can be published. If a draft fails "
            "any check it is sent back for revision rather than published.</p>"
            "<h2>What the checks enforce</h2>"
            "<ul>"
            "<li>No fabricated first-hand experience. We do not write \"we tested\" "
            "or \"we have owned this for two years\" — nobody on this project has "
            "handled these products.</li>"
            "<li>No dead links. Every outbound link is checked before publishing.</li>"
            "<li>Product links point to listings that were verified to exist at the "
            "time of writing.</li>"
            "<li>Every guide ends with an FAQ, and each article is checked for "
            "duplicate topics against the rest of the site.</li>"
            "</ul>"
            "<h2>What we are not</h2>"
            "<p>We are not a lab. We do not run instrumented tests, and we do not "
            "claim to. Where we state a price, it is the price visible on the "
            "linked listing when the article was written — check it yourself before "
            "buying, because prices move.</p>"
            "<h2>Affiliate disclosure</h2>"
            "<p>Some links are affiliate links: if you buy through them we may earn "
            "a commission at no extra cost to you. This does not change which "
            "products we list, and it is disclosed at the top of every guide.</p>"
            "<h2>Corrections</h2>"
            "<p>If something here is wrong, tell us at <a href=\"mailto:%s\">%s</a> "
            "and we will fix it. Corrections are the main way this site improves.</p>"
            % (self.name, self.email, self.email)
        )
        self._trust_page(
            "editorial-policy", "Editorial Policy", ep,
            desc=("How %s produces and checks its guides: AI-assisted drafting, "
                  "automated quality gates, no fabricated testing claims, and how "
                  "to report an error." % self.name),
        )

        self._trust_page(
            "contact", "Contact", contact,
            desc=("How to reach %s with a question, a correction, or a product "
                  "suggestion — and what to expect when you get in touch." % self.name),
        )


    def build_redirects(self):
        """按 config.yaml 的 redirects: 写出跳转页。

        为什么需要它：合并重复选题（例如把 best-air-fryers-under-50 并进
        best-air-fryer-under-50）时，旧地址不能直接 404 —— 那会丢掉已经
        被收录的链接。GitHub Pages 不能配 301，静态站的标准做法是在原地址
        留一个最小跳转页，三样都给、兼容性最好：
          - <link rel="canonical">  告诉搜索引擎真正的地址是哪个
          - <meta robots noindex,follow>  别索引这个跳转页，但把权重传出去
          - <meta http-equiv="refresh">  让浏览器立刻跳，读者无感
        另加一行可见链接，兜住禁用跳转的情况。
        """
        try:
            cfg = load_config()
        except Exception:
            return 0
        red = cfg.get("redirects") or {}
        if not isinstance(red, dict):
            return 0
        n = 0
        for old, new in red.items():
            new = str(new)
            target = new if new.startswith("http") else self.path(new.lstrip("/"))
            out = OUT_DIR / str(old).strip("/") / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<title>Moved</title>'
                '<link rel="canonical" href="%s">'
                '<meta name="robots" content="noindex,follow">'
                '<meta http-equiv="refresh" content="0; url=%s">'
                '</head><body><p>This page has moved to '
                '<a href="%s">%s</a>.</p></body></html>'
                % (target, target, target, target), encoding="utf-8")
            n += 1
        return n

    def build_seo_files(self, posts):
        # lastmod: use each post's own publish date instead of stamping every
        # URL with "today". The old behaviour told search engines the whole
        # site changed every single day, which trains them to ignore the field
        # entirely. Index/listing pages legitimately change on every build.
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [(self.path(""), today)]
        entries.append((self.path("categories"), today))
        entries += [(self.path("privacy-policy"), today),
                    (self.path("about"), today),
                    (self.path("contact"), today),
                    # GEO 新增：编辑政策页必须进 sitemap，否则搜索引擎发现不了它，
                    # AI 也就读不到"这个站是怎么做审核的"这一关键信任信号。
                    (self.path("editorial-policy"), today)]
        entries += [(self.path("category", c), today) for c in CATEGORY_NAMES]
        entries += [(self.path("posts", slug_of(p)), p["meta"].get("date") or today)
                    for p in posts]
        urls_xml = "".join(
            "<url><loc>%s</loc><lastmod>%s</lastmod></url>" % (u, lm)
            for u, lm in entries
        )
        (OUT_DIR / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n' % urls_xml,
            encoding="utf-8",
        )
        # ★ GEO（生成式引擎优化）：显式欢迎 AI 爬虫。
        # 原来只有 User-agent: * / Allow: / —— 没挡，但也没表态。
        # 明确授权能减少被误判为"不希望被索引"的风险。
        # 这些爬虫取内容是为了【回答用户问题并注明来源】，属于我们要的曝光。
        ai_bots = ["GPTBot", "OAI-SearchBot", "ChatGPT-User",
                   "ClaudeBot", "Claude-User", "PerplexityBot",
                   "Google-Extended", "Applebot-Extended", "CCBot",
                   "Bytespider", "meta-externalagent"]
        robot_lines = ["User-agent: *", "Allow: /", ""]
        for _b in ai_bots:
            robot_lines += ["User-agent: %s" % _b, "Allow: /", ""]
        robot_lines.append("Sitemap: %s" % self.path("sitemap.xml"))
        robot_lines.append("")
        (OUT_DIR / "robots.txt").write_text(
            "\n".join(robot_lines), encoding="utf-8",
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
            # 相关性排序（而不是同分类前 3 篇）+ 5 篇：
            # 让每篇文章都链向"最像它"的同伴，而不是永远链向同几个页面。
            related = pick_related(post, posts, n=5)
            self.build_post(post, related)
        self.build_seo_files(posts)
        self.build_redirects()
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


# ---------------------------------------------------------------------------
# 相关文章选择：按【相关性】而不是"同分类前 3 篇"
# ---------------------------------------------------------------------------
# 旧逻辑是 cat_posts[:3] —— 同分类里按文档顺序取前 3 篇。后果很严重：
# 每个分类里固定那 2~3 篇吃掉全部内链权重，其余 20+ 篇几乎没有任何内链指向，
# 读者也永远被推向同样几篇文章。内链是搜索引擎理解站点结构的主要信号，
# 这个缺陷等于把整个分类的权重压在几个页面上。
#
# 这里改成余弦相似度（标题 + 描述 + 关键词 + 正文开头），同分类加权。
# 同分类加权是有意的：读者点"空气炸锅"之后更可能想看同类内容，
# 也能强化"分类 = 主题簇"的信号。跨分类高相关仍然保留通路。
_STOP = set("""the a an and or of for to in on with without best cheap budget
under over top guide how why what when which is are was were be been you your our
their it its this that these those from by as at into than then them they we us
about more most less least can could should would will just only also very
really good great better worse than make makes made use uses used get gets got
""".split())


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


_VEC_CACHE = {}


def score_vec(post):
    """把一篇文章压成一个词集合，用于算相关性（结果缓存，构建时只算一次）。"""
    key = id(post)
    v = _VEC_CACHE.get(key)
    if v is None:
        meta = post.get("meta", {})
        parts = [meta.get("title", ""), meta.get("description", ""),
                 " ".join(meta.get("keywords") or []),
                 meta.get("category", ""),
                 # 正文开头 1500 字符：足够表达主题，又不必扫全文
                 " ".join((post.get("body") or "")[:1500].split())]
        v = _tokens(" ".join(str(x) for x in parts))
        _VEC_CACHE[key] = v
    return v


def _relevance(a, b):
    if not a or not b:
        return 0.0
    # 用 sqrt 归一化，避免长文章天然占便宜
    return len(a & b) / ((len(a) ** 0.5) * (len(b) ** 0.5))


def pick_related(post, posts, n=5):
    """按相关性挑 n 篇相关文章（同分类加权，结果确定性可复现）。"""
    me = score_vec(post)
    cat = post.get("meta", {}).get("category", "")
    scored = []
    for p in posts:
        if p is post:
            continue
        s = _relevance(me, score_vec(p))
        if p.get("meta", {}).get("category", "") == cat:
            s += 0.25
        scored.append((s, p))
    # 同分时按 slug 排序，保证每次构建结果一致（否则 diff 会天天变）
    scored.sort(key=lambda x: (-x[0], slug_of(x[1])))
    return [p for _, p in scored[:n]]


# 入口必须放在【所有定义之后】：
# Python 是自上而下执行的，如果 __main__ 块写在前面，
# main() 会在 pick_related 等函数定义之前被调用 -> NameError。


if __name__ == "__main__":
    main()
