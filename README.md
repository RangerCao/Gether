# Gether

> 多源长视频深度研报生成 Agent —— 不总结视频，而是裁决视频之间的分歧。

Gether 接收用户指定的 B站 / YouTube 视频集合，完成多模态解析（字幕 / ASR / 关键帧 OCR），构建可溯源的 Evidence Ledger，检测并标注 claim 级冲突，最终生成一份带时间戳引用的深度研报。

## 架构

```text
React/TS 前端
   │  HTTP + SSE
   ▼
Spring Boot 治理层（JDK 21 虚拟线程）
   │  任务状态机 / SSE Hub / 异步调度 / 鉴权限流
   ▼  HTTP 异步 + 回调
FastAPI 执行层（无状态）
   │  解析 → 证据包 → 混合检索 → LangGraph 编排 → 裁决
   ▼
PostgreSQL(pgvector) + MinIO + Redis(可选)
```

设计文档：

- [分阶段实施计划](docs/分阶段实施计划.md)
- [配置与技术选型](docs/配置与技术选型.md)
- [项目架构选型说明](docs/项目架构选型说明：Java%20+%20FastAPI%20双栈架构设计.md)
- [竞品分析](docs/竞品分析.md)
- [核心协议](docs/protocol.md)
- [开发日志](docs/开发日志.md)
- [P3.5 验收报告](docs/P3.5验收报告.md)
- [混合检索调优记录](docs/混合检索调优记录.md)
- [视频发现与筛选设计](docs/视频发现与筛选设计.md)
- [Discovery、证据检索与排序设计](docs/Discovery与检索排序设计.md)
- [项目初衷与评测原则](docs/项目初衷与评测原则.md)
- [简历项目定位与演示脚本](docs/简历项目定位与演示脚本.md)
- [任务状态与错误链路设计](docs/任务状态与错误链路设计.md)
- [前端信息架构与设计规范](docs/前端信息架构与设计规范.md)

Discovery 评测：

```bash
python eval/scripts/eval_discovery.py --no-live
python eval/scripts/eval_discovery.py --question ai_coding_efficiency
```

广泛问题集：`eval/datasets/discovery_broad_questions.json`（48 个问题；48 个已标注，480 个 Top-10 候选已 pooled seed 标注）。

```bash
python eval/scripts/baseline_broad.py --limit 3
python eval/scripts/eval_discovery.py --no-live   --dataset eval/datasets/discovery_broad_questions.json   --session-dir eval/results/discovery_broad_sessions
python eval/scripts/eval_rankers.py
python eval/scripts/export_pool.py
python eval/scripts/train_ltr.py
python eval/scripts/llm_rerank.py --limit 30
python eval/scripts/holdout_eval.py
python eval/scripts/tune_mnp.py
python eval/scripts/coverage_report.py
python eval/scripts/cross_encoder_rerank.py
# 选中集合 Recall 报告见 eval/results/broad_selection_recall.md
```

## 当前进度

| 阶段 | 状态 |
|---|---|
| P0 骨架与环境 | ✅ |
| P1 多模态解析 | ✅ 字幕/ASR/OCR/落库全链路；YouTube 实机受网络限制 |
| P2 索引与检索 | ✅ 30 query 调参 + 过滤 + 10k 压测（P95 296ms） |
| P2.6 视频发现 | ✅ B站-only：搜索/排序/过滤/HITL；content_type 过滤后 precision 1.0 / recall 0.67 |
| P5.5 Discovery 排序 | 🚧 默认 8 条/180 分钟；分层 Recall 0.534，P 0.813；m/n/p 维持 |
| P3 Agent 与裁决 | 🚧 LangGraph + HITL + Ledger/Guard + SSE + 冲突面板；多视频真实验收待补 |
| P4 Java 治理层 | ✅ 状态机/虚拟线程/SSE/HMAC/前端代理/熔断/500 SSE soak/崩溃隔离/JWT |
| P5 前端 | ✅ 自动链路；快速/高质量模式；阶段耗时；SSE 失败同步回退 |
| P6 评测与包装 | ⬜ |
| 单视频研报 | ✅ B站 BV/链接 → 六段式研报 + 内容时间轴 + 证据缺口 |

## 单视频研报

给一个 BV 号或 B 站链接，解析原片并生成一份可回查的研报：视频信息卡 → 一句话结论 → 内容脉络（时间轴）→ 核心观点与依据 → 关键数据 → 论证质量与局限 → 行动建议。可选附一个问题，会额外生成「针对你的问题」章节。

```bash
# 治理层（需 JWT）
curl -X POST http://localhost:8080/api/tasks/single \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"input":"BV1GJ411x7h7","question":"这条视频的结论可信吗？","mode":"fast"}'

# 执行层直连
curl -X POST http://localhost:8000/internal/single/tasks \
  -H "Content-Type: application/json" \
  -d '{"input":"https://www.bilibili.com/video/BV1GJ411x7h7"}'
```

单视频模式与多视频的差别：固定六段式大纲（少一次 planner LLM 调用）、按时间轴窗口铺满全片而非只检索开头、不做跨来源冲突检测，改为输出「证据缺口」与证据支撑率。


## 一键演示

Windows 直接双击：

```text
start_demo.bat
```

