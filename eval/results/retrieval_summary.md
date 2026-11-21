# 混合检索调参结论（P2 / P2.5）

- 数据：`BV1gehE6DEJ8` v2 证据 369 条
- 查询：**30 条人工 query**（`eval/datasets/BV1gehE6DEJ8_queries.json`）
- Embedding：`BAAI/bge-small-zh-v1.5`（512 维，FastEmbed ONNX）
- 分块：目标 360 字符 / 上限 480 / 重叠 60 → 18 个 chunk
- 融合：RRF `k=40`，向量权重 1.0，BM25 权重 1.0，候选 50

## 最终指标（30 query）

| 方案 | Recall@10 | nDCG@10 | MRR |
|---|---:|---:|---:|
| **混合检索（最终）** | **0.9284** | **0.8673** | **0.8931** |
| 向量单路 | 0.9110 | 0.8090 | 0.8236 |
| BM25 单路 | 0.8915 | 0.7930 | 0.8250 |

结论：
- 相对向量单路，nDCG@10 提升约 **+7.2%**；
- 相对 BM25 单路，nDCG@10 提升约 **+9.4%**。

## 分块对比（第一轮，30 query）

| 分块 | Recall@10 | nDCG@10 | MRR | chunks |
|---|---:|---:|---:|---:|
| 220 / 320 / 60 | 0.8671 | 0.8007 | 0.8756 | 30 |
| 360 / 500 / 80 | 0.9284 | 0.8673 | 0.8931 | 18 |
| 500 / 700 / 100 | 0.9802 | 0.8341 | 0.8353 | 13 |

结论：大块提高 recall 但降低排序质量；360 字符是当前平衡点。

## 10k 分块延迟（P2.5）

- 分块数：10,000
- 查询数：40
- 建索引：47.9s
- **P50：192.5ms / P95：296.4ms / P99：310.7ms**
- chunk 向量为随机归一化向量（只测延迟），query 使用真实 embedding。

## 复现

```bash
bash scripts/dev.sh eval-retrieval --round 1
bash scripts/dev.sh eval-retrieval --round 2
bash scripts/dev.sh eval-retrieval --round 3

GETHER_TEST_DATABASE_URL=... \
  services/executor/.venv/Scripts/python eval/scripts/bench_retrieval_10k.py
```

完整网格：`retrieval_grid_round1.md` / `round2.md` / `round3.md`。
延迟原始结果：`bench_10k.json` / `bench_10k.md`。

详细过程与全部表格见：[docs/混合检索调优记录.md](../../docs/混合检索调优记录.md)。
