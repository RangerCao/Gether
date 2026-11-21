# 广泛集 LTR / rerank 基线

48 题 × Top-10 pooled labels；留一问题交叉验证。

| ranker | P@5 | nDCG@5 |
|---|---:|---:|
| auto-mnp | 0.7292 | 0.7538 |
| R-only | 0.8125 | 0.8680 |
| A-only | 0.6250 | 0.6192 |
| F-only | 0.6583 | 0.6541 |
| equal | 0.6958 | 0.7208 |
| logistic-LTR | 0.7958 | 0.8439 |

注意：这是 pointwise logistic LTR 基线，不是最终 cross-encoder；
标签为 Top-10 pooled seed，仍需扩大人工复核。
