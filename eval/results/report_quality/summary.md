# 研报质量审计

| task | sections | claims | ledger | cites | valid cite | timed | evidence claims | unsupported | sources | conflicts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4233f903 | 6 | 44 | 61 | 44 | 1.00 | 1.00 | 27/44 | 17 | 1 | 0 |
| bf850d3f | 1 | 2 | 2 | 1 | 1.00 | 1.00 | 1/2 | 1 | 1 | 0 |
| dc931998 | 6 | 83 | 171 | 131 | 1.00 | 1.00 | 43/83 | 40 | 11 | 0 |
| 059254f0 | 1 | 4 | 5 | 3 | 1.00 | 1.00 | 2/4 | 2 | 2 | 0 |
| 18f0ad2f | 4 | 43 | 68 | 54 | 1.00 | 1.00 | 29/43 | 14 | 1 | 0 |

## 指标说明

- `valid cite`：正文 `[ev:id]` 能在 citations 中找到的比例；
- `timed`：引用 start/end 时间戳结构合法的比例；
- `evidence claims`：ledger 中有非空 evidence_id 的 claim 数；
- `unsupported`：没有非空 ledger evidence 的 claim 数；
- 该脚本不判断 evidence 是否真的支持 claim，需 LLM/人工审计。
