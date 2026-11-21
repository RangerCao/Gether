# 10k 分块检索延迟

- 分块数：10000
- 查询数：40
- 建索引耗时：47.9s
- P50：192.48 ms
- P95：296.38 ms
- P99：310.74 ms
- 平均：210.01 ms

> chunk 向量为随机归一化向量，仅用于测量 pgvector HNSW + BM25 + RRF 的检索延迟；query 使用真实 embedding。
