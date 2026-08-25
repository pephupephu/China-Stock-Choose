# 数据来源与端点约定

> 所有字段**仅来自公开披露数据**。任何来自第三方研报、券商观点、
> 自媒体评论的输入都被刻意排除。

## 数据源一览

| 来源 | 端点 | 字段 | 备注 |
| --- | --- | --- | --- |
| 巨潮资讯网 | `webapi.cninfo.com.cn` / `data.cninfo.com.cn` | 历史现金分红 / 公告披露 | 一手法定披露；不开放 Tushare 高级数据 |
| 新浪财经 | `money.finance.sina.com.cn` / `finance.sina.com.cn` | 资产负债表、利润表、现金流量表、日 K 线 | 通过 AKShare 拉取；存在字段映射 |
| 同花顺 | `basic.10jqka.com.cn` | 主营业务构成 | 增厚行业洞察的辅助字段 |
| 申万指数 | `index_component_sw`（新浪镜像） | 行业成分 / 历史 PE / PB | 用于行业均值计算 |

> 任何 Eastmoney push2 端点（如 `push2.eastmoney.com`）在国内
> 网络环境下常被代理拦截，所以本项目刻意绕开它们。后续要替换为
> Tushare PRO 接口时务必保留 cninfo 作为"原始披露"的核验。

## 字段映射

| 输出字段 | 选取来源 |
| --- | --- |
| `cash_dividend_per_share_history` | 巨潮 - 派现比例 / 送股比例 / 转增比例 拆分；只统计"现金分红"，剔除送转股 |
| `dividend_yield_pct_at_close` | `cash_per_share / 期末收盘价`（以最近一次披露日为准） |
| `pe_ttm` | `期末收盘价 / EPS_TTM`，EPS_TTM 取最近 4 个季度的归属于母公司净利润 ÷ 当期股本 |
| `pb` | `期末收盘价 / 每股净资产`，净资产取 归属于母公司所有者权益合计 期末值 |
| `roe_ttm_pct` | `净利润_TTM / ((期初权益 + 期末权益) / 2) * 100` |
| `debt_ratio_pct` | `负债合计 / 资产总计` 期末值 |
| `payout_ratio_pct` | `现金分红总额（每股分红 × 股本） / 同期归母净利润 * 100` |
| `operating_cash_flow_total` | 经营活动产生的现金流量净额（年报） |
| `operating_cash_flow_per_share` | OCF / 股本 |
| `main_business_summary` | 同花顺 top-level 经营字段拼接，仅做"辅助提示" |
| `audit_opinion` | 财报附注 + AKShare `stock_financial_report_sina` 末列"是否审计" |
| `industry_shenwan` | `index_component_sw(symbol="801xxx")` 反向匹配所属行业 |

## 已知盲点

- **年报披露日到 SEC 更新之间有 ≥ 2 小时延迟**：刚好在 16:00 收市
  之后跑筛选，可能拿到的是*上一年度*的审计意见和分红方案；建议在
  19:00 CST 之后跑常规日筛。
- **Sina 利润表"归属于母公司所有者的净利润"包含"少数股东损益"**
  调整前后差——本项目**始终用归属于母公司**版本，跨期可对比。
- **审计意见字段在 Sina 末行**：靠文本匹配 `标准无保留 / 保留 / 
  无法表示 / 否定` 关键词；若某年报未披露，置为 `None`，不会强制剔除。
- **上市日期**：Sina/K-Line 没有显式返回。可退化为"最早一份财报报告日"
  ——这是为不引入额外请求的折中。

## 缓存策略

- 默认 6 小时 TTL，写到 `output/.cache/{fn}__{hash}.parquet`。
- 跨进程复用：周一拉一次，周五再跑几乎零 API 请求。
- 缓存粒度 = (函数名, 入参 JSON)；相同股票 + 相同日期范围算一次。
- 在 GitHub Actions 上缓存路径天然被 runner 回收；本地跑 `--force`
  参数（或删除 `output/.cache/`）可强制刷新。

## 安全建议

- `SMTP_PASSWORD` 是邮箱**客户端授权码**，不是登录密码。QQ / 163 / Gmail
  都支持生成。
- 不要把 `.env` 提交到 Git，已经在 `.gitignore` 中显式忽略。
- `AKSHARE_PROXY` 如果设置，不要打印到日志；当前日志只记录
  `fetcher.*` 的命中计数。