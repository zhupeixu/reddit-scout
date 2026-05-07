---
name: reddit-scout
description: Reddit 选品侦察工具。从真实 Reddit 用户讨论中挖掘跨境电商产品机会，生成深度分析报告（多个具体痛点 + 用户原话引用 + 产品规格建议 + 数据支撑的机会评分）。无需 Reddit API Key，通过 Chrome cookies 访问。三种模式：宽泛/定向/周报；可一键自动推送结果到飞书文档 + 多维表格。当用户要求"跑选品分析"、"找产品机会"、"Reddit 选品"、"选品报告"、"周报"时触发。
---

# Reddit 选品侦察（reddit-scout）

从多个 Reddit 版块抓取热帖评论，由 Claude 深度分析产品机会，输出 1000+ 字的结构化报告，可一键推送到飞书。

## 依赖

```bash
pip install browser_cookie3 anthropic
```

- `browser_cookie3`：自动读取本机 Chrome 的 Reddit 登录态。首次运行 macOS 弹 Keychain 授权窗口，点"始终允许"。
- `anthropic`：Claude API（需 `ANTHROPIC_API_KEY`）
- `lark-cli`（仅 `--bitable` 时需要）：用于创建飞书文档 + 写入多维表

## 三种模式

### 1. 宽泛模式（默认）

适合不知道研究什么品类时，让 Claude 从 6 个版块的热帖中**自主选出 1 个最有价值的产品机会**。

```bash
python3 scripts/scout.py
```

### 2. 定向模式（`--product`）

适合已经有产品方向，让 Claude 规划搜索策略并深度分析该品类。**输出 1 个产品的深度报告**。

```bash
python3 scripts/scout.py --product "女士钱包"
python3 scripts/scout.py -p "women's wallet" --model claude-opus-4-7
```

工作流程：
1. Claude 规划搜索策略（subreddits + 关键词 + 过滤词）
2. 每个 subreddit 内搜索前 2 个关键词，剩余关键词全站搜索
3. 按核心词过滤无关帖，优先目标版块、按评论数排序
4. 抓取 25 帖评论 → Claude 深度分析

### 3. 周报模式（`--weekly`）

横扫 10 个品类版块，让 Claude 识别 **5 个不同方向**的产品机会，适合定期扫描热点。

```bash
python3 scripts/scout.py --weekly
python3 scripts/scout.py -w --bitable     # 推荐：周一早上跑一遍 + 自动入库
```

覆盖品类：BuyItForLife / HomeImprovement / Frugal / Cooking / Parenting / dogs / camping / femalefashionadvice / malelivingspace / declutter

## 飞书自动推送（`--bitable`）

```bash
python3 scripts/scout.py --weekly --bitable
python3 scripts/scout.py -p "攀岩鞋" --bitable
```

加上 `--bitable` 后，分析完成会自动跑完三步：
1. **创建飞书文档**：把整篇报告创建成飞书 docx，记录 doc_url
2. **写入选品记录表**：每个机会一行（含评分细分、痛点摘要、机会点、竞品现状、跟进状态、文档链接）
3. **写入 Reddit 热帖表**：每个机会的证据帖一行，关联到对应选品记录

实现机制：分析 prompt 末尾要求 Claude 输出一个 `<!--BITABLE_DATA{...}-->` JSON 块（HTML 注释包裹，markdown 渲染时不显示），脚本解析后调用 `lark-cli` 完成入库。

### 配置多维表 token

默认指向脚本顶部 `DEFAULT_BITABLE` 中预设的 base/table。如要自己建库，复制 `~/.reddit-scout.json`：

```json
{
  "base_token": "你的_base_token",
  "table_research": "选品记录表_table_id",
  "table_posts": "Reddit热帖表_table_id"
}
```

### 多维表 Schema

**选品记录** 表字段：
- 产品名称（text） / 分析日期（datetime） / 分析模式（select：定向/宽泛/周报）/ 输入方向（text）
- 机会评分（number）/ 需求真实性（number）/ 市场空间（number）/ 差异化可行性（number）
- 核心痛点（text）/ 机会点（text）/ 竞品现状（text）
- 覆盖版块（text）/ 扫描帖数（number）/ 精选帖数（number）
- 跟进状态（select：待评估/研究中/已立项/已放弃）
- 飞书文档（url）/ 备注（text）/ 亚马逊验证（text，可选）

