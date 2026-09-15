"""Generate a Pinterest pin image (1000x1500) for every article.

Output: site/static/pins/<slug>.png
Run BEFORE build_site.py so each article page can reference its pin image
via the og:image meta tag (Pinterest picks that image up when you save a pin).

Fonts: uses DejaVu Sans (preinstalled on GitHub Actions Ubuntu runners);
falls back to a tiny built-in bitmap font on systems without it.
"""
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"
OUT_DIR = ROOT / "site" / "static" / "pins"

W, H = 1000, 1500

CATEGORY_COLORS = {
    "kitchen": (226, 108, 54),
    "organization": (0, 138, 132),
    "cleaning": (52, 130, 198),
    "home-office": (88, 92, 176),
    "pet": (188, 120, 160),
    "garden": (74, 140, 86),
    "energy": (224, 166, 52),
    "tools": (122, 112, 100),
}
DEFAULT_COLOR = (90, 100, 110)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


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
        posts.append({
            "slug": meta.get("slug") or path.stem,
            "title": meta.get("title") or path.stem,
            "category": meta.get("category") or "misc",
        })
    return posts


def gradient(base):
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    top = base
    bottom = tuple(int(c * 0.5) for c in base)
    for y in range(H):
        t = y / (H - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=color)
    return img


def wrap_title(text, width=20):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:6]


def make_pin(post, site_name, site_url):
    color = CATEGORY_COLORS.get(post["category"], DEFAULT_COLOR)
    img = gradient(color)
    draw = ImageDraw.Draw(img)
    font_brand = load_font(44)
    font_cat = load_font(40)
    font_title = load_font(74)
    font_url = load_font(38)

    def center_x(text, font):
        return (W - draw.textlength(text, font=font)) / 2

    draw.text((center_x(site_name, font_brand), 70), site_name,
              font=font_brand, fill=(255, 255, 255))
    cat_text = post["category"].replace("-", " ").upper()
    draw.text((center_x(cat_text, font_cat), 175), cat_text,
              font=font_cat, fill=(255, 255, 255))

    lines = wrap_title(post["title"])
    y = 330
    line_h = 100
    for line in lines:
        x = center_x(line, font_title)
        draw.text((x + 4, y + 4), line, font=font_title, fill=(0, 0, 0))
        draw.text((x, y), line, font=font_title, fill=(255, 255, 255))
        y += line_h

    draw.text((center_x(site_url, font_url), H - 120), site_url,
              font=font_url, fill=(230, 230, 230))
    return img


def main():
    config = load_config()
    site = config.get("site", {})
    site_name = site.get("name", "ThriftyNest")
    site_url = site.get("url", "").rstrip("/")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts = load_posts()
    if not posts:
        print("[pins] no posts found, nothing to do.")
        return
    count = 0
    for post in posts:
        img = make_pin(post, site_name, site_url)
        img.save(OUT_DIR / (post["slug"] + ".png"), "PNG")
        count += 1
    print("[pins] generated %d pin images -> %s" % (count, OUT_DIR))


if __name__ == "__main__":
    main()
