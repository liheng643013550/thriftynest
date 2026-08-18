# 🚀 SETUP — 15 分钟启动指南

把 ThriftyNest 从"文件夹"变成"每天自动更新、自动赚钱的网站"，一共 5 步。

> 你在国内的话：访问 GitHub / Google / Amazon 需要稳定的网络环境（你懂的），收款用 Payoneer——详见 `MONETIZATION.md`。

## 你需要准备的东西

| 需要 | 费用 | 说明 |
|---|---|---|
| GitHub 账号 | 免费 | github.com 注册 |
| DeepSeek API Key | 10 元 | platform.deepseek.com 注册 → API Keys → 创建，充值 10 元够跑几个月 |

## Step 1 — 把项目推送到 GitHub

在 GitHub 网页上点 **New repository**（仓库名建议 `thriftynest`，选 Public），**不要**勾选任何初始化选项（README、.gitignore 都不要，本地已有）。

然后在本项目文件夹打开终端，执行：

```bash
git init
git add .
git commit -m "init: ThriftyNest automated SEO site"
git branch -M main
git remote add origin https://github.com/你的用户名/thriftynest.git
git push -u origin main
```

## Step 2 — 先改 config.yaml 里的网址（重要）

推送前先打开 `config.yaml`，把 `site.url` 改成你的真实地址：

- 仓库名 `thriftynest`（项目页）：`https://你的用户名.github.io/thriftynest`
- 如果你建的是 `你的用户名.github.io` 仓库（用户名站点）：`https://你的用户名.github.io`

**为什么先改**：sitemap.xml 和文章 canonical 链接都用这个地址。推错了也没事，改完再推送一次即可。

## Step 3 — 添加 API Key 密钥

仓库页面 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**：

- Name: `DEEPSEEK_API_KEY`
- Secret: 你在 DeepSeek 平台复制的 Key

## Step 4 — 开启 GitHub Pages

仓库页面 → **Settings** → **Pages** → **Build and deployment** → Source 选 **GitHub Actions**。

## Step 5 — 手动跑一次，验证全链路

仓库页面 → **Actions** → 左侧 **Daily Publish** → **Run workflow** → 绿色按钮。

等 2-4 分钟，流水线会：生成 3 篇新文章 → 构建站点 → 部署。跑完后：

1. Actions 页面该次运行全绿 ✅
2. 访问 `https://你的用户名.github.io/thriftynest`（或你的地址）能看到网站
3. 仓库里多了 `content/posts/` 新文件和更新后的 `site/`

**恭喜，你的自动内容站上线了。** 之后它每天北京时间 11:00 自动运行，你什么都不用做。

---

## 日常维护（每月 10 分钟）

| 频率 | 动作 |
|---|---|
| 每周 | 看一眼 Actions 是否全绿（红了会发邮件给你） |
| 每月 | 检查 Google Search Console 收录数、文章是否有报错 |
| 每月 | 抽查几篇文章的 Amazon 链接是否失效（AI 生成的 ASIN 可能是占位符，需要换成真实商品链接，见 MONETIZATION.md） |

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| Actions 日志显示 "no API key found" | 没加 Secret 或名字不对 | 检查 Secret 名字必须是 `DEEPSEEK_API_KEY` |
| Actions 失败，报错带 `LLMError` | API Key 无效/余额不足 | 去 DeepSeek 平台充值或换 Key |
| 打开网址 404 | Pages 没配置好 | Settings → Pages → Source 选 GitHub Actions |
| 文章没生成 | 选题池写完了（48 个用光后） | 往 `scripts/keywords.py` 的 TOPICS 里加新选题 |
| 想每天多写/少写 | 节奏配置 | 改 `config.yaml` → `posts_per_day`，推送即可 |
| 想立刻多生成几篇 | 手动触发 | Actions → Run workflow → limit 填 5 |
| 想换更聪明的模型 | 成本换质量 | `config.yaml` → `llm.model` 改成 `deepseek-reasoner`（贵几倍，慎用） |

## 进阶（Phase 2 再做，不急）

- **自定义域名**（$10/年，强烈推荐，SEO 加分）：买 `thriftynest.com` 这类域名 → GitHub Pages 设置里绑定 → 改 `config.yaml` 的 `url` 并推送。
- **GitHub 通知**：Settings → Notifications 开启 Actions 失败邮件提醒。
- **隐私政策页**：申请 AdSense 前需要。新建 `content/privacy.md` 或在 `site/` 手动加一页（模板里没有，属于手动活，半小时搞定）。

## 免责声明

本项目是通用模板，不构成收入承诺。搜索引擎收录、排名和收入存在不确定性，请理性看待（现实预期见 `MONETIZATION.md`）。
