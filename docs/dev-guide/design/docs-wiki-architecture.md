---
version: 1.0
author: designer
date: 2026-08-25
status: draft
cross_refs: [mermaid-style.md, architecture.md, infrastructure.md]
---

# docs/ ↔ GitHub Wiki 信息架构与 README 拆分方案

## 1. 目标

将 `docs/` 目录直接用作 GitHub Wiki 内容源，实现 **docs/ 与 Wiki 页面严格一一对应**（除 `_Sidebar.md` / `_Footer.md` 等下划线特殊文件外，每个 `.md` 即一个 wiki 页面）；把 984 行 `README.md` 按"用户手册 / 开发者手册"拆分迁入 `docs/`，README 精简为项目名片 + 文档索引，**README 与 wiki 不重复承载相同信息**；同时收编现有 13 份设计文档与 `docs/deployment.md`，并给出 docs/ → Wiki 的自动同步方案。

## 2. 方案概述

`docs/` 分三区——`user-guide/`（使用者与部署者）、`dev-guide/`（贡献者，设计文档整体移入 `dev-guide/design/`）、根级特殊文件（`Home.md` / `_Sidebar.md` / `_Footer.md`）；同步脚本/Action 将 docs/ 树**压平为连字符页名**推送到 `REPO.wiki.git`，推送时剥离 YAML frontmatter、把站内 `.md` 相对链接重写为 wiki 页名；README 只保留简介、功能速览、最简快速开始与文档索引表，其余内容单一归属到对应 wiki 页面。

## 3. 详细设计

### 3.1 GitHub Wiki 机制要点与命名规则

1. **独立 git 仓库**：Wiki 内容存储在 `OWNER/REPO.wiki.git` 独立仓库，可 clone / push，本方案的同步即"把转换后的 docs/ 推进这个仓库"。
2. **文件即页面**：每个 `.md` 文件 = 一个页面，页面名取自文件名；标题展示为去 `.md`、首字母大写、连字符转空格的文本。**下划线开头的特殊文件（`_Sidebar.md`、`_Footer.md`）不计入页面列表**，分别作为全局侧边栏与页脚渲染。
3. **`Home.md` 是着陆页**：访问 wiki 根地址默认打开该页，承担目录之外的入口导航职责。
4. **链接用相对 Markdown 语法**：`[文字](页名)` 在 wiki 内可点击；**禁止 `[[WikiLink]]` 语法**——它在主仓库浏览 `docs/` 时不可点击，双场景失效。
5. **链接解析陷阱（扁平化的根因）**：wiki 的相对链接以**当前页面所在路径**为基准，且 `_Sidebar.md` 内容被嵌入到每个页面上下文解析；若保留子目录，嵌套页与根页深度不一致，共享侧边栏里的相对链接必有一处断链。→ 据此选择 §3.2 的扁平页名映射。
6. **YAML frontmatter 不被 wiki 渲染管线解析**，会原样显示为文本 → 同步时统一剥离（主仓库副本保留）。
7. **wiki 渲染 mermaid / 表格 / 代码块与主仓库一致**，`mermaid-style.md` 的 init 模板同样适用于 wiki 页。

**文件命名规则**：全小写 + 连字符（`getting-started.md`）；无空格、无下划线（下划线保留给特殊文件）；英文名（中文标题靠 `# 一级标题` 呈现）；长度 ≤ 40 字符；目录层级 ≤ 2（对应页名前缀 ≤ 2 段）。

### 3.2 目标 docs/ 目录树

