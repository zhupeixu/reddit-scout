---
name: reddit-scout
description: Reddit 选品侦察工具。从真实 Reddit 用户讨论中挖掘跨境电商产品机会，生成深度分析报告（仿 noinoi 风格：多个具体痛点 + 用户原话引用 + 产品规格建议 + 机会评分）。无需 Reddit API Key，通过 Chrome cookies 访问。可选将报告推送至飞书文档并发私信给用户。当用户要求"跑选品分析"、"找产品机会"、"Reddit 选品"、"选品报告"时触发。
---

# Reddit 选品侦察（reddit-scout）

从多个 Reddit 版块抓取热帖评论，由 Claude 深度分析出一个最有价值的产品机会，输出约 1000+ 字的结构化报告，可选推送飞书。

## 依赖

运行前确认已安装：

```bash
pip install browser_cookie3 anthropic
```

- `browser_cookie3`：自动读取本机 Chrome 的 Reddit 登录态，无需 API Key、无需配置——前提是 Chrome 里已登录 Reddit。首次运行时 macOS 会弹系统授权窗口（"python3 想要访问 Chrome Safe Storage"），点"始终允许"即可，之后不再弹
- `anthropic`：Claude API（需设置环境变量 `ANTHROPIC_API_KEY`）

## 核心脚本

`scripts/scout.py` — 一键完成抓取 + 分析 + 保存报告。

```bash
# 默认（claude-sonnet-4-6，质量/成本均衡）
python3 /path/to/reddit-scout/scripts/scout.py

# 指定更强的模型（分析更深，成本更高）
python3 scripts/scout.py --model claude-opus-4-7

# 指定更快/更便宜的模型（快速验证）
python3 scripts/scout.py --model claude-haiku-4-5-20251001
```

可用模型：

| 模型 | 特点 | 适合场景 |
|------|------|---------|
| `claude-opus-4-7` | 最强分析深度 | 正式报告、要求高 |
| `claude-sonnet-4-6` | 质量/成本均衡（默认） | 日常使用 |
| `claude-haiku-4-5-20251001` | 最快/最便宜 | 快速测试、验证数据 |

输出路径：`/tmp/reddit_deep_YYYYMMDD_HHMM.md`

### 可调参数（脚本顶部 SUBREDDITS 变量）

```python
SUBREDDITS = [
    ("BuyItForLife", "top", "week"),   # (版块名, 排序, 时间段)
    ("Frugal", "top", "week"),
    ("HomeImprovement", "hot", None),  # hot 不需要时间段
]
POSTS_PER_SUB = 15           # 每版块帖子数
COMMENTS_PER_POST = 30       # 每帖最多评论数
TOP_POSTS_FOR_ANALYSIS = 20  # 取评论数前N帖送分析
```

要更换版块，参考 `references/subreddits.md` 中按品类整理的推荐组合，直接修改 `SUBREDDITS` 变量后重新运行。

## 工作流程

### 基础流程：生成分析报告

1. 运行脚本（约 2-3 分钟）
2. 读取输出文件，呈现报告内容给用户

```bash
python3 scripts/scout.py
```

报告保存至 `/tmp/reddit_deep_YYYYMMDD_HHMM.md`，运行结束时输出路径。

### 进阶流程：推送飞书文档 + 私信

报告生成后，执行以下步骤（需 `lark-cli` 已配置）：

**Step 1 — 创建飞书文档**

```bash
lark-cli docs +create \
  --title "Reddit 选品机会：[产品名] (YYYY-MM-DD)" \
  --markdown "$(cat /tmp/reddit_deep_YYYYMMDD_HHMM.md)"
```

记录返回的 `doc_url`。

**Step 2 — 发送产品图片**（可选）

图片必须先下载到本地，使用相对路径发送：

```bash
cd ~
lark-cli im +messages-send \
  --user-id <用户open_id> \
  --image ./product_image.jpg \
  --as bot
```

**Step 3 — 发送摘要 + 文档链接**

```bash
lark-cli im +messages-send \
  --user-id <用户open_id> \
  --as bot \
  --markdown $'## [产品名]：Reddit 选品机会\n\n**机会评分：** X/10\n\n**核心痛点：**\n- 痛点一\n- 痛点二\n\n**机会点：**\n- 方向一\n- 方向二\n\n完整报告：<doc_url>'
```

飞书 markdown 不支持 `---` 分隔线（渲染为 `<br>`），消息中不要使用，用空行替代。

## 报告结构（Claude 输出格式）

脚本让 Claude 输出以下结构（约 1000+ 字，单次单品深度分析）：

```
## [产品名]：Reddit 买家痛点深度研究

**品类背景**（2-3句：市场规模、核心用户群）

### 痛点一：[具体标题]
本质 + Reddit 证据（r/XX，「帖子标题」X赞/X评）
用户原话（英文原文引用）
市场分析

### 痛点二/三/四：...（同上结构）

## 机会点
① 具体方向（材质/尺寸/制造可行性/卖点文案方向）
② ...
③ ...

## 竞品现状（现有竞品、定价区间、市场缺口）

## 机会评分：X/10
需求真实性X分 / 市场空间X分 / 差异化可行性X分
综合判断（含风险）

## 目标买家画像（人群/购买动机/价格敏感度/触达渠道）

## 本次研究数据（覆盖版块/扫描帖数/精选讨论数）
```

## 常见问题

**Keychain 弹窗**：首次运行 macOS 弹窗要求访问 Chrome 密钥串，选"始终允许"。之后无需重复操作。

**Reddit 某帖评论请求超时**：已内置超时处理，自动跳过，不影响整体分析。

**报告中出现"请选A/B"等交互内容**：说明 Claude 判断当前数据中无明显产品机会，可换一批 subreddit 或增大 `TOP_POSTS_FOR_ANALYSIS`。

**飞书发图片路径报错**：图片必须用相对路径。先 `cd ~`，再 `--image ./filename.jpg`，不能用绝对路径如 `/tmp/xxx.jpg`。
