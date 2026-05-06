# reddit-scout

一个 Claude Code skill，从 Reddit 真实用户讨论中自动挖掘跨境电商选品机会，生成深度分析报告，可一键推送到飞书文档 + 多维表格。

## 核心特性

- **三种模式**：宽泛（自动发现）/ 定向（指定品类）/ 周报（横扫 10 个品类输出 5+ 机会）
- **数据支撑评分**：评分必须列出本次数据中的帖子数 / 评论数 / 赞数，不允许凭感觉打分
- **一键飞书入库**：`--bitable` flag 自动跑完「创建飞书文档 + 写入多维表」三步
- **无需 API Key**：通过本地 Chrome cookies 访问 Reddit

## 安装

```bash
npx skills add zhupeixu/reddit-scout
```

依赖：

```bash
pip install browser_cookie3 anthropic
```

- `browser_cookie3`：自动读取本机 Chrome 的 Reddit 登录态。首次运行 macOS 弹 Keychain 授权，点"始终允许"
- `ANTHROPIC_API_KEY`：环境变量
- `lark-cli`（仅 `--bitable` 时需要）：飞书 CLI

## 使用

安装 skill 后，直接对 Claude Code 说：

> "跑一次选品分析"
> "做一份本周 Reddit 选品周报，存进飞书"
> "深度分析女士钱包品类，结果入库"

也可以直接命令行：

```bash
# 宽泛模式（自动发现 1 个产品机会）
python3 scripts/scout.py

# 定向模式（深度分析指定品类）
python3 scripts/scout.py --product "女士钱包"

# 周报模式（5 个不同方向的机会）
python3 scripts/scout.py --weekly

# 加 --bitable：自动创建飞书文档 + 推送多维表
python3 scripts/scout.py --weekly --bitable
python3 scripts/scout.py -p "攀岩训练板" -b
```

## 报告样例

> **女士钱包：Reddit 买家痛点深度研究**
>
> **机会评分：7.5/10**（需求真实性 2.5/3，市场空间 2/3，差异化可行性 3/4）
>
> **数据依据**：14 个帖子 / 共 112 条评论，其中 5 篇帖子（累计 734 赞）明确提及材质脱皮/变形。
>
> ### 痛点一：钱包在数月内鼓胀变形或开裂脱皮
> r/BuyItForLife「Best wallets for women that actually last」（223 赞 / 176 评）
> "I am tired of going through womens wallets that either become bulky or start falling apart..."
>
> ### 机会点
> ① 植鞣全粒面皮革，厚度 ≥1.2mm，手工缝线（非胶粘），定价 $45-65
> ② 卡槽内衬改为光面黄铜/尼龙，消除卡片卡顿

## 飞书多维表自动推送

加上 `--bitable` 后，分析完成会自动：

1. **创建飞书文档**：把整篇 markdown 报告创建成 docx，记录 `doc_url`
2. **写入选品记录表**：每个机会一行（含评分细分、痛点、机会点、竞品现状、跟进状态、文档链接）
3. **写入 Reddit 热帖表**：每个机会的证据帖一行，关联到对应选品记录

实现机制：分析 prompt 末尾让 Claude 输出 `<!--BITABLE_DATA{...}-->` JSON 块（HTML 注释包裹，渲染时不显示），脚本解析后调用 `lark-cli` 入库。

第一次使用要先：

```bash
lark-cli auth login --domain base --recommend
lark-cli auth login --domain doc  --recommend
```

然后修改 `scripts/scout.py` 顶部 `DEFAULT_BITABLE` 的 base_token / table_id 指向你自己的多维表，或者在 `~/.reddit-scout.json` 里覆盖。

## 模型选择

| 模型 | 特点 | 适合场景 |
|------|------|---------|
| `claude-opus-4-7` | 最强分析深度 | 正式报告 |
| `claude-sonnet-4-6` | 默认，质量/成本均衡 | 日常使用 |
| `claude-haiku-4-5-20251001` | 最快/最便宜 | 快速验证 |

```bash
python3 scripts/scout.py --weekly --model claude-opus-4-7
```

## 关于机会评分

评分严格基于本次抓取的数据，报告中会列出：

| 维度 | 得分 | 数据依据 |
|------|------|---------|
| 需求真实性 | X/3 | 明确提及该痛点的帖子数、评论数、总赞数 |
| 市场空间 | X/3 | 搜索结果密度、版块规模 |
| 差异化可行性 | X/4 | 竞品缺陷引用 vs 改进方案可行性 |

并附"诚实声明"：Reddit 讨论量不等于市场规模，评分仅反映 Reddit 上的声音。

## 输出

```
~/reddit-scout-reports/reddit_<标签>_YYYYMMDD_HHMM.md
```

可用 `--output` 覆盖。