```
docs/
├── Home.md                          # wiki 着陆页（一一对应页面）
├── _Sidebar.md                      # wiki 侧边栏（特殊文件，不计页面）
├── _Footer.md                       # wiki 页脚（特殊文件，不计页面）
│
├── user-guide/                      # 用户手册 —— 面向使用者与部署者
│   ├── getting-started.md           #   快速开始（环境要求 / Docker / 本地无 Docker / 访问地址）
│   ├── configuration.md             #   配置说明（全部环境变量分组 + .env 示例）
│   ├── deployment.md                #   生产部署（现 docs/deployment.md 迁入 + README 生产部署合并，唯一归属）
│   ├── features-finance.md          #   财经模块详解
│   ├── features-tech.md             #   科技模块详解
│   ├── features-dashboard.md        #   监控仪表盘详解
│   ├── authentication.md            #   认证与登录（SSO 5 家 + /ibadmin 本地管理员 + 角色权限）
│   ├── real-time-sse.md             #   实时推送 SSE（7 事件表 / 心跳 / 重连）
│   ├── api-reference.md             #   API 速览（端点总表 + Swagger/ReDoc 入口）
│   ├── commands.md                  #   常用命令（Makefile 30+ 命令）
│   ├── data-sources.md              #   数据源清单（财经/科技源表 + 数据管道图 + 刷新频率）
│   ├── multi-tenant.md              #   多租户使用说明
│   └── faq.md                       #   常见问题
│
└── dev-guide/                       # 开发者手册 —— 面向贡献者
    ├── architecture-overview.md     #   架构与代码导读（技术栈表 + 架构图 + 项目结构树）
    ├── development.md               #   开发指南（新采集器/新端点/新组件 + 测试）
    ├── cicd.md                      #   CI/CD 与分支策略（细节引用 design/infrastructure.md）
    └── design/                      #   设计文档 —— 现 docs/design/ 整体移入（13 份 + 新增 2 份）
        ├── architecture.md          api.md              database.md
        ├── data-flow.md             content-categories.md data-sources.md
        ├── frontend.md              finance-tab.md      tech-tab.md
        ├── dashboard-tab.md         security.md         admin-login.md
        ├── infrastructure.md        mermaid-style.md    docs-wiki-architecture.md
        └── …（文件名全部保持不变）
```

**路径变化说明（内容完整不丢失）**：

| 现路径 | 新路径 | 对应 wiki 页名 | 说明 |
|--------|--------|---------------|------|
| `docs/design/*.md`（13 份） | `docs/dev-guide/design/*.md` | `dev-guide-design-<名>` | `git mv` 整目录，**内容零修改**；设计文档间均为同目录相对链接（如 `(data-flow.md)`），随目录整体移动依然有效；frontmatter 的 `cross_refs` 只写文件名，不受影响 |
| `docs/deployment.md` | `docs/user-guide/deployment.md` | `user-guide-deployment` | 迁入时与 README"生产部署"章节**合并去重**（见 §3.6），不丢内容 |
| 本任务新增 2 份设计文档（暂在 `docs/design/`） | `docs/dev-guide/design/` | 同上 | 随目录统一移动 |

**页名映射规则（slug）**：`docs/` 相对路径去 `.md`、`/` → `-`。例：`user-guide/configuration.md` → 页面 `user-guide-configuration`；`Home.md` / `_Sidebar.md` / `_Footer.md` 原名保留。此映射为**双射**，满足"严格一一对应"。

> 备选方案 A1（wiki 仓库保留子目录、不压平）：可行但须在同步时把站内链接全部改写为 `/OWNER/REPO/wiki/…` 绝对路径，否则触发 §3.1-5 的侧边栏断链；默认不选，若团队明确要求"wiki 里也看到目录层级"再启用。

**`_Sidebar.md` 结构示例**：

