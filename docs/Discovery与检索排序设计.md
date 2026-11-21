# Discovery、证据检索与排序设计

> 覆盖两条链路：
>
> 1. **Discovery**：研究问题 → 平台搜索 → 候选视频 → 排序 → 选片；
> 2. **Evidence Retrieval**：视频解析 → 证据分块 → 混合检索 → 研报引用。
>
> 本文是两者的统一设计文档。评分部分以 **`m·R + n·A + p·F`** 为主排序公式；Topic Gate 与集合约束是第二层资格与选择逻辑，内容类型作为排序特征，不做问题类型硬门控。

---

## 0. 文档定位

| 文档 | 作用 |
|---|---|
| 本文 | Discovery、证据检索、排序公式与评测的统一设计 |
| `视频发现与筛选设计.md` | Discovery 早期方案与历史调参记录 |
| `混合检索调优记录.md` | 证据块混合检索的三轮调参与指标 |
| `配置与技术选型.md` | 参数、模型、基础设施选型 |

本文不替代历史调参记录；当评分口径冲突时，以本文的 `m/n/p` 公式为准。

---

## 1. 目标与边界

### 1.1 目标

- 从自然语言问题自动找到一组**相关、可信、时效合适、覆盖多立场**的视频。
- 排序必须可解释：能说明某个视频为什么排在前面。
- 排序策略必须自动适应用户问题，而不是要求用户手动选择权重。
- 发现阶段只使用标题、简介、作者、播放、发布时间等元数据，不做完整 ASR/OCR。
- 解析完成后，证据检索必须能定位到具体视频、时间段和证据包。
- 每个选中视频和每条引用都能回溯到来源。

### 1.2 不做

- 不做全网爬虫。
- 不在发现阶段下载视频。
- 不承诺“唯一正确答案”；目标是找到足够支撑研报的证据集合。
- 不把权威性等同于绝对播放量。
- 不让 Topic Gate 或内容惩罚偷偷替代主排序公式。

---

## 2. 总体链路

```text
用户问题
   │
   ▼
┌──────────────── Discovery ────────────────┐
│ Rewrite：实体 / 意图 / 优先级 / avoid      │
│ Plan：facets + required_any + exclude_any  │
│ Search：B站 / YouTube，多 query             │
│ Metadata：播放、时长、作者、发布时间        │
│ Score = mR + nA + pF（主排序）              │
│ Topic Gate：是否在讲这个主题？              │
│ 内容质量特征：教程/官方/研究/观点             │
│ Selection：覆盖 + 反方 + 时长 + 多样性      │
└────────────────────────────────────────────┘
   │
   ▼
VideoRef 集合（HITL 确认）
   │
   ▼
解析：字幕 / ASR / OCR / 证据包
   │
   ▼
┌──────────── Evidence Retrieval ───────────┐
│ 分块：360 / 480 / 60                       │
│ 向量：bge-small-zh-v1.5 + pgvector HNSW    │
│ 稀疏：jieba + BM25                         │
│ 融合：RRF(k=40, 1:1)                       │
│ 过滤：video / modality / 时间范围           │
└────────────────────────────────────────────┘
   │
   ▼
研报章节 + 时间戳引用 + 冲突裁决
```

### 2.1 两个“检索”不是一回事

| 名称 | 输入 | 数据 | 输出 | 目的 |
|---|---|---|---|---|
| Discovery Search | 用户问题 | 平台搜索结果 + 元数据 | 候选视频 | 选哪些视频值得解析 |
| Evidence Retrieval | 章节/子问题 | 已解析证据块 | 证据块 + 时间戳 | 为研报找证据 |

Discovery 排序使用 `mR + nA + pF`；证据检索使用 BM25/向量 RRF。两者的分数不可直接比较。

---

## 3. Discovery

### 3.1 Query Rewrite

用户问题先改写成适合平台搜索和向量匹配的形式，同时保留原问题。

```json
{
  "original": "AI 编程工具真的能提升开发效率吗？",
  "rewritten": "AI 编程工具 实际提升开发效率 实测 优缺点 真实体验",
  "intent": "evaluation",
  "entities": ["Cursor", "Copilot", "Claude Code", "AI 编程"],
  "must_have": ["编程", "代码", "开发", "研发"],
  "avoid": ["教程", "安装", "短剧", "绘画"],
  "priority": "relevance",
  "priority_reason": "问题在问真实效果，没有明确时间窗或权威来源要求"
}
```