或使用 Git Bash：

```bash
bash scripts/start_demo.sh start
bash scripts/start_demo.sh status
bash scripts/start_demo.sh stop
bash scripts/start_demo.sh logs
```

脚本会自动：

- 启动 Docker Desktop（如果未运行）
- 启动 PostgreSQL / Java Governor / Python Executor / 前端
- 等待健康检查
- 打开 `http://localhost:5173`

首次启动 Docker 可能需要 1–3 分钟。运行日志在：

```text
.run/logs/
```

## 快速开始

依赖：Python 3.12+、Node 20+、Docker、Git。Java 21 仅在本地运行治理层时需要，也可用 Docker。

```bash
# 1. 准备配置（已存在则不会覆盖）
bash scripts/dev.sh init
# 编辑 .env：至少填写 DeepSeek 或 DashScope 的 API Key；
# 没有 Key 时设置 LLM_PROVIDER=mock 可离线跑通链路。

# 2. 启动基础设施（Postgres+pgvector / MinIO / Redis）
bash scripts/dev.sh up

# 3. 安装并启动执行层
bash scripts/dev.sh install
bash scripts/dev.sh executor

# 4. 打开 http://localhost:8000/docs 查看内部接口

# 5. 本地 fixture 演示（创建任务 → 解析 → 查证据）
bash scripts/dev.sh demo
```

无 Make 环境统一使用 `scripts/dev.sh`：

| 命令 | 作用 |
|---|---|
| `bash scripts/dev.sh init` | 复制配置模板 |
| `bash scripts/dev.sh up` / `down` | 启动/停止基础设施 |
| `bash scripts/dev.sh install` | 安装 Python 依赖 |
| `bash scripts/dev.sh executor` | 启动 FastAPI（8000） |
| `bash scripts/dev.sh test` | 运行执行层测试 |
| `bash scripts/dev.sh smoke` | 离线冒烟测试（mock LLM） |
| `bash scripts/dev.sh check-llm` | 检查当前 LLM 供应商连通性 |
| `bash scripts/dev.sh demo` | 本地 fixture 演示：创建任务→解析→查证据 |
| `bash scripts/dev.sh export <BV号/链接>` | 解析单个视频并导出 evidence.md / .csv / .json |
| `bash scripts/dev.sh fuse <evidence.json>` | 对已有结果重做 OCR/ASR 融合，生成 fused.md |
| `bash scripts/dev.sh eval-retrieval` | 混合检索网格搜索（Recall/MRR/nDCG） |
| `python eval/scripts/bench_retrieval_10k.py` | 10k 分块检索延迟压测（需测试数据库） |
| `bash scripts/dev.sh lint` | ruff 检查 |
| `bash scripts/dev.sh web` | 启动前端（5173） |
| `bash scripts/dev.sh governor` | 启动 Java 治理层（8080） |

可选能力（体积较大，按需安装）：

```bash
cd services/executor
.venv/Scripts/python -m pip install -e ".[asr]"        # faster-whisper (CPU)
.venv/Scripts/python -m pip install -e ".[asr,asr-gpu]" # NVIDIA GPU（cuBLAS/cuDNN）
.venv/Scripts/python -m pip install -e ".[ocr]"        # RapidOCR (PP-OCR ONNX)
.venv/Scripts/python -m pip install -e ".[ocr-paddle]"  # 可选：PaddleOCR
```

> Windows 下请在 Git Bash 中执行；executor 通过 `scripts/run.py` 在 Windows 强制使用
> SelectorEventLoop（psycopg async 不支持 Proactor）。Makefile 供 CI / Linux 使用。

## 已实现的 P1 链路

```text
URL → 源归一化 → 字幕优先（yt-dlp）→ 无字幕则 ASR → 关键帧 OCR
    → Evidence Packet（时间戳/模态/去重）→ PostgreSQL
```

实测：3 个 B站短视频共 126 条证据（66 ASR + 60 OCR）；33 分钟长视频用 `base` + `beam_size=1` 产出 1009 条 ASR 证据，CPU 耗时约 4.3 分钟。

支持本地文件 fixture（`file:///.../demo.mp4` + 同名 `.srt`），用于无网络、无 ffmpeg 的链路演示。

人工检查输出质量：

```bash
bash scripts/dev.sh export BV1ZP4B6MECn
# 输出：data/output/bilibili/BV1ZP4B6MECn/evidence.md
#       data/output/bilibili/BV1ZP4B6MECn/evidence.csv
#       data/output/bilibili/BV1ZP4B6MECn/evidence.json
```

> YouTube 在当前网络环境不可达，已通过适配器单测覆盖；配置 `YTDLP_PROXY` 后可直接使用。

## 目录

```text
docs/                文档与架构图
infra/               docker-compose、initdb、配置模板
services/executor/   Python / FastAPI 执行层
services/governor/   Java / Spring Boot 治理层
services/web/        React / TypeScript 前端
eval/                评测集与指标脚本
scripts/             本地开发脚本
```

## 边界

- 仅处理用户指定的有限视频集合，不做全网搜索。
- 冲突裁决基于结构化 Evidence Ledger 与确定性规则，不做多 Agent 自由辩论。
- 仅用于个人学习与研究，不存储/分发受版权保护的完整视频。
