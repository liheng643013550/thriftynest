"""Push all post URLs to Bing (and IndexNow partners) for instant indexing.

Best-effort: failures are logged but never fail the pipeline — IndexNow is an
accelerator, not a requirement (Bing also crawls the sitemap on its own).

Configured via config.yaml -> site.indexnow_key (see BING.md).
"""
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"

INDEXNOW_URL = "https://api.indexnow.org/indexnow"


def main():
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

    urls = [base + "/"]
    for path in sorted(POSTS_DIR.glob("*.md")):
        urls.append(base + "/posts/" + path.stem + "/")

    payload = {
        "host": host,
        "key": key,
        "keyLocation": base + "/" + key + ".txt",
        "urlList": urls,
    }
    request = urllib.request.Request(
        INDEXNOW_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            print("[indexnow] submitted %d URLs (HTTP %s)" % (len(urls), resp.status))
    except urllib.error.HTTPError as exc:
        print("[indexnow] WARNING: HTTP %s: %s (ignored)" % (
            exc.code, exc.read().decode("utf-8", "replace")[:300]))
    except Exception as exc:  # best effort - never fail the pipeline
        print("[indexnow] WARNING: %s (ignored)" % exc)


if __name__ == "__main__":
    main()
