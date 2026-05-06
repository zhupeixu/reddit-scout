# reddit-scout

一个 Claude Code skill，从 Reddit 真实用户讨论中自动挖掘跨境电商选品机会，生成深度分析报告。

## 效果示例

> **家居文档整理系统：Reddit 买家痛点深度研究**
>
> **机会评分：8.5/10**
>
> ### 痛点一：房屋交接信息断层导致维修成本激增
> r/HomeImprovement「Previous owner left a binder in the garage」（69,271赞 / 1,245评）
> "I was already mentally preparing for an HVAC bill... until I remembered the binder in the garage."
>
> ### 机会点
> ① 实体"房屋护照"套装（精装活页夹 + 预印分类模板 + 二维码数字版），定价 $39.99
> ② 房产中介白标版"交接礼包"，B2B 批发 $15-25/套

## 工作原理

1. 从多个 subreddit 抓取热帖（无需 Reddit API Key，通过本地 Chrome cookies 访问）
2. 取评论数最多的 20 个帖子，抓取高赞评论
3. 由 Claude 自主识别最有价值的产品机会，生成 1000+ 字深度报告

报告结构参考 noinoi 风格：**多个具体痛点 + 用户原话引用 + 产品规格建议 + 竞品分析 + 机会评分 + 买家画像**。

## 安装

```bash
npx skills add zhupeixu/reddit-scout
```

## 依赖

```bash
pip install browser_cookie3 anthropic
```

- **browser_cookie3**：自动读取本机 Chrome 的 Reddit 登录态，首次运行 macOS 会弹 Keychain 授权窗口，点"始终允许"即可
- **ANTHROPIC_API_KEY**：需在环境变量中设置

## 使用

安装 skill 后，在 Claude Code 中直接说：

> "跑一次选品分析"
> "帮我做 Reddit 选品，重点看宠物用品"
> "用 opus 模型跑一次深度选品分析"

Claude 会自动调用脚本，完成后输出完整报告。

也可以直接运行脚本：

```bash
# 默认（claude-sonnet，质量/成本均衡）
python3 scripts/scout.py

# 更深度分析
python3 scripts/scout.py --model claude-opus-4-7

# 快速便宜
python3 scripts/scout.py --model claude-haiku-4-5-20251001
```

## 配置 Subreddit

修改 `scripts/scout.py` 顶部的 `SUBREDDITS` 变量，`references/subreddits.md` 里有按品类整理的推荐组合：

| 品类 | 推荐组合 |
|------|---------|
| 家居刚需（默认） | BuyItForLife + Frugal + HomeImprovement |
| 宠物 | dogs + cats + puppy101 + BuyItForLife |
| 健身/户外 | Fitness + running + camping + HydroHomies |
| 厨房/食品 | Cooking + MealPrepSunday + Frugal |
| 育儿 | Parenting + beyondthebump + BuyItForLife |

## 可选：推送到飞书

报告生成后，可通过 `lark-cli` 创建飞书文档并发私信，详见 `SKILL.md`。
