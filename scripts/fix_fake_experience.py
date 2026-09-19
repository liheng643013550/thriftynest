#!/usr/bin/env python3
"""把存量文章里【编造的第一人称体验】改写成真实表述。

为什么必须做
------------
导购站写「我实测过 / 我买了 / 在我家厨房…」属于虚假体验陈述：
  - Google E-E-A-T 要求真实体验，编造属于掉分/被人工处置项
  - Amazon Associates 协议禁止虚假陈述
新版生成器的 prompt 已禁止这类写法，但【存量老文章是旧 prompt 产的，不会自动重写】。

改写原则（不发明、不丢信息）
----------------------------
只改【命中编造模式的那一段】，把"我实测"换成"规格表列出 / 用户评价普遍反映"。
事实、数字、商品名、链接一律保持不动。

安全阀门（不过就不写回）
------------------------
1. 改写后不得再命中任何编造模式
2. 所有 [文字](链接) 对必须【完全一致】—— 不许改链接、不许增删链接
3. 段落"形态"必须一致（行数、标题/列表/表格/正文的行类型完全相同）
4. 长度比例 0.5 ~ 1.8
5. 不许出现 "Here is / Sure / 以下是" 这类前言
任一条不过 -> 重试一次（更严格的提示）-> 仍不过则跳过该段并记录，绝不写回半成品。

用法：
    python scripts/fix_fake_experience.py --check
    python scripts/fix_fake_experience.py --apply --limit 3      # 小样
    python scripts/fix_fake_experience.py --apply                # 全量
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from llm import LLMError, complete  # noqa: E402

POSTS = HERE.parent / "content" / "posts"

# ---------------------------------------------------------------- 编造模式
BANNED = {
    "I tested": re.compile(r"\bI(?:['\u2019]ve| have)?\s+(?:tested|tried|used|reviewed|measured|"
                           r"weighed|timed|compared|ran)\b", re.I),
    # 必须允许 I've / I have —— 旧写法是 \bI\s+(?:bought|own…)，于是
    # "I've bought"、"I have owned" 全都漏掉（实测线上有 11 处这种漏网的）。
    "I bought/own": re.compile(r"\bI(?:['\u2019]ve|'d| have| had)?\s+"
                               r"(?:bought|purchased|own|owned|ordered|kept|returned)\b", re.I),
    "I found/noticed": re.compile(r"\bI(?:['\u2019]ve|'d| have| had)?\s+"
                                  r"(?:found|noticed|saw|discovered|realized|learned)\b", re.I),
    "my <place>": re.compile(r"\b(?:in|on|at|into)\s+my\s+(?:kitchen|home|apartment|living room|"
                             r"bathroom|garage|house|yard|closet|office|dorm|rv|car)\b", re.I),
    "my <pet/family>": re.compile(r"\bmy\s+(?:dog|cat|puppy|kitten|kid|kids|son|daughter|"
                                  r"wife|husband|partner|roommate)\b", re.I),
    "we tested": re.compile(r"\bwe(?:['\u2019]ve| have)?\s+(?:tested|tried|used|reviewed|measured|"
                            r"bought|found|noticed|kept|compared|ran)\b", re.I),
    # 这两条是实测漏网的：线上真出现过 "in our tests" 和 "I have a confession"，
    # 而旧动词表里既没有 "in our tests" 也没有这类自述式开场。
    "in our tests": re.compile(r"\bin (?:our|my) tests?\b", re.I),
    "confession/self-narration": re.compile(
        r"\bI (?:have|'ve|had) (?:to )?(?:make )?a confession\b"
        r"|\bI(?:['\u2019]ll| will) admit\b|\bI used to think\b", re.I),
    "our <place>": re.compile(r"\b(?:in|on|at)\s+our\s+(?:kitchen|home|test|testing|"
                              r"apartment|house|office)\b", re.I),
    "I recommend to friends": re.compile(r"\bI(?:['\u2019]ve|'d| have| had)?\s+"
                                         r"(?:recommend|tell friends|tell people)\b", re.I),
}

# 这些是"作者实测"的间接说法，也一并算上（更严）
EXTRA = {
    "hours of testing": re.compile(r"\b(?:after|during)\s+(?:hours|weeks|months)\s+of\s+"
                                   r"(?:testing|use|research)\b", re.I),
}

HEAD_RX = re.compile(r"^(#{1,6})\s")

SYSTEM = (
    "You are a meticulous copy editor for a US home-and-kitchen buying guide. "
    "You never invent first-hand experience: the publication does not own, buy, "
    "or physically test products. You rewrite so claims rest on published specs, "
    "manufacturer statements, and aggregated owner reviews. You never add facts."
)

PROMPT = """Rewrite the passage below so that it makes NO claim of personal testing, buying, owning, using, measuring, or recommending.

