#!/usr/bin/env python3
"""Detect and merge duplicate articles, transplanting real ASINs.

Why this exists
---------------
This repo has two kinds of article files with different naming conventions:

  * 6 hand-written "seed" articles using PLURAL slugs
    (best-air-fryers-under-50.md) that carry REAL Amazon ASINs.
  * 126 generated articles using slugs computed by slugify(), which used to
    keep plurals, so the de-duplication key never matched the seed files.
    Result: the projector wrote a second article with the same title
    (best-air-fryer-under-50.md) and both shipped.

This script:
  1. groups content/posts/*.md by NORMALISED slug to find duplicates,
  2. picks a keeper (longer article wins; ties broken by real-ASIN count),
  3. transplants real Amazon links from the losers into the keeper by
     matching product names,
  4. removes the loser .md and its site/posts/<slug>/ output,
  5. reports whatever could not be matched so you can finish it by hand.

Usage:
    python scripts/merge_duplicate.py                 # dry run, report only
    python scripts/merge_duplicate.py --apply         # actually change files
    python scripts/merge_duplicate.py --apply --prune-orphans
        # also delete site/posts/<slug>/ dirs whose .md no longer exists

Always run the dry run first. With --apply a timestamped backup of every
touched file is written to content/posts/.bak_merge/.
"""
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "content" / "posts"
SITE_POSTS = ROOT / "site" / "posts"

LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*(https://www\.amazon\.com/dp/[^\)\s]+)\s*\)")
REAL_ASIN_RE = re.compile(r"/dp/(?!PLACEHOLDER)([A-Z0-9]{8,})")

STOPWORDS = {"the", "a", "an", "and", "for", "with", "of", "to", "in", "on",
             "it", "is", "pro", "series", "qt", "quart", "qt.", "inch", "in."}


# ---------------------------------------------------------------- slugify
def _singularize(w):
    irr = {"knives": "knife", "shelves": "shelf", "leaves": "leaf",
           "potatoes": "potato", "tomatoes": "tomato", "series": "series",
           "species": "species", "children": "child", "feet": "foot",
           "teeth": "tooth", "mice": "mouse", "people": "person"}
    if w in irr:
        return irr[w]
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "zes", "ches", "shes", "sses")):
        return w[:-2]
    if w.endswith(("ss", "us", "is", "as", "os")):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


