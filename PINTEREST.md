# 📌 PINTEREST — 第二流量引擎操作手册

Pinterest 是家居/厨房类内容的**第一大免费流量来源**，也是你目前唯一能"主动造流量"的渠道（Google 只能等）。这套系统已经帮你把最重的活干完了：

- ✅ 每篇文章**自动生成 Pin 图**（1000×1500，按分类配色）
- ✅ 文章页面**自动带上 og:image**——Pinterest 保存时自动识别这张图
- ✅ 预留了 Pinterest 网站认领位（`config.yaml` → `pinterest_verify`）

你要做的只是：**一次性设置（30 分钟）** + **每周发 pin（10 分钟）**。

---

## 一、一次性设置（30 分钟，只做一次）

### 1. 注册 Pinterest 企业账号（免费）

去 [pinterest.com](https://www.pinterest.com) 注册，选 **"创建企业账号"（Create a business account）**，填网站地址 `https://liheng643013550.github.io/thriftynest`。

> 个人账号也能用，但企业账号才有"认领网站"和分析功能，必须用企业账号。

### 2. 认领网站（关键，5 分钟）

1. Pinterest → 右上角头像 → **Settings（设置）**
2. 找到 **Claim（认领）** → 网站那一栏点 **Claim website**
3. 选择 **"HTML Tag"** 方式 → 会给你一串代码：
   ```
   <meta name="p:domain_verify" content="一串字符" />
   ```
4. **把 `content="..."` 里那串字符复制发给我**（或自己操作）
5. 打开本地 `config.yaml`，找到 `pinterest_verify: ""`，把字符填进去（引号内）
6. 把 `config.yaml` 上传到 GitHub（scripts 文件夹旁，仓库根目录，Add file → Upload files）→ 跑一次 Actions → 全绿
7. 回 Pinterest 点 **"提交验证"（Submit）**，显示已验证 ✅

> 不想填 config 的话，也可以把整段 `<meta .../>` 代码发我，我像加 Google 验证那样直接写进模板。

### 3. 创建 8 个画板（Boards，5 分钟）

对应你的分类，画板名直接带关键词（Pinterest 用户会搜索它们）：

| 画板名（英文） | 放什么 |
|---|---|
| Budget Kitchen Gadgets | kitchen 类 |
| Kitchen Organization Ideas | organization 类 |
| Cheap Cleaning Hacks | cleaning 类 |
| Home Office on a Budget | home-office 类 |
| Budget Pet Products | pet 类 |
| Garden on a Budget | garden 类 |
| Save Money on Energy | energy 类 |
| DIY & Tools for Home | tools 类 |

### 4. 安装浏览器插件（2 分钟）

Chrome/Edge 应用商店搜 **"Pinterest Save Button"**（官方插件）→ 安装 → 登录。之后打开你的文章页，图片上会浮现 **Save（保存）** 按钮，点一下就能发 pin，不用下载图片。

---

## 二、每周例行（10 分钟，固定动作）

每周选一天（比如周日），打开网站看**本周新增的 3 篇文章**：

1. 打开文章页（如 `.../thriftynest/posts/best-air-fryer-toaster-oven-combo-under-100/`）
2. 鼠标移到文章顶部的 Pin 图上 → 点 **Save** 按钮（插件）
3. 选择对应画板 → 编辑描述 → 发布
4. 每篇可以发 **2-3 个 pin**（换不同描述），但**每天总量别超过 5 个**，细水长流

### 描述模板（直接抄）

```
Best air fryer toaster oven combos under $100 🔥 2-in-1 appliances that actually save counter space (and money). Full comparison + what to skip → [链接]
```

套路：**关键词开头 + 数字/利益点 + 情绪符号 + 箭头链接**。

---

## 三、最佳实践（记住 4 条）

1. **持续 > 爆发**：每天 1-3 个 pin，比一周一次发 20 个强 10 倍。
2. **描述带关键词**：用户在 Pinterest 搜索 "air fryer under $50" 时，描述里有这个词才会被搜到。
3. **标题在图上要可读**：系统生成的 Pin 图已经处理好了（大标题+分类色），别二次加工。
4. **回访老文章**：过 2-3 个月把老文章重新 pin 一次（换描述），老内容也能二次爆。

---

## 四、期望管理（实话）

- Pinterest 和 Google 一样是**慢变量**：前 1-3 个月展示量少，正常。
- 3 个月后看 **Pinterest Analytics（分析）**：哪个 pin 展示多、点击多，就多写那类选题。
- 家居类 pin 的"半衰期"很长（几个月后还在被人看），**发了就一直在积累**——这正是它适合当第二引擎的原因。
- 与 Google 的差别：Pinterest 的流量**来得更快、更稳定**（不靠排名，靠图钉曝光），但天花板低于 Google。

## 五、常见问题

| 问题 | 回答 |
|---|---|
| 能全自动发 pin 吗？ | 短期不行——Pinterest 官方 API 要申请开发者应用（审核严格），手动 10 分钟/周已经是最高性价比 |
| 图钉图在哪看？ | 每篇文章的 `site/static/pins/<文章名>.png`，或直接看文章页顶部 |
| 国内能注册吗？ | 需要能访问 Pinterest 的网络环境（你能访问就说明没问题） |
| 需要付费工具吗？ | 不需要。Tailwind（排程工具）等流量起来、时间不够用了再考虑 |