要求：

- 保留对象、时间、场景、约束。
- 决策型问题改写成对比/取舍，而不是教程步骤。
- 评估型问题必须覆盖风险和反方。
- `priority` 只允许：`relevance | authority | freshness | balanced`。
- 解析失败时使用规则兜底，不阻塞发现流程。

### 3.2 Plan：facet 与主题锚点

```json
{
  "topic": {
    "entities": ["Cursor", "Copilot", "AI 编程"],
    "must_have_any": ["编程", "代码", "开发", "研发"],
    "exclude_topics": ["短剧", "绘画", "剪辑"]
  },
  "facets": [
    {
      "id": "f1",
      "label": "效率提升实测",
      "query": "Cursor Copilot AI 编程 效率 实测",
      "stance": "positive",
      "required_any": ["编程", "代码", "开发"],
      "exclude_any": ["短剧", "绘画"]
    },
    {
      "id": "f2",
      "label": "代码质量风险",
      "query": "AI 编程 代码质量 缺陷 风险",
      "stance": "negative",
      "required_any": ["编程", "代码", "缺陷"],
      "exclude_any": ["短剧", "绘画"]
    }
  ],
  "negative_terms": ["教程", "安装", "短剧", "绘画"]
}
```

规则：

- 3–6 个 facet；评估型必须包含 negative/risk facet。
- 每个 facet 的 query 必须带主题实体，不能只写“工具测评”这类泛词。
- `required_any` 用于判断 facet 是否真正命中。
- `exclude_any` 用于拒绝相邻领域。
- 决策型 fallback 不得硬编码汽车领域；根据问题实体生成通用模板。

### 3.3 Search

| 来源 | 方式 | 当前状态 |
|---|---|---|
| B站 | `search/type` API + cookies | 已接入，默认 |
| B站标签 | 搜索结果自带 `tag` 字段 | 用于关键词/tag 命中，不按分区 |
| B站 | `yt-dlp bilisearch` + cookies | 保留为兼容模式 |
| YouTube | `yt-dlp ytsearch` | 代码已接入，网络受限 |

抓取预算：

| 参数 | 默认 | 说明 |
|---|---:|---|
| 每 query 结果数 | 20 | 先取第一页 |
| 每 facet query 数 | 2 | 中文 + 同义/英文 |
| 总 query 数 | 最多 10 | 去重后 |
| 元数据补全 | Top 30 | 只对高潜候选调用详情 |

工程要求：

- 查询之间保留间隔，412/429 指数退避。
- B站默认使用官方 `x/web-interface/search/type` API，而不是 yt-dlp 搜索页。
- API 结果已带播放/时长/作者/发布时间，跳过逐条 view 补全。
- 单个 query 失败只记录 warning，不阻塞其他 query。
- 同一 query 使用 TTL 缓存，减少重复搜索。
- 412/429 后本次会话停止该 provider 的后续请求，并进入 cooldown。
- 查询间隔默认 4s，避免连续触发风控。

### 3.4 Metadata

B站详情接口补全：

```text
title / description / duration / uploader / published_at
views / likes / coins / favorites / danmaku / reply / owner_mid
```

注意：

- 搜索结果本身往往没有播放量和时长，必须补全后才能做播放量过滤。
- 元数据补全应按“搜索相关性 + 权威度预估”选择 Top N，而不是只按搜索顺序。
- 缺失播放量不直接淘汰，但不能无条件通过低播放例外。

### 3.5 去重与粗过滤

去重顺序：

1. `(source, source_video_id)` 完全一致；
2. 规范化标题 hash（去标点、空格、emoji）；
3. 标题/简介 embedding 相似度 ≥ 0.9 聚簇；
4. 同作者、同系列只保留最佳 1–2 条。

粗过滤：

- 直播、预告、纯音乐、播放列表、付费内容；
- 时长默认 3–180 分钟；
- 命中 `negative_terms` / `exclude_any` 的标题降权或淘汰；
- 无发布时间不直接淘汰，`F=0.5`。

### 3.6 Session 状态

```text
DISCOVERING
  → WAITING_SELECTION（可选 HITL）
  → CONFIRMED
  → task 创建
```

发现失败必须落库为 `FAILED` 或带 warning 返回，不能长期停留在 `DISCOVERING`。