```markdown
**InstantBoard**
* [首页](Home)

**用户手册**
* [快速开始](user-guide-getting-started)
* [配置说明](user-guide-configuration)
* [生产部署](user-guide-deployment)
* [财经模块](user-guide-features-finance)
* [科技模块](user-guide-features-tech)
* [监控仪表盘](user-guide-features-dashboard)
* [认证与登录](user-guide-authentication)
* [实时推送 SSE](user-guide-real-time-sse)
* [API 速览](user-guide-api-reference)
* [常用命令](user-guide-commands)
* [数据源清单](user-guide-data-sources)
* [多租户](user-guide-multi-tenant)
* [常见问题](user-guide-faq)

**开发者手册**
* [架构与代码导读](dev-guide-architecture-overview)
* [开发指南](dev-guide-development)
* [CI/CD](dev-guide-cicd)
* 设计文档
  * [总体架构](dev-guide-design-architecture)
  * [API 设计](dev-guide-design-api)
  * [数据库设计](dev-guide-design-database)
  * [数据流与 SSE](dev-guide-design-data-flow)
  * [内容分类](dev-guide-design-content-categories)
  * [数据源采集](dev-guide-design-data-sources)
  * [前端设计](dev-guide-design-frontend)
  * [财经设计](dev-guide-design-finance-tab)
  * [科技设计](dev-guide-design-tech-tab)
  * [Dashboard 设计](dev-guide-design-dashboard-tab)
  * [安全设计](dev-guide-design-security)
  * [管理员登录](dev-guide-design-admin-login)
  * [基础设施](dev-guide-design-infrastructure)
  * [Mermaid 图示规范](dev-guide-design-mermaid-style)
  * [文档/Wiki 架构](dev-guide-design-docs-wiki-architecture)
```

### 3.3 README 拆分映射表

原则：用户手册面向使用者与部署者（配置、部署、功能用法、FAQ）；开发者手册面向贡献者（架构、开发、CI/CD、设计）。"留" = 保留在精简后 README。

| README 章节（现行号） | 去向 | 目标页面 | 说明 |
|----------------------|------|---------|------|
| 标题 + badges + 目录 (1-41) | **留**（改写） | — | 目录改为"文档索引"表 |
| 项目简介 (43-54) | **留**（精简） + Home.md 镜像 | `Home.md` | 一句话定位 + 两板块表；唯一允许与 Home.md 共享的"一句话简介"（见 §3.6 例外） |
| 核心功能 (56-71) | **留** | — | 功能速览列表保留于 README；各功能的详细说明只在 wiki 功能页 |
| 技术架构 + 架构图 (74-131) | 迁 | `dev-guide/architecture-overview.md` | 技术栈全表与架构图入 wiki；README 只留 4 行"技术要点" |
| 快速开始 (133-210) | 迁（README 留 3 行命令） | `user-guide/getting-started.md` | 环境要求 / override 机制 / 本地无 Docker / 服务清单全部迁出 |
| 配置说明 (214-304) | 迁 | `user-guide/configuration.md` | 环境变量分组全表迁出；README 只留 `cp .env.example .env` 一行 |
| 项目结构 (307-390) | 迁 | `dev-guide/architecture-overview.md` | 目录树并入开发者导读 |
| 功能详解·财经 (396-454) | 迁 | `user-guide/features-finance.md` | 子面板 / NAV 公式 / 指数商品表 / 刷新频率 |
| 功能详解·科技 (457-487) | 迁 | `user-guide/features-tech.md` | 四领域 × 子分类 / 视图模式 / 排序 / 数据源示例 |
| 功能详解·Dashboard (491-516) | 迁 | `user-guide/features-dashboard.md` | 指标类别 / ⚠️未实现清单同样随迁 |
| 功能详解·SSO 认证 (518-577) | 迁 | `user-guide/authentication.md` | 与"本地管理员登录"合并为一页 |
| 功能详解·本地管理员登录 (579-608) | 迁 | `user-guide/authentication.md` | 用法定位写这一页；内部契约细节由链接指向 `design/admin-login.md` |
| 功能详解·实时推送 (612-634) | 迁 | `user-guide/real-time-sse.md` | 7 事件表 + 注释 |
| API 文档 (638-670) | 迁 | `user-guide/api-reference.md` | 端点总表 + Swagger/ReDoc 入口 |
| 常用命令 (674-725) | 迁 | `user-guide/commands.md` | README 只留 `make help` 一行 |
| 测试 (729-749) | 迁 | `dev-guide/development.md`（测试节） | |
| 生产部署 (753-805) | 迁（与 `docs/deployment.md` 合并） | `user-guide/deployment.md` | 部署信息的**唯一归属** |
| CI/CD (809-822) | 迁 | `dev-guide/cicd.md` | 细则引用 `design/infrastructure.md` |
| 数据源 (826-878，含管道图) | 迁 | `user-guide/data-sources.md` | 源清单表 + 采集器表 + 数据管道图（按 `mermaid-style.md` 改造后版本） |
| 多租户 (882-893) | 迁 | `user-guide/multi-tenant.md` | |
| 常见问题 (897-935) | 迁 | `user-guide/faq.md` | |
| 开发指南 (939-979) | 迁 | `dev-guide/development.md` | "设计文档清单"改由 _Sidebar + Home.md 索引承载 |
| 许可证 (982-984) | **留** | — | |

