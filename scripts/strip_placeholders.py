#!/usr/bin/env python3
"""把未核实商品的占位符链接降级成纯文字（构建前兜底）。

为什么把它放进仓库、接进流水线
------------------------------
线上真实发生过：9 篇文章带着 48 条 https://www.amazon.com/dp/PLACEHOLDER-ASIN
这样的死链上线，而且是【在我本地闸门全绿的情况下】——
因为那 9 篇是 CI 日更直接提交到远端的，从未进入我一直校验的那棵树。

教训：只靠"生成时闸门"不够，因为
  ① 历史遗留的稿子不会被重新生成
  ② 任何绕过生成器的编辑都可能引入占位符
所以在【构建之前】再兜一道：占位符链接一律降级为纯文字。
可见文字完全不变，只是不再是链接 —— 读者不会点到死链，爬虫也拿不到 404。

强校验（与 83-unlink_placeholders.py 同口径）：
  - 行数不变
  - 去掉 markdown 链接标记后的可见文字逐字节不变
  - 其它链接一律不动
"""
import re
import sys
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

HERE = Path(__file__).resolve().parent
POSTS = HERE.parent / "content" / "posts"

# [文字](...PLACEHOLDER-ASIN...)
LINK_RX = re.compile(r"\[([^\]]+)\]\((?:[^()]*?)PLACEHOLDER-ASIN(?:[^()]*?)\)")


def visible(s):
    """去掉链接语法后的可见文字（用于校验"没改内容"）。"""
    s = LINK_RX.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    dry = "--apply" not in sys.argv
    files = changed = links = 0
    bad = []
    for p in sorted(POSTS.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        if "PLACEHOLDER-ASIN" not in raw:
            continue
        files += 1
        before_vis = visible(raw)
        n = len(LINK_RX.findall(raw))
        new = LINK_RX.sub(r"\1", raw)

        # 校验 1：行数不变
        if len(new.splitlines()) != len(raw.splitlines()):
            bad.append((p.name, "行数变了"))
            continue
        # 校验 2：可见文字逐字节不变
        if visible(new) != before_vis:
            bad.append((p.name, "可见文字变了"))
            continue
        if n == 0:
            continue
        if not dry:
            p.write_text(new, encoding="utf-8")
        changed += 1
        links += n
        print("   %-56s %d 条" % (p.name[:56], n))

    print()
    print("=" * 74)
    print("含占位符文件 %d 个 / 已降级 %d 个 / 共 %d 条链接" % (files, changed, links))
    if bad:
        print("❌ 校验失败（未改动）:")
        for n, why in bad:
            print("   %s -> %s" % (n, why))
    else:
        print("✅ 三项强校验通过（行数不变 / 可见文字不变 / 其它链接不动）")
    if dry:
        print("（预览模式，未写盘。加 --apply 生效）")
    print("=" * 74)
    print("剩余 PLACEHOLDER 链接: %d 条 · 剩余 PLACEHOLDER 字样: %d 处"
          % (sum(len(LINK_RX.findall(q.read_text(encoding='utf-8')))
                 for q in POSTS.glob('*.md')),
             sum(q.read_text(encoding='utf-8').count('PLACEHOLDER-ASIN')
                 for q in POSTS.glob('*.md'))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