---

## 4. 排序公式（核心）

### 4.1 三个基础分数

所有分数先归一化到 `[0, 1]`，再进入综合公式。

#### 相关性 R

```text
topic_similarity = 0.60 · P(anchor/semantic topic match)
                 + 0.40 · P(embedding similarity)

best_facet = max(facet_confidence)

R = 0.55 · topic_similarity
  + 0.30 · best_facet
  + 0.15 · entity_match
```

其中：

- `P(·)` 表示候选池内百分位归一化；
- `entity_match`：问题实体是否出现在标题/简介中，命中为 1，否则为 0；
- `facet_confidence` 见 4.2。

#### 权威性 A

```text
A = 0.60 · percentile(log1p(views))
  + 0.40 · percentile(engagement_rate)

engagement_rate = (likes + coins + favorites) / max(views, 1)
```

使用百分位而不是绝对播放量，避免大账号碾压小账号。

后续可扩展：

```text
A' = 0.50 · view_percentile
   + 0.25 · engagement_percentile
   + 0.25 · creator_authority
```

`creator_authority` 来自认证、粉丝数、官方机构等。

#### 时效性 F

```text
age_days = (now - published_at) / 86400

F = exp(-age_days / 180)
```

- 半衰期默认 180 天；
- 无发布时间：`F = 0.5`；
- 可用 `DISCOVERY_FRESHNESS_HALF_LIFE_DAYS` 调整。

### 4.2 facet 置信度

```text
facet_confidence =
  0.50 · lexical_norm(BM25(facet.query, title + description))
+ 0.50 · semantic_norm(cosine(facet.query, title))
```

- 两路都做候选池内归一化；
- `facet_confidence` 不再与主题相关性混在一起；
- 命中阈值按评测集校准，不使用全局固定的 0.55；
- 负向/风险 facet 必须命中 `required_any`，避免把支持观点算成反方；
- 正向/中性 facet 在 Topic Gate 通过后允许语义命中，避免漏掉综述型视频；
- `required_any` 仍然作为置信度解释与负向门控字段保留。

```text
facet_hit =
  facet_confidence >= 阈值
  且未命中 exclude_any
  且（非负向 facet 或 required_any 命中）
```

### 4.3 综合得分

```text
Score = m · R + n · A + p · F

m + n + p = 1
```

`Score` 是候选排序的唯一主分数。

禁止在公式里隐式加入：

- 内容类型惩罚；
- 时长惩罚；
- 去重惩罚；
- 来源偏好。

这些属于 Gate 或 Selection 约束，必须独立记录，便于解释和调参。

### 4.4 自动选择 m / n / p

用户不需要手动选择“相关性优先/权威性优先/时效性优先”。系统从问题语义自动判断。

#### 四种策略

| 策略 | m 相关性 | n 权威性 | p 时效性 | 典型问题 |
|---|---:|---:|---:|---|
| 相关性优先 | **0.60** | 0.25 | 0.15 | 真的有效吗、怎么选、实测对比 |
| 权威性优先 | 0.25 | **0.60** | 0.15 | 权威研究、官方数据、专家观点 |
| 时效性优先 | 0.25 | 0.15 | **0.60** | 2026 最新、今年政策、近期趋势 |
| 均衡 | 0.40 | 0.30 | 0.30 | 普通开放问题、信号冲突 |

#### 推断顺序

1. 先看问题中的明确信号：时效、权威、实测/决策。
2. 明确信号存在时按规则决定，避免 LLM 对所有问题都返回 `balanced`。
3. 信号冲突或没有信号时，参考 `rewrite_question` 的 `priority`。
4. 仍无法判断时使用 `balanced`。

规则兜底信号：

```text
时效性：
最新、最近、今年、本季度、当前、现在、近期、
2025、2026、政策、趋势、动态、前沿、发布

权威性：
权威、官方、专家、教授、研究、论文、数据、
报告、机构、学术、大厂、可信、专业

相关性：
实测、体验、对比、优缺点、怎么选、真实、
案例、翻车、避坑、使用感受、是否、真的、效果
```

判定：

```text
if freshness_score >= 2 and authority_score >= 2:
    balanced
elif freshness_score >= 2:
    freshness
elif authority_score >= 2:
    authority
elif relevance_score >= 1 or intent in {evaluation, decision}:
    relevance
else:
    balanced
```

#### 输出与展示