def slugify_key(name):
    """Normalised key for duplicate detection (must match generate_articles)."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return "-".join(_singularize(w) for w in words)


# ------------------------------------------------------------------ helpers
def read(path):
    return path.read_text(encoding="utf-8")


def split_front(text):
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return parts[1], parts[2]


def get_field(front, key):
    m = re.search(r"(?m)^%s\s*:\s*(.+)$" % key, front)
    return m.group(1).strip().strip("'\"") if m else ""


def word_count(body):
    plain = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", body)
    plain = re.sub(r"[#*`>|\[\]()!]", " ", plain)
    return len(plain.split())


def amazon_links(text):
    """[(link_text, url)] for every Amazon markdown link."""
    return LINK_RE.findall(text)


def real_links(text):
    return [(t, u) for t, u in amazon_links(text) if REAL_ASIN_RE.search(u)]


def placeholder_count(text):
    return text.count("PLACEHOLDER-ASIN")


def tokens(name):
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
            if t not in STOPWORDS and len(t) > 1}


def names_match(a, b):
    """Fuzzy product-name match: containment, or >=2 shared significant tokens."""
    if not a or not b:
        return False
    na, nb = a.lower().strip(), b.lower().strip()
    if na in nb or nb in na:
        return True
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    shared = ta & tb
    # at least 2 shared tokens, or a brand-ish first token plus one more
    return len(shared) >= 2


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Merge duplicate ThriftyNest articles")
    ap.add_argument("--apply", action="store_true", help="make the changes")
    ap.add_argument("--prune-orphans", action="store_true",
                    help="also delete site/posts/<slug>/ with no matching .md")
    args = ap.parse_args()

    if not POSTS_DIR.is_dir():
        sys.exit("content/posts not found at %s" % POSTS_DIR)

    files = sorted(POSTS_DIR.glob("*.md"))
    groups = {}
    for p in files:
        groups.setdefault(slugify_key(p.stem.replace("-", " ")), []).append(p)

    dupes = {k: v for k, v in groups.items() if len(v) > 1}

    print("=" * 72)
    print("重复文章检测 / 合并")
    print("  文章文件数 : %d" % len(files))
    print("  唯一 slug  : %d" % len(groups))
    print("  重复组数   : %d" % len(dupes))
    print("  mode       : %s" % ("APPLY" if args.apply else "DRY RUN"))
    print("=" * 72)

    if not dupes:
        print("\n没有发现重复文章。✅")
    total_fixed = 0
    deletions = []

    for key, paths in sorted(dupes.items()):
        info = []
        for p in paths:
            text = read(p)
            front, body = split_front(text)
            info.append({
                "path": p,
                "title": get_field(front, "title"),
                "words": word_count(body),
                "real": len(real_links(text)),
                "ph": placeholder_count(text),
                "text": text,
                "front": front,
                "body": body,
            })
        # keeper: most words; tie -> more real ASINs; tie -> fewer placeholders
        info.sort(key=lambda d: (-d["words"], -d["real"], d["ph"]))
        keeper = info[0]
        losers = info[1:]

        print("\n" + "-" * 72)
        print("重复 slug 键: %s" % key)
        for d in info:
            mark = "KEEP  " if d is keeper else "MERGE "
            print("  %s%-46s %5dw  真实ASIN:%2d  死链:%3d"
                  % (mark, d["path"].name, d["words"], d["real"], d["ph"]))
        print("  标题: %s" % keeper["title"])

        # transplant real links from losers into keeper
        keeper_text = keeper["text"]
        transplants, unmatched = [], []
        for loser in losers:
            for name, url in real_links(loser["text"]):
                # find the keeper's own link with a matching product name
                target = None
                for kname, kurl in amazon_links(keeper_text):
                    if names_match(name, kname):
                        target = (kname, kurl)
                        break
                if not target:
                    unmatched.append((name, url, loser["path"].name))
                    continue
                old_md = "[%s](%s)" % (target[0], target[1])
                new_md = "[%s](%s)" % (target[0], url)
                if old_md in keeper_text and kurl_is_placeholder(target[1]):
                    keeper_text = keeper_text.replace(old_md, new_md, 1)
                    transplants.append((target[0], url, loser["path"].name))
                elif not kurl_is_placeholder(target[1]):
                    continue  # keeper already has a real link here
                else:
                    unmatched.append((name, url, loser["path"].name))

        print("  可迁移真实链接: %d" % len(transplants))
        for name, url, src in transplants:
            print("      + %-34s -> %s" % (name[:34], REAL_ASIN_RE.search(url).group(1)))
        if unmatched:
            print("  需人工处理（找不到对应产品，保留在下面这份待办里）: %d" % len(unmatched))
            for name, url, src in unmatched:
                print("      ? %-34s (%s) from %s" % (name[:34],
                      REAL_ASIN_RE.search(url).group(1), src))

        if args.apply:
            bak = POSTS_DIR / ".bak_merge"
            bak.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # write updated keeper
            shutil.copy2(keeper["path"], bak / (stamp + "_" + keeper["path"].name))
            keeper["path"].write_text(keeper_text, encoding="utf-8")
            print("  已更新 %s（迁移 %d 条，剩余死链 %d）"
                  % (keeper["path"].name, len(transplants),
                     placeholder_count(keeper_text)))
            total_fixed += len(transplants)
            # remove losers
            for loser in losers:
                shutil.copy2(loser["path"], bak / (stamp + "_" + loser["path"].name))
                loser["path"].unlink()
                deletions.append(loser["path"].stem)
                print("  已删除 %s" % loser["path"].name)

    # ----------------------------------------------------- prune site output
    print("\n" + "=" * 72)
    if deletions:
        print("需要清理的 site/posts 目录（%d 个）:" % len(deletions))
        for stem in deletions:
            d = SITE_POSTS / stem
            exists = d.is_dir()
            print("   %-46s %s" % (stem, "存在" if exists else "（无）"))
            if args.apply and exists:
                shutil.rmtree(d)
                print("      -> 已删除")

    if args.prune_orphans:
        stems = {p.stem for p in POSTS_DIR.glob("*.md")}
        orphans = [d for d in SITE_POSTS.glob("*")
                   if d.is_dir() and d.name not in stems]
        print("\n额外发现 %d 个孤儿 site/posts 目录（.md 已不存在）:" % len(orphans))
        for d in orphans[:30]:
            print("   %s" % d.name)
            if args.apply:
                shutil.rmtree(d)

    print("\n" + "=" * 72)
    if args.apply:
        print("完成。备份在 %s" % (POSTS_DIR / ".bak_merge"))
        print("迁移真实链接 %d 条，删除重复文章 %d 篇。" % (total_fixed, len(deletions)))
        print("下一步：  python scripts/build_site.py && python scripts/verify_site.py")
    else:
        print("这是 DRY RUN，没有改动任何文件。确认无误后加 --apply 执行。")
    print("=" * 72)


def kurl_is_placeholder(url):
    return "PLACEHOLDER-ASIN" in url


if __name__ == "__main__":
    main()
