# China-Stock-Choose · 每日 A 股选股推送

> 每日对中国 A 股全市场做一次规则化筛选，将命中清单以邮件推送到你的
> 邮箱，同时把可读化的结果提交到这个仓库。所有数据均来自公开披露来源，
> 不引入任何第三方研报或主观评论。

| 项目 | 内容 |
| --- | --- |
| **仓库地址** | https://github.com/pephupephu/China-Stock-Choose |
| **运行环境** | Python 3.10+，AKShare（巨潮 / 新浪 / 同花顺 / 申万数据） |
| **执行时间** | 每个交易日 18:30 UTC（次日 02:30 北京时间，收盘后） |
| **许可证** | MIT |

## 功能要点

- 全 A 遍历执行"硬过滤 + 打分"的筛选流水线；
- 多源核验：巨潮分红、新浪财务报表、同花顺主营构成、申万行业分类；
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
| 2 | 剔除年报非标审计意见股票 | 财报附注披露 |
| 3 | 近两年股息率 ≥ 4%（按现金分红 + 最新收盘价计算，剔除送股/转增） | 巨潮 `stock_dividend_cninfo` |
| 4 | TTM 市盈率 0 < PE < 30 | 新浪日 K + 利润表 |
| 5 | 近 3 年扣非净利润 > 0（主业持续盈利） | 新浪利润表 |
| 6 | ROE(TTM) ≥ 10% | 新浪资产负债表 + 利润表 |
| 7 | 资产负债率 ≤ 70% | 新浪资产负债表 |
| 8 | 分红支付率 ≥ 40%（>100% 仅警告、不剔除） | 巨潮分红 + 新浪净利润 |
| 9 | 当年经营性现金流 ≥ 现金分红总额 | 新浪现金流量表 |
| 10 | 每股经营活动现金流 > 0 | 新浪现金流量表 |
| 11 | 近 3 年主营收入 YoY 最大跌幅 ≤ 20% | 新浪 / 同花顺 |

阈值全部通过环境变量调节（见 `.env.example`）。所有阈值的"调整"都需要
通过提交变更来留痕，避免被静默改写。

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

仓库自带 `.github/workflows/daily.yml`，A 股收盘后自动执行。需要你在
GitHub 仓库的 Settings → Secrets 中填入：

| 密钥 | 备注 |
| --- | --- |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` | 通常 `smtp.qq.com:465` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 邮箱账号 + 客户端授权码 |
| `EMAIL_SENDER` / `EMAIL_RECIPIENTS` | 发件人 / 收件人（逗号分隔） |
| `EMAIL_SUBJECT_PREFIX` | 邮件主题前缀（可空） |
| `AKSHARE_PROXY` | 可选 HTTP/HTTPS 代理（国内访问多数站点需要） |

也可以在 Actions 页面手动 `Run workflow`，勾选 `smoke` 选项在测试时
禁用邮件。

## 数据来源

- 巨潮资讯网（`cninfo.com.cn`）— 历史分红 / 公司公告。
- 新浪财经（`finance.sina.com.cn`）— 资产负债表 / 利润表 / 现金流量表 / 日 K。
- 同花顺（`10jqka.com.cn`）— 主营业务构成。
- 申万指数（新浪镜像）— 行业分类、行业平均 PE / PB。

详见 `docs/data-sources.md`。

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
- **本项目仅供研究学习使用，不构成任何投资建议。下单前请务必回到原始
  公告核对。**

## 许可证

MIT，详见 `LICENSE`。