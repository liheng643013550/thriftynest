#!/usr/bin/env python3
"""Repair articles that contain leaked template placeholders.

Why this exists
---------------
The generation prompt showed the link format as a literal example:

    [Product Name](https://www.amazon.com/dp/PLACEHOLDER-ASIN?tag=__AMAZON_TAG__)

Some runs copied `[Product Name]` verbatim instead of substituting a real
product name. Three published articles ended up looking like this:

    [Product Name](https://www.amazon.com/dp/PLACEHOLDER-ASIN?tag=__AMAZON_TAG__)
    is my go-to recommendation for the main drawer in your kitchen.

So the page renders a product link whose visible text is literally
"Product Name" - useless to the reader, unmappable to a real ASIN, and thin
content for search engines.

The fix has two halves:
  1. the prompt no longer contains a copyable placeholder (done in
     generate_articles.py v2), and
  2. this tool finds already-broken articles, quarantines them, and lets the
     generator rewrite them.

Because the topic is still in keywords.py, rewriting produces the SAME slug,
so the URL is unchanged - no 404, no redirect needed.

Usage:
    python scripts/repair_articles.py                 # report only
    python scripts/repair_articles.py --apply         # quarantine broken files
    python scripts/repair_articles.py --apply --regenerate
        # ...and immediately rewrite them with the LLM (needs an API key)
"""
import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "content" / "posts"
SITE_POSTS = ROOT / "site" / "posts"

# Placeholder shapes that must never appear as article content.
RESIDUE = [
    (r"\[Product Name\]", "literal '[Product Name]' link text"),
    (r"\[Brand[^\]]*\]", "literal '[Brand...]' link text"),
    (r"\[(?:Product|Name|Link|URL)\]", "literal placeholder link text"),
    (r"\[Your [^\]]{1,20}\]", "literal '[Your ...]' link text"),
    (r"\[X\]", "literal '[X]' placeholder"),
    (r"\{\{?[a-z_]+\}?\}", "unresolved {template} variable"),
    (r"(?i)\blorem ipsum\b", "lorem ipsum filler"),
]


def scan():
    hits = {}
    for p in sorted(POSTS_DIR.glob("*.md")):
        if p.name.endswith(".orig"):
            continue
        raw = p.read_text(encoding="utf-8")
        found = []
        for pat, label in RESIDUE:
            n = len(re.findall(pat, raw))
            if n:
                found.append((label, n))
        if found:
            hits[p] = found
    return hits


