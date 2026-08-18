"""Generate new articles from the topic pool using the configured LLM.

Usage:
    python scripts/generate_articles.py [--limit N] [--topic "keyword"] [--dry-run]

Reads config.yaml, picks unused topics, writes content/posts/<slug>.md with
YAML frontmatter + markdown body. A topic is "used" when its slug file
exists, so runs never produce duplicates.

If no API key is present it prints a warning and exits 0, so the site still
builds before you configure the key (see SETUP.md).
"""
import argparse
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

WRITER_SYSTEM = (
    "You are an expert SEO copywriter for ThriftyNest, a budget home & kitchen "
    "blog. Write practical, honest, easy-to-read US English. No fluff, no hype, "
    "no marketing speak. Be specific and helpful, like a knowledgeable friend "
    "who loves saving money."
)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def existing_slugs():
    if not POSTS_DIR.exists():
        return set()
    return {p.stem for p in POSTS_DIR.glob("*.md")}


def pick_topics(config, limit, override=None):
    used = existing_slugs()
    if override:
        topic = next((t for t in TOPICS if t["kw"].lower() == override.lower()), None)
        if not topic:
            sys.exit("Topic not found in pool: %s" % override)
        return [topic]
    fresh = [t for t in TOPICS if slugify(t["kw"]) not in used]
    random.shuffle(fresh)  # vary what gets written each day
    return fresh[:limit]


def build_prompt(topic, year):
    if topic["type"] == "comparison":
        structure = (
            "Open with a short intro (2-3 sentences) that states the reader's need.\n"
            "Add a 'What to look for' section with 4-6 buying-criteria bullets.\n"
            "Then a markdown comparison table with columns: Product | Price | Best For | Key Pros.\n"
            "Then one H2 section per product (5-7 products): name, price, what makes it "
            "great, downsides, who should buy it.\n"
            "Wrap up with a short 'The bottom line' verdict section.\n"
            "For every product, link it in this exact format: "
            "[Product Name](https://www.amazon.com/dp/PLACEHOLDER-ASIN?tag=__AMAZON_TAG__)"
        )
    else:
        structure = (
            "Open with a short intro (2-3 sentences).\n"
            "Use 5-8 H2 sections that walk through the topic step by step.\n"
            "Include a markdown bullet list or table where useful.\n"
            "Wrap up with a short 'The bottom line' section.\n"
            "Where a product genuinely helps, link it in this exact format: "
            "[Product Name](https://www.amazon.com/dp/PLACEHOLDER-ASIN?tag=__AMAZON_TAG__)"
        )
    return (
        'Write a complete blog article for the keyword: "%s"\n'
        "\n"
        "Title (use exactly): %s\n"
        "\n"
        "Requirements:\n"
        "- 1000-1500 words, US English, written for a general audience.\n"
        "- %s\n"
        "- Use H2 for section headings. Do NOT use H1, bold headings, or emoji inside the body.\n"
        "- Tone: practical, honest, friendly. Plain words. Short paragraphs.\n"
        "- Mention real product names and well-known brands only; never invent brands.\n"
        "- Output ONLY the article body in Markdown. No preamble, no title line, "
        "no closing remarks.\n"
    ) % (topic["kw"], topic["title"].format(year=year), structure)


def write_article(topic, config, year):
    prompt = build_prompt(topic, year)
    llm_cfg = config.get("llm", {})
    body = complete(
        prompt,
        provider=llm_cfg.get("provider", "deepseek"),
        model=llm_cfg.get("model"),
        temperature=llm_cfg.get("temperature", 0.8),
        max_tokens=llm_cfg.get("max_tokens", 4096),
        system=WRITER_SYSTEM,
    )
    slug = slugify(topic["kw"])
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plain = re.sub(r"[#*`>\[\]()!|]", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    description = plain[:150].rstrip(" ,.-") + "..."
    front = {
        "title": topic["title"].format(year=year),
        "slug": slug,
        "date": date,
        "category": topic["cat"],
        "type": topic["type"],
        "keywords": [topic["kw"]],
        "description": description,
    }
    head = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n"
    path = POSTS_DIR / (slug + ".md")
    path.write_text(head + body.strip() + "\n", encoding="utf-8")
    return path


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

    has_key = any(os.environ.get(k) for k in
                  ("DEEPSEEK_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"))
    if not has_key:
        print("[generate] WARNING: no API key found - skipping generation. "
              "Add the DEEPSEEK_API_KEY secret (see SETUP.md).")
        return

    topics = pick_topics(config, limit, override=args.topic)
    if not topics:
        print("[generate] No fresh topics left in the pool. "
              "Add more to scripts/keywords.py.")
        return
    if args.dry_run:
        for t in topics:
            print("-", t["kw"])
        return

    year = datetime.now(timezone.utc).year
    ok = 0
    for i, topic in enumerate(topics, 1):
        try:
            path = write_article(topic, config, year)
        except LLMError as exc:
            print("[generate] FAILED (%s): %s" % (topic["kw"], exc))
            continue
        print("[generate] %d/%d wrote %s" % (i, len(topics), path.name))
        ok += 1
        if i < len(topics):
            time.sleep(2)  # polite rate limiting between calls
    print("[generate] done: %d article(s) written." % ok)


if __name__ == "__main__":
    main()