Discovery 响应中增加：

```json
{
  "ranking": {
    "priority": "freshness",
    "weights": {"m": 0.25, "n": 0.15, "p": 0.60},
    "reason": "检测到“2026 / 最新”，本次按时效性优先排序"
  }
}
```

前端展示“本次排序策略”，但不提供权重滑块。高级用户可通过 API 覆盖，不作为默认交互。

### 4.5 排序示例

| 问题 | priority | m | n | p |
|---|---|---:|---:|---:|
| AI 编程工具真的能提升开发效率吗？ | relevance | 0.60 | 0.25 | 0.15 |
| 2026 年 AI 编程工具最新格局 | freshness | 0.25 | 0.15 | 0.60 |
| 有哪些权威研究证明 AI 编程有效？ | authority | 0.25 | 0.60 | 0.15 |
| 人生第一辆车应不应该买二手？ | relevance | 0.60 | 0.25 | 0.15 |
| 普通开放问题 | balanced | 0.40 | 0.30 | 0.30 |

### 4.6 改造前 / 改造后

改造前：

```text
R_current = max(
  0.5 · question_lexical + 0.5 · question_semantic,
  facet_scores
)

score_current =
  0.55 · R
+ 0.15 · A
+ 0.15 · F
+ 0.05 · source
- 0.10 · duration_penalty
- 0.20 · content_penalty
- 0.10 · noise_penalty
```

问题：

- `max(facet_scores)` 会把宽泛 facet 当成主题相关；
- 语义相似度映射后普遍偏高，区分度不足；
- 内容/时长惩罚混入排序，不可解释；
- 权重硬编码，没有自动倾向。

改造后：

```text
R = 0.55 · topic_score
  + 0.30 · best_facet
  + 0.15 · anchor_hit

Score = m · R + n · A + p · F
```

- 内容/时长/重复度不再进入主分数；
- Topic Gate 使用主题锚点，不再让语义相似度单独放行；
- 负向 facet 必须显式命中风险词；
- 前端展示 `priority`、`m/n/p` 和原因。


---

## 5. 资格门

门是第二层，用来淘汰“分数高但明显不该选”的候选。

### 5.1 Topic Gate

只回答：**这个视频是不是在讲用户的问题？**

```text
topic_gate =
  命中 entities / must_have_any 主题锚点
  （有锚点时不再仅凭语义相似度放行；
   允许省略“工具/助手/平台”等通用后缀）
```

拒绝条件：

- 命中 `exclude_any` / `exclude_topics`；
- 只命中宽泛 facet，但没有任何主题实体/锚点；
- 命中 `exclude_topics`。

输出：

```json
{
  "topic_gate": false,
  "rejection_reason": "topic_mismatch",
  "rejected_by": ["exclude:短剧"]
}
```

典型例子：

```text
问题：AI 编程工具
候选：4 家 AI 短剧工具测评
结果：topic_mismatch
```

### 5.2 内容质量信号（不是问题类型硬门控）

内容类型只作为**排序特征**，不再按“决策型/权威型”写死准入规则。

保留为特征的分类：

```text
tutorial / research / experience / opinion / news / career / official / other
```

可用于：

- 排序特征；
- 可解释展示；
- 后续 LTR / rerank 的输入；
- 安全/合规硬过滤（广告、纯搬运等）。

不再使用：

```text
识别为决策型 → 只允许 experience/opinion
识别为权威型 → 只允许 research/official
```

这类机制会针对少数问题类型过拟合，已移除。

### 5.3 门与排序的顺序

```text
1. 计算 R / A / F
2. 自动推断 priority → m / n / p
3. Score = mR + nA + pF
4. Topic Gate 淘汰主题错误
5. 内容质量作为排序特征，不做问题类型硬过滤
6. 对 eligible 候选做集合选择
```

排序是主逻辑；门只决定“有没有资格进入选择”，不改变 `m/n/p`。

---

## 6. 集合选择

`Score` 只解决“单个视频排第几”。选片还要解决“这组视频是否覆盖问题”。

### 6.1 目标数量

```text
facets = 3..6
n_min = 2
n_max = min(8, floor(duration_budget / avg_duration))
n_target = clamp(ceil(1.5 * facets), n_min, n_max)
```

默认：

