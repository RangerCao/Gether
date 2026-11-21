# 固定 holdout 对照

holdout：broad_04, broad_11, broad_17, broad_22, broad_28, broad_45

| ranker | P@5 | nDCG@5 |
|---|---:|---:|
| auto | 0.6667 | 0.7303 |
| R-only | 0.7333 | 0.8115 |
| LTR | 0.7333 | 0.8090 |
| LLM | 0.7000 | 0.7679 |

注意：LLM 指标来自 `broad_llm_rerank.json` 的同一 holdout 运行。
