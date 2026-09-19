"""Generate new articles from the topic pool using the configured LLM.

Usage:
    python scripts/generate_articles.py [--limit N] [--topic "keyword"] [--dry-run]

Reads config.yaml, picks unused topics, writes content/posts/<slug>.md with
YAML frontmatter + markdown body. A topic is "used" when its slug file
exists, so runs never produce duplicates.

If no API key is present it prints a warning and exits 0, so the site still
builds before you configure the key (see SETUP.md).

----------------------------------------------------------------------------
本版相比原版的改动（v2）
----------------------------------------------------------------------------
1. [修复] `--dry-run` 现在不要求 API key。原版在 dry-run 之前就检查 key 并
   提前 return，导致没配 key 时无法预览选题。
2. [合规] WRITER_SYSTEM 明确禁止"我们测试过/我们买过/我们测量过"这类
   虚假亲历声称 —— 本管线没有任何测试能力，伪造 Experience 是 E-E-A-T 的
   主动欺骗，比"没有经验"更危险。
3. [质量] 删除 de_templatify()。它的作用是"给句子加 In practice,/Honestly,
   之类开场词以掩盖 AI 痕迹"，实测效果边际、会在多个文章间形成新的模板
   指纹、并会产生 "In most cases, Do not ..." 这类语法错误。替换为
   content_variants()：从 Prompt 层面注入 audience/angle/structure 三维
   变体，产生真正的结构差异。
4. [质量] 新增意图路由 detect_intent()：X vs Y / is X worth it /
   why is my X / how much does X cost 四类使用专门的写作骨架，而不是
   一律套"5-7 个产品清单"。
5. [修复] slugify() 归一化英文单复数（fryers->fryer、cookers->cooker 等），
   避免"种子文章用复数 slug、选题池算单数 slug"导致的去重失效与重复内容。
6. [稳健] 写入前校验正文长度与 FAQ 小节，不合格的文章拒绝落盘。
7. [可观测] 日志带 [generate] 前缀与序号，失败逐条打印原因（原版已如此，
   此处补充"写入后回显字数与 FAQ 状态"）。
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from llm import LLMError, complete
from keywords import TOPICS

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"

# ---------------------------------------------------------------------------
# 人设：明确禁止虚假的亲历/测试声称
# ---------------------------------------------------------------------------
WRITER_SYSTEM = (
    "You are an experienced SEO copywriter for ThriftyNest, a budget home & "
    "kitchen blog. You write practical, honest, easy-to-read US English that "
    "real people find genuinely useful. No fluff, no hype, no marketing speak.\n"
    "\n"
    "CRITICAL HONESTY RULES (never break these):\n"
    "- You have NO testing lab, NO review samples, and NO hands-on experience. "
    "Never write that you, your team, or this site physically tested, bought, "
    "measured, or tried a product.\n"
    "- Never use phrases like 'we tested', 'after weeks of testing', "
    "'in our tests', 'we measured', 'our review unit', 'we bought'.\n"
    "- Base every claim on published specifications, manufacturer data, and "
    "the consensus of verified owner reviews. Attribute it: 'Owner reviews "
    "consistently report...', 'The specs show...', 'At this price the "
    "trade-off is...', 'Reviewers often mention...'.\n"
    "- If you are not sure about a number, use a clearly hedged range and say "
    "it varies by model, instead of inventing a precise figure.\n"
    "- Only name real, well-known brands and real product model names. Never "
    "invent a brand. Never invent an exact price as if it were verified."
)


# ---------------------------------------------------------------------------
# slug / 选题挑选
# ---------------------------------------------------------------------------
def _singularize(word):
    """Conservative English singularizer used only for slug de-duplication.

    We only need *consistency* between the keyword pool and existing filenames,
    not linguistic perfection — so this stays deliberately narrow and errs on
    the side of leaving a word alone. Irregulars are handled by _IRREGULAR
    first; anything ambiguous is returned unchanged.
    """
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"          # berries -> berry
    if word.endswith(("ses", "xes", "zes", "ches", "shes", "sses")):
        return word[:-2]                # boxes -> box, dishes -> dish, glasses -> glass
    if word.endswith(("ss", "us", "is", "as", "os")):
        return word                     # glass, status, analysis, canvas, photos
    if word.endswith("s"):
        return word[:-1]                # containers -> container, tools -> tool
    return word


# 不规则复数（先查表，再退回规则）
_IRREGULAR = {
    "knives": "knife", "shelves": "shelf", "leaves": "leaf", "halves": "half",
    "wolves": "wolf", "lives": "life", "selves": "self",
    "potatoes": "potato", "tomatoes": "tomato", "heroes": "hero",
    "series": "series", "species": "species", "mattress": "mattress",
    "children": "child", "feet": "foot", "teeth": "tooth", "mice": "mouse",
    "people": "person", "men": "man", "women": "woman",
}

# 直接映射（在规则之前命中，用于规则会处理错的常见词）
_PLURAL_MAP = {
    "headphones": "headphone", "clippers": "clipper", "shears": "shear",
    "tongs": "tong", "pants": "pant", "scissors": "scissor",
}


def slugify(text):
    """Keyword -> URL slug.

    归一化英文单复数，使"人工命名的种子文章 slug"与"程序算出的 slug"一致，
    避免同一主题被写两次（重复内容）。规则保守：只做可确定的单数化，
    无法确定时保持原样。
    """
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    words = [_PLURAL_MAP.get(w, _singularize(w)) for w in words]
    return "-".join(w for w in words if w)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _topic_key(text):
    """选题去重键 = slugify + 英文单复数归一。

    为什么必须有这一层：slugify 只做词形转换，不认单复数。于是
    "best air fryer under 50" 与 "best air fryers under 50" 成了两个不同的键，
    日更把同一件事写了两遍 —— 两个页面抢同一个关键词，排名信号互相抵消。
    实测已经产生了两组：
        best-air-fryer-under-50   / best-air-fryers-under-50
        best-coffee-maker-under-100 / best-coffee-makers-under-100
    """
    s = slugify(text)
    out = []
    for w in s.split("-"):
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return "-".join(out)


def clean_title(title):
    """去掉标题里暗示“我们测评过”的括号后缀。

    为什么必须做：(Tested & Compared) / (Honest Review) 这类后缀会直接印在
    搜索结果标题上 —— 那等于对所有人宣称这个站做过实测，是合规问题
    （E-E-A-T 与 Amazon 运营协议都不允许），而且标题变长会被截断，点击率反而降。
    实测线上已有 4 个标题带这类后缀（2 篇 air fryer + 2 篇 coffee maker）。
    """
    t = re.sub(
        r"\s*[\(\[]\s*[^()\[\]]*\b(?:tested|tested\s*(?:&|and)\s*compared|honest\s+review|"
        r"reviewed|hands[- ]on|lab[- ]tested|we\s+tried)\b[^()\[\]]*\s*[\)\]]",
        "", title or "", flags=re.I)
    # 结尾的 " - Tested & Compared" 这类也一并去掉
    t = re.sub(r"\s*[-–—]\s*(?:tested|tested\s*(?:&|and)\s*compared|honest\s+review)\s*$",
               "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip()


def existing_slugs():
    """Already-written topics, as NORMALIZED keys.

    Both sides of the de-duplication comparison must go through the same
    normalisation, otherwise the day you change it every existing article
    looks "unwritten" again and gets regenerated as a duplicate.
    (That is exactly the bug that produced best-air-fryers-under-50.)
    """
    if not POSTS_DIR.exists():
        return set()
    return {_topic_key(p.stem.replace("-", " ")) for p in POSTS_DIR.glob("*.md")}


def pick_topics(config, limit, override=None):
    used = existing_slugs()
    if override:
        norm = _topic_key(override)
        topic = next((t for t in TOPICS
                      if t["kw"].lower() == override.lower() or _topic_key(t["kw"]) == norm),
                     None)
        if not topic:
            sys.exit("Topic not found in pool: %s" % override)
        return [topic]
    # 池子内部也要按归一后的键去重：池里若同时存在单复数两个变体，
    # 不能在同一天（或不同天）把同一件事写两遍。
    fresh, seen = [], set()
    for t in TOPICS:
        k = _topic_key(t["kw"])
        if k in used or k in seen:
            continue
        seen.add(k)
        fresh.append(t)
    random.shuffle(fresh)  # vary what gets written each day
    return fresh[:limit]


# ---------------------------------------------------------------------------
# 意图路由：让不同意图走不同的写作骨架
# ---------------------------------------------------------------------------
def detect_intent(topic):
    kw = topic["kw"].lower().strip()
    if " vs " in kw or " versus " in kw:
        return "vs"
    if "worth it" in kw or "worth the" in kw or kw.startswith(("is it worth", "is a ", "is an ")):
        if "worth" in kw:
            return "worthit"
    if re.match(r"^why (is|does|do|are|did)\b", kw):
        return "problem"
    if re.match(r"^how much\b", kw):
        return "cost"
    return topic["type"]  # comparison / howto / list


# ---------------------------------------------------------------------------
# 内容变体：从 Prompt 层面产生真实的结构多样性（替代 de_templatify）
# ---------------------------------------------------------------------------
VARIANT_AUDIENCE = [
    "a first-time renter with a very small space",
    "a busy parent who has no time to shop around",
    "a college student cooking in a dorm",
    "someone downsizing into a smaller home",
    "a home cook on a strict monthly budget",
    "a couple setting up their first apartment",
    "someone who hates spending money on things that break",
]

VARIANT_ANGLE = [
    "total cost over the first year of ownership",
    "durability and what fails first at this price",
    "ease of cleaning and how much time it costs weekly",
    "footprint and whether it fits a small space",
    "noise level and whether it disturbs neighbours or sleep",
    "energy use and the running cost per month",
    "how easy it is to find replacement parts and filters",
]

VARIANT_STRUCTURE = [
    "Start with the single biggest mistake buyers make, then work through the picks.",
    "Start with a short reality check about what this price range can and cannot do.",
    "Open with the one question that decides everything for this category.",
    "Open by describing the exact frustrating situation this solves.",
]

STRUCTURES = {
    "comparison": (
        "Open with a short intro (2-3 sentences) that states the reader's need.\n"
        "Add a 'What actually matters' section with 4-6 buying-criteria bullets.\n"
        "Name 5-7 SPECIFIC real products (brand + model, e.g. 'Cosori Pro LE', "
        "'Ninja AF080', 'Instant Pot Duo 6 Qt'). Give each one its own H2 section "
        "with: rough price band, what makes it a good pick, its real downsides, "
        "and who should buy it.\n"
        "Include a markdown comparison table with columns: Product | Price | "
        "Best For | Key Pros.\n"
        "Every one of those named products MUST have its own Amazon link.\n"
        "Wrap up with a short 'The bottom line' verdict section."
    ),
    "howto": (
        "Open with a short intro (2-3 sentences).\n"
        "Use 5-8 H2 sections that walk through the topic step by step.\n"
        "Include a markdown bullet list or table where it genuinely helps.\n"
        "Where a specific real product genuinely makes the job easier, name it "
        "(brand + model) and link it. A how-to usually has 1-3 of these.\n"
        "Wrap up with a short 'The bottom line' section."
    ),
    "list": (
        "Open with a short intro (2-3 sentences).\n"
        "Give each item its own H2 section with a concrete, specific explanation. "
        "Name a real product (brand + model) for each item and link it.\n"
        "Include a markdown table summarising all items if it helps scanning.\n"
        "Wrap up with a short 'The bottom line' section."
    ),
    "vs": (
        "Open with 2-3 sentences framing the decision and who each option suits.\n"
        "Add a 'Quick verdict' section that picks a winner for 2-3 common situations.\n"
        "Then a markdown head-to-head table: Category | Option A | Option B.\n"
        "Then 4-6 H2 sections, each covering ONE decision factor (cost to own, "
        "space, cleaning, speed, noise, versatility) with a clear verdict per factor.\n"
        "Name at least 2 real products per side (brand + model) and link each one "
        "— this is a buying decision article, so concrete picks are required.\n"
        "Add a short 'When to choose each one' section.\n"
        "Wrap up with 'The bottom line'."
    ),
    "worthit": (
        "Open with 2-3 sentences stating the reader's doubt directly.\n"
        "Add a 'What it actually costs' section: purchase price plus the running "
        "and consumable costs over the first year.\n"
        "Add a 'What you get for that money' section.\n"
        "Add a 'When it is NOT worth it' section — be genuinely willing to say no.\n"
        "Add a short 'The cheaper alternative' section naming 1-2 real products "
        "(brand + model) and linking them.\n"
        "Wrap up with 'The bottom line' giving a plain yes/no with the condition."
    ),
    "problem": (
        "Open with 1-2 sentences naming the problem and reassuring the reader it is "
        "usually fixable and cheap.\n"
        "Then one H2 per cause (4-6 causes), each with: how to confirm it, the fix, "
        "and roughly what the fix costs.\n"
        "Add a 'When to stop and replace it instead' section naming 1-2 real "
        "replacement products (brand + model) with links.\n"
        "Add a short 'Preventing this next time' section.\n"
        "Wrap up with 'The bottom line'."
    ),
    "cost": (
        "Open with 2-3 sentences promising the real numbers.\n"
        "Add a 'The quick answer' section right away with the headline range.\n"
        "Then a 'How the number is built' section showing the formula in plain words "
        "(watts x hours x your electricity rate, or equivalent for the category).\n"
        "Then 3-4 H2 sections covering what changes the number up or down, naming "
        "real products (brand + model) with links where a specific one is the "
        "cheapest or most efficient choice.\n"
        "Add a markdown table comparing typical scenarios.\n"
        "Wrap up with 'The bottom line' and 2-3 concrete ways to pay less."
    ),
}

# ---------------------------------------------------------------------------
# 已核实的真实商品池（止血点）
# ---------------------------------------------------------------------------
# 旧规则是「必须命名真实商品」+「别编 ASIN，留 PLACEHOLDER-ASIN 等人补」。
# 结果：一直没人补 -> 每天新增 8~24 条死链；而「必须命名真实商品」逼得模型
# 编造型号（FlexiSpot M2B 在 Amazon 上直接 "No results for your search"）。
#
# 现在的做法：给模型一份【已通过双信号复核】的真实商品清单，只准从里面选。
# 池子里没有合适的，就【不放链接】—— 绝不允许再编一个。
# 池子来自 data/verified_products.json（由 74-build_product_pool.py 从
# ASIN 工作单里 verified=yes 的行导出，250 个真实商品）。
PRODUCT_POOL_PATH = ROOT / "data" / "verified_products.json"
_POOL_CACHE = None


def load_product_pool():
    global _POOL_CACHE
    if _POOL_CACHE is None:
        try:
            _POOL_CACHE = json.loads(PRODUCT_POOL_PATH.read_text(encoding="utf-8"))
        except Exception:
            _POOL_CACHE = {"by_category": {}, "count": 0}
    return _POOL_CACHE


def all_allowed_asins():
    out = set()
    for items in load_product_pool().get("by_category", {}).values():
        for it in items:
            if it.get("asin"):
                out.add(it["asin"].strip().upper())
    return out


def pool_for(cat, limit=28):
    """该分类的商品优先，不够就用其他分类补 —— 跨类目也能用得上。"""
    pool = load_product_pool().get("by_category", {})
    out = list(pool.get(cat, []))
    if len(out) >= limit:
        return out[:limit]
    for c, items in pool.items():
        if c == cat:
            continue
        for it in items:
            if len(out) >= limit:
                break
            out.append(it)
        if len(out) >= limit:
            break
    return out


def build_link_rule(products):
    """生成"只准从池子里选"的链接规则，附上商品清单。"""
    lines = [
        "Linking rules (follow exactly — these override anything else):",
        "  - You may ONLY name and link products from the APPROVED PRODUCT LIST below.",
        "  - Use the product's EXACT name as the link text and its EXACT ASIN in the URL:",
        "        [<exact product name>](https://www.amazon.com/dp/<exact ASIN>?tag=__AMAZON_TAG__)",
        "  - Copy the ASIN character-for-character. Never alter, shorten or invent an ASIN.",
        "  - NEVER write the literal text PLACEHOLDER-ASIN in a URL. A link that does not",
        "    point at a verified real product must not exist at all.",
        "  - NEVER invent a brand, a model number, or a product that is not in the list.",
        "    (Made-up model numbers are the worst possible failure here: readers search for",
        "     them on Amazon and find nothing, which destroys trust in the whole guide.)",
        "  - If no listed product genuinely fits this topic, write the article with NO",
        "    product names and NO links — give buying criteria, specs, capacity ranges and",
        "    price bands instead. That is a valid outcome. Do NOT invent anything to fill in.",
        "  - Never write a guessed price. Ranges with 'typically'/'usually' only.",
        "  - The same product must be called the same thing everywhere in the article.",
        "",
        "APPROVED PRODUCT LIST (use the exact names and ASINs shown):",
    ]
    for p in products:
        lines.append("  - %s  |  ASIN %s" % (p.get("name", ""), p.get("asin", "")))
    return "\n".join(lines)


def content_variants(topic, year):
    """Deterministically pick one audience/angle/structure variant for this topic.

    Deterministic (seeded by keyword + year) so re-running the same topic gives
    the same article, but different topics get genuinely different treatment.
    """
    seed = "%s|%s" % (topic["kw"], year)
    rng = random.Random(seed)
    return (
        rng.choice(VARIANT_AUDIENCE),
        rng.choice(VARIANT_ANGLE),
        rng.choice(VARIANT_STRUCTURE),
    )


def build_prompt(topic, year):
    intent = detect_intent(topic)
    structure = STRUCTURES.get(intent, STRUCTURES["howto"])
    audience, angle, opener = content_variants(topic, year)
    return (
        'Write a complete blog article for the keyword: "%s"\n'
        "\n"
        "Title (use exactly): %s\n"
        "\n"
        "Write for: %s\n"
        "Weight the article toward this angle: %s\n"
        "%s\n"
        "\n"
        "Requirements:\n"
        "- 1100-1600 words, US English, written for a general audience.\n"
        "- %s\n"
        "- %s\n"
        "- Use H2 for section headings. Do NOT use H1, bold headings, or emoji "
        "inside the body.\n"
        "- Tone: practical, honest, friendly. Plain words. Short paragraphs. Vary "
        "sentence length; never open three paragraphs the same way.\n"
        "- Use concrete numbers (watts, quarts, ounces, minutes, dollars per year) "
        "and hedge them honestly ('typically around', 'budget models usually').\n"
        "- Do NOT claim you or this site tested, bought, or measured anything. "
        "Attribute claims to specs and owner reviews instead.\n"
        "- Write a 'Frequently Asked Questions' H2 section at the very END with "
        "exactly 3 questions. Format each as an H3 heading for the question "
        "followed by a short (1-3 sentence) answer paragraph. The FAQ section is "
        "REQUIRED — do not skip it.\n"
        "- Every FAQ answer must use ONLY facts, numbers, sizes, prices and product "
        "names that ALREADY appear in the article body above. Never introduce a new "
        "statistic, wattage, capacity, price or product name in the FAQ, and never "
        "put a link in the FAQ. (An earlier version of this pipeline invented "
        "figures such as '2,400W' inside FAQs — readers take those as fact.)\n"
        "- Only name products that appear in the APPROVED PRODUCT LIST below. Never "
        "invent a brand, a model number, or a price.\n"
        "- Output ONLY the article body in Markdown. No preamble, no title line, "
        "no closing remarks.\n"
    ) % (topic["kw"], topic["title"].format(year=year), audience, angle, opener,
         structure, build_link_rule(pool_for(topic.get("cat", ""))))


# ---------------------------------------------------------------------------
# 正文清理（只修格式，不做"去 AI 味"的伪装）
# ---------------------------------------------------------------------------
def normalize_body(body):
    """Light formatting cleanup. Deliberately does NOT rewrite prose.

    Previously this project ran de_templatify(), which sprinkled openers like
    'In practice,' and 'Honestly,' to mask AI authorship. That produced its own
    template fingerprint across articles and even broke grammar
    ('In most cases, Do not ...'). Real variation now comes from the prompt.
    """
    body = body.strip()
    # 去掉模型偶发泄漏的 H1 标题行
    body = re.sub(r"^#\s+.*\n+", "", body)
    # 去掉正文里的 emoji（要求里禁过，但偶尔仍会出现）
    body = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", body)
    # 合并 3 个以上连续空行
    body = re.sub(r"\n{3,}", "\n\n", body)
    # FAQ 标题漏了 '## ' 前缀：模型偶尔只写一行纯文本标题。
    # 后果是闸门认不出、构建脚本提取不到 -> 页面拿不到 FAQPage 结构化数据。
    # 实测真发生过（best-small-desk-for-small-spaces-budget.md）。
    body = re.sub(r"(?m)^Frequently Asked Questions[ \t]*$",
                  "## Frequently Asked Questions", body)
    return body.strip()


# 只挑"像统计事实"的数字：带货币/百分号/计量单位，或 1000 以上。
# 为什么不像 87-add_faq.py 那样查所有数字：那是离线工具，误杀一篇的代价只是
# "这篇保留旧 FAQ"；这里是日更闸门，误杀的代价是【当天不发文章】。
# 所以这里放行裸的小整数（"2 or 3 times"、"in 5 minutes" 是正常英文），
# 只拦真正会被读者当事实的统计量（$100、2,400W、1,500 sq ft、15%）。
_UNIT = (r"(?:%|percent|watts?|w\b|sq\.?\s?ft|quarts?|qt\b|oz\b|ounces?|cups?|"
         r"gallons?|inches?|lbs?\b|pounds?|degrees?|hours?|minutes?|months?|years?)")


def _stat_numbers(text):
    out = set()
    for m in re.finditer(r"\$\s*(\d[\d,\.]*)|(\d[\d,\.]*)\s*" + _UNIT, text, re.I):
        s = (m.group(1) or m.group(2) or "").rstrip(",.")
        if s:
            out.add(s)
            out.add(s.replace(",", ""))
    for m in re.finditer(r"\b(\d{1,3}(?:,\d{3})+|\d{4,})\b", text):
        s = m.group(1)
        out.add(s)
        out.add(s.replace(",", ""))
    return out


def quality_gate(body, topic):
    """Reject obviously broken output instead of committing it."""
    problems = []
    intent = detect_intent(topic)
    words = len(re.findall(r"\S+", body))
    if words < 500:
        problems.append("too short (%d words)" % words)
    if not re.search(r"(?im)^##\s+.*(Frequently Asked Questions|\bFAQ\b)", body):
        problems.append("missing required FAQ section")
    if not re.search(r"(?m)^##\s+\S", body):
        problems.append("no H2 sections")
    # 编造第一人称体验：E-E-A-T 与 Amazon 运营协议都禁止。
    # 关键教训：旧版本只查 "we tested"，【漏掉了 "I"】—— 结果 CI 每天新生成的
    # 文章里持续溜进 "I've tested"、"I have owned a pair for two years"，
    # 实测 20 篇 24 处，而且没人发现（清理工具压根没接进流水线）。
    # 这里与 scripts/fix_fake_experience.py 的口径对齐，两个入口用同一套判断。
    first_person = (
        r"\b(?:I|we)(?:['\u2019]ve|['\u2019]d| have| had)?\s+"
        r"(?:tested|tried|used|reviewed|measured|bought|purchased|owned|ordered|"
        r"kept|returned)\b"
        r"|\bin our tests?\b|\bour review unit\b"
        r"|\b(?:we|I) kept\b|\bI (?:have|'ve|had) (?:to )?(?:make )?a confession\b"
        r"|\bI used to think\b|\bI(?:['\u2019]ll| will) admit\b"
        r"|\b(?:in|on|at)\s+my\s+(?:kitchen|home|apartment|living room|bathroom|"
        r"garage|office|house)\b"
        r"|\bmy\s+(?:dog|cat|puppy|kitten|kid|kids|son|daughter|wife|husband)\b"
    )
    if re.search(first_person, body, re.I):
        problems.append("contains a fake first-hand testing/ownership claim")

    # 模板占位符残留：模型会把 prompt 里的示例当内容抄下来，
    # 导致页面上出现文字就是 "Product Name" 的链接（真实发生过的缺陷）。
    leftovers = re.findall(
        r"\[(?:Product Name|Brand|Product|Name|Link|URL|Your [^\]]{1,20})\]"
        r"|\{\{?[a-z_]+\}?\}|\[X\]", body)
    if leftovers:
        problems.append("template placeholder leaked as content: %s"
                        % sorted(set(leftovers))[:4])

    # --- 链接合规：止血点 ---------------------------------------------------
    # 旧规则允许 PLACEHOLDER-ASIN（"等人类补"），但一直没人补，
    # 于是每天新增 8~24 条死链，一个月就能把辛苦修好的存量全部冲掉。
    # 现在：出现占位符、或链接指向未核实 ASIN，直接拒稿。
    if "PLACEHOLDER-ASIN" in body:
        problems.append("contains a PLACEHOLDER-ASIN dead link (must never happen)")

    links = re.findall(
        r"\[([^\]]+)\]\((https://www\.amazon\.com/dp/([A-Za-z0-9\-]+)[^\)]*)\)", body)
    allowed = all_allowed_asins()
    unknown = sorted({a.upper() for _, _, a in links if a.upper() not in allowed})
    if unknown:
        problems.append("link(s) point at unverified ASIN(s): %s" % unknown[:4])

    # 链接数量：只做"该有却没有"的兜底，不再强迫凑数。
    # 旧代码按 intent 硬要 4/3 条，池子里没有合适的时等于逼模型编 ——
    # 这正是"编造商品名"的另一个源头。
    named = [n for n, _, _ in links
             if not re.fullmatch(r"(?i)product name|brand|product|link|x", n.strip())]
    cat = topic.get("cat", "")
    pool_n = len(pool_for(cat))
    if not named:
        # 只有"导购类"主题（对比/vs/清单）才强求链接。
        # 纯怎么做/排障类文章本来就未必需要商品，硬要会把好文章拒掉。
        # （而且提示词里明确允许"没有合适商品就不放链接"—— 闸门不能和提示词打架，
        #   之前就是这么自相矛盾，实测拒稿率高达 56%，日更从 3 篇掉到 1 篇。）
        buying_guide = intent in ("comparison", "vs", "list")
        if buying_guide and pool_n >= 12:
            problems.append("0 product links, but the verified pool for '%s' has "
                            "%d items - a %s article should have used some"
                            % (cat, pool_n, intent))
        else:
            print("[generate] note: no product links (pool for '%s' has %d)"
                  % (cat, pool_n))
    # FAQ 段不许自作主张引入新数字 —— 旧生成器就是这么在 FAQ 里编出
    # "2,400W"、"1,500 sq ft" 这种看似专业的统计的。正文可以有数字，
    # 但 FAQ 只能用正文已有的。
    faq_m = re.search(r"(?m)^##\s+.*(Frequently Asked Questions|\bFAQ\b)", body)
    if faq_m:
        faq, body_part = body[faq_m.start():], body[:faq_m.start()]
        if re.search(r"\]\(|https?://", faq):
            problems.append("FAQ section contains a link (not allowed)")
        extra = _stat_numbers(faq) - _stat_numbers(body_part)
        if extra:
            problems.append("FAQ introduces numbers not in the article: %s"
                            % sorted(extra)[:6])
    return words, problems


def write_article(topic, config, year):
    llm_cfg = config.get("llm", {})
    base_prompt = build_prompt(topic, year)

    # 拒稿就带着【具体理由】重试一次。
    # 为什么必须重试：闸门拒稿 = 这次 API 调用白花 + 当天少一篇文章。
    # 实测（9 篇连跑）首次拒稿率 56% —— 日更会从 3 篇掉到 1 篇。
    # 而绝大多数拒稿都是"差一点点"（漏了 FAQ、FAQ 里多写了个数字、
    # 忘了用商品池里的东西），把理由明确回灌给模型，它基本一次就能改对。
    # 注意：仍然保留闸门 —— 重试两次都不合格就放弃，绝不降标准放行。
    problems = None
    for attempt in (1, 2):
        prompt = base_prompt
        if problems:
            prompt += (
                "\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED. Fix ALL of these problems:\n"
                + "\n".join("  - %s" % p for p in problems)
                + "\n\nThe article must still follow every rule above. "
                  "Do not drop the FAQ section, do not invent numbers in the FAQ, "
                  "and if this is a buying guide, use products from the "
                  "APPROVED PRODUCT LIST with their exact ASINs.\n"
            )
        body = normalize_body(complete(
            prompt,
            provider=llm_cfg.get("provider", "deepseek"),
            model=llm_cfg.get("model"),
            temperature=llm_cfg.get("temperature", 0.8) if attempt == 1 else 0.6,
            max_tokens=llm_cfg.get("max_tokens", 4096),
            system=WRITER_SYSTEM,
        ))
        words, problems = quality_gate(body, topic)
        if not problems:
            break
        if attempt == 1:
            print("[generate]   retry: %s" % "; ".join(problems)[:120])
    if problems:
        raise LLMError("quality gate rejected article after retry: %s"
                       % "; ".join(problems))

    slug = slugify(topic["kw"])

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plain = re.sub(r"[#*`>\[\]()!|]", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    description = plain[:150].rstrip(" ,.-") + "..."
    front = {
        # 清掉标题里的“测评过”暗示后缀（见 clean_title 的说明）
        "title": clean_title(topic["title"].format(year=year)),
        "slug": slug,
        "date": date,
        "category": topic["cat"],
        "type": topic["type"],
        "intent": detect_intent(topic),
        "keywords": [topic["kw"]],
        "description": description,
    }
    head = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n"
    path = POSTS_DIR / (slug + ".md")
    path.write_text(head + body.strip() + "\n", encoding="utf-8")
    return path, words


def main():
    parser = argparse.ArgumentParser(description="Generate new ThriftyNest articles")
    parser.add_argument("--limit", type=int, default=None,
                        help="how many articles to generate (default: posts_per_day from config)")
    parser.add_argument("--topic", default=None, help="force one specific topic by keyword")
    parser.add_argument("--dry-run", action="store_true",
                        help="only list which topics would be generated")
    args = parser.parse_args()

    config = load_config()
    pub = config.get("publishing", {})
    limit = args.limit if args.limit is not None else pub.get("posts_per_day", 3)

    topics = pick_topics(config, limit, override=args.topic)

    # --dry-run 不做任何网络调用，因此不需要 API key
    if args.dry_run:
        print("[generate] DRY RUN - no API key needed, nothing will be written.")
        if not topics:
            print("[generate] no fresh topics left in the pool.")
            return
        for i, t in enumerate(topics, 1):
            print("  %2d. [%-11s/%-10s] %s" % (i, t["cat"], detect_intent(t), t["kw"]))
        print("[generate] %d topic(s) would be generated." % len(topics))
        return

    has_key = any(os.environ.get(k) for k in
                  ("DEEPSEEK_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"))
    if not has_key:
        print("[generate] WARNING: no API key found - skipping generation. "
              "Add the DEEPSEEK_API_KEY secret (see SETUP.md).")
        return

    if not topics:
        print("[generate] No fresh topics left in the pool. "
              "Add more to scripts/keywords.py.")
        return

    year = datetime.now(timezone.utc).year
    ok = 0
    for i, topic in enumerate(topics, 1):
        try:
            path, words = write_article(topic, config, year)
        except LLMError as exc:
            print("[generate] FAILED (%s): %s" % (topic["kw"], exc))
            continue
        print("[generate] %d/%d wrote %s (%d words, intent=%s)"
              % (i, len(topics), path.name, words, detect_intent(topic)))
        ok += 1
        if i < len(topics):
            time.sleep(2)  # polite rate limiting between calls
    print("[generate] done: %d article(s) written." % ok)


if __name__ == "__main__":
    main()
