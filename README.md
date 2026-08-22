# 🏠 ThriftyNest — 零成本自动化英文 SEO 内容站

> "Smart picks for a budget home."
> 一个**完全自动运转**的英文博客：AI 每天自动写文 → 自动发布 → 自动部署 → 靠 AdSense + Amazon 联盟被动变现。

## 它怎么赚钱（现实版）

| 阶段 | 时间 | 流量门槛 | 收入来源 |
|---|---|---|---|
| Phase 1 | 0–3 个月 | 被 Google 收录、慢慢涨 | 基本为 0，攒内容 |
| Phase 2 | 3–6 个月 | 1k–10k 月会话 | Amazon 联盟点击 + AdSense |
| Phase 3 | 6–12 个月 | 10k+ 月会话 | Ezoic / Mediavine 广告平台 |

**诚实声明**：没有人能保证收入。本项目把"生产系统"做到全自动、零成本、可长期持有，但搜索引擎收录、排名和收入都需要时间。零成本意味着见效慢——这是数学，不是缺陷。详细预期与运营要点见 [`MONETIZATION.md`](MONETIZATION.md)。

## 系统架构

```
GitHub Actions (免费定时任务, 每天 03:00 UTC)
    │
    ├─ 1. generate_articles.py ── 从选题库挑新关键词
    │        └─ DeepSeek API (每篇成本 ≈ ¥0.01) 生成 1000–1500 词文章
    │
    ├─ 2. build_site.py ── 零框架静态站生成器
    │        ├─ SEO：sitemap.xml / robots.txt / RSS / JSON-LD / canonical / OG
    │        ├─ 站内互链：分类页 + 相关文章 + 面包屑
    │        └─ 变现：Amazon 链接自动加你的 tag、AdSense 广告位注入
    │
    └─ 3. actions/deploy-pages ── 免费部署到 GitHub Pages
```

全程无人值守：你只需启动一次，之后它每天自己写、自己发、自己部署。

## 目录结构

```
thriftynest/
├── config.yaml                  # ★ 所有配置在这改（网址、tag、广告ID、节奏）
├── scripts/
│   ├── generate_articles.py     # 文章生成器（DeepSeek API）
│   ├── keywords.py              # 189 个内置长尾选题库
│   ├── make_illustrations.py    # 文章插图自动生成器（每篇 3 张）
│   ├── ping_indexnow.py         # Bing 即时收录推送（IndexNow）
│   ├── llm.py                   # 极简 LLM 客户端（可换 OpenAI）
│   └── build_site.py            # 静态站生成器
├── content/posts/               # 文章仓库（markdown + frontmatter）
├── templates/base.html          # 站点母版
├── static/style.css             # 全站样式
├── site/                        # 构建产物（部署目录）
├── .github/workflows/daily-publish.yml
└── BING.md                      # Bing 第二引擎设置手册
```

## 快速启动（3 步，约 15 分钟）

完整图文步骤见 **[`SETUP.md`](SETUP.md)**。

1. **准备**：GitHub 账号 + [DeepSeek 开放平台](https://platform.deepseek.com) API Key（充值 10 元够跑几个月）。
2. **推送**：把这个文件夹变成你的 GitHub 仓库并推送。
3. **激活**：Settings → Pages → 构建源选 **GitHub Actions**；仓库加 Secret `DEEPSEEK_API_KEY`；手动跑一次 `Daily Publish` workflow。

之后它每天自动运行。把 `config.yaml` 里的 `url`、`amazon_tag`、`adsense_client` 填上，变现钩子自动生效。

## 成本清单

| 项目 | 费用 |
|---|---|
| GitHub Pages 托管 | 免费 |
| GitHub Actions 定时任务 | 免费 |
| DeepSeek API（每天 3 篇） | ≈ ¥0.03/天，≈ ¥1/月 |
| 自定义域名（可选，Phase 2 再买） | $10/年 |

## 技术要点

- **零框架**：Python 标准库 + 2 个轻依赖（`Markdown`、`PyYAML`），GitHub Actions 秒级构建。
- **不会写重复文章**：选题库按关键词去重，写过的自动跳过。
- **优雅降级**：没配 API Key 时生成器跳过、不报错，站点照常构建。
- **SEO 基本功**：语义化 HTML、唯一 title/description、canonical、Article JSON-LD、面包屑、sitemap、RSS、站内互链，全部自动。

---

**免责声明**：本项目提供的代码与内容为通用模板，不构成收入承诺。广告与联盟收入受平台政策、流量、排名等多因素影响。