### 3.4 精简后 README 大纲（目标 150–200 行）

1. **标题 + badges + 一句话简介**（~12 行）
2. **项目简介**（~10 行）：一段话 + 财经/科技两板块一行表（与 Home.md 共享此一句，属例外，见 §3.6）
3. **核心功能**（~18 行）：现有要点列表精简保留（正文层面不做强制去 emoji，只保可读）
4. **技术要点**（~8 行）：4 行 bullet（前端 / 后端 / 实时 / 存储）+ 链接 `[架构与代码导读](docs/dev-guide/architecture-overview.md)`；**不再放技术栈全表与架构图**
5. **快速开始**（~14 行）：`git clone` → `bash scripts/setup-dev.sh` → 访问 `http://localhost:3000`；一行注"本地需 Python 3.11+/Node 24+"；链接 `[完整快速开始](docs/user-guide/getting-started.md)`
6. **文档索引**（~45 行）：两栏表——用户手册 13 页 / 开发者手册 3 页 + 设计文档入口，全部为指向 `docs/…` 的相对链接
7. **许可证**（~5 行）

**链接写法（双场景策略）**：

- README → docs：`[配置说明](docs/user-guide/configuration.md)`。README **不参与同步**，此链接只需在主仓库可点击，保持带 `.md` 的仓库相对路径即可；
- docs 页面之间：**源文件一律写带 `.md` 扩展名的相对链接**（如 `[处理管道](../design/data-flow.md#33-数据处理管道设计)`），保证在主仓库浏览时可点击；**同步脚本**推送时按 §3.5 规则将其重写为无扩展、扁平化的 wiki 页名（`(dev-guide-design-data-flow#33-数据处理管道设计)`）。这样两侧都可点击，写作者无需双写；
- docs → 代码文件：写仓库根相对路径（`[manager.py](../backend/app/scheduler/manager.py)`），同步脚本转换为 `https://github.com/OWNER/REPO/blob/main/…` 绝对链接（wiki 上无法解析仓库相对路径）。

### 3.5 同步方案

**选型**：

| 候选 | 结论 |
|------|------|
| A. 手动 `scripts/sync-wiki.sh` | 保留为**本地调试/应急**手段 |
| B. GitHub Actions（push main 且 `docs/**` 变更时自动同步，内部复用 A 的脚本） | **选定**：单一事实来源在 `docs/`，合入即发布，无需人记得手动跑 |
| C. 第三方 sync action | 不选：供应链风险，且转换规则（压平页名 / 剥 frontmatter / 链接重写）本就是自定义逻辑 |

**`scripts/sync-wiki.sh` 文件级设计（逻辑，不必照抄）**：

1. 入参/环境：`DOCS_DIR=docs`、`WIKI_REPO=https://x-access-token:${WIKI_TOKEN}@github.com/OWNER/REPO.wiki.git`、当前 commit SHA；
2. `mktemp -d` 克隆 wiki 仓库；若克隆失败（wiki 从未在页面启用，仓库不存在）→ 报错并提示"先在仓库 Wiki 页签手动创建任意页以初始化"；
3. 清空工作区（保留 `.git`）；
4. 遍历 `docs/**/*.md` 构建**页名映射表**：`slug = 相对路径去 .md、/ → -`；`Home.md`/`_Sidebar.md`/`_Footer.md` 保留原名；校验映射为双射（冲突即失败）；
5. 逐文件转换并写入对应 slug 文件名：
   - 剥离首部 YAML frontmatter（`---` 至 `---`；对本就无 frontmatter 的文件——如现 `deployment.md`——须兼容跳过）；
   - 站内链接重写：按"当前文件所在目录"解析相对路径 → 查映射表 → 替换为 `](slug#锚点)`；映射外的 `.md` 目标视为越界，报错提示；
   - 指向代码的仓库相对链接 → 转 `…/blob/main/…` 绝对链接；
   - mermaid 块与其他内容原样通过；
