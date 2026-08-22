"""Generate 3 landscape illustrations (1200x675) for every article.

Output: site/static/img/<slug>-1.png .. -3.png
Run BEFORE build_site.py; build_site inserts them into the article body
(after the 1st/2nd paragraph, mid-article, and near the end).

Pure Pillow, zero dependencies. Category-colored like the site theme.
"""
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
POSTS_DIR = ROOT / "content" / "posts"
OUT_DIR = ROOT / "site" / "static" / "img"

W, H = 1200, 675

VARIANT_BADGES = {
    1: "TOP PICKS",
    2: "BUDGET TIPS",
    3: "COMPARE & SAVE",
}

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


def gradient(base, variant):
    """Vertical gradient; each variant shifts the tone so the 3 images differ."""
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    shift = {1: 1.0, 2: 0.82, 3: 0.66}[variant]
    top = tuple(int(c * shift) for c in base)
    bottom = tuple(int(c * 0.45 * shift) for c in base)
    for y in range(H):
        t = y / (H - 1)
        color = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=color)
    return img


def wrap_title(text, width=24):
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
    return lines[:3]


def make_illustration(post, variant, site_name, site_url):
    color = CATEGORY_COLORS.get(post["category"], DEFAULT_COLOR)
    img = gradient(color, variant)
    draw = ImageDraw.Draw(img)
    font_brand = load_font(34)
    font_badge = load_font(42)
    font_title = load_font(60)
    font_url = load_font(28)

    def center_x(text, font):
        return (W - draw.textlength(text, font=font)) / 2

    # brand top-left, url bottom-right
    draw.text((48, 36), site_name, font=font_brand, fill=(255, 255, 255))
    draw.text((W - 48 - draw.textlength(site_url, font=font_url), H - 52),
              site_url, font=font_url, fill=(235, 235, 235))

    # badge
    badge = VARIANT_BADGES[variant]
    bw = draw.textlength(badge, font=font_badge)
    bx = center_x(badge, font_badge)
    draw.rounded_rectangle(
        [bx - 22, 118, bx + bw + 22, 118 + 62], radius=31,
        fill=(255, 255, 255), outline=(255, 255, 255)
    )
    draw.text((bx, 118), badge, font=font_badge, fill=color)

    # title
    lines = wrap_title(post["title"])
    y = 250
    line_h = 86
    for line in lines:
        x = center_x(line, font_title)
        draw.text((x + 4, y + 4), line, font=font_title, fill=(0, 0, 0))
        draw.text((x, y), line, font=font_title, fill=(255, 255, 255))
        y += line_h
    return img


def main():
    config = load_config()
    site = config.get("site", {})
    site_name = site.get("name", "ThriftyNest")
    site_url = site.get("url", "").rstrip("/")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    posts = load_posts()
    if not posts:
        print("[img] no posts found, nothing to do.")
        return
    count = 0
    for post in posts:
        for variant in (1, 2, 3):
            img = make_illustration(post, variant, site_name, site_url)
            img.save(OUT_DIR / ("%s-%d.png" % (post["slug"], variant)), "PNG")
            count += 1
    print("[img] generated %d illustrations -> %s" % (count, OUT_DIR))


if __name__ == "__main__":
    main()
