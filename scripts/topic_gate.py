#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题目闸门：在生成正文之前先否决坏题目。

为什么要有这个：
    旧流程是「套模板造题 → AI 写正文 → 检查链接没死 → 发布」。
    题目本身是垃圾时，后面所有环节都白干 —— 而且会往里加一篇垃圾页。
    用户的原话：「每天产出应该是少量过关页 + 大量被拒绝的题目，拒绝日志比新文章数量重要。」

判定全部是规则，不调模型（CI 里要快要稳）：
    ① 只留三种 page_type：roundup / versus / worth_it
       其余（how-to、why、how much、definition…）一律不进生产队列
    ② versus 两边必须是「同一件工作」—— 不在白名单就拒（宁拒勿滥）
    ③ 瘦变体（for one person / for dorm / first apartment / studio / college）拒
    ④ 价格带不可能（under $N 低于该类目真实入门价）拒
    ⑤ 「how to X cheaply」这类无限变体拒
    ⑥ 认不出商品类的拒

每条被拒的题目都记进 reject log，附拒绝原因。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "content" / "_topic_reject_log.jsonl"

# ── 同一件工作的 versus 白名单（只放真的能对比的）──────────────────
SAME_JOB = {
    ("air fryer", "countertop oven"),
    ("air fryer", "toaster"),          # 现在 toaster oven 归 countertop oven，这条兜底
    ("corded drill", "cordless drill"),
    ("hand saw", "circular saw"),
    ("stick vacuum", "upright vacuum"),
    ("robot vacuum", "stick vacuum"),
    ("cast iron pan", "nonstick pan"),
    ("french press", "drip coffee maker"),
    ("dutch oven", "slow cooker"),
    ("mop", "steam mop"),
    ("storage bin", "storage basket"),
    ("wrench set", "adjustable wrench"),
    ("measuring cup", "measuring spoon"),
    ("cutting board", "butcher block"),
    ("food processor", "blender"),
    ("rice cooker", "pressure cooker"),
    ("kettle", "electric kettle"),
    ("humidity", "dehumidifier"),
    ("space heater", "oil filled heater"),
}

# ── 瘦变体：这类题目本身就是换皮 ────────────────────────────────
THIN = re.compile(r"\b(for one person|for a dorm|for dorm|dorm room|first apartment|"
                  r"for a studio|studio apartment|for college|for renters|tiny apartment)\b", re.I)

# ── 无限 how-to 变体 ───────────────────────────────────────────
HOWTO_CHEAP = re.compile(r"^how to .*\b(cheaply|on a budget|for cheap)\b", re.I)

# ── 非导购意图的前缀 ───────────────────────────────────────────
NON_BUYING = re.compile(r"^(how to|how much|how often|how long|how many|why |what |when |where |"
                        r"which |can you|should you|do you|does |is it cheaper)", re.I)

# ── 各类目的真实入门价下限（低于它就是编的价）─────────────────────
PRICE_FLOOR = {
    "vacuum": 40, "robot vacuum": 90, "stick vacuum": 50, "upright vacuum": 60,
    "air fryer": 35, "blender": 20, "food processor": 35, "slow cooker": 20,
    "cookware": 40, "cookware set": 60, "dutch oven": 30, "rice cooker": 25,
    "kettle": 15, "electric kettle": 18, "french press": 15, "coffee maker": 25,
    "dehumidifier": 90, "space heater": 25, "mattress": 120, "mattress protector": 20,
    "cutting board": 10, "mop": 15, "steam mop": 60, "storage bin": 12,
    "drill": 30, "cordless drill": 50, "tool kit": 25, "humidity": 5,
    "grow light": 20, "plant": 5, "ladder": 40, "desk": 60, "standing desk": 150,
}
DEFAULT_FLOOR = 12

BUY_SIGNAL = re.compile(r"\b(best|cheapest|cheap|affordable|budget|top|under \$?\d+|worth it)\b", re.I)


def _sides(kw):
    if not re.search(r"\bvs\b|\bversus\b", kw, re.I):
        return None
    parts = re.split(r"\s+vs\.?\s+|\s+versus\s+", kw, flags=re.I)
    return [p.strip().lower() for p in parts] if len(parts) == 2 else None