| 参数 | 默认 |
|---|---:|
| `max_videos` | 8 |
| `min_videos` | 2 |
| `hard_max_videos` | 8 |
| `duration_budget_min` | 180 |
| 单视频上限 | 60 分钟 |

### 6.2 覆盖

- 每个 facet 至少一个**高置信**候选；
- 只有 `facet_hit=true` 才计入 coverage；
- 不允许一个泛化视频因为“命中所有 facet”就结束选择；
- 覆盖不足时返回 `insufficient`，不静默凑数。

### 6.3 反方与立场

评估型/决策型问题：

```text
selected 中必须至少有 1 个 negative/risk facet 的高置信候选
```

如果不存在：

- `sufficiency.ok = false`；
- warning：`缺少反方/风险来源`；
- 前端允许用户手动补充链接。

反方不是加分项，而是硬约束。

### 6.4 时长预算

选择循环中累计：

```text
total_minutes += estimated_duration
if total_minutes > duration_budget_min:
    skip
```

- 未知时长按 20 分钟估算；
- 超过单视频上限的候选默认跳过；
- 唯一反方证据可例外，但必须 warning。

### 6.5 低播放例外

低播放候选进入集合必须同时满足：

```text
最多 1 个
且覆盖尚未覆盖的 facet
且属于 evidence 类型
且 views >= 300
且 engagement 位于候选池 Top 10%
```

不满足则不选；低播放裁剪后若有空位，优先用正常播放候选补位。

### 6.6 多样性与 MMR

- 同 cluster 最多 1 条；
- 同作者最多 1 条，唯一反方来源例外；
- 标题规范化去重；
- MMR：

```text
select_score =
  λ · normalized_Score
+ (1 - λ) · novelty
+ coverage_bonus
- redundancy_penalty
```

默认 `λ = 0.7`，从配置读取，不硬编码。

### 6.7 充分性

```text
ok =
  selected >= min_videos
  且 high_confidence_coverage >= 0.6
  且 最高相关性 >= 0.35
  且 evaluation/decision 有反方
```

不通过时：

- 自动放宽：增加同义 query、降低播放门槛、放宽时间窗；
- 仍不足：返回 `insufficient` 和 warnings；
- 不用低质量视频凑数。

---

## 7. 证据检索

视频确认并解析后，进入证据块检索。

### 7.1 分块

```text
chunk_target_chars  = 360
chunk_max_chars     = 480
chunk_overlap_chars = 60
```

规则：

- 按视频分组，按时间排序；
- 同一视频内聚合 ASR/OCR 证据，目标 360 字；
- 超过 480 字的单条证据按字符比例切分，并估算对应时间戳；
- 块间保留尾部证据作为重叠；
- 每个 chunk 保留 `video_id`、`start_ms`、`end_ms`、`evidence_ids`、`modalities`。

### 7.2 向量路

| 项 | 值 |
|---|---|
| 模型 | `BAAI/bge-small-zh-v1.5` |
| 维度 | 512 |
| 运行时 | FastEmbed ONNX |
| 存储 | PostgreSQL + pgvector |
| 索引 | HNSW |
| 距离 | cosine |

Discovery 阶段的向量只匹配视频标题；证据检索匹配 chunk 文本。

### 7.3 BM25 路

```text
jieba 分词 + 停用词过滤 + BM25Okapi
```

BM25 负责：

- 专有名词；
- 数字；
- 精确短语；
- 中文短句。

### 7.4 RRF 融合

```text
RRF_score(d) = Σ w_i / (k + rank_i(d))
```

当前参数：

```text
candidate_k = 50
rrf_k       = 40
vector_weight = 1.0
bm25_weight   = 1.0
top_k         = 10
```

设计原则：

- RRF 不依赖两路分数尺度；
- 两路候选先各取 `candidate_k`；
- 融合后取 `top_k`；
- 过滤条件必须先作用于两路，避免“过滤后向量路仍返回被排除的块”。

### 7.5 过滤

支持：

```json
{
  "task_id": "...",
  "query": "杭州房租为什么高",
  "video_id": "...",
  "modalities": ["asr"],
  "time_start_ms": 90000,
  "time_end_ms": 130000,
  "top_k": 10
}
```

- 向量路：SQL 动态 `WHERE`；
- BM25 路：先过滤 chunk，再构建/检索；
- 无过滤时缓存 chunks + BM25；
- 索引重建后失效缓存。

### 7.6 当前指标

来自 `混合检索调优记录.md`：

