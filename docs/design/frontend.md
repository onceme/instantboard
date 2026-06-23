---
version: 1.0
author: designer
date: 2026-06-23
status: draft
cross_refs: [architecture.md, api.md, finance-tab.md, tech-tab.md, dashboard-tab.md]
---

# InstantBoard 前端 UI 设计

## 1. 目标

定义 InstantBoard Vue 3 前端的完整 UI 设计，包括项目结构、组件层级、响应式布局策略、导航方案（含替代方案分析）、三个主 Tab 的详细界面、主题支持和移动端适配。

## 2. 方案概述

采用 **侧边导航 + 动态面板** 替代传统 Tab，通过 Vue 3 Composition API + Pinia 状态管理 + SSE 实时更新，实现响应式暗/亮色主题的实时信息聚合面板。

## 3. 详细设计

### 3.1 Vue.js 项目结构

```mermaid
graph LR
  subgraph src["frontend/src/"]
    App["App.vue<br/>根组件: Sidebar + MainContent + SSE初始化"]
    MainFile["main.ts<br/>createApp + Pinia + Router + ThemePlugin"]

    subgraph router["router/"]
      RouterIndex["index.ts<br/>路由: / → Finance, /tech, /dashboard, /settings, /login"]
    end

    subgraph views["views/ — 页面级组件"]
      FinanceView["FinanceView.vue"]
      TechView["TechView.vue"]
      DashboardView["DashboardView.vue"]
      SettingsView["SettingsView.vue"]
      LoginView["LoginView.vue"]
    end

    subgraph compLayout["components/layout/ — 布局组件"]
      AppSidebar["AppSidebar.vue<br/>侧边导航栏"]
      AppHeader["AppHeader.vue<br/>顶部栏 用户信息+主题切换"]
      AppFooter["AppFooter.vue<br/>底部信息"]
      MainContent["MainContent.vue<br/>主内容区域容器"]
    end

    subgraph compCommon["components/common/ — 公共组件"]
      MessageCard["MessageCard.vue<br/>消息卡片 标题、摘要、来源、时间、链接"]
      SearchBar["SearchBar.vue<br/>搜索框"]
      LoadingSpinner["LoadingSpinner.vue"]
      ErrorAlert["ErrorAlert.vue"]
      Pagination["Pagination.vue"]
      EmptyState["EmptyState.vue"]
      ConfirmationDialog["ConfirmationDialog.vue"]
      ThemeToggle["ThemeToggle.vue"]
    end

    subgraph compFinance["components/finance/ — 财经专用组件"]
      MarketTicker["MarketTicker.vue<br/>顶部市场指数滚动条"]
      WatchlistPanel["WatchlistPanel.vue<br/>自选列表侧面板"]
      StockDetail["StockDetail.vue<br/>股票详情卡片"]
      FundDetail["FundDetail.vue<br/>基金详情卡片"]
      NAVCalculator["NAVCalculator.vue<br/>NAV估值显示"]
      MarketIndexCard["MarketIndexCard.vue<br/>单个市场指数卡片"]
      CommodityCard["CommodityCard.vue<br/>大宗商品卡片"]
      FinanceSearch["FinanceSearch.vue<br/>财经搜索组件"]
      FinanceSubNav["FinanceSubNav.vue<br/>财经子导航面板切换"]
      QuoteChart["QuoteChart.vue<br/>行情迷你图表 sparkline"]
      FinanceGrid["FinanceGrid.vue<br/>财经主网格布局"]
    end

    subgraph compTech["components/tech/ — 科技专用组件"]
      TopicFilter["TopicFilter.vue<br/>话题标签过滤器"]
      NewsFeed["NewsFeed.vue<br/>新闻流列表"]
      NewsCard["NewsCard.vue<br/>科技新闻卡片"]
      TopicTag["TopicTag.vue<br/>话题标签组件"]
      CategoryPanel["CategoryPanel.vue<br/>四大领域面板"]
      TrendChart["TrendChart.vue<br/>话题热度图"]
    end

    subgraph compDashboard["components/dashboard/ — Dashboard专用组件"]
      HealthPanel["HealthPanel.vue<br/>健康总览面板"]
      MetricsChart["MetricsChart.vue<br/>指标图表"]
      ServiceStatus["ServiceStatus.vue<br/>服务状态卡片"]
      SystemInfo["SystemInfo.vue<br/>系统信息卡片"]
      DataSourceHealth["DataSourceHealth.vue<br/>数据源健康表"]
      SchedulerPanel["SchedulerPanel.vue<br/>定时任务面板"]
      SSEConnections["SSEConnections.vue<br/>SSE连接统计"]
      ResourceUsage["ResourceUsage.vue<br/>资源使用图表"]
    end

    subgraph compSettings["components/settings/ — 设置专用组件"]
      CategoryEditor["CategoryEditor.vue<br/>分类管理"]
      SourceEditor["SourceEditor.vue<br/>数据源管理"]
      ProfileSettings["ProfileSettings.vue<br/>用户设置"]
      ThemeToggleSet["ThemeToggle.vue<br/>主题切换"]
    end

    subgraph stores["stores/ — Pinia状态管理"]
      authStore["auth.ts<br/>认证状态 token, user, login/logout"]
      financeStore["finance.ts<br/>财经数据 quotes, watchlist, indices, commodities"]
      techStore["tech.ts<br/>科技数据 news, topics"]
      dashboardStore["dashboard.ts<br/>Dashboard数据 system, services, sources"]
      settingsStore["settings.ts<br/>设置数据 categories, sources, theme"]
      sseStore["sse.ts<br/>SSE连接管理 channels, events"]
    end

    subgraph composables["composables/ — Composition API复用逻辑"]
      useSSE["useSSE.ts<br/>SSE连接建立、事件处理、自动重连"]
      useAuth["useAuth.ts<br/>登录、刷新token、权限检查"]
      useFetch["useFetch.ts<br/>REST API请求封装 axios wrapper"]
      useResponsive["useResponsive.ts<br/>响应式断点检测"]
      useTheme["useTheme.ts<br/>主题切换逻辑"]
      useWatchlist["useWatchlist.ts<br/>自选列表操作"]
      useInfiniteScroll["useInfiniteScroll.ts<br/>无限滚动加载"]
    end

    subgraph types["types/ — TypeScript类型定义"]
      financeType["finance.ts<br/>StockQuote, FundNAV, MarketIndex, Commodity..."]
      techType["tech.ts<br/>NewsItem, TopicTag..."]
      dashboardType["dashboard.ts<br/>SystemInfo, ServiceStatus..."]
      commonType["common.ts<br/>ApiResponse, PaginatedResponse, SSEEvent..."]
    end

    subgraph utils["utils/"]
      apiUtil["api.ts<br/>axios实例配置 baseURL, interceptors"]
      formatUtil["format.ts<br/>数字格式化 currency, percent, 日期格式化"]
      constantsUtil["constants.ts<br/>断点常量、默认配置"]
    end

    subgraph styles["styles/"]
      variables["variables.scss<br/>CSS变量 颜色、间距、字体、断点"]
      globalStyle["global.scss<br/>全局样式"]
      mixins["mixins.scss<br/>SCSS mixins 响应式、主题"]
      dark["dark.scss<br/>暗色主题覆盖"]
      light["light.scss<br/>亮色主题覆盖"]
    end
  end

  App --> router
  App --> views
  App --> compLayout
  App --> stores
  App --> composables


### 3.2 组件层级设计

```mermaid
graph TD
  AppVue["App.vue"]

  AppVue --> SideVue["AppSidebar.vue — 侧边导航"]
  SideVue --> NavItems["导航项: Finance / Tech / Dashboard / Settings"]
  SideVue --> LogoAppName["Logo + App Name"]
  SideVue --> ThemeSide["ThemeToggle"]
  SideVue --> UserLogout["UserAvatar + Logout"]

  AppVue --> HeaderVue["AppHeader.vue — 顶部栏"]
  HeaderVue --> PageTitle["当前页面标题"]
  HeaderVue --> SearchGlobal["SearchBar — 全局搜索，在财经页增强"]
  HeaderVue --> Notification["通知提示"]
  HeaderVue --> ThemeMobile["ThemeToggle — 移动端"]

  AppVue --> MainVue["MainContent.vue — 路由出口"]

  MainVue --> FinView["FinanceView.vue"]
  FinView --> MktTicker["MarketTicker.vue — 横向滚动市场指数条，固定顶部"]
  FinView --> FinSubNav["FinanceSubNav.vue — 子区域切换"]
  FinSubNav --> SubOpts["子选项: Overview | Watchlist | Search | Market Indices | Commodities"]
  FinView --> FinGrid["FinanceGrid.vue — 主内容网格"]
  FinGrid --> AreaMain["区域1: 主面板 根据SubNav切换"]
  AreaMain --> OverviewPanel["Overview → WatchlistPanel + 重点关注项"]
  AreaMain --> WatchlistFull["Watchlist → WatchlistPanel 完整"]
  AreaMain --> SearchPanel["Search → FinanceSearch + 结果列表"]
  AreaMain --> MarketPanel["Market → MarketIndexCard × N"]
  AreaMain --> CommodPanel["Commodities → CommodityCard × N"]
  FinGrid --> AreaRight["区域2: 右侧侧边栏 固定"]
  AreaRight --> WatchMini["WatchlistPanel — 迷你版，始终可见"]
  AreaRight --> NAVCalc["NAVCalculator — 关注的基金估值"]
  AreaRight --> TopNewsCards["Top News Cards"]
  FinView --> StockDetailM["StockDetail.vue — 模态框/抽屉"]
  FinView --> FundDetailM["FundDetail.vue — 模态框/抽屉"]
  FinView --> QuoteChartM["QuoteChart.vue — sparkline图"]

  MainVue --> TechView["TechView.vue"]
  TechView --> TopicFilter["TopicFilter.vue — 横向话题标签栏"]
  TechView --> CatPanel4["CategoryPanel.vue × 4 — 四大领域面板"]
  CatPanel4 --> PanelTitle["面板标题 + 图标 + 新增数量"]
  CatPanel4 --> NewsCardsPerPanel["NewsCard × N — 每个面板内"]
  CatPanel4 --> ExpandCollapse["展开/折叠"]
  TechView --> NewsFeedAll["NewsFeed.vue — 合并全部新闻流"]
  TechView --> TrendChartView["TrendChart.vue — 话题热度趋势"]
  TechView --> NewsCardSingle["NewsCard.vue — 单条新闻卡片"]
  NewsCardSingle --> CardTitleSummary["标题 + 摘要"]
  NewsCardSingle --> CardSourceTime["来源名称 + 发布时间"]
  NewsCardSingle --> CardTopicTags["TopicTag × N"]
  NewsCardSingle --> CardOrigLink["原文链接"]
  NewsCardSingle --> CardImage["图片 可选"]

  MainVue --> DashView["DashboardView.vue"]
  DashView --> HealthPanelTop["HealthPanel.vue — 总览面板，顶部"]
  HealthPanelTop --> StatusLight["整体状态指示灯 Green/Yellow/Red"]
  HealthPanelTop --> KeyNumbers["关键数字摘要 SSE连接数、活跃数据源数、24h事件数"]
  DashView --> SysInfoResUse["SystemInfo.vue + ResourceUsage.vue — 系统信息行"]
  DashView --> ServiceStatusGrid["ServiceStatus.vue × N — 服务卡片网格"]
  DashView --> DSHealthTable["DataSourceHealth.vue — 数据源健康表格"]
  DashView --> SchedPanel["SchedulerPanel.vue — 定时任务列表"]
  DashView --> SSEConnChart["SSEConnections.vue — SSE统计图表"]
  DashView --> MetricsChartView["MetricsChart.vue — 各指标实时曲线，Chart.js"]

  MainVue --> SetView["SettingsView.vue"]
  SetView --> CatEditor["CategoryEditor.vue — 分类CRUD"]
  SetView --> SrcEditor["SourceEditor.vue — 数据源CRUD"]
  SetView --> ProfSettings["ProfileSettings.vue — 用户信息"]
  SetView --> ThemeSetView["ThemeToggle.vue"]

  MainVue --> LoginView["LoginView.vue"]
  LoginView --> SSOButtons["SSO按钮 × 5 — Google, Azure AD, GitHub, Apple, Facebook"]