MANDATORY RULES
- Keep EVERY fact, number, price, brand, model name, and Markdown link byte-identical.
  Do not add, remove, or reword any link or its link text.
- Replace first-hand claims with honest attribution, for example:
    "I tested 12 models"        -> "Reviewers and spec sheets cover a dozen models"
    "In my kitchen it lasted"   -> "Owner reports describe it lasting"
    "we found the handle hot"   -> "owner reviews frequently mention the handle runs hot"
- Do not use the words I, we, my, our, or us anywhere in the output.
- Do not add or remove any Markdown structure (headings, list markers, tables,
  emphasis, images). Keep the same number of lines.
- Keep roughly the same length and the same plain, practical tone.
- Do NOT hedge into vagueness: keep all concrete details.
- Output ONLY the rewritten passage. No preamble, no surrounding quotes, no notes.

PASSAGE
---
%s
---
REWRITTEN PASSAGE:"""

RETRY_PROMPT = """Your previous answer violated a rule. Rewrite again, more carefully.

The output must:
- contain NONE of the words: I, we, my, our, us
- keep every link and link text exactly as in the input
- have exactly the same number of lines and the same Markdown structure
- be one continuous passage, no bullet points unless the input had them

PASSAGE
---
%s
---
REWRITTEN PASSAGE:"""


# ---------------------------------------------------------------- 工具函数
def enc():
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


def hits(text, include_extra=True):
    names = [n for n, rx in BANNED.items() if rx.search(text)]
    if include_extra:
        names += [n for n, rx in EXTRA.items() if rx.search(text)]
    return names


def split_front(text):
    """返回 (frontmatter_with_delims, body)。"""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return "---" + parts[1] + "---", parts[2]
    return "", text


def blocks_of(body):
    """按空行切成块，返回 (起始偏移, 块文本) 列表，保留分隔符。"""
    out, i = [], 0
    for m in re.finditer(r"(?:[^\n]*(?:\n|$))", body):
        pass  # 占位，下面用更简单的方式
    # 用 splitlines 保留结构
    lines = body.split("\n")
    cur, start = [], 0
    for idx, ln in enumerate(lines):
        if ln.strip() == "":
            if cur:
                out.append((start, "\n".join(cur)))
                cur = []
            start = idx + 1
        else:
            if not cur:
                start = idx
            cur.append(ln)
    if cur:
        out.append((start, "\n".join(cur)))
    return out


def shape(text):
    sig = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            sig.append("blank")
        elif HEAD_RX.match(s):
            sig.append("h%d" % len(HEAD_RX.match(s).group(1)))
        elif s[:1] in "-*+" and len(s) > 1 and s[1] == " ":
            sig.append("li")
        elif s.startswith("|"):
            sig.append("tbl")
        elif s.startswith(">"):
            sig.append("quote")
        elif s.startswith("<"):
            sig.append("html")
        else:
            sig.append("p")
    return sig


def links(text):
    return re.findall(r"\[([^\]]*)\]\(([^)]*)\)", text)


def is_rewritable(block):
    """表格、图片/HTML、纯链接行不改（结构风险大、收益低）。"""
    s = block.strip()
    if s.startswith("|") or s.startswith("<"):
        return False
    if HEAD_RX.match(s):
        return False
    if re.fullmatch(r"\[[^\]]*\]\([^)]*\)", s):
        return False
    return True


def clean_output(raw):
    t = raw.strip()
    # 去掉模型偶尔加的代码围栏
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    t = t.strip()
    # 去掉整体包裹的引号
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'“”":
        t = t[1:-1].strip()
    # 去掉常见前言
    t = re.sub(r"^(?:Here(?:'s| is)[^\n]*:|Sure[^\n]*:|Rewritten passage:)\s*\n?",
               "", t, flags=re.I).strip()
    return t


def normalize(orig, new):
    """把模型换行还原：原文是单段时，把输出的多行合回一段。"""
    so, sn = shape(orig), shape(new)
    if so and set(so) == {"p"} and len(so) == 1:
        if sn and set(sn) == {"p"}:
            return " ".join(x.strip() for x in new.split("\n") if x.strip())
    return new


def verify(orig, new, include_extra=True):
    """返回 (ok, reason)。"""
    if not new.strip():
        return False, "empty"
    bad = hits(new, include_extra)
    if bad:
        return False, "still banned: %s" % ",".join(bad)
    if re.search(r"\b(?:I|we|my|our|us)\b", new):
        return False, "first-person pronoun remains"
    if links(orig) != links(new):
        return False, "links changed (%d -> %d)" % (len(links(orig)), len(links(new)))
    if shape(orig) != shape(new):
        return False, "structure changed %s -> %s" % (shape(orig)[:6], shape(new)[:6])
    r = len(new) / max(1, len(orig))
    if not (0.5 <= r <= 1.8):
        return False, "length ratio %.2f" % r
    return True, "ok"


def rewrite_block(block, include_extra=True, retries=2):
    """返回 (new_text or None, note)。"""
    prompt = PROMPT % block
    for attempt in range(retries + 1):
        try:
            raw = complete(prompt, temperature=0.25 if attempt == 0 else 0.15,
                           max_tokens=1200, system=SYSTEM)
        except LLMError as exc:
            return None, "LLM 失败: %s" % str(exc)[:90]
        new = normalize(block, clean_output(raw))
        ok, why = verify(block, new, include_extra)
        if ok:
            return new, "ok"
        prompt = RETRY_PROMPT % block
        last = why
    return None, "校验不过: %s" % last


# ---------------------------------------------------------------- 主流程
def scan(files, include_extra=True):
    todo = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        front, body = split_front(text)
        for start, blk in blocks_of(body):
            if not is_rewritable(blk):
                continue
            h = hits(blk, include_extra)
            if h:
                todo.append((f, start, blk, h))
    return todo


def process_one(f, include_extra=True, workers=3):
    """处理一篇文章，返回 (文件名, 改了几段, 失败几段, 剩余命中段数)。"""
    text = f.read_text(encoding="utf-8")
    front, body = split_front(text)
    blocks = blocks_of(body)
    targets = [(s, b) for s, b in blocks
               if is_rewritable(b) and hits(b, include_extra)]
    if not targets:
        return f.name, 0, 0, 0

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(rewrite_block, b, include_extra): s for s, b in targets}
        for fu, s in futs.items():
            results[s] = fu.result()

    # 重建 body（按原偏移替换；失败的保持原样）
    new_lines = body.split("\n")
    changed = failed = 0
    for s, b in targets:
        new, note = results[s]
        if new is None:
            failed += 1
            continue
        new_lines[s:s + len(b.split("\n"))] = new.split("\n")
        changed += 1
    new_body = "\n".join(new_lines)
    out_text = (front + new_body) if front else new_body

    left = sum(1 for _, blk in blocks_of(new_body)
               if is_rewritable(blk) and hits(blk, include_extra))

    if changed:
        f.write_text(out_text, encoding="utf-8", newline="")
    return f.name, changed, failed, left


def main():
    enc()
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--include-filler", action="store_true",
                    help="同时去掉 honestly / believe it or not 这类填充腔")
    args = ap.parse_args()

    include_extra = args.include_filler
    if args.files:
        files = [POSTS / n for n in args.files]
    else:
        files = sorted(POSTS.glob("*.md"))

    todo = scan(files, include_extra)
    arts = sorted({str(f.name) for f, _, _, _ in todo})
    print("扫描 %d 篇 -> 命中 %d 篇 / %d 段" % (len(files), len(arts), len(todo)))
    if args.check:
        for f, s, b, h in todo[:25]:
            print("  [%s] %s" % (f.name[:38], ",".join(h)))
            print("      %s" % b.replace("\n", " ")[:140])
        if len(todo) > 25:
            print("  ... 还有 %d 段" % (len(todo) - 25))
        return 0

    if not args.apply:
        print("（这是扫描；加 --apply 才会改写）")
        return 0

    targets = arts if args.limit is None else arts[:args.limit]
    print("开始改写 %d 篇（workers=%d）..." % (len(targets), args.workers))
    t0 = time.time()
    tot_c = tot_f = tot_l = 0
    for i, name in enumerate(targets, 1):
        f = POSTS / name
        nm, c, fa, left = process_one(f, include_extra, args.workers)
        tot_c += c
        tot_f += fa
        tot_l += left
        flag = "OK " if (c and not fa and not left) else ("部分" if c else "跳过")
        print("  %3d/%d [%s] %-46s 改 %d 失败 %d 剩余 %d"
              % (i, len(targets), flag, nm[:46], c, fa, left))
    print()
    print("共改 %d 段，失败 %d 段，剩余命中 %d 段，耗时 %.1f 分钟"
          % (tot_c, tot_f, tot_l, (time.time() - t0) / 60.0))
    return 0 if tot_l == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
