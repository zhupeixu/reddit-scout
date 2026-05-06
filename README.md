# reddit-scout

一个 Claude Code skill，从 Reddit 真实用户讨论中自动挖掘跨境电商选品机会，生成深度分析报告。

## 效果示例

> **女士钱包：Reddit 买家痛点深度研究**
>
> **机会评分：6/10**（需求真实性 2/3，市场空间 2/3，差异化可行性 2/4）
>
> **数据依据**：14 个帖子 / 共 112 条评论，其中 5 篇帖子（累计 734赞）明确提及材质脱皮/变形问题。
>
> ### 痛点一：钱包在数月内鼓胀变形或开裂脱皮
> r/BuyItForLife「Best wallets for women that actually last」（223赞 / 176评）
> "I am tired of going through womens wallets that either become bulky or start falling apart after a short time."
>
> ### 机会点
> ① 植鞣全粒面皮革，厚度 ≥1.2mm，手工缝线（非胶粘），定价 $45-65
> ② 卡槽内衬改为光面黄铜/尼龙，消除卡片卡顿，作为核心产品页面卖点

## 工作原理

1. 两种模式：**宽泛模式**（自动发现机会）或**定向模式**（指定产品方向）
2. 无需 Reddit API Key，通过本地 Chrome cookies 访问
3. Claude 规划搜索策略 → 抓取热帖+高赞评论 → 深度分析买家痛点
4. 机会评分要求列出具体数据（帖子数、评论数、赞数），不凭感觉打分

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
> "用 opus 模型分析一下女士钱包"

Claude 会自动调用脚本，完成后输出完整报告。

也可以直接运行脚本：

```bash
# 宽泛模式（自动发现机会，默认 claude-sonnet）
python3 scripts/scout.py

# 定向模式（指定产品方向）
python3 scripts/scout.py --product "女士钱包"
python3 scripts/scout.py -p "women's wallet" --model claude-opus-4-7

# 指定输出目录（默认 ~/reddit-scout-reports/）
python3 scripts/scout.py -p "pet carrier" --output ~/my-reports/
```

## 配置宽泛模式的 Subreddit

修改 `scripts/scout.py` 顶部的 `BROAD_SUBREDDITS` 变量，`references/subreddits.md` 里有按品类整理的推荐组合：

| 品类 | 推荐组合 |
|------|---------|
| 家居刚需（默认） | BuyItForLife + Frugal + HomeImprovement |
| 宠物 | dogs + cats + puppy101 + BuyItForLife |
| 健身/户外 | Fitness + running + camping + HydroHomies |
| 厨房/食品 | Cooking + MealPrepSunday + Frugal |
| 育儿 | Parenting + beyondthebump + BuyItForLife |
| 女性时尚 | femalefashionadvice + handbags + BuyItForLife |

## 关于机会评分

评分**严格基于本次抓取的数据**，报告中会列出评分依据表格：

| 维度 | 得分 | 数据依据 |
|------|------|---------|
| 需求真实性 | X/3 | 明确提及该痛点的帖子数、评论数、总赞数 |
| 市场空间 | X/3 | 搜索结果密度、版块规模 |
| 差异化可行性 | X/4 | 竞品缺陷引用 vs 改进方案可行性 |

并附"诚实声明"：Reddit 讨论量不等于市场规模，评分仅反映 Reddit 上的声音。

## 可选：推送到飞书

报告生成后，可通过 `lark-cli` 创建飞书文档并发私信，详见 `SKILL.md`。
