#!/usr/bin/env python3
"""本地运行器（Local runner）—— 绕开 embed 版 Python 的 sys.path 限制。

背景：Windows 免安装（embeddable）版 Python 存在 `python312._pth` 文件时，
不会把"脚本所在目录"加入 sys.path，导致 `python scripts/generate_articles.py`
报 `ModuleNotFoundError: No module named 'llm'`（后者与 generate_articles.py 同目录）。

GitHub Actions 上用的是完整版 Python，**没有这个问题**，
所以仓库里的 workflow 不需要改。这个运行器只是为了本机调试方便。

用法（在仓库根目录执行）：
    python run.py generate_articles.py --dry-run --limit 10
    python run.py build_site.py
    python run.py make_illustrations.py
    python run.py verify_site.py --sample 12
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("仓库根目录: %s" % ROOT)
        print("可运行的脚本:")
        for p in sorted(SCRIPTS.glob("*.py")):
            print("  - %s" % p.name)
        return 2

    target = sys.argv[1]
    path = SCRIPTS / target
    if not path.is_file():
        # 允许传相对/绝对路径
        path = Path(target)
    if not path.is_file():
        print("找不到脚本: %s" % target)
        return 2

    # 关键一步：把 scripts/ 与仓库根都放进 sys.path
    for p in (str(SCRIPTS), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)

    # 还原 argv，让被调脚本看到自己的参数
    sys.argv = [str(path)] + sys.argv[2:]
    runpy.run_path(str(path), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
