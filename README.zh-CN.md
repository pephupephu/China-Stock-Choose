# China-Stock-Choose · 每日 A 股选股推送

> 每日对中国 A 股全市场做一次规则化筛选，将命中清单以邮件推送到你的
> 邮箱，同时把可读化的结果提交到这个仓库。所有数据均来自公开披露来源
> （巨潮 / 新浪 / 同花顺 / 申万），不引入任何第三方研报、雪球评论、
> 媒体观点或付费数据源。

| 项目 | 内容 |
| --- | --- |
| **仓库地址** | https://github.com/pephupephu/China-Stock-Choose |
| **运行环境** | Python 3.10+，AKShare（巨潮 / 新浪 / 同花顺 / 申万数据） |
| **执行时间** | 每个交易日 10:30 UTC（18:30 北京时间，收盘后） |
| **许可证** | MIT |

## 功能要点

- 全 A 遍历执行"硬过滤 + 打分"的筛选流水线；
- 多源核验：巨潮分红、新浪财务报表、同花顺主营构成与在建工程、
  申万行业分类；
- 多模态输出：HTML + 纯文本 + JSON，按日期落盘；
- 默认 6 小时的本地 parquet 缓存，重复运行开箱即快；
- 任何人 fork 之后，只需替换 SMTP 凭据即可自用。

## 快速上手

```bash
git clone https://github.com/pephupephu/China-Stock-Choose
cd China-Stock-Choose
python -m pip install -r requirements.txt
cp .env.example .env         # 然后填入 SMTP 凭据
python -m src.main test      # 用几只白马做烟测
python -m src.main run       # 全量 + 邮件 + 落盘
```

命令行：

```
python -m src.main run       # 全量流水线 + 邮件
python -m src.main screen    # 只跑筛选、不发邮件
python -m src.main test      # 烟测几只大白马
```

## 合并后的筛选规则

硬过滤（一票否决，未满足直接剔除）：

| 编号 | 规则 | 数据来源 |
| --- | --- | --- |
| 1 | 剔除 ST / *ST 股票 | 简称字符判定 |
| 2 | 剔除年报非标审计意见股票（默认 `RULE_EXCLUDE_QUALIFIED=true`） | 财报附注披露 |
| 3 | 近两年股息率**每年均** ≥ 4%（按现金分红 / 期末收盘价计算，**不做每股分红金额过滤**） | 巨潮 `stock_dividend_cninfo` |
| 4 | TTM 市盈率 0 < PE < 30 | 新浪日 K + 利润表 |
| 5 | 近 3 年扣非净利润 > 0（主业持续盈利） | 新浪利润表 |
| 6 | 近 3 年 ROE > 10% | 新浪资产负债表 + 利润表 |
| 7 | 资产负债率 ≤ 70%（> 60% 仅警示、不剔除） | 新浪资产负债表 |
| 8 | 分红支付率 ≥ 40%（> 100% 仅警告、不剔除） | 巨潮分红 + 新浪净利润 |
| 9 | OCF ≥ 当年现金分红（默认**仅警告**，可改 `RULE_REQUIRE_OCF_COVERS_DIVIDEND=true` 启用硬过滤） | 新浪现金流量表 |
| 10 | 每股经营活动现金流 > 0 | 新浪现金流量表 |
| 11 | 近 3 年主营收入 YoY 最大跌幅 ≤ 20% | 新浪 / 同花顺 |

**不做市值过滤**：本项目刻意不再对自由流通市值或总股本设定硬阈值，
对应用户最新规则"不要市值"。

所有阈值均通过环境变量调节（见 `.env.example`），调整需提交留痕，
避免被静默改写。

## 输出字段

推送邮件里展示的字段（每只都原值原样呈现，便于人工对照原始披露）：

- 股票代码 / 股票名称（`symbol` / `name`）
- 板块 / 行业（申万分类，``industry_name``）、上市时间（`listing_date`）
- 现价（`close_price`）以及报价时点（`close_price_as_of`）
- PE(TTM)、PB、**市赚率**（PC Ratio = 股价 / 每股经营活动现金流，
  作为 PE 的并列估值参考）、ROE(TTM)
- 每股 OCF、负债率、分红支付率
- 近 2 年每股分红、近 2 年股息率（年报对应收盘价）
- 近 3 年主营收入 YoY
- **在建工程 / 重大事项**（取自同花顺主营构成 + 年报附注，便于人工复查）
- 一次性特别分红、审计意见、负债率/分红现金流等警示标签
- 评分 `score`（按综合评分降序，邮件正文只展示前 50 条）

## 输出

每次运行都会写到：

```
output/pick_YYYY-MM-DD.md      # 提交到 Git 的 markdown
output/pick_YYYY-MM-DD.html    # 邮件正文（带样式）
output/pick_YYYY-MM-DD.json    # 完整结构化结果
```

邮件正文采用 `multipart/alternative`，对不支持 HTML 的客户端自动回退到
纯文本版本。

## 定时任务

仓库自带 `.github/workflows/daily.yml`，每个交易日 10:30 UTC（北京
时间 18:30，A 股收盘后）自动执行 `python -m src.main run`，全量筛选并
把结果发送到你的邮箱。需要你在 GitHub 仓库的 Settings → Secrets 中填入：

| 密钥 | 备注 |
| --- | --- |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` | 通常 `smtp.qq.com:465` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 邮箱账号 + 客户端授权码 |
| `EMAIL_SENDER` / `EMAIL_RECIPIENTS` | 发件人 / 收件人（逗号分隔） |
| `EMAIL_SUBJECT_PREFIX` | 邮件主题前缀（可空） |
| `AKSHARE_PROXY` | 可选 HTTP/HTTPS 代理（国内访问多数站点需要） |
| `TUSHARE_TOKEN` | 可选，Tushare PRO token（用于多源核验） |

也可以在 Actions 页面手动 `Run workflow`：不勾选 `smoke` 会执行完整
流水线并发送邮件；勾选 `smoke` 则只运行 `python -m src.main screen`
筛选、不发邮件（干跑）。

## 数据来源与真实性声明

- 巨潮资讯网（`cninfo.com.cn`）— 历史分红 / 公司公告。
- 新浪财经（`finance.sina.com.cn`）— 资产负债表 / 利润表 / 现金流量表 /
  日 K。
- 同花顺（`10jqka.com.cn`）— 主营业务构成、在建工程标签（推送结果里的
  "在建重大工程" 取自同花顺主营业务构成与年报附注，必须回原文复核）。
- 申万指数（新浪镜像）— 行业分类、行业平均 PE / PB。

**不引用**任何券商研报、雪球评论、媒体观点或付费数据源；阈值与结论
只基于上面四个公开披露来源。详见 `docs/data-sources.md`。

## 开发

```bash
pytest -q
```

测试覆盖指标解析和规则引擎，不依赖网络。"线上冒烟"用 `python -m src.main test`，
会拉取 4 只白马的真实数据。

## 局限性与免责声明

- 新浪免费的财报接口相对正式披露有几小时到数天的延迟。
- AKShare 端点偶尔会调整字段；若某次运行报错，请查阅 Actions 日志和
  上游 changelog。
- 任何标注为"在建重大工程"的字段来自同花顺业务标签，应作为复查线索
  回到年报附注或公告原文交叉验证后再做决策。
- **本项目仅供研究学习使用，不构成任何投资建议。下单前请务必回到原始
  公告核对。**

## 许可证

MIT，详见 `LICENSE`。
