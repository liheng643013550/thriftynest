# 🔍 BING — 第二流量引擎（最快见效，一次性设置）

Bing 为什么值得加：
- **快**：新站几周内就有收录和流量（Google 要 3 个月）
- **一个引擎带三个口子**：Bing 的索引同时供给 **DuckDuckGo** 和 **ChatGPT / Perplexity 等 AI 搜索**
- **IndexNow 即时收录**：你的流水线已接好——每天发布新文章时，自动通知 Bing"有新内容了"，Bing 几小时内就会抓取

**要做的只有一次（约 15 分钟）**，之后全自动。

---

## 第 1 步：注册并添加网站（10 分钟）

1. 打开 [bing.com/webmasters](https://www.bing.com/webmasters) → 用 **Microsoft 账号**登录（没有就注册一个，免费）
2. 进入后选 **"Import from Google Search Console"（从 GSC 导入）** → 授权 Google → 你的网站应该出现在列表里 → 导入
   - ✅ 这是最快方式，因为你已经在 Search Console 验证过了，Bing 直接继承验证
3. 如果导入失败/没有 GSC：手动添加 → 输入网站 `https://liheng643013550.github.io/thriftynest` → 用 **HTML meta tag** 方式验证（会给你一段 `<meta name="msvalidate.01" .../>` 代码——**把它发我**，我写进模板，跟 Google 验证一样）

## 第 2 步：提交站点地图（1 分钟）

1. Bing Webmaster 左侧菜单 → **Sitemaps（站点地图）**
2. 输入：
   ```
   https://liheng643013550.github.io/thriftynest/sitemap.xml
   ```
3. 提交，状态显示成功即可

## 第 3 步：配置 IndexNow 密钥（2 分钟，之后全自动）

1. Bing Webmaster 左侧菜单 → 你的网站 → **"API 提交" / "IndexNow"** 区域
2. 它会显示一个密钥（一串 32 位字符，形如 `1a2b3c...`）→ **复制**
3. 打开本地 `config.yaml`，找到：
   ```
   indexnow_key: ""
   ```
   改成：
   ```
   indexnow_key: "你复制的密钥"
   ```
4. 把 `config.yaml` 上传到 GitHub（仓库根目录，Add file → Upload files）→ 跑一次 **Actions**（Daily Publish → Run workflow）
5. 流水线会自动：
   - 在网站根目录生成密钥验证文件
   - 发布时自动把所有文章推送给 Bing

## 第 4 步：验证（可选，1 分钟）

浏览器打开：

```
https://liheng643013550.github.io/thriftynest/你的密钥.txt
```

能看到密钥内容 = 一切就绪。Bing Webmaster 里通常也会自动检测到验证文件。

---

## 之后发生什么

| 时间 | 效果 |
|---|---|
| 当天 | 流水线每次运行自动推送 URL，Bing 开始抓取 |
| 1-2 周 | Bing 收录你的文章（比 Google 快得多） |
| 持续 | 每天新文章几小时内被 Bing 收录，DuckDuckGo / AI 搜索同步受益 |

## 常见问题

| 问题 | 回答 |
|---|---|
| 密钥填错了/换了 | 改 config.yaml 再推送一次即可，新密钥文件自动上线 |
| 和 Google 冲突吗？ | 不冲突。Google 自己爬自己的，Bing 走 IndexNow，互不干扰 |
| 需要每月维护吗？ | 不需要。一次性设置，之后全自动 |
| IndexNow 推送失败会怎样？ | 不会影响网站发布——流水线里它是"尽力而为"，失败了只是少一个加速，Bing 照样会按 sitemap 定期爬 |
