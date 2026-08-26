---
version: 1.2
author: designer
date: 2026-08-26
status: draft
cross_refs: [frontend.md, api.md, data-sources.md, database.md, data-flow.md, content-categories.md]
---

# InstantBoard 科技模块详细设计

## 目录

1. 目标
2. 方案概述
3. 详细设计
   - 3.1 四大领域定义与子分类
   - 3.2 各领域数据源选型
   - 3.3 新闻流展示设计
   - 3.4 话题标签与分类体系
   - 3.5 推荐算法/排序策略
   - 3.6 数据刷新频率
   - 3.7 新闻查询与过滤功能
   - 3.8 SSE 事件类型定义 (科技频道)
4. 关键决策
5. 边界情况
6. 与其他模块的依赖

## 1. 目标

定义 InstantBoard 科技聚合 Tab 的完整功能设计，覆盖四大领域（机器人、AI、大规模嵌入式、太空科技）的信息聚合、数据源选型、展示方案、话题标签体系、排序策略和刷新机制。

## 2. 方案概述

提供 **话题驱动的信息聚合面板**，顶部 TechSubNav 切换一级领域，四大领域以可展开/折叠的 CategoryPanel 展示（双视图），TopicFilter 标签栏支持二级子分类单选过滤，新闻卡片提供热度/时间/相关性三种排序，SSE 实时推送新条目。

## 3. 详细设计

### 3.1 四大领域定义与子分类

#### 3.1.1 领域总览

| 领域 | slug | 图标 | 主题色 | 核心关注话题 |
|------|------|------|--------|-------------|
| **机器人** | robotics | 🤖 | #8B5CF6 (紫) | 人形机器人、工业机器人、协作机器人、自动驾驶、无人机、机器人OS |
| **人工智能** | ai | 🧠 | #3B82F6 (蓝) | 大语言模型、生成式AI、AI芯片、AI伦理、多模态、AI Agent |
| **大规模嵌入式** | embedded | ⚡ | #F59E0B (橙) | IoT、边缘计算、RISC-V、实时OS、FPGA、芯片设计、嵌入式AI |
| **太空科技** | space | 🚀 | #10B981 (绿) | 商业航天、卫星互联网、深空探测、太空站、火箭回收、太空制造 |

#### 3.1.2 子分类与关注话题详细定义

**机器人领域子分类**:

| 子分类 | slug | 关注话题 |
|--------|------|---------|
| 人形机器人 | humanoid | Atlas, Optimus, Figure 01, 双足行走, 运动控制 |
| 工业机器人 | industrial | 焊接/装配/搬运, AMR, 柔性制造, 产线自动化 |
| 协作机器人 | cobot | UR, Franka, 安全标准ISO 10218, 人机协作 |
| 自动驾驶 | autonomous-driving | L4/L5, Waymo, Tesla FSD, 激光雷达, V2X |
| 无人机 | drone | eVTOL, 农业植保, 快递物流, 反无人机 |
| 机器人 OS/软件 | robot-software | ROS2, MoveIt, Isaac Sim, 仿真平台 |

**人工智能领域子分类**:

| 子分类 | slug | 关注话题 |
|--------|------|---------|
| 大语言模型 | llm | GPT, Claude, Llama, Gemini, Sora, 推理能力, 长上下文 |
| 生成式AI | generative-ai | 图像生成(DALL-E/Midjourney), 视频生成, 3D生成, 音乐AI |
| AI芯片/硬件 | ai-hardware | NVIDIA GPU, TPU, NPU, Groq, 存算一体, HBM |
| AI伦理与治理 | ai-ethics | AI安全, 对齐问题, 监管法规, AI偏见, 可解释AI |
| 多模态AI | multimodal | 视觉语言模型, 语音AI, 跨模态理解, OCR |
| AI Agent/应用 | ai-agent | AutoGPT, LangChain, RAG, Coding Agent, AI助手 |

**大规模嵌入式领域子分类**:

| 子分类 | slug | 关注话题 |
|--------|------|---------|
| IoT与边缘计算 | iot-edge | MQTT, CoAP, 边缘推理, TinyML, 数字孪生 |
| RISC-V与处理器 | risc-v | 开源指令集, RV32/RV64, 向量扩展, 芯片实现 |
| 实时操作系统 | rtos | FreeRTOS, Zephyr, RTLinux, 时序保证 |
| FPGA与硬件加速 | fpga | Xilinx/Altera, HLS, 部分重配置, ASIC替代 |
| 芯片设计 | chip-design | EDA工具, 后摩尔定律, 3D封装, Chiplet |
| 嵌入式AI | embedded-ai | 模型量化, 端侧推理, NPU微架构, 低功耗AI |

**太空科技领域子分类**:

| 子分类 | slug | 关注话题 |
|--------|------|---------|
| 商业航天 | commercial-space | SpaceX, Blue Origin, Rocket Lab, 发射市场 |
| 卫星互联网 | satellite-internet | Starlink, Kuiper, OneWeb, 相控阵天线 |
| 深空探测 | deep-space | 月球基地, Mars, 望远镜, 行星科学 |
| 空间站与在轨服务 | orbital | ISS, Tiangong, 在轨制造, 空间碎片清除 |
| 火箭技术 | rocket-tech | 可回收, 液氧甲烷, 固体火箭, 发动机创新 |
| 太空制造与资源 | space-manufacturing | 太空3D打印, 月球采矿, 原位资源利用ISRU |

### 3.2 各领域数据源选型

> 注：下表频率列为种子配置的 `refresh_interval_seconds`；`web_scrape` 类型源均 `is_active=False`（无 web_scrape 采集器），仅作模板保留；Reddit（social）种子已激活（公开 JSON、无需凭据；`config.library=reddit` + `subreddits` 配置）。

#### 3.2.1 机器人领域数据源

| 数据源名称 | 类型 | URL/来源 | 频率 | 状态 |
|-----------|------|---------|------|------|
| **HackerNews-Robotics** | RSS | hnrss.org/newest?q=robot+robotics+drones | 120s | ✅ active |
| **The Robot Report (Google News)** | RSS | news.google.com/rss/search?q=robotics+robots+automation | 300s | ✅ active（注：实际是 Google News 搜索 RSS，非 robotreport 官方源） |
| **IEEE Robotics** | RSS | ieee.org/publications/rss_feed.xml | 86400s | ✅ active |
| **ROS Blog** | RSS | ros.org/blog/rss.xml | 604800s | ✅ active |
| **Automotive News** | Web抓取 | autonews.com | 86400s | ⛔ inactive（无 web_scrape 采集器） |

#### 3.2.2 AI 领域数据源

| 数据源名称 | 类型 | URL/来源 | 频率 | 状态 |
|-----------|------|---------|------|------|
| **MIT Tech Review AI Feed** | RSS | technologyreview.com/feed | 300s | ✅ active |
| **HackerNews-AI/ML** | RSS | hnrss.org/newest?q=AI+machine+learning+LLM | 120s | ✅ active |
| **Arxiv CS.AI** | RSS | arxiv.org/rss/cs.AI | 1800s | ✅ active |
| **OpenAI Blog** | Web抓取 | openai.com/blog | 1800s | ⛔ inactive |
| **The Batch (deeplearning.ai)** | RSS | deeplearning.ai/the-batch | 604800s | ✅ active |

#### 3.2.3 大规模嵌入式领域数据源