def _type_of(s):
    """用 build_intents_v2 的词表把一段话归到商品类；认不出返回 None。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "v2"))
        from build_intents_v2 import classify
        return classify(s)
    except Exception:
        return None


def gate(kw):
    """返回 (allow, page_type, reason)。"""
    # ★先把 slug 形态归一成普通文本：关键词池里是「best robot vacuum」，
    #   但测试/存量 slug 是「best-robot-vacuum」。不归一的话，
    #   \bvs\b、\bworth it\b、瘦变体、价格下限全都会失配（踩过）。
    k = re.sub(r"\s+", " ", kw.replace("-", " ").replace("_", " ")).strip()

    # ⑤ how-to …cheaply
    if HOWTO_CHEAP.match(k):
        return False, "", "how-to 无限变体（cheaply / on a budget）"

    # versus
    sides = _sides(k)
    if sides:
        a, b = _type_of(sides[0]), _type_of(sides[1])
        if not a or not b:
            return False, "versus", "认不出两边的商品类（%r / %r），不敢自动发" % (sides[0][:20], sides[1][:20])
        if a == b:
            return False, "versus", "两边归到同一类（%s vs %s），是重复题" % (a, b)
        if tuple(sorted([a, b])) not in SAME_JOB:
            return False, "versus", "不是同一件工作：%s vs %s" % (a, b)
        if THIN.search(k):
            return False, "versus", "瘦变体（%s）" % THIN.search(k).group(0)
        return True, "versus", ""

    # ③ 瘦变体
    m = THIN.search(k)
    if m:
        return False, "", "瘦变体（%s）—— 换皮模板" % m.group(0)

    # ④ 价格带不可能
    mp = re.search(r"under\s*\$?\s*(\d+)", k, re.I)
    if mp:
        n = int(mp.group(1))
        t = _type_of(k)
        floor = PRICE_FLOOR.get(t or "", DEFAULT_FLOOR)
        if n < floor:
            return False, "roundup", "价格带不可能：under $%d，而 %s 真实入门价约 $%d" % (n, t or "该品类", floor)

    # worth_it
    if re.search(r"\bworth it\b|\bworth the\b", k, re.I):
        return True, "worth_it", ""

    # ① 非导购意图
    if NON_BUYING.match(k):
        return False, "", "不是导购意图（how/why/what 类）"

    # roundup：必须有购买信号
    if not BUY_SIGNAL.search(k):
        return False, "", "没有购买信号（既不是 best/cheap/under，也不是 worth it）"

    # ⑥ 认不出商品类（= 商品池里没有这类商品的已核实 ASIN）
    if not _type_of(k):
        return False, "roundup", ("商品池里没有这类商品的已核实 ASIN（%s）"
                                  "—— 按 spec 写手不许发明型号，所以不能写"
                                  % k[:30])

    return True, "roundup", ""


def log_reject(kw, page_type, reason):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "kw": kw,
           "page_type": page_type, "reason": reason}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    """自测：拿一批已知的好题和坏题过闸门，打印结果。"""
    bad = ["step-ladder-vs-utility-knife", "watering-can-vs-hanging-planter",
           "window-insulation-kit-vs-drying-rack", "best-robot-vacuum-under-20",
           "best-air-fryer-for-one-person", "how-to-clean-a-couch-cheaply",
           "why-is-my-kettle-so-loud", "best-cheap-blender-for-soup-not-smoothy"]
    good = ["best-french-press", "best-budget-mop-for-hardwood-floors",
            "smart-thermostat-worth-it", "cast-iron-pan-vs-nonstick-pan"]
    print("=== 应该被拒 ===")
    for k in bad:
        ok, pt, why = gate(k)
        print("   %s %-46s %s" % ("✅拒" if not ok else "❌放行", k, why[:60]))
    print()
    print("=== 应该放行 ===")
    for k in good:
        ok, pt, why = gate(k)
        print("   %s %-46s %s" % ("✅放行" if ok else "❌拒", k, (pt or why)[:60]))


if __name__ == "__main__":
    main()
