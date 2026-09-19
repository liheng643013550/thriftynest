#!/usr/bin/env python3
"""流量报告：把 hits.sh 的浏览量 + 出站点击数整理成人能看的报告。

为什么要做这个
--------------
统计已经埋好了，但数据不主动来找你 —— 得有人去查。而且 hits.sh 的接口
对"从未被访问过的页面"返回 404（不是 0），直接查会看起来像出错。
这个脚本把 404 当成 0，并把 196 篇文章聚合成一张能读的表。

两个用法
--------
  ① 本地：python scripts/traffic_report.py            -> 写 traffic-report.md/.html
  ② CI：weekly site-audit 里自动附带流量段（见 site-audit.yml）

必须知道的坑
------------
读数据只能走只读接口 https://hits.sh/api/urns/<key>。
直接 GET .../<key>.svg 会【把计数 +1】—— 那是写操作，会把数据搞脏。
"""
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
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
API = "https://hits.sh/api/urns/"


def fetch(key, tries=3):
    """只读取计数。404 = 该页面从未被访问 -> 返回 0（不是错误）。"""
    for i in range(tries):
        try:
            req = urllib.request.Request(API + key, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
            d = json.loads(raw)
            return {
                "total": int(d.get("total") or 0),
                "monthly": int(d.get("monthly") or 0),
                "weekly": int(d.get("weekly") or 0),
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"total": 0, "monthly": 0, "weekly": 0}
            time.sleep(2 + i * 2)
        except Exception:
            time.sleep(2 + i * 2)
    return None  # 真正取不到（网络问题），和"0"区分开


def main():
    # 站点前缀从 config.yaml 取，避免写死
    cfg = (ROOT / "config.yaml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^\s*url:\s*"([^"]+)"', cfg)
    base = (m.group(1) if m else "").rstrip("/")
    prefix = re.sub(r"^https?://", "", base)

    posts = sorted((ROOT / "content" / "posts").glob("*.md"))
    slugs = [p.stem for p in posts]

    print("站点: %s" % base)
    print("待查页面: 1 首页 + %d 篇文章（每篇查 浏览+出站 两次）" % len(slugs))
    print()

    rows = []

    home = fetch(prefix)
    if home is None:
        print("❌ 首页计数取不到（网络问题），中止")
        return 2
    rows.append(("（站点首页）", home, None))

    # 并行查询 + 短路优化。
    # 单线程逐页查实测要 9 分钟以上（392 次请求 × 每次 1~1.5 秒），
    # 对"双击就能看"的工具来说太慢。两个改动：
    #   ① 8 个线程并发 -> 约 1 分钟
    #   ② 浏览量为 0 的页面【不查出站】—— 没人看就没人点，必然是 0，
    #      请求数直接减半（新站绝大多数页面都是 0，实际开销降得更多）
    from concurrent.futures import ThreadPoolExecutor

    def probe(slug):
        v = fetch(prefix + "/posts/" + slug)
        if v is None:
            return slug, None, None
        if v["total"] <= 0:
            return slug, v, {"total": 0, "monthly": 0, "weekly": 0}
        o = fetch(prefix + "/posts/" + slug + "/outbound")
        return slug, v, o

    unreachable = 0
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for slug, v, o in ex.map(probe, slugs):
            done += 1
            if v is None or o is None:
                unreachable += 1
            rows.append((slug,
                         v or {"total": -1, "monthly": -1, "weekly": -1},
                         o or {"total": -1, "monthly": -1, "weekly": -1}))
            if done % 40 == 0:
                print("  已查 %d / %d ..." % (done, len(slugs)))

    views_total = sum(r[1]["total"] for r in rows if r[1]["total"] > 0)
    views_week = sum(r[1]["weekly"] for r in rows if r[1]["weekly"] > 0)
    out_total = sum(r[2]["total"] for r in rows if r[2] and r[2]["total"] > 0)
    # 统计"文章"时要把首页排除掉 —— rows 里第一行是首页，
    # 不排除会出现"有访问的文章 1/196"但明细里只有首页的怪现象。
    art_rows = [r for r in rows if not r[0].startswith("（")]
    viewed = [r for r in art_rows if r[1]["total"] > 0]
    clicked = [r for r in art_rows if r[2] and r[2]["total"] > 0]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    L = []
    L.append("# ThriftyNest 流量报告")
    L.append("")
    L.append("生成时间：%s（UTC）" % now)
    L.append("")
    L.append("## 总体")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append("| 全站浏览总量（历史累计） | **%d** |" % views_total)
    L.append("| 全站浏览（最近 7 天） | **%d** |" % views_week)
    L.append("| 出站点击（点到亚马逊的次数） | **%d** |" % out_total)
    L.append("| 有访问的文章 | %d / %d |" % (len(viewed), len(slugs)))
    L.append("| 有点击的文章 | %d / %d |" % (len(clicked), len(slugs)))
    L.append("")
    if views_total == 0:
        L.append("> 还没有任何浏览量。对一个月、零外链的新站来说这是正常起点——")
        L.append("> 数据从 0 开始才有对比意义。等搜索引擎收录后再看这份报告。")
        L.append("")
    L.append("## 明细（只列有数据的）")
    L.append("")
    if viewed or clicked:
        L.append("| 页面 | 浏览总量 | 近 7 天 | 出站点击 |")
        L.append("|---|---|---|---|")
        for name, v, o in sorted(rows, key=lambda r: -(r[1]["total"] or 0)):
            if (v["total"] or 0) <= 0 and (not o or (o["total"] or 0) <= 0):
                continue
            L.append("| %s | %d | %d | %s |"
                     % (name[:56], v["total"], v["weekly"],
                        (o["total"] if o and o["total"] >= 0 else "－")))
    else:
        L.append("_暂无任何页面有访问记录。_")
    L.append("")
    L.append("## 说明")
    L.append("")
    L.append("- 计数来自 hits.sh，键 = 页面 URL 去掉协议（每页一个独立计数器）。")
    L.append("- **读数只用只读接口**；直接请求 `.svg` 会把数字 +1（那是写操作）。")
    L.append("- 出站点击 = 读者点走亚马逊链接的次数。这是唯一能判断"
             "「哪篇文章真的能带货」的指标。")
    L.append("- 局限：hits.sh 只有浏览量，**没有独立访客 / 来源 / 国家**；")
    L.append("  爬虫和链接预览也会计入，所以数字偏高。")
    if unreachable:
        L.append("- ⚠️ 本次有 %d 个页面计数取不到（网络问题），数值偏低。" % unreachable)
    L.append("")

    md = "\n".join(L)
    (ROOT / "traffic-report.md").write_text(md, encoding="utf-8")

    # 同时给一份能直接在浏览器看的
    html_body = md
    html_body = re.sub(r"(?m)^# (.+)$", r"<h1>\1</h1>", html_body)
    html_body = re.sub(r"(?m)^## (.+)$", r"<h2>\1</h2>", html_body)
    html_body = re.sub(r"(?m)^> (.+)$", r"<blockquote>\1</blockquote>", html_body)
    html_body = re.sub(r"(?m)^\|(.+)\|$",
                       lambda mm: "<tr>" + "".join(
                           "<td>%s</td>" % c.strip()
                           for c in mm.group(1).split("|")) + "</tr>", html_body)
    html_body = re.sub(r"</tr>\s*</tr>", "</tr>", html_body)
    html_body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html_body)
    html = ("<!doctype html><meta charset='utf-8'>"
            "<title>ThriftyNest 流量报告</title>"
            "<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;"
            "max-width:900px;margin:40px auto;padding:0 16px;line-height:1.7;color:#222}"
            "table{border-collapse:collapse;width:100%;margin:12px 0}"
            "td{border:1px solid #ddd;padding:6px 10px;font-size:14px}"
            "tr:first-child td{background:#f4f4f4;font-weight:600}"
            "h1{font-size:24px}h2{font-size:18px;margin-top:28px;border-bottom:1px solid #eee;padding-bottom:4px}"
            "blockquote{border-left:3px solid #ccc;margin:10px 0;padding:4px 12px;color:#555}"
            "</style><body>" + html_body + "</body>")
    (ROOT / "traffic-report.html").write_text(html, encoding="utf-8")

    print()
    print("=" * 74)
    print("浏览总量 %d（近 7 天 %d）· 出站点击 %d · 有访问的文章 %d/%d"
          % (views_total, views_week, out_total, len(viewed), len(slugs)))
    print("报告已写到:")
    print("  %s" % (ROOT / "traffic-report.md"))
    print("  %s" % (ROOT / "traffic-report.html"))
    if unreachable:
        print("⚠️ %d 个页面取不到，数值偏低" % unreachable)
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
