"""Push post URLs to Bing (and IndexNow partners) for instant indexing.

Best-effort: failures are logged but never fail the pipeline — IndexNow is an
accelerator, not a requirement (Bing also crawls the sitemap on its own).

Configured via config.yaml -> site.indexnow_key (see BING.md).

----------------------------------------------------------------------------
v2 改动
----------------------------------------------------------------------------
原版每次运行都把**全站所有 URL**（132+ 条）推给 IndexNow。IndexNow 协议虽然
允许，但反复提交未变更的 URL 是低质量信号，长期看会被降权。

现在默认只推送**最近 N 天新增/更新的文章**（N 由 --days 控制，默认 3）。
两种例外需要全量推送，用 --all：
  * 换了域名（site.url 变了）—— 需要让引擎重新抓取全部页面
  * 改了模板/SEO 结构 —— 所有页面内容都变了
"""
import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"

INDEXNOW_URL = "https://api.indexnow.org/indexnow"


def post_date(path):
    """Read the `date:` field from a post's YAML frontmatter (empty if absent)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not raw.startswith("---"):
        return ""
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return ""
    return str(meta.get("date") or "")


def main():
    ap = argparse.ArgumentParser(description="Submit URLs to IndexNow")
    ap.add_argument("--all", action="store_true",
                    help="submit every URL (use after a domain change or a "
                         "template/SEO overhaul)")
    ap.add_argument("--days", type=int, default=3,
                    help="how many days back to include (default 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be submitted without calling the API")
    args = ap.parse_args()

    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    site = config.get("site", {})
    base = site.get("url", "").rstrip("/")
    key = (site.get("indexnow_key") or "").strip()
    if not key:
        print("[indexnow] no indexnow_key in config - skipping (see BING.md).")
        return

    host = urlsplit(base).netloc
    if not host:
        print("[indexnow] cannot determine host from site.url - skipping.")
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    urls = []
    skipped = 0
    for path in sorted(POSTS_DIR.glob("*.md")):
        if not args.all:
            d = post_date(path)
            if d and d < cutoff:
                skipped += 1
                continue
        urls.append(base + "/posts/" + path.stem + "/")

    # 首页与分类页只在全量模式下提交（它们的内容由文章驱动，无需每次推）
    if args.all:
        urls.insert(0, base + "/")

    if not urls:
        print("[indexnow] nothing to submit (no posts newer than %s). "
              "Use --all to resubmit everything." % cutoff)
        return

    payload = {
        "host": host,
        "key": key,
        "keyLocation": base + "/" + key + ".txt",
        "urlList": urls,
    }

    mode = "ALL" if args.all else "incremental (>= %s)" % cutoff
    if args.dry_run:
        print("[indexnow] DRY RUN (%s): would submit %d URL(s)" % (mode, len(urls)))
        for u in urls[:20]:
            print("   " + u)
        if len(urls) > 20:
            print("   ... and %d more" % (len(urls) - 20))
        return

    request = urllib.request.Request(
        INDEXNOW_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            print("[indexnow] submitted %d URL(s) [%s] (HTTP %s)"
                  % (len(urls), mode, resp.status))
    except urllib.error.HTTPError as exc:
        print("[indexnow] WARNING: HTTP %s: %s (ignored)" % (
            exc.code, exc.read().decode("utf-8", "replace")[:300]))
    except Exception as exc:  # best effort - never fail the pipeline
        print("[indexnow] WARNING: %s (ignored)" % exc)


if __name__ == "__main__":
    main()
