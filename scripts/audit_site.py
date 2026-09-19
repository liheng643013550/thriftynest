#!/usr/bin/env python3
"""站点巡检：一次性体检所有"会悄悄变坏"的东西，输出 Markdown 报告。

为什么要有这个
--------------
内容站的问题是"慢性病"：死链、缺图、缺 FAQ、内链集中、页面变薄……
单次看不出来，攒三个月就废了。人工不可能每周查，所以做成自动的。

它只【体检 + 报告】，不自动改内容 —— 改内容交给人（或专门的工具）。
CI 里由 site-audit.yml 每周跑一次，把报告开成/更新成一个 issue，
于是"待办清单"自己长出来，不需要谁记得。

退出码：0 = 全绿；1 = 有告警（CI 里用 continue-on-error 避免刷红）
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import ctypes
    cp = ctypes.windll.kernel32.GetConsoleOutputCP()
    if cp and cp not in (0, 65001):
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="cp%d" % cp, errors="replace")
            except Exception:
                pass
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
SITE = ROOT / "site"


def read(p):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


posts = sorted(POSTS.glob("*.md"))
pages = sorted(d for d in (SITE / "posts").iterdir() if d.is_dir()) if (SITE / "posts").exists() else []
report = []
warn = []


def line(s=""):
    report.append(s)
    print(s)


today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
line("# ThriftyNest 站点巡检")
line()
line("生成时间：%s（UTC）" % today)
line()

# ---- 1. 内容完整性 ----
line("## 1. 内容完整性")
line()
line("| 检查 | 结果 |")
line("|---|---|")
PLACE = "PLACEHOLDER-ASIN"
dead = sum(read(p).count(PLACE) for p in posts)
line("| 死链（%s） | %d %s |" % (PLACE, dead, "✅" if dead == 0 else "❌"))
if dead:
    warn.append("有 %d 条死链" % dead)

no_faq = [p.name for p in posts
          if not re.search(r"(?im)^##\s+.*(Frequently Asked Questions|\bFAQ\b)", read(p))]
line("| 缺 FAQ 的文章 | %d %s |" % (len(no_faq), "✅" if not no_faq else "❌"))
if no_faq:
    warn.append("%d 篇缺 FAQ" % len(no_faq))

no_h2q = [p.name for p in posts if not re.search(r"(?m)^###\s+.*\?", read(p))]
line("| 缺 H3 问答的文章 | %d %s |" % (len(no_h2q), "✅" if not no_h2q else "⚠️"))

bare = [p.name for p in posts if re.search(r"(?m)^Frequently Asked Questions[ \t]*$", read(p))]
line("| FAQ 标题缺 '## ' 前缀 | %d %s |" % (len(bare), "✅" if not bare else "❌"))
if bare:
    warn.append("%d 篇 FAQ 标题格式错（会丢结构化数据）" % len(bare))

# 必须与生成器闸门、fix_fake_experience.py 用【同一套】判定。
# 旧版本这里是宽松式 \b(we|I)\b[^.]{0,20}\b(tested|...)\b —— 允许任意字符
# 夹在中间，会把 "I recommend ... tried" 这类无关联的句子判成编造体验，
# 实测一次误报 20 篇。巡检误报比漏报更贵：每周狼来了就没人看了。
FAKE = (r"\b(?:I|we)(?:'ve|'d| have| had)?\s+"
        r"(?:tested|tried|used|reviewed|measured|bought|purchased|owned|ordered|"
        r"kept|returned|found|noticed|learned|compared|ran)\b"
        r"|\bin (?:our|my) tests?\b|\bour review unit\b"
        r"|\bI (?:have|'ve|had) (?:to )?(?:make )?a confession\b"
        r"|\bI used to think\b|\bI(?:'ll| will) admit\b")
fake = [p.name for p in posts if re.search(FAKE, read(p))]
line("| 编造第一人称体验 | %d %s |" % (len(fake), "✅" if not fake else "❌"))
if fake:
    warn.append("%d 篇有编造体验" % len(fake))

# ---- 2. 词数与结构 ----
line()
line("## 2. 长度与结构")
line()
words = [len(re.findall(r"\S+", read(p))) for p in posts]
if words:
    ws = sorted(words)
    thin = [p.name for p, w in zip(posts, words) if w < 800]
    line("| 文章数 | %d |" % len(posts))
    line("| 平均 / 中位词数 | %d / %d |" % (sum(words) // len(words), ws[len(ws) // 2]))
    line("| 最短 / 最长 | %d / %d |" % (min(words), max(words)))
    line("| 偏薄（<800 词） | %d %s |" % (len(thin), "✅" if not thin else "⚠️"))
    if thin:
        warn.append("%d 篇偏薄" % len(thin))

# ---- 3. 内链分布（关键指标：集中度）----
line()
line("## 3. 内部链接分布")
line()
if pages:
    hits = Counter()
    for d in pages:
        h = read(d / "index.html")
        for m in re.finditer(r'href="[^"]*posts/([^"/]+)/"', h):
            hits[m.group(1)] += 1
    total_links = sum(hits.values())
    orphans = [d.name for d in pages if d.name not in hits]
    top10 = sum(c for _, c in hits.most_common(max(1, len(pages) // 10)))
    conc = (100.0 * top10 / total_links) if total_links else 0
    line("| 页面内链总数 | %d |" % total_links)
    line("| 平均每页 | %.1f |" % (total_links / len(pages)))
    line("| 从未被链的文章（孤岛） | %d %s |" % (len(orphans), "✅" if not orphans else "❌"))
    line("| 前 10%% 页面占内链比例 | %.0f%% %s |" % (conc, "✅" if conc < 35 else "⚠️ 集中"))
    if orphans:
        warn.append("%d 篇孤岛（没有任何内链指向）" % len(orphans))
    if conc >= 35:
        warn.append("内链集中度 %.0f%% 偏高" % conc)

# ---- 4. 变现与合规 ----
line()
line("## 4. 变现与合规")
line()
cfg = read(ROOT / "config.yaml")
home = read(SITE / "index.html")
# 合规检查必须看【文章页】：披露语只在文章里、联盟链接也只在文章里。
# 拿首页查会全线误报（首页既没有 affiliate-note 也没有 amazon 链接）——
# 这是我第一版巡检的真实 bug，会让人误以为合规项全挂了。
art = ""
if pages:
    art = read(pages[0] / "index.html")
probe = art or home
row = []
for key in ("amazon_tag", "adsense_client", "adsense_slot"):
    m = re.search(r"(?m)^\s*%s:\s*(.*)$" % key, cfg)
    v = (m.group(1) or "").strip().strip('"').strip("'") if m else ""
    row.append((key, bool(v)))
line("| 开关 | 状态 |")
line("|---|---|")
for k, v in row:
    line("| %s | %s |" % (k, "已填 ✅" if v else "空 ⏳（等账号）"))
line("| 联盟披露（页脚） | %s |" % ("✅" if re.search(r"(?i)as an amazon associate", home) else "❌"))
line("| 正文顶部披露 | %s |"
     % ("✅" if 'class="affiliate-note"' in probe else "❌"))
line("| 联盟链接 rel=sponsored | %s |"
     % ("✅" if re.search(r'amazon\.com[^>]*rel="[^"]*sponsored', probe) else "❌"))
line("| 出站点击追踪 | %s |" % ("✅" if "outbound" in probe else "❌"))
if 'class="affiliate-note"' not in probe:
    warn.append("文章页没有正文顶部披露（FTC 最佳实践）")

# ---- 5. 结构化数据与抓取 ----
line()
line("## 5. 结构化数据与抓取")
line()
if pages:
    sample = pages[:30]
    types = Counter()
    for d in sample:
        for t in re.findall(r'"@type"\s*:\s*"([^"]+)"', read(d / "index.html")):
            types[t] += 1
    for t in ("FAQPage", "Article", "BreadcrumbList"):
        n = types.get(t, 0)
        line("| %s | %d / %d %s |" % (t, n, len(sample),
                                      "✅" if n == len(sample) else "⚠️"))
    # Question 是每页 3 个，不能拿"页数"当分母比 —— 正确断言是"每页至少 3 个"
    qn = types.get("Question", 0)
    per = (qn / len(sample)) if sample else 0
    line("| Question（每页均摊） | %.1f / 页 %s |"
         % (per, "✅" if per >= 3 else "⚠️"))
    if per < 3:
        warn.append("部分页面 FAQPage 里的 Question 少于 3 个")
sm = read(SITE / "sitemap.xml")
line("| sitemap URL 数 | %d |" % sm.count("<loc>"))
line("| sitemap 有 lastmod | %s |" % ("✅" if "<lastmod>" in sm else "❌"))
line("| robots.txt | %s |" % ("✅" if (SITE / "robots.txt").exists() else "❌"))

# ---- 汇总 ----
line()
line("## 汇总")
line()
if warn:
    line("需要关注 %d 项：" % len(warn))
    line()
    for w in warn:
        line("- [ ] %s" % w)
else:
    line("**全部通过 ✅** 没有任何需要处理的项。")

out = ROOT / "audit-report.md"
out.write_text("\n".join(report) + "\n", encoding="utf-8")
print()
print("报告已写到 %s" % out)
sys.exit(1 if warn else 0)