def main():
    ap = argparse.ArgumentParser(description="Repair articles with leaked placeholders")
    ap.add_argument("--apply", action="store_true", help="quarantine broken files")
    ap.add_argument("--regenerate", action="store_true",
                    help="with --apply, immediately rewrite them via the LLM")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap how many to regenerate")
    ap.add_argument("--restore-slug", action="store_true",
                    help="after regenerating, rename the new file back to the "
                         "ORIGINAL slug so the published URL does not 404")
    args = ap.parse_args()

    print("=" * 74)
    print("文章占位符残留修复")
    print("  posts dir : %s" % POSTS_DIR)
    print("  mode      : %s" % ("APPLY" if args.apply else "DRY RUN"))
    print("=" * 74)

    hits = scan()
    if not hits:
        print("\n没有发现占位符残留。✅")
        return

    print("\n发现 %d 篇文章含占位符残留：\n" % len(hits))
    for p, found in hits.items():
        total = sum(n for _, n in found)
        print("  %-46s %2d 处" % (p.name, total))
        for label, n in found:
            print("        %-38s x%d" % (label, n))

    # 选题是否还在池里（决定能否原地重新生成；URL 不变）
    # 注意：两侧都必须过 slugify 归一化 —— 文件名是复数（...organizers），
    # 而 slugify 会归一成单数（...organizer），只比一侧必然不匹配。
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from keywords import TOPICS  # noqa
        from generate_articles import slugify  # noqa
        pool = {slugify(t["kw"]) for t in TOPICS}
    except Exception as exc:  # pragma: no cover
        pool = set()
        slugify = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
        print("\n  [warn] 无法加载选题池（%s），跳过可再生成性检查" % exc)

    def norm_stem(stem):
        return slugify(stem.replace("-", " "))

    regen_ok, regen_no = [], []
    for p in hits:
        (regen_ok if norm_stem(p.stem) in pool else regen_no).append(p.stem)

    print("\n可原地重新生成（选题在池中，slug 与 URL 都不变）：%d 篇" % len(regen_ok))
    for s in regen_ok:
        print("     %s" % s)
    if regen_no:
        print("\n⚠️ 选题不在池中，删除后会 404，需手工重写：%d 篇" % len(regen_no))
        for s in regen_no:
            print("     %s" % s)

    if not args.apply:
        print("\n" + "=" * 74)
        print("这是 DRY RUN，没有改动任何文件。确认后加 --apply。")
        print("=" * 74)
        return

    # ------------------------------------------------------------- apply
    bak = POSTS_DIR / ".bak_repair"
    bak.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    removed = []
    for p in hits:
        shutil.copy2(p, bak / (stamp + "_" + p.name))
        p.unlink()
        removed.append(p.stem)
        # 同时清掉它的构建产物，避免旧页面残留
        d = SITE_POSTS / p.stem
        if d.is_dir():
            shutil.rmtree(d)
        print("  已隔离 %s（备份在 .bak_repair/）" % p.name)

    print("\n已隔离 %d 篇。备份目录：%s" % (len(removed), bak))

    if args.regenerate:
        if not regen_ok:
            print("没有可原地重新生成的选题，跳过。")
            return
        todo = regen_ok[:args.limit] if args.limit else regen_ok
        print("\n开始重新生成 %d 篇（需要 DEEPSEEK_API_KEY）..." % len(todo))
        sys.path.insert(0, str(ROOT / "scripts"))
        from keywords import TOPICS  # noqa
        from generate_articles import slugify  # noqa
        by_slug = {slugify(t["kw"]): t["kw"] for t in TOPICS}
        for s in todo:
            kw = by_slug.get(norm_stem(s))
            if not kw:
                print("\n--- 跳过 %s（选题池里找不到对应关键词）" % s)
                continue
            print("\n--- regenerate: %s" % kw)
            # 优先走仓库根的 run.py（Windows 免安装 Python 下必须，
            # 否则 scripts/ 不在 sys.path，import llm/keywords 会失败）
            runner = ROOT / "run.py"
            if runner.is_file():
                cmd = [sys.executable, str(runner), "generate_articles.py",
                       "--topic", kw]
            else:
                cmd = [sys.executable,
                       str(ROOT / "scripts" / "generate_articles.py"),
                       "--topic", kw]
            r = subprocess.run(cmd, cwd=str(ROOT))
            print("    exit=%d" % r.returncode)

        # 保持原 URL：把新文件改名回原来的 slug。
        # 为什么需要：slugify 会把复数归一成单数（...organizers -> ...organizer），
        # 所以重新生成会换 URL，旧地址 404。而旧地址已经进了 sitemap。
        # 去重两侧都过 slugify，所以保留复数 slug 不会造成重复生成。
        if args.restore_slug:
            print("\n恢复原 slug（避免 404）...")
            for old_stem in removed:
                if (POSTS_DIR / (old_stem + ".md")).is_file():
                    continue  # 已经是原名
                want = norm_stem(old_stem)
                cand = [p for p in POSTS_DIR.glob("*.md")
                        if not p.name.endswith(".orig")
                        and norm_stem(p.stem) == want]
                if len(cand) != 1:
                    print("     %-44s 跳过（匹配到 %d 个文件）" % (old_stem, len(cand)))
                    continue
                src = cand[0]
                text = src.read_text(encoding="utf-8")
                # 同时改写 frontmatter 的 slug（slug_of() 优先取 frontmatter）
                new_text, n = re.subn(r"(?m)^slug:\s*.+$", "slug: %s" % old_stem,
                                      text, count=1)
                if n == 0:
                    print("     %-44s 跳过（frontmatter 无 slug 字段）" % old_stem)
                    continue
                src.write_text(new_text, encoding="utf-8")
                dst = POSTS_DIR / (old_stem + ".md")
                src.rename(dst)
                print("     %-44s <- %s（URL 保持不变）" % (old_stem, src.name))
    else:
        print("\n下一步（任选）：")
        print("  A. 用修好的 prompt 重新生成：")
        print("     python run.py generate_articles.py --topic \"<关键词>\"")
        print("  B. 或重新跑一次本工具并加 --regenerate")
        print("  C. 或直接把这些文件从仓库删掉（URL 会 404，不推荐）")
    print("\n之后务必跑： python run.py build_site.py && python run.py verify_site.py")


if __name__ == "__main__":
    main()