| 数据源名称 | 类型 | URL/来源 | 频率 | 状态 |
|-----------|------|---------|------|------|
| **Hackaday** | RSS | hackaday.com/blog/feed | 300s | ✅ active |
| **Embedded.com** | RSS | embedded.com/**feed/** | 300s | ✅ active |
| **RISC-V International Blog** | Web抓取 | riscv.org/blog | 1800s | ⛔ inactive |
| **EE Times** | RSS | eetimes.com/rss | 86400s | ✅ active |
| **Zephyr Project Blog** | RSS | zephyrproject.org/blog/rss | 2592000s | ✅ active |

#### 3.2.4 太空科技领域数据源

| 数据源名称 | 类型 | URL/来源 | 频率 | 状态 |
|-----------|------|---------|------|------|
| **SpaceNews** | RSS | spacenews.com/feed | 300s | ✅ active |
| **NASA News** | RSS | nasa.gov/rss/dyn/breaking_news.rss | 1800s | ✅ active |
| **SpaceX Updates** | Web抓取 | spacex.com/updates | 1800s | ⛔ inactive |
| **ESA News** | RSS | esa.int/RSS | 1800s | ✅ active |
| **Ars Technica Space** | RSS | arstechnica.com/science/feed | 300s | ✅ active |

#### 3.2.5 跨领域通用数据源

| 数据源名称 | 类型 | 覆盖领域 | 状态 |
|-----------|------|---------|------|
| **Reddit (r/artificial+robotics+embedded+space)** | Social | 全领域 | ✅ active（reddit 采集器：公开 JSON、无需凭据；种子 `library=reddit` + 4 子版，600s） |
| **Google News Tech** | RSS | 全领域 | ✅ active |
| **Twitter/X (recent search)** | Social | 全领域 | ⛔ inactive（twitter 采集器已实现：API v2 recent search、需付费档 `TWITTER_BEARER_TOKEN`；种子激活为后续特性） |

### 3.3 新闻流展示设计

#### 3.3.1 展示方案对比

| 方案 | 描述 | 优点 | 缺点 | 适用场景 |
|------|------|------|------|---------|
| **A: 纯卡片列表** | 所有新闻按时间排列 | 简单、直觉 | 无法体现领域差异、信息量大易迷失 | 通用新闻流 |
| **B: 领域面板分组** | 四大领域各一个面板 | 领域清晰、可折叠 | 切换成本高、跨领域话题不便 | 多领域聚合 |
| **C: 时间线式** | 时间轴+事件节点 | 时间感强 | 实现复杂、不适合高频信息 | 低频事件(如太空发射) |
| **D: 领域面板 + 合并流** | 面板分组为主，可切换合并流 | 兼顾领域区分和跨领域浏览 | 需两种布局切换逻辑 | **InstantBoard最佳** |

#### 3.3.2 最终推荐: **方案 D — 领域面板 + 合并流切换**（已实现）

- **领域面板视图**：四块 CategoryPanel 两列网格；每面板默认显示 5 条、可展开至 10 条（`CategoryPanel.vue`），面板内还提供二级子分类过滤（选中后只显示匹配标签的条目）
- **合并流视图**：`NewsFeed.vue` 无限滚动；除服务端过滤外还有一层**客户端过滤**（按当前 domain + 选中 subcategory 对已加载条目再过滤），切换标签无需重新请求
- 一级领域切换由顶部 `TechSubNav` 完成（全部/机器人/AI/嵌入式/太空）

#### 3.3.3 NewsCard 组件设计（现状）

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
    NC["NewsCard<br/>科技新闻卡片"]
    subgraph Main["主要展示元素"]
        NT["标题 (可点击 → 原文链接)"]
        NS["摘要 (2行截断)"]
        DC["领域色条 (左边缘色块)"]
        NImg["缩略图 (image_url 有则渲染, 加载失败隐藏)"]
    end
    subgraph Meta["辅助信息元素"]
        NR["来源名称"]
        NP["发布时间 (相对时间)"]
        NTag["TopicTag × N (最多显示3个)"]
        NTagEdit["手动打标: 标签行 + 内联输入 / TopicTag × 移除"]
        NH["热度指示 (hn_score, 如有)"]
    end
    NC --> Main
    Main ~~~ Meta
```

领域色条配色图例（自图内移出）：

| 颜色 | 领域 |
|------|------|
| 紫 | 机器人 (robotics) |
| 蓝 | AI (ai) |
| 橙 | 嵌入式 (embedded) |
| 绿 | 太空 (space) |

**三级标签手动标注（已实现）**：标签行（`.card-tags`）中每个 TopicTag 内含"×"，点击经 `DELETE /api/v1/items/{item_id}/tags/{tag}` 乐观移除（失败回滚并内联提示）；标签行尾"+"按钮展开内联输入框（Enter/确认提交），经 `POST /api/v1/items/{item_id}/tags` 打标，客户端按 `^[a-z0-9-]{1,32}$` 预校验，成功后以服务端返回的 `topic_tags` 覆盖本地。卡片最多显示 3 个标签（`visibleTags` slice），超出部分仍可经服务端持有。详见 [content-categories.md](content-categories.md) §3.6.1 与 [api.md](api.md) 条目手动打标 API。

> ✅ **图片缩略图（已实现）**：`/tech/news` 响应的 `image_url` 由采集器产出——RSS 按 `media:content` → `media:thumbnail` → `enclosure` → 正文 `<img>` 优先级取第一个可用图片（均无则 None）；Reddit 取 `preview.images[0].source.url` 回退 `thumbnail`（占位符 self/default/nsfw 视为无图）；HN/Arxiv/Twitter 天然无图。`NewsCard.vue` 在 `image_url` 非空时于卡片右侧渲染缩略图（桌面 96×72 / 移动端 ≤767px 64×48，均圆角裁切 + `loading="lazy"` + 包裹原文链接），加载失败（`@error`）置 `imgFailed` 后隐藏；无图保持原布局。

### 3.4 话题标签与分类体系

#### 3.4.1 标签层级设计（现状：一级 + 二级）

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
    Root["话题标签层级"]

    Root --> L1["一级标签 (领域级, 4个固定)"]
    L1 --> robotics["robotics"]
    L1 --> ai["ai"]
    L1 --> embedded["embedded"]
    L1 --> space["space"]

    robotics --> L2R["二级标签 × 6"]
    ai --> L2A["二级标签 × 6"]
    embedded --> L2E["二级标签 × 6"]
    space --> L2S["二级标签 × 6"]

    L3["三级标签 (话题级, 动态生成)"]
    L3 --> note["…"]
```

> ✅ **热门话题标签展示（已实现）**：`/tech/topics` 话题统计结果由 `HotTopics.vue` 消费——按 `count` 降序取 top 12 渲染为可点击标签（含 count 徽标），点击经 `GET /tech/news?tag=` 过滤新闻流（JSONB containment，与 domain/subcategory 叠加），再点取消（见 §3.4.3）。前端 `TechTopic` 类型已与后端契约对齐（`{tag, label, count, last_active_at}`）。
> ✅ **三级动态标签的产出（已实现，条件性）**：规则标签产出不足（< 3 个）且 TF-IDF 滑窗语料已预热（≥ 50 条）时，会从标题中补充最多 3 个高频术语三级标签（见 §3.4.2）；未满足条件时热门标签仍以一级/二级标签与基础标签（tech/general）的统计为主。

#### 3.4.2 标签自动提取算法（现状）

```python
# processors/categorizer.py — TechTopicExtractor（关键词匹配 + TF-IDF 三级补全）

# 1. 规则匹配: 关键词→标签映射表 (约 192 条)，命中即叠加对应一级/二级标签
#    如 "GPT" → ["llm", "ai"], "Starlink" → ["satellite-internet", "space"]
# 2. 未命中任何关键词 → 回退 general/tech 基础标签
# 3. TF-IDF 三级补全 (流式口径, 已实现): 仅当规则标签 < 3 个时触发
#    - 语料: 进程内滑窗最近 2000 条已处理文本 (deque + 增量 df, 滑出递减);
#      预热不足 50 条时跳过
#    - 候选: 仅标题中出现的词元 (小写化, 长度 3-40, 连字符/+/# 白名单,
#      去约 115 条英文停用词 + http/https/www 噪声, 排除纯数字)
#    - 评分: (标题tf×2 + 摘要tf) × idf(ln((1+N)/(1+df))+1);
#      取 得分 ≥ 2.5 且 df ≥ 3 的候选, 每条最多 3 个
#    - 输出: slug 化 ([a-z0-9-]{1,32}), 与一级/二级去重后追加在末尾;
#      同分按 slug 字典序 (确定性); 任何异常降级为纯规则标签
```

> ✅ **已实现**：TF-IDF 流式提取高频术语作为三级标签（口径见上方代码块；批量重打标 `POST /categories/{id}/reclassify` 复用同一进程内语料实例）。⚠️ LLM 标注（原设计步骤3，本来就属未来增强）仍未实现。
> 注：`services/tech.py` 中还有一份 46 条的重复关键词表（`KEYWORD_TO_TAG`），其 `extract_topic_tags` 方法无人调用，属冗余代码。

#### 3.4.3 TopicFilter 与热门话题标签（现状）

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#ffffff", "primaryTextColor": "#000000", "primaryBorderColor": "#767676", "lineColor": "#767676", "arrowheadColor": "#767676", "secondaryColor": "#ffffff", "secondaryTextColor": "#000000", "secondaryBorderColor": "#767676", "tertiaryColor": "#ffffff", "tertiaryTextColor": "#000000", "tertiaryBorderColor": "#767676", "edgeLabelBackground": "#ffffff", "textColor": "#000000", "nodeTextColor": "#000000", "mainBkg": "#ffffff", "nodeBorder": "#767676", "clusterBkg": "#ffffff", "clusterBdr": "#767676", "clusterTextColor": "#000000", "titleColor": "#000000", "fontSize": "14px"}, "flowchart": {"nodeSpacing": 40, "rankSpacing": 50, "wrappingWidth": 180, "useMaxWidth": true}}}%%
graph TD
    TSN["TechSubNav (TechView顶部)<br/>一级领域切换: 全部 / 机器人 / AI / 嵌入式 / 太空"]
    TFC["TopicFilter<br/>平铺 24 个二级子分类标签"]
    TFC --> Interact["交互: 点击标签 → 单选过滤 (再点取消)"]
    HT["HotTopics<br/>/tech/topics 结果按 count 降序取 top 12"]
    HT --> HTInteract["交互: 点击 → GET /tech/news?tag= 过滤<br/>(与 domain/subcategory 叠加, 再点取消)"]
```

> 与旧设计差异：一级领域切换在 **TechSubNav**（非 TopicFilter）；TopicFilter **单选**（非多选），无"展开更多"分组。
>
> ✅ **热门话题标签（已实现）**：`HotTopics.vue` 挂载于 TopicFilter 下方，消费 `techStore.topics`（TechView 挂载时经 `init()` → `fetchTopics()` 拉取，已复用）：按 `count` 降序取 top 12 渲染 `label` + count 徽标；点击经 `techStore.setTag` 切换 `activeTag` 并驱动 `/tech/news?tag=` 查询——服务端过滤使**面板/合并流两种视图同时生效**（合并流另在 `NewsFeed` 客户端再过滤，覆盖 SSE 注入的未过滤条目），再点同一标签取消；`TopicFilter` 的「全部」按钮同时清除标签过滤。加载中显示骨架占位，无话题数据不渲染。
> ⚠️ 热门标签内容仍以现有一级/二级标签与基础标签为主——三级动态标签仅在规则标签不足（< 3）且语料预热（≥ 50 条）后产出（TF-IDF 已实现，见 §3.4.2），随采集量增长逐步进入热门统计。

### 3.5 推荐算法/排序策略

#### 3.5.1 排序策略（现状）

| 模式 | 实际实现 (`services/tech.py`) | 说明 |
|------|------------------------------|------|
| 🔥 热度优先 `hot` | SQL 先 `ORDER BY priority DESC, published_at DESC` 取页，再对当页条目按 `hot_score` 客户端重排 | **默认** |
| 🕐 时间优先 `time` | `ORDER BY published_at DESC` | |
| 🎯 相关性 `relevance` | **候选池重排**：按 `priority DESC` 取候选（LIMIT `min(page_size*3, 300)`，同级以 `published_at DESC` 定序），Python 侧重计分 `score = priority + \|favorite_tags ∩ topic_tags\| × 2`，稳定排序后切页 | 有用户偏好时的个性化排序；`hot`/`time` 不受偏好影响 |

> ✅ **已实现**：用户相关性加权改为**候选池重排**口径（常量 `RELEVANCE_CANDIDATE_MULTIPLIER=3`、`RELEVANCE_CANDIDATE_LIMIT=300`、`RELEVANCE_TAG_BOOST=2`）：
> - 偏好来源为 `users.preferences.favorite_tags`（经 `GET/PUT /api/v1/users/me/preferences` 管理，见 [api.md](api.md) §3.2），前端在 SettingsView → ProfileSettings「关注话题」点选科技二级标签维护；
> - 候选池 = 过滤后按 `priority DESC` 的前 `min(page_size×3, 300)` 条（不做 OFFSET），池内按 `priority + 交集×2` 重计分并**稳定排序**（同分保持候选顺序），再从排好序的池中切出当页；池外条目在本页窗内不可达，`meta.total` 仍是过滤后全量计数；
> - 无偏好（未设置或列表为空）时**退化为纯优先级排序**（`ORDER BY priority DESC` + 常规 OFFSET/LIMIT），行为与旧版一致；`GET /tech/news` 仅在 `sort=relevance` 时查询偏好。

#### 3.5.2 热度分公式（现状措辞修正）

```python
# services/tech.py — _calculate_hot_score
HALF_LIFE_SECONDS = 12 * 3600  # 43200

base_score = item.priority                # 1-10
age_seconds = max(0, (now - item.published_at).total_seconds())
decay_factor = math.exp(-age_seconds / HALF_LIFE_SECONDS)
score = base_score * decay_factor

# HN 加权: 字段实际是 extra_data.get("hn_score")（非 hn_votes）
if hn_score:
    score += math.log1p(hn_score) * 0.5
```

> 语义说明：代码是 `exp(-age/43200)`，即**时间常数 τ=12h 的指数衰减**，实际半衰期为 τ·ln2 ≈ **8.3h**（旧文档"半衰期12小时"不准确）。
> ⚠️ **数据缺口**：种子中 HN 源走 hnrss.org **RSS**（source_type=rss），RSS entry 不含 score 字段，因此 `hn_score` 实际恒为空、加权恒为 0；直连 Firebase API 的 `HackerNewsCollector` 存在但无种子源使用。

### 3.6 数据刷新频率

| 数据类型 | 刷新频率 | 说明 |
|---------|---------|------|
| RSS新闻 (主流源) | 120-300s | SSE item_update |
| RSS新闻 (低频源, 如NASA/Arxiv/EE Times) | 1800-86400s | SSE item_update |
| HackerNews (RSS) | 120s | 经 hnrss.org RSS，非 Firebase API |
| 话题统计 | 按需 (`GET /tech/topics`)，TechView 挂载时经 `init()` 拉取；科技条目入库后经 `topic_stats_update` SSE 推送 | Redis 缓存 900s；SSE 推送以条目入库触发 + 同租户 900s 窗口节流（见 §3.8）；前端 `HotTopics.vue` 展示 top 12 热门标签、点击过滤（见 §3.4.3） |

**与财经对比**: 科技资讯刷新频率整体低于财经行情，因为新闻更新频率远低于市场行情。

### 3.7 新闻查询与过滤功能（现状）

> ⚠️ **未实现**：全文搜索 — 原设计的 `GET /tech/search?q=` 端点与 `q` 参数完全未实现，TechView 也没有搜索框。

**实际接口**: `GET /api/v1/tech/news`，参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `domain` | 一级标签 | robotics/ai/embedded/space，JSONB containment 过滤 (`topic_tags @>`) |
| `subcategory` | 二级标签 | 同上 |
| `tag` | 任意话题标签 | 热门标签点击过滤（`HotTopics.vue` 驱动），JSONB containment (`topic_tags @> '["{tag}"]'`)，与 domain/subcategory 叠加；空/空白值忽略 |
| `sort` | hot \| time \| relevance | 见 §3.5.1 |
| `source_id` | UUID | 指定数据源 |
| `since` | ISO8601 | `published_at >= since` |
| `page` / `page_size` | int | 分页，page_size ≤ 100 |

响应字段：`id, title, summary, url, source_name, source_id, category_id, topic_tags, domain_tag, published_at, fetched_at, image_url, priority, extra_data, hot_score` + 分页 meta。

**过滤维度**：领域、子分类、话题标签（`tag`）、数据源、时间范围均已实现（后端）；关键词全文搜索未实现。

### 3.8 SSE 事件类型定义 (科技频道, 现状)

| 事件类型 | 数据内容 | 触发条件 | 频率 |
|---------|---------|---------|------|
| `item_update` | `{id, title, summary, url, source_name, topic_tags, published_at, priority}`（**不含 category_id**，见 `scheduler/manager.py`） | 新条目入库 | 随采集 |
| `topic_stats_update` | 话题统计数组（即 `GET /tech/topics` 的 `data` 字段：`[{tag, label, count, last_active_at}]`，载荷为数组本身） | 科技条目入库（`scheduler/manager.py::_run_collection` 成功分支，仅科技分类且至少 1 条入库后触发） | 同租户 900s 窗口节流 |
| `source_health_update` | 完整行状态契约见 [data-flow.md](data-flow.md) §3.5.4 | 数据源健康状态变更 | 实时 |
| `heartbeat` | `{timestamp}` | 保持连接 | 30s |

> ✅ **已实现**：`topic_stats_update` — **节流口径：条目入库触发 + 900s 窗口节流**。触发点为科技源采集成功且至少 1 条入库（`scheduler/manager.py::_run_collection`）；`SSEService.publish_topic_stats_update` 以原子 `SET NX EX 900`（Redis 键 `tech:topic_stats_pushed:{tenant_id}`，见 [database.md](database.md) §3.2）保证同租户 15 分钟内至多推送一次，与 `GET /tech/topics` 的 900s 缓存窗口对齐。载荷复用 `TechService.get_topics` 的缓存读取路径（缓存缺失时计算一次），故推送内容与 REST 响应一致；统计加载失败时删除节流键，允许下次触发在窗口内重试。Redis 不可用时静默跳过（不推送、不报错）。前端 `stores/tech.ts` 收到事件后以载荷整体替换 `techStore.topics`（`HotTopics.vue` 随之刷新）。

## 4. 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 四大领域范围 | 机器人/AI/嵌入式/太空 | 用户需求明确、领域边界清晰 |
| 展示方案 | 领域面板+合并流切换（方案D，已实现） | 兼顾领域区分和跨领域浏览 |
| 排序策略 | 三种可切换（默认 hot） | 时间衰减公式为 τ=12h 指数衰减（半衰期≈8.3h） |
| 标签体系 | 一级4个 + 二级24个；三级为动态话题标签（条件性产出） | 结构化且可扩展 |
| 标签提取 | 关键词匹配（~192条）+ TF-IDF 三级补全（滑窗2000/预热50/仅标题候选/上限3，LLM未实现） | 简单可控；规则标签不足时 TF-IDF 补缺，确定性、异常降级 |
| 主数据源类型 | RSS 为主；web_scrape 源暂不可用（无采集器），reddit（social）种子已激活（公开 JSON、无需凭据） | RSS 最稳定 |
| NewsCard设计 | 领域色条+标题+摘要+标签+缩略图（`image_url` 有则渲染，懒加载，加载失败隐藏） | 信息密度适中 |

## 5. 边界情况

- **重复新闻**: 不同源报道同一事件 → 去重基于 URL+published_at (见[database.md](database.md) items表UNIQUE约束)
- **跨领域新闻**: 一条新闻涉及多个领域 → 允许多个topic_tags，在所有相关面板中显示
- **无用户偏好数据**: 相关性排序退化为纯优先级排序
> ⚠️ **未实现**：
> - 「RSS 源停更 >7 天 → degraded 提示」— 健康状态只按连续失败判定（3次→degraded、10次→down），**0 条采集也计为成功**
> - 「单日 >100 条折叠」— 无此逻辑
> - 「HackerNews 429 专门降频」— 无专门处理（且实际走 RSS 不经 Firebase API）
> - 「Web抓取反爬应对」— web_scrape 采集器本身不存在

## 6. 与其他模块的依赖

- → [frontend.md](frontend.md): TechView 组件层级、布局、NewsCard设计
- → [api.md](api.md): 科技API端点 (`/api/v1/tech/*`)、SSE事件定义
- → [database.md](database.md): items表(topic_tags GIN索引)、categories表、sources表
- → [data-sources.md](data-sources.md): 科技数据源详细配置、RSS/API URL
- → [data-flow.md](data-flow.md): RSS采集→处理→去重→分类→推送完整流程
- → [content-categories.md](content-categories.md): 分类层级结构定义、标签体系规范
- → [architecture.md](architecture.md): tech模块职责划分