**Reddit 热帖** 表字段：
- 关联研究（link → 选品记录）
- 帖子标题 / 版块 / 赞数 / 评论数 / 高赞评论 / 帖子摘要 / 发现日期

## 模型选择

```bash
python3 scripts/scout.py --weekly --model claude-opus-4-7      # 最强
python3 scripts/scout.py --weekly --model claude-haiku-4-5-20251001  # 最快/最便宜
```

| 模型 | 适合场景 |
|------|---------|
| `claude-opus-4-7` | 正式深度报告 |
| `claude-sonnet-4-6` | 默认，质量/成本均衡 |
| `claude-haiku-4-5-20251001` | 快速验证 |

## 输出路径

```
~/reddit-scout-reports/reddit_<标签>_YYYYMMDD_HHMM.md
```

可用 `--output` 覆盖。

## 报告结构

```
## [产品名]：Reddit 买家痛点深度研究
**品类背景**

### 痛点一/二/三/四：[标题]
（本质 + Reddit 证据 + 用户原话 + 市场分析）

## 机会点 ①②③（具体规格/材质/做法/定价）

## 竞品现状

## 机会评分：X/10
| 维度 | 得分 | 数据依据 |
（需求真实性/市场空间/差异化可行性，每项必须列举本次数据中的帖子数、评论数、赞数）
诚实声明：样本规模限制

## 目标买家画像

## 本次研究数据

<!--BITABLE_DATA
{...}        // 程序解析用的 JSON 块（渲染时不显示）
-->
```

周报模式输出 5 个机会 + 3 个次级信号，每个机会简化版结构。

## 关于机会评分

评分**严格基于本次抓取的真实数据**。Claude 必须在评分表格中列出具体数字（帖子数 / 评论数 / 赞数），并附"诚实声明"——Reddit 讨论量不等于市场规模，分数只反映 Reddit 上的声音。

## 常见问题

**首次运行 macOS Keychain 弹窗**：选"始终允许"，之后不再弹。

**Reddit 评论请求超时**：内置 15s 超时处理，自动跳过。

**`--bitable` 推送失败**：确认 `lark-cli auth login --domain base --recommend` 已完成；检查 `~/.reddit-scout.json` 中 base_token / table_id 正确。

**报告里没有 BITABLE_DATA 块**：模型偶尔会忘记输出 JSON 块，重试一次或换 opus 模型。

## 每日自动化（daily.py + launchd）

`scripts/daily.py` 实现：每天选一个**新方向**做深度分析 → 推送多维表 → 飞书交互式卡片私信。

**避重机制**：脚本启动时读取多维表近 60 天已分析产品，让 Claude 选一个全新方向（考虑时令/季节）。

### 安装定时任务（macOS launchd）

```bash
# 1. 修改 plist 中的接收人 open_id（已默认配置好）
#    在 daily.py 顶部 DAILY_RECIPIENT_OPEN_ID 改成你的

# 2. 复制 plist 到 LaunchAgents
cp scripts/com.perryxu.reddit-scout-daily.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.perryxu.reddit-scout-daily.plist

# 4. 验证（应看到 com.perryxu.reddit-scout-daily）
launchctl list | grep reddit-scout

# 立即触发一次（不等到下次定时）：
launchctl start com.perryxu.reddit-scout-daily
```

修改运行时间：编辑 plist 里的 `StartCalendarInterval`（Hour/Minute）。

**为什么用 launchd 而不是 cron**：launchd 在用户 GUI 会话里运行，能通过 Keychain 解密 Chrome cookies；纯 cron 因为没有 keychain access 经常失败。

### 卡片内容结构

| 区域 | 内容 |
|------|------|
| Header | 🎯 今日 Reddit 选品 · 日期；颜色按评分（≥8 红 / ≥6.5 蓝 / 其他灰） |
| 双列 | 产品方向 / 品类 / 评分 / 数据规模 |
| 选题理由 | 为什么 Claude 今天选了这个方向 |
| 痛点+机会+竞品+买家 | 1-2 句话总结 |
| 评分依据 | 数据声明 |
| 按钮 | 完整报告（飞书文档）/ 多维表 |

### 日志

`~/reddit-scout-reports/daily_YYYYMMDD.log` — 当日执行日志
`~/reddit-scout-reports/launchd.{out,err}.log` — launchd 标准输出