6. `git add -A`；`git diff --cached --quiet` 无变化则跳过推送；
7. `git commit -m "docs: sync wiki from main@<sha7>"` 并 push；
8. 清理临时目录。

**`.github/workflows/sync-wiki.yml` 设计**：

- 触发：`on: push: branches: [main], paths: ["docs/**"]` + `workflow_dispatch`；
- `permissions: contents: read`；步骤 = checkout → `bash scripts/sync-wiki.sh`；
- 凭据：`env: WIKI_TOKEN: ${{ secrets.WIKI_SYNC_TOKEN }}`。
- **注意**：默认 `GITHUB_TOKEN` 对 `.wiki.git` 的写权限在多数仓库不可靠 → 用具备 repo 范围的 PAT 或部署密钥存入 secret；这是本方案唯一的外部配置依赖。

**单向同步纪律**：wiki 仓库是**生成产物**，只读；任何人在 wiki 网页端的编辑会被下次同步覆盖（在 `_Footer.md` 中注明"本页由 docs/ 自动生成，请勿在 wiki 端直接编辑"）。

### 3.6 去重策略与单一归属

原则：**每条信息只有一个唯一归属页；其余位置只允许放链接**。冲突时以"更靠近实现的一层"为准（设计文档 > dev-guide > user-guide > README）。

| 信息 | 唯一归属 | 其他位置的做法 |
|------|---------|---------------|
| 项目一句话简介 | README §1 | `Home.md` 允许镜像同句（唯一豁免），其余不重复 |
| 环境变量解释 | `user-guide/configuration.md` | README 只留 `cp .env.example .env`；deployment.md 的 .env 完整模板引用配置页，不复述字段含义 |
| 部署步骤 / SSL / 多架构 / 开发-生产差异 | `user-guide/deployment.md` | README 生产部署章节整体移除，只留链接 |
| API 端点明细 | `user-guide/api-reference.md`（速览表）+ `design/api.md`（契约）+ Swagger（运行时权威） | README 只留 Swagger/ReDoc 地址 |
| SSE 内部契约（事件契约 / Redis 频道 / EventRouter） | `dev-guide/design/data-flow.md` | user-guide/real-time-sse.md 只讲用户视角（有什么事件、心跳、断线重连），链回设计文档章节 |
| 架构与技术选型理由 | `dev-guide/design/architecture.md` | `dev-guide/architecture-overview.md` 只放导读 + 图，链回 |
| 数据源清单 | `user-guide/data-sources.md` | dev-guide 不重复列表，只讲采集器实现并链接 |
| Mermaid 风格 | `dev-guide/design/mermaid-style.md` | 不重复 |