| 方案 | Recall@10 | nDCG@10 | MRR |
|---|---:|---:|---:|
| 混合 1:1 | 0.9284 | 0.8673 | 0.8931 |
| 向量单路 | 0.9110 | 0.8090 | 0.8236 |
| BM25 单路 | 0.8915 | 0.7930 | 0.8250 |

注意：评测集来自单一长视频的 30 个 query，结论用于相对比较，不代表所有领域。

---

## 8. 数据模型

### 8.1 DiscoveryCandidate

```text
candidate_id
source / source_video_id / url
title / description
duration_ms
uploader
published_at
views / likes / coins / favorites

relevance              R
authority              A
freshness              F
score                  mR + nA + pF
matched_facets
facet_confidence       {facet_id: score}
topic_score
topic_gate
content_types
rejection_reason
cluster_id
selected
reason
```

### 8.2 DiscoverySession

```text
session_id
question
rewritten_question
intent
ranking_priority
ranking_weights        {m, n, p}
ranking_reason
facets
queries
negative_terms
candidates
selected
sufficiency
status
task_id
warnings
```

### 8.3 RetrievalHit

```text
chunk_id
video_id
text
start_ms / end_ms
evidence_ids
modalities
rrf_score
vector_rank / bm25_rank
vector_score / bm25_score
```

---

## 9. API

### 9.1 Discovery（Java 对外）

```http
POST /api/discovery/sessions
Content-Type: application/json

{"question": "AI 编程工具真的能提升效率吗？"}
```

```http
GET /api/discovery/sessions/{session_id}
```

```http
POST /api/discovery/sessions/{session_id}/confirm
{"selected_ids": ["candidate_id"]}
```

### 9.2 Discovery（Python 内部）

```http
POST /internal/discovery/sessions
GET  /internal/discovery/sessions/{session_id}
POST /internal/discovery/sessions/{session_id}/confirm
```

### 9.3 Evidence Retrieval

```http
POST /internal/retrieve
Content-Type: application/json

{
  "task_id": "...",
  "query": "摄像头报警项目怎么工作",
  "top_k": 10
}
```

---

## 10. 可观测性与前端

Discovery 结果页必须展示：

- 本次排序策略：`priority` + `reason`；
- 每个候选的 `R / A / F / Score`；
- `topic_gate` 是否通过；
- `facet_confidence` 与 `matched_facets`；
- `content_types`；
- `rejection_reason`；
- 被拒绝的 Top 候选与原因；
- sufficiency warnings、播放量回退、搜索失败。

不允许只展示一个黑盒 `final_score`。

---

## 11. 评测

### 11.1 数据集

当前 seed set：`eval/datasets/discovery_questions.json`（9 个问题，全部有标签；Java/汽车为启发式 seed）。

评测脚本：`eval/scripts/eval_discovery.py`。

覆盖：

- 科技/产品评测；
- 决策型；
- 时效型；
- 权威研究型；
- 人物/行业；
- 相邻领域干扰（AI 编程 vs AI 短剧）。

每个问题人工标注：

```text
relevant 0/1
facet 标签
stance
content_type
ideal_selected_set
```

### 11.2 指标

| 指标 | 说明 |
|---|---|
| Priority Accuracy | 自动策略是否符合人工判断 |
| nDCG@10 | 候选排序质量 |
| Selection Precision | 选中视频的相关比例 |
| Topic False Positive Rate | 主题错误候选比例 |
| Facet Coverage | 高置信 facet 覆盖 |
| Stance Coverage | 正/反/中立覆盖 |
| Total Duration | 解析成本 |
| Low-view Precision | 低播放例外中的相关比例 |

### 11.3 当前基线（9 问题）

| 指标 | 结果 |
|---|---:|
| Priority accuracy | 9/9 |
| 平均 Precision | 0.5630 |
| 平均 Recall | 0.6315 |
| false positive 总数 | 17 |
| 平均总时长 | 41.5 分钟 |

说明：这是切换到 B站 `search/type` API、候选池扩展到 117–165 后的新基线；旧 0.8463 基线只有约 30 个候选，不能直接比较。

较弱项：Java 被 Python/Go 替代语言干扰、汽车低播放召回不足、authority 候选内容质量不足。

### 11.4 验收线