```

### 3.3 响应式布局策略

**基于 W3Schools 数据的断点设计**:

| 断点名称 | CSS 断点 | 目标分辨率 | 占比 | 布局策略 |
|---------|---------|----------|------|---------|
| **xs** | ≤768px | 手机/小平板 | ~6% | 单列，侧边栏折叠为抽屉，MarketTicker 隐藏 |
| **sm** | 768-1024px | 1024×768, 1280×800 | ~2% | 两列简化，侧边栏折叠，主内容全宽 |
| **md** | 1024-1366px | 1366×768 | ~18% | 两列，侧边栏展开，右侧面板简化 |
| **lg** | 1366-1920px | 1920×1080 | ~18% | 三列 (侧边栏 + 主内容 + 右侧面板) |
| **xl** | ≥1920px | >1920×1080 | ~47% | 三列，间距加大，右侧面板可展开 |

**CSS 变量定义** (`variables.scss`):
```scss
// 断点
$breakpoint-xs: 0;
$breakpoint-sm: 768px;
$breakpoint-md: 1024px;
$breakpoint-lg: 1366px;
$breakpoint-xl: 1920px;

// 响应式 mixins
@mixin respond-to($bp) {
  @if $bp == xs { @media (max-width: #{$breakpoint-sm - 1px}) { @content; } }
  @if $bp == sm { @media (min-width: $breakpoint-sm) and (max-width: #{$breakpoint-md - 1px}) { @content; } }
  @if $bp == md { @media (min-width: $breakpoint-md) and (max-width: #{$breakpoint-lg - 1px}) { @content; } }
  @if $bp == lg { @media (min-width: $breakpoint-lg) and (max-width: #{$breakpoint-xl - 1px}) { @content; } }
  @if $bp == xl { @media (min-width: $breakpoint-xl) { @content; } }
}

// 间距
$sidebar-width: 220px;
$sidebar-collapsed-width: 60px;
$right-panel-width: 300px;
$content-padding: 16px;
$card-gap: 12px;

// 主题色 (通过 CSS 变量实现暗/亮切换)
:root {
  // 亮色
  --bg-primary: #FFFFFF;
  --bg-secondary: #F5F7FA;
  --bg-card: #FFFFFF;
  --text-primary: #1A1A2E;
  --text-secondary: #6B7280;
  --border-color: #E5E7EB;
  --accent: #3B82F6;
  --success: #10B981;
  --warning: #F59E0B;
  --danger: #EF4444;
  --sidebar-bg: #1A1A2E;
  --sidebar-text: #E5E7EB;
}

[data-theme="dark"] {
  --bg-primary: #0F172A;
  --bg-secondary: #1E293B;
  --bg-card: #1E293B;
  --text-primary: #E2E8F0;
  --text-secondary: #94A3B8;
  --border-color: #334155;
  --accent: #60A5FA;
  --success: #34D399;
  --warning: #FBBF24;
  --danger: #F87171;
  --sidebar-bg: #0F172A;
  --sidebar-text: #94A3B8;
}
```

### 3.4 导航方案分析

#### 方案对比

| 方案 | 优点 | 缺点 | 适合场景 |
|------|------|------|---------|
| **A: 传统 Tab** | 简单熟悉、实现容易 | 固定顶部占用空间、子tab嵌套混乱、扩展性差 | 3个简单页 |
| **B: 侧边导航** | 可折叠节省空间、子菜单自然、扩展性好 | 宽屏才好用、移动端需要抽屉模式 | 多页面/多功能 |
| **C: 拖拽式仪表盘** | 自由度高、个性化 | 实现复杂、布局易混乱、移动端困难 | 高度定制化需求 |
| **D: 侧边导航 + 面板切换** | 侧边导航优点 + 内容区灵活切换子面板 | 需要设计面板切换逻辑 | **InstantBoard 最佳选择** |

#### 最终推荐: **方案 D — 侧边导航 + 动态面板**

理由:
1. InstantBoard 有 3 个主要功能区 + 设置页，侧边导航比 tab 更优雅
2. Finance 页需要多个子面板 (Overview/Watchlist/Search/Indices)，侧边导航配合子面板切换比嵌套 tab 更清晰
3. 侧边导航可折叠，在 ≤1366px 分辨率下节省空间 (占比 18%+)
4. 未来新增模块只需在侧边导航添加一项，不需重构 tab 系统

**侧边导航设计**:

```mermaid
graph LR
  subgraph fullpage["全页面布局"]
    subgraph sidebar["AppSidebar 侧边导航"]
      Logo["IB Logo"]
      Sep1["────"]
      NavFin["💰 Finance"]
      NavTech["🔬 Tech"]
      NavDash["📊 Dashboard"]
      NavSet["⚙️ Settings"]
      Sep2[""]
      ThemeBtn["🌙 ThemeToggle"]
      Sep3[""]
      UserBtn["👤 UserAvatar + Logout"]
    end

    subgraph mainarea["主区域"]
      subgraph header["AppHeader"]
        HeaderContent["页面标题 + SearchBar"]
      end

      subgraph contentrow["内容行"]
        MainContent["主内容区域<br/>根据SubNav切换"]
        RightPanel["右侧面板<br/>Watchlist Mini<br/>NAV Estimate"]
      end

      subgraph ticker["底部固定 财经页"]
        MarketTicker["MarketTicker 财经页固定"]
      end
    end
  end
```

**移动端 (<768px)**: 侧边栏变为底部抽屉 (drawer)，通过 hamburger 菜单触发

### 3.5 Tab1 财经界面详细设计

见 [finance-tab.md](finance-tab.md) §4 完整设计。

**核心布局**:
- 顶部固定: MarketTicker (横向滚动指数条)
- 主内容区: FinanceSubNav (子面板切换) + 动态内容
- 右侧面板 (lg+): WatchlistMini + NAVEstimate + TopNews
- 搜索交互: 模态框/抽屉式 StockDetail / FundDetail

### 3.6 Tab2 科技界面详细设计

见 [tech-tab.md](tech-tab.md) §4 完整设计。

**核心布局**:
- 顶部: TopicFilter (话题标签栏，可切换/多选)
- 主体: 四大领域 CategoryPanel (两列网格)
- 每个面板可展开/折叠，内含 NewsCard 列表
- 底部可选: 合并全部新闻流 (NewsFeed) + TrendChart

### 3.7 Tab3 Dashboard 界面详细设计

见 [dashboard-tab.md](dashboard-tab.md) §4 完整设计。

**核心布局**:
- 顶部: HealthPanel (总览，关键数字)
- 中间: 2×2 卡片网格 (SystemInfo, ServiceStatus×2, ResourceUsage)
- 下方: DataSourceHealth 表 + SchedulerPanel
- 最下方: SSEConnections + MetricsChart

### 3.8 暗色/亮色主题支持

**实现方案**: CSS 变量 + `data-theme` 属性切换

```
ThemeToggle → useTheme composable:
  - 读取 localStorage 主题偏好
  - 检测系统 prefers-color-scheme
  - 切换 document.documentElement.dataset.theme
  - Pinia settings.ts 记录主题状态
  - SSE 不受主题影响

优先级: localStorage > 系统偏好 > 默认亮色
```

**暗色主题设计要点**:
- 背景: 深蓝灰 (#0F172A / #1E293B)，不是纯黑
- 文字: 高对比度浅色 (#E2E8F0)
- 卡片: 微妙边框区分 (#334155)，不用阴影
- 图表: 使用透明填充色，网格线浅色

**涨跌配色方案 (可切换)**:

默认采用**中国配色** (红涨绿跌)，用户可在 SettingsView → ProfileSettings 中切换为**国际配色** (绿涨红跌)。配色方案存储在 `users.preferences.colorScheme` 中，优先级: 用户偏好 > 租户配置覆盖 > 默认中国配色。

| 配色方案 | 上涨 (positive) | 下跌 (negative) | 适用场景 |
|---------|----------------|----------------|---------|
| **中国配色 (默认)** | 🔴 红色 `--up-color: #EF4444` / 暗色 `#F87171` | 🟢 绿色 `--down-color: #10B981` / 暗色 `#34D399` | 中国A股/港股用户直觉 |
| **国际配色 (可选)** | 🟢 绿色 `--up-color: #10B981` / 暗色 `#34D399` | 🔴 红色 `--down-color: #EF4444` / 暗色 `#F87171` | 美股/国际市场用户习惯 |

CSS 变量实现:
```scss
:root {
  --up-color: #EF4444;    // 中国配色默认: 红涨
  --down-color: #10B981;  // 中国配色默认: 绿跌
}
[data-theme="dark"] {
  --up-color: #F87171;
  --down-color: #34D399;
}
[data-color-scheme="international"] {
  --up-color: #10B981;    // 国际配色: 绿涨
  --down-color: #EF4444;  // 国际配色: 红跌
}
[data-theme="dark"][data-color-scheme="international"] {
  --up-color: #34D399;
  --down-color: #F87171;
}
```

前端组件使用 `var(--up-color)` 和 `var(--down-color)` 替代硬编码颜色，所有涨跌相关 UI (WatchlistPanel、MarketIndexCard、CommodityCard、StockDetail) 统一使用 CSS 变量。

### 3.9 移动端适配策略

| 组件 | 桌面 (≥1024px) | 平板 (768-1024px) | 手机 (<768px) |
|------|---------------|-----------------|-------------|
| Sidebar | 展开 220px | 折叠 60px (图标) | 隐藏，汉堡菜单抽屉 |
| MarketTicker | 横向滚动全宽 | 横向滚动，减少项 | 隐藏，数据在主面板内 |
| 右侧面板 | 300px 固定 | 隐藏，内容移入主面板 | 隐藏 |
| FinanceGrid | 三列网格 | 两列 | 单列 |
| NewsCard | 完整 (标题+摘要+图) | 标题+摘要 (无图) | 标题+来源+时间 (极简) |
| SearchBar | 宽搜索框 | 较窄 | 全宽，点击展开 |
| Dashboard 卡片 | 2×2网格 | 2列 | 单列堆叠 |

**触控优化**:
- 所有可点击元素最小 44×44px 触控区域
- 滑动手势: 左滑删除自选项，右滑展开详情
- MarketTicker 移动端改为竖向列表
- SSE 连接在移动端降低刷新频率 (30s → 60s 心跳)

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 导航方案 | 侧边导航 + 动态面板 | 比传统Tab扩展性更好，子面板比子Tab更灵活 |
| 状态管理 | Pinia | Vue 3官方推荐，比Vuex更简洁 |
| CSS策略 | SCSS变量 + CSS变量 | SCSS编译时变量(断点/mixin) + CSS运行时变量(主题) |
| 图表库 | Chart.js | 轻量(~60KB gzip)、足够满足Dashboard需求、无D3的复杂性 |
| 图标库 | Lucide Icons | 轻量SVG图标、Vue组件式使用、树摇优化 |
| HTTP客户端 | axios | 拦截器方便(JWT注入)、错误处理统一 |
| 响应式策略 | 5断点分级 | 精确覆盖W3Schools分辨率分布 |
| 主题切换 | CSS变量 + data-theme | 运行时切换、无需重建CSS、性能好 |

## 5. 边界情况

- **SSE 断线重连**: useSSE composable 处理重连逻辑，重连后自动补拉缺失数据 (since参数)
- **JWT 过期**: axios interceptor 检测 401 → 自动 refresh → 失败跳转登录页
- **低端设备**: 检测 performance.memory，降级：减少图表、降低刷新频率
- **离线**: Service Worker 缓存基础框架，显示最后成功获取的数据 + 离线提示

## 6. 与其他模块的依赖

- → [api.md](api.md): 前端消费的所有API端点
- → [finance-tab.md](finance-tab.md): 财经界面详细设计
- → [tech-tab.md](tech-tab.md): 科技界面详细设计
- → [dashboard-tab.md](dashboard-tab.md): Dashboard界面详细设计