现存重叠的处置：`README 生产部署` + `README 配置章节的 SSO 凭据示例` + `docs/deployment.md` 三处按上表收敛——SSO/JWT/各提供商凭据示例归 `configuration.md`，部署流程与完整生产 `.env` 模板归 `deployment.md`，字段解释只在 `configuration.md`。

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| wiki 页名结构 | **压平为连字符 slug**（A2），docs/ 侧保留子目录 | _Sidebar 相对链接在各深度页面统一可点（§3.1-5）；映射双射，仍满足一一对应 |
| 设计文档位置 | 物理移入 `docs/dev-guide/design/` | 侧边栏归入开发者手册；文件名不变 → 同目录互链与 `cross_refs` 零成本 |
| 链接形态 | 源文件带 `.md` 相对链接，同步时重写；禁用 `[[wiki]]` 语法 | 主仓库浏览与 wiki 双场景均可点击，写作者单写一份 |
| frontmatter | 主仓库保留、wiki 侧剥离 | wiki 渲染管线不解析 YAML，原样显示会污染页面 |
| 同步机制 | GitHub Actions（`docs/**` 变更触发）+ 同一脚本本地可跑 | 合入即发布；逻辑单一来源 |
| 同步凭据 | 专用 `WIKI_SYNC_TOKEN` secret | 默认 GITHUB_TOKEN 对 wiki 仓库写权限不可靠 |
| README 角色 | 名片 + 索引，零细节 | 已拍板：README 与 wiki 不重复承载相同信息 |
| `Home.md` | 独立撰写，不复用 README | README 的徽章与仓库相对链接在 wiki 不适用；仅豁免共享"一句话简介" |
| 文件命名 | 小写连字符 | §3.1 机制要求；标题中文化靠 `# 一级标题` |

## 5. 边界情况

- **wiki 仓库未初始化**：从未启用过 Wiki 时 `.wiki.git` 不存在，脚本首次运行需先在网页端创建任意页；文档中明示该前置步骤。
- **页面改名/移动无重定向**：git 侧改名不会像网页端改名那样自动留重定向 → 改名时须在 PR 内检查外部（其他仓库、搜索引擎）引用；站内引用由同步脚本自动跟随新页名。
- **中文锚点**：GitHub 按中文字符原样生成 fragment；转换时保留原文不做转写；建议标题用"编号 + 短中文"降低锚点脆性。
- **无 frontmatter 的文档**（如现 `docs/deployment.md`）：剥离逻辑必须兼容跳过，不得误删首个 `---` 分隔线。
- **越界链接**：站内链接目标不在映射表中（指向 docs/ 之外或拼写错误）→ 同步时显式报错，不静默放行。
- **emoji**：本次去 emoji 决策范围为 mermaid 节点标签（见 `mermaid-style.md`）；正文表格中的 🟢/⚠️ 等行文约定暂不动。
- **wiki 无全文检索**：导航全靠 _Sidebar + Home 索引，因此用户手册页数控制在 ~13 页、命名必须见名知意。
- **大表格渲染**：设计文档中超宽表格在 wiki 会自动横向滚动，无需特殊处理。
- **Actions 未触发**：`paths: docs/**` 过滤下，仅改代码不触发同步；`workflow_dispatch` 作为手动补偿入口。
- **并行冲突**：wiki 仓库单向生成，不存在并发写冲突；若人工误推了 wiki 仓库，下一次同步整体覆盖即自愈。

## 6. 施工顺序与模块依赖

建议顺序（供 coder 参照）：

1. 建 `docs/user-guide/` 13 页与 `docs/dev-guide/` 3 页（内容从 README 对应章节**移动**，按 §3.3 表），写 `Home.md` / `_Sidebar.md` / `_Footer.md`；
2. `git mv docs/design docs/dev-guide/design`、`git mv docs/deployment.md docs/user-guide/deployment.md` 并与 README 生产部署章节合并去重；
3. 精简 README 至 §3.4 大纲（所有迁出的章节此时才可删除）；
4. 落地 `scripts/sync-wiki.sh` 与 `.github/workflows/sync-wiki.yml`，在测试分支先行验证一轮同步（含链接重写正确性）；
5. 按 `mermaid-style.md` §5 清单分批改造图块（与上述步骤相互独立，可并行）。

依赖关系：

- → `mermaid-style.md`：图改造不改变文件名与链接，两者可并行，但 PR1 需顺带验证 wiki 端 mermaid 渲染；
- → `infrastructure.md`：新增的 `sync-wiki.yml` 工作流应登记进其 §3.3 CI/CD 清单与 `dev-guide/cicd.md`；
- → `architecture.md` / `frontend.md`：无内容依赖，仅受目录移动的路径更新影响（README 与各文档中对 `docs/design/…` 的引用需按 §3.2 映射表批量替换）。