```text
Priority Accuracy     >= 80%
nDCG@10               >= 0.80
Selection Precision   >= 0.80
Topic False Positive  = 0
Facet Coverage        >= 0.80
Stance Coverage       = 100%（evaluation/decision）
Total Duration        <= 120 分钟
```

### 11.5 回归用例

- AI 编程 vs AI 短剧；
- DeepSeek 教程 vs AI 编程效率；
- 「如何买人生第一辆车」vs 决策型问题；
- 48 播放 Cursor 视频；
- 81 分钟长视频；
- 全支持方候选；
- 2026 最新问题必须选中时效策略。

---

## 12. 参数表

### 12.1 权重

| 参数 | 默认 |
|---|---:|
| `DISCOVERY_WEIGHT_RELEVANCE` | 0.60 / 0.25 / 0.25 / 0.40（按策略） |
| `DISCOVERY_WEIGHT_AUTHORITY` | 0.25 / 0.60 / 0.15 / 0.30 |
| `DISCOVERY_WEIGHT_FRESHNESS` | 0.15 / 0.15 / 0.60 / 0.30 |

### 12.2 Discovery

| 参数 | 默认 |
|---|---:|
| `DISCOVERY_MIN_VIDEOS` | 2 |
| `DISCOVERY_MAX_VIDEOS` | 5 |
| `DISCOVERY_HARD_MAX_VIDEOS` | 8 |
| `DISCOVERY_DURATION_BUDGET_MIN` | 120 |
| `DISCOVERY_MAX_VIDEO_MINUTES` | 60 |
| `DISCOVERY_FRESHNESS_HALF_LIFE_DAYS` | 180 |
| `DISCOVERY_MAX_AGE_DAYS` | 0（关闭） |
| `DISCOVERY_MIN_FACET_COVERAGE` | 0.6 |
| `DISCOVERY_FACET_MATCH_THRESHOLD` | 待评测校准 |
| `DISCOVERY_MMR_LAMBDA` | 0.7 |

### 12.3 Retrieval

| 参数 | 默认 |
|---|---:|
| `CHUNK_TARGET_CHARS` | 360 |
| `CHUNK_MAX_CHARS` | 480 |
| `CHUNK_OVERLAP_CHARS` | 60 |
| `RETRIEVAL_CANDIDATES` | 50 |
| `RRF_K` | 40 |
| `VECTOR_WEIGHT` | 1.0 |
| `BM25_WEIGHT` | 1.0 |
| `RETRIEVAL_TOP_K` | 10 |

---

## 13. 实施状态

| 能力 | 状态 |
|---|---|
| B站搜索 + 元数据补全 | ✅ |
| facet planning / rewrite | ✅ 基础版 |
| 内容类型规则 | ✅ 基础版，需增强 |
| `mR + nA + pF` 公式 | ✅ 已落地 |
| 自动 priority 推断 | ✅ 规则优先 + LLM 兜底 |
| Topic Gate | ✅ 锚点门控 |
| 内容质量特征 | ✅ 分类保留；问题类型硬过滤已移除 |
| 时长累计 / 反方硬约束 | ✅ 已落地 |
| 低播放上限 | ✅ 最多 1 条，可补位 |
| 证据分块 + 向量 + BM25 + RRF | ✅ |
| 检索过滤 | ✅ |
| offline 评测集 | 🚧 单 query 历史结果，待扩充 |
| 前端策略/分数展示 | ✅ 已展示 priority、m/n/p、主题分 |

### 建议顺序

1. **评测与公式**：priority 推断、m/n/p、R/A/F 归一化、nDCG；
2. **资格门**：Topic Gate、集合覆盖、rejected reasons；
3. **集合约束**：反方、时长、低播放、MMR；
4. **工程优化**：搜索缓存、每 facet 配额、元数据预排序；
5. **前端**：展示策略、分数拆解、被拒候选。

---

## 14. 决策记录

| 决策 | 结论 |
|---|---|
| 主排序 | `Score = mR + nA + pF`，`m+n+p=1` |
| 用户是否选权重 | 不要求；系统自动推断 |
| 门与排序顺序 | 排序为主，门为资格约束 |
| 内容/时长惩罚 | 不进入主公式，放在 Gate/Selection |
| 反方 | 硬约束，不是加分项 |
| 低播放 | 最多 1 个，必须覆盖未覆盖 facet |
| 证据检索 | 向量 + BM25 + RRF，独立于 Discovery 排序 |
