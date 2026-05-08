#!/usr/bin/env python3
"""
每日选品自动化（reddit-scout daily）

执行流程：
1. 查询多维表近 60 天已分析产品（避免重复）
2. 让 Claude 选一个新的当下热门方向
3. 用定向模式跑深度分析
4. 创建飞书文档 + 推送多维表
5. 把分析结果做成飞书交互式卡片，私信发给指定用户

适合 launchd / cron 每日定时调用。
"""
import json, subprocess, time, datetime, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scout import (
    load_bitable_config, get_cookies, plan_search, search_reddit,
    fetch_comments, save_report, create_feishu_doc, push_to_bitable,
    extract_bitable_data, analyze_targeted, discover_relevant_subreddits,
    filter_posts_by_relevance, amazon_validate, reddit_get,
    MAX_POSTS_FOR_ANALYSIS, DEFAULT_MODEL,
)

# ── 用户配置 ──────────────────────────────────────────────
DAILY_RECIPIENT_OPEN_ID = "ou_0df2b09f3185bb15e3c1ea089a80e75e"  # scout-bot 命名空间下的 open_id
LARK_PROFILE = "scout-bot"  # lark-cli profile 名（出海选品客服机器人）
BITABLE_DOMAIN = "ycnm1prsz3tg.feishu.cn"
DAYS_LOOKBACK = 60
MODEL = DEFAULT_MODEL
# ─────────────────────────────────────────────────────────

client = anthropic.Anthropic(
    auth_token=os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"),
)


def get_recent_products(days=DAYS_LOOKBACK):
    """从多维表读取最近 N 天分析过的产品名 + 输入方向 + 品类"""
    cfg = load_bitable_config()
    r = subprocess.run([
        "lark-cli", "base", "+record-list",
        "--base-token", cfg["base_token"],
        "--table-id", cfg["table_research"],
        "--limit", "200", "--format", "json"
    ], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        if not d.get("ok"):
            return [], [], []
        data = d["data"]
        fields = data["fields"]
        if "产品名称" not in fields or "分析日期" not in fields:
            return [], [], []
        name_idx = fields.index("产品名称")
        date_idx = fields.index("分析日期")
        dir_idx = fields.index("输入方向") if "输入方向" in fields else None
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        names, dirs, recent_rows = [], [], []
        for row in data["data"]:
            try:
                date_str = row[date_idx]
                if isinstance(date_str, str):
                    dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
                    if dt >= cutoff:
                        n = row[name_idx]
                        if isinstance(n, list): n = n[0] if n else ""
                        if n:
                            names.append(n)
                            recent_rows.append((dt, n))
                        if dir_idx is not None:
                            dr = row[dir_idx]
                            if isinstance(dr, list): dr = dr[0] if dr else ""
                            if dr: dirs.append(dr)
            except (ValueError, TypeError, IndexError):
                continue
        # 按日期降序，取最近 5 个产品名（用于品类轮换约束）
        recent_rows.sort(key=lambda x: x[0], reverse=True)
        last_5 = [n for _, n in recent_rows[:5]]
        return names, dirs, last_5
    except Exception:
        return [], [], []


def pick_fresh_direction(recent_names, recent_dirs, last_5_names=None, model=MODEL):
    """让 Claude 选一个不重复的当下热门方向，并强制品类轮换"""
    today = datetime.date.today().isoformat()
    seen = sorted(set(recent_names + recent_dirs))[:80]
    seen_block = "\n".join(f"- {p}" for p in seen) if seen else "（无历史）"
    last_5_block = "\n".join(f"- {p}" for p in (last_5_names or [])) or "（无）"
    prompt = f"""你是一名跨境电商选品研究员。今天是 {today}。

最近 {DAYS_LOOKBACK} 天已分析过的产品（不要重复或近似）：
{seen_block}

**最近 5 次分析的产品（按时间降序）**：
{last_5_block}

请挑选一个**全新**的产品方向，**严格遵守**：

**1. 品类必须轮换（最重要）**
从下面 12 个大品类里选：
户外/露营 | 厨房/烹饪 | 家居/收纳 | 宠物 | 育儿/母婴 | 健身/运动恢复 | 个护/美妆 | 办公/学习 | 汽车配件 | 工具/DIY | 园艺/植物 | 服饰/配饰

**规则**：如果"最近 5 次"里某品类出现 ≥ 2 次，本次**禁止**选该品类；优先选"最近 5 次"完全没出现过的品类。

**2. 时令辅助**
在轮换允许的品类里，再考虑节令/季节趋势。**轮换 > 时令**——宁可选轮换品类的相对冷门产品，也不要硬选当季但重复的品类。

**3. 必须满足**
- 在 Reddit 英文社区有真实买家讨论
- 实物 SKU 可制造可发货（非软件/服务/数字/食品）
- 用 1-3 个英文词描述（便于 Reddit 搜索）

只返回 JSON：
{{"direction": "英文产品方向（如 cat litter mat / women hiking pants / standing desk converter）", "category": "中文大品类（必须是上面 12 类之一）", "reason_cn": "为什么挑这个的中文 1 句话理由，**必须明确说明本次避开了哪个最近高频出现的品类**"}}"""
    resp = client.messages.create(
        model=model, max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()
    return json.loads(raw)


def build_card(opp, direction, reason, doc_url, bitable_url, scan, amazon_info=None):
    """构造飞书交互式卡片 JSON"""
    today = datetime.date.today().isoformat()
    score = opp.get("score", "?")
    template = "red" if isinstance(score, (int, float)) and score >= 8 else \
               "blue" if isinstance(score, (int, float)) and score >= 6.5 else "grey"

    pain = (opp.get("pain_summary") or "").strip()
    op = (opp.get("opportunity_summary") or "").strip()
    comp = (opp.get("competition_summary") or "").strip()
    persona = (opp.get("buyer_persona") or "").strip()
    notes = (opp.get("notes") or "").strip()
    amazon_summary = (opp.get("amazon_validation") or "").strip()

    elements = [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**📦 产品方向**\n{opp.get('product_name', direction)}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**🏷️ 品类**\n{opp.get('category', '?')}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**📊 机会评分**\n**{score}/10**"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**📈 数据规模**\n{scan.get('posts_scanned')} 帖 / {scan.get('comments_analyzed')} 评"}},
            ]
        },
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**🤖 今日选题理由**\n{reason}"},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**🔥 核心痛点**\n{pain}"},
        {"tag": "markdown", "content": f"**💡 机会点**\n{op}"},
    ]
    if comp:
        elements.append({"tag": "markdown", "content": f"**🏪 竞品现状**\n{comp}"})

    # Amazon 验证区块
    if amazon_info or amazon_summary:
        elements.append({"tag": "hr"})
        if amazon_info:
            top_skus = amazon_info.get("top_skus", [])[:3]
            kw_market = amazon_info.get("keyword_market", [])[:1]
            kw_text = ""
            if kw_market:
                k = kw_market[0]
                kw_text = (f"  · {k.get('keyword','?')} 月搜 **{k.get('monthly_searches','?')}** | "
                           f"增长 {k.get('growth_pct','?')}% | 均价 ${k.get('avg_price','?')} | "
                           f"PPC ${k.get('bid_avg','?')} | 头部 {k.get('top_brands','')}\n")
            sku_text = "\n".join(
                f"  · {s.get('brand','?')} ${s.get('price','?')} · 月销 **{s.get('units_monthly','?')}** "
                f"({s.get('rating','?')}★/{s.get('ratings_count','?')}评)"
                for s in top_skus
            ) or "  暂无数据"
            cat = amazon_info.get("category", "?").split(":")[-1]
            elements.append({"tag": "markdown",
                "content": f"**🛒 亚马逊验证（{amazon_info.get('month','')}，类目: {cat}）**\n\n{kw_text}\n**Top 3 SKU:**\n{sku_text}"})
        if amazon_summary:
            elements.append({"tag": "markdown", "content": f"**📌 亚马逊洞察**\n{amazon_summary}"})

    if persona:
        elements.append({"tag": "markdown", "content": f"**🎯 目标买家**\n{persona}"})
    if notes:
        elements.append({"tag": "note", "elements": [{"tag": "lark_md", "content": f"📐 评分依据：{notes}"}]})

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "📄 完整报告"}, "type": "primary", "url": doc_url, "multi_url": {"url": doc_url, "pc_url": doc_url, "ios_url": doc_url, "android_url": doc_url}},
            {"tag": "button", "text": {"tag": "plain_text", "content": "📋 多维表"}, "type": "default", "url": bitable_url, "multi_url": {"url": bitable_url, "pc_url": bitable_url, "ios_url": bitable_url, "android_url": bitable_url}},
        ]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"🎯 今日 Reddit 选品 · {today}"}
        },
        "elements": elements
    }


def send_card(open_id, card_json):
    r = subprocess.run([
        "lark-cli", "im", "+messages-send",
        "--user-id", open_id,
        "--msg-type", "interactive",
        "--content", json.dumps(card_json, ensure_ascii=False),
        "--as", "bot",
        "--profile", LARK_PROFILE,
    ], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip().startswith("{"):
        print(f"❌ 私信发送失败: {r.stdout[:300]} {r.stderr[:300]}")
        return False
    try:
        d = json.loads(r.stdout)
        return d.get("ok", False)
    except json.JSONDecodeError:
        return False


# ── Reddit 主导的双阶段选题 ─────────────────────────────

# 12 个跨品类的"买家信号版块"——每个品类挑 1 个真实买家讨论密集的版块
SIGNAL_SUBREDDITS = [
    "BuyItForLife",   # 综合：耐用品讨论
    "Frugal",         # 综合：性价比/推荐
    "ShouldIBuyThis", # 综合：购买咨询
    "Cooking",        # 厨房
    "HomeImprovement",# 家居
    "Parenting",      # 育儿
    "pets",           # 宠物
    "Fitness",        # 健身
    "SkincareAddiction", # 个护
    "malelivingspace",# 男性家居
    "femalefashionadvice", # 女性服饰
    "DIY",            # 工具/DIY
]


def discover_reddit_candidates(seen_products, cookie_str, n_candidates=10, model="claude-sonnet-4-6"):
    """
    从 Reddit 信号版块抓近 7 天热帖 → Haiku 提取候选产品方向（去重历史）
    返回候选列表，每个含: direction / category / source_posts / signal_count
    """
    print("📡 扫信号版块（近 7 天，并行）...", flush=True)
    all_posts = []

    def _fetch_sub(sub):
        posts = reddit_get(
            f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=15",
            cookie_str
        )
        out = []
        if posts and isinstance(posts, dict):
            for item in posts.get('data', {}).get('children', []):
                d = item.get('data', {})
                if d.get('id'):
                    out.append({
                        "subreddit": sub,
                        "title": d.get('title', ''),
                        "selftext": (d.get('selftext') or '')[:200],
                        "score": d.get('score', 0),
                        "num_comments": d.get('num_comments', 0),
                    })
        return out

    # 4 并发足够,Reddit 对未登录-like 流量在 8+ 并发开始限流
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed(ex.submit(_fetch_sub, s) for s in SIGNAL_SUBREDDITS):
            all_posts.extend(fut.result())
    print(f"   抓到 {len(all_posts)} 条原始信号", flush=True)

    if not all_posts:
        return []

    # Haiku 抽取候选产品方向
    posts_block = "\n".join(
        f"{i+1}. r/{p['subreddit']} ({p['score']}赞/{p['num_comments']}评) {p['title'][:120]}"
        + (f" | {p['selftext'][:80]}" if p['selftext'] else "")
        for i, p in enumerate(all_posts[:120])  # 限制 token
    )
    seen_block = "\n".join(f"- {n}" for n in seen_products[:30]) if seen_products else "（无历史）"

    prompt = f"""你是跨境电商选品分析师。下面是 Reddit 12 个买家信号版块近 7 天的热帖。

请从中**抽取 {n_candidates} 个具体可制造的产品方向**——必须是实物 SKU（不是软件/服务/食品/宠物本身），且**不能与下面已分析过的方向重复或近似**。

【已分析过的方向（避重）】
{seen_block}

【抽取规则】
- 优先含明确购买/咨询/抱怨/求推荐信号的帖子（"recommend"/"wish there was"/"problem with"/"best for"）
- 跨品类：12 个里要至少覆盖 6 个不同品类（厨房/家居/宠物/育儿/健身/个护/服饰/办公/汽车/工具/园艺/户外）
- 用 1-3 个英文词描述（便于后续 Reddit 搜索）

【输出格式】严格按格式输出 {n_candidates} 行，每行 1 个候选：
`英文产品方向|中文品类|参考帖序号(如 3,17,42)|为什么这是机会(中文 ≤25 字)`

例：
cat litter mat|宠物|5,12|高赞抱怨现有产品太薄
standing desk converter|办公|8,33|多人求推荐升降桌方案

只输出 {n_candidates} 行，不要其他内容。

【近 7 天热帖】
{posts_block[:18000]}"""

    try:
        resp = client.messages.create(
            model=model, max_tokens=2000,
            system="你是跨境电商选品分析助手，严格按用户指定的格式和语言输出，不要解释身份或拒绝任务。",
            messages=[{"role": "user", "content": prompt}]
        )
        lines = resp.content[0].text.strip().split("\n")
    except Exception as e:
        print(f"⚠️  Haiku 抽取失败: {e}")
        return []

    candidates = []
    import re as _re
    for ln in lines:
        # 去掉行首的 "N. " 或 "N、" 等编号前缀
        clean = _re.sub(r'^\s*\d+[\.\)、]\s*', '', ln.strip())
        # 必须是 4 段 | 分隔的格式
        parts = [p.strip() for p in clean.split("|")]
        if len(parts) < 4:
            continue
        # 第一段必须含英文（产品方向）
        if not _re.search(r'[a-zA-Z]', parts[0]):
            continue
        try:
            refs = [int(x.strip()) - 1 for x in parts[2].split(",") if x.strip().isdigit()]
            source_posts = [all_posts[i] for i in refs if 0 <= i < len(all_posts)]
            candidates.append({
                "direction": parts[0],
                "category": parts[1],
                "hint": parts[3],
                "source_posts": source_posts,
            })
        except (ValueError, IndexError):
            continue
    print(f"   Haiku 抽出 {len(candidates)} 个候选", flush=True)
    return candidates


def score_reddit_candidate(candidate, cookie_str):
    """
    给一个候选打分：
    - 近 30 天 Reddit search 帖数
    - 累计赞 / 累计评论
    - 含买家信号词的帖子比例（recommend/wish/problem/frustrated/looking for）
    """
    direction = candidate["direction"]
    posts = search_reddit(direction, cookie_str=cookie_str, limit=30, timeframe="month")
    if not posts:
        return 0, {"posts_30d": 0, "total_score": 0, "total_comments": 0, "buyer_signal_pct": 0}

    BUYER_SIGNALS = ["recommend", "wish there", "best for", "looking for",
                      "problem with", "frustrated", "issue with", "anyone tried",
                      "should i buy", "anyone use", "what brand", "any good",
                      "worth it", "vs.", "review of", "comparison"]
    # 注意:不要用 "or"/"vs"/"review" 这种短词做子串匹配,会被 "for"/"before"/"works" 误命中,
    # 把宠物情感帖也打成"含买家信号"。要么换成多词短语,要么用正则词边界。
    total_score = sum(p.get('score', 0) for p in posts)
    total_comments = sum(p.get('num_comments', 0) for p in posts)
    buyer_signal = sum(
        1 for p in posts
        if any(sig in (p.get('title', '') + ' ' + p.get('selftext', '')).lower()
               for sig in BUYER_SIGNALS)
    )
    pct = buyer_signal / max(len(posts), 1)

    # 综合分：log(帖数) × log(累计赞) × (1 + 买家信号比例)
    import math
    score = (
        math.log(max(len(posts), 1) + 1) * 30
        + math.log(max(total_score, 1) + 1) * 5
        + math.log(max(total_comments, 1) + 1) * 3
        + pct * 50
    )
    return round(score, 1), {
        "posts_30d": len(posts),
        "total_score": total_score,
        "total_comments": total_comments,
        "buyer_signal_pct": round(pct * 100, 1),
    }


def reddit_driven_pick(seen_products, last_5_names, cookie_str):
    """
    Reddit 主导的双阶段选题：
    1. 候选生成（扫信号版块 + Haiku 抽取）
    2. 每个候选打分（近 30 天讨论密度 + 买家信号）
    3. 返回 Top 1 + 全部排名（用作"为什么是它"的事前依据）
    """
    candidates = discover_reddit_candidates(seen_products, cookie_str, n_candidates=10)
    if not candidates:
        return None, []

    print("📊 候选打分（近 30 天 Reddit 讨论密度,并行）...", flush=True)
    # 4 并发,10 个候选打分由 ~30s 串行 → ~10s 并行
    with ThreadPoolExecutor(max_workers=4) as ex:
        future_to_cand = {ex.submit(score_reddit_candidate, c, cookie_str): c for c in candidates}
        ranked = []
        for fut in as_completed(future_to_cand):
            c = future_to_cand[fut]
            score, stats = fut.result()
            c["score"] = score
            c["stats"] = stats
            ranked.append(c)
            print(f"   · {c['direction']:<35} 分 {score:>6.1f}  | {stats['posts_30d']:>3} 帖, {stats['total_score']:>6} 赞, 信号词 {stats['buyer_signal_pct']}%")

    ranked.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n🥇 Top 候选：{ranked[0]['direction']}（{ranked[0]['category']}，分 {ranked[0]['score']}）", flush=True)
    return ranked[0], ranked


def generate_data_driven_reason(direction, amazon_info, reddit_stats, ranked_candidates=None, model="claude-sonnet-4-6"):
    """
    基于真实数据生成专业选题理由（用 Haiku 快速生成）。
    优于 pick_fresh_direction 给的"避开 X 品类"机械理由。
    """
    amz_block = ""
    amz_insufficient = False
    if amazon_info:
        kw = amazon_info.get("keyword_market", [])
        kw_text = " | ".join(
            f"'{k['keyword']}': 月搜{k.get('monthly_searches','?')}, 增长{k.get('growth_pct','?')}%, 供需比{k.get('supply_demand_ratio','?')}, 均价${k.get('avg_price','?')}"
            for k in kw[:2]
        )
        top_skus = amazon_info.get("top_skus", []) or []
        top_brands = list({s.get("brand") for s in top_skus[:5] if s.get("brand")})
        top_units = sum((s.get("units_monthly") or 0) for s in top_skus[:3])
        # 防脑补：Amazon 数据不足时,显式标记,prompt 里禁止虚构市场结论
        if len(top_skus) == 0:
            amz_insufficient = True
            amz_block = f"""
- Amazon 类目: {amazon_info.get('category','?').split(':')[-1]}（{amazon_info.get('category_products_total','?')} 商品）
- ⚠️ Top SKU 数据为 0(关键词过滤后无匹配 / 类目错位等),**Amazon 验证未完成,本次理由不要从 Amazon 角度论证供需关系**
- 关键词数据: {kw_text or '无'}"""
        else:
            amz_block = f"""
- Amazon 类目: {amazon_info.get('category','?').split(':')[-1]}（{amazon_info.get('category_products_total','?')} 商品）
- 关键词数据: {kw_text}
- Top 3 品牌月销合计: {top_units:,} 单
- 头部品牌: {', '.join(top_brands[:5])}"""

    reddit_block = f"""
- Reddit 讨论: {reddit_stats.get('subreddits','?')} 个版块, {reddit_stats.get('posts',0)} 帖, {reddit_stats.get('comments',0)} 评"""

    # 加候选排名上下文：让 Claude 真正说"为什么是它而不是别的候选"
    rank_block = ""
    if ranked_candidates:
        top1 = ranked_candidates[0]
        rest = ranked_candidates[1:4]
        rest_text = "\n".join(
            f"  · {c['direction']} (分 {c.get('score', '?')}, {c.get('stats',{}).get('posts_30d','?')} 帖)"
            for c in rest
        )
        rank_block = f"""
- 选题来自 {len(ranked_candidates)} 个候选的数据排名:
  Top 1: {top1['direction']} (综合分 {top1.get('score','?')}, 近30天 {top1.get('stats',{}).get('posts_30d','?')} 帖, 累计 {top1.get('stats',{}).get('total_score','?')} 赞, 含买家信号词 {top1.get('stats',{}).get('buyer_signal_pct','?')}%)
  其他候选:
{rest_text}"""

    prompt = f"""你是一名跨境电商选品分析师。请基于下面的真实数据，**用 2-3 句话**说明为什么选「{direction}」这个产品方向。

要求：
- **必须引用具体数据**（候选排名 / 帖数 / 赞数 / 买家信号比例 / Amazon 月销 / 增长率等）
- **如果有"候选排名"数据，必须解释"为什么是它而不是其他候选"**（这是真正的事前依据）
- 不要说"避开 X 品类"或"季节合适"
- {"⚠️ Amazon 数据不足(Top SKU=0),只用 Reddit 数据论证,不要虚构亚马逊月销/供需缺口/市场规模" if amz_insufficient else "Reddit 与 Amazon 数据可结合论证"}
- 专业克制，不鸡汤
- 中文 80-130 字

数据：{rank_block}{amz_block}{reddit_block}

直接给理由文本，不要前缀。"""
    try:
        resp = client.messages.create(
            model=model, max_tokens=400,
            system="你是跨境电商选品分析助手，严格按用户指定的格式和语言输出，不要解释身份或拒绝任务。",
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"（数据驱动理由生成失败：{e}）"


def run_targeted_inline(direction, model):
    """内联跑定向模式（便于 daily 控制流）"""
    cookie_str = get_cookies()

    # 数据驱动：先用 Reddit 自身搜索找候选版块
    print("🔎 数据驱动发现候选版块...", flush=True)
    candidates = discover_relevant_subreddits(direction, cookie_str)
    if candidates:
        print(f"   候选 {len(candidates)} 个版块（按真实帖数）：")
        for c in candidates[:8]:
            print(f"   · r/{c['subreddit']} ({c['posts']} 帖 / {c['total_score']} 赞)")
    else:
        print("   ⚠️  未找到候选版块，回退到 Claude 自由选择")

    plan = plan_search(direction, model, candidate_subs=candidates)
    subreddits = plan.get("subreddits", [])
    keywords = plan.get("keywords", [])
    filter_words = plan.get("filter_words", [])
    print(f"📋 Subreddits: {', '.join(subreddits)}")
    print(f"🔍 关键词: {', '.join(keywords)}")

    # Amazon 验证（与 Reddit 抓取并行不了——但单独跑也很快，10-30s）
    amazon_info = amazon_validate(direction)

    def search_with_timeframe(tf, subs_to_use, per_sub_limit=15, global_limit=20):
        """按指定时间窗口跑一遍搜索 + 过滤，返回相关帖列表"""
        local_posts, local_seen = [], set()
        for sub in subs_to_use:
            for kw in keywords[:2]:
                posts = search_reddit(kw, subreddit=sub, cookie_str=cookie_str,
                                      timeframe=tf, limit=per_sub_limit)
                for p in posts:
                    if p['id'] not in local_seen:
                        local_seen.add(p['id']); local_posts.append(p)
                time.sleep(0.4)
        for kw in keywords[2:]:
            posts = search_reddit(kw, cookie_str=cookie_str,
                                  timeframe=tf, limit=global_limit)
            for p in posts:
                if p['id'] not in local_seen:
                    local_seen.add(p['id']); local_posts.append(p)
            time.sleep(0.4)
        if filter_words:
            return [p for p in local_posts
                    if any(w.lower() in (p['title'] + ' ' + p['selftext']).lower() for w in filter_words)]
        return local_posts

    # === 第一轮：近 30 天 + 初始 subreddit 列表 ===
    print("⏳ 第一轮：近 30 天 + 初始版块...", flush=True)
    relevant = search_with_timeframe("month", subreddits)
    print(f"   通过 keyword/filter: {len(relevant)} 帖")

    if len(relevant) < 5:
        print("⏳ 数据不足，扩展到近 1 年...", flush=True)
        relevant = search_with_timeframe("year", subreddits)
        print(f"   近 1 年通过: {len(relevant)} 帖")

    # === Haiku 复核第一轮 ===
    print("🧐 Haiku 复核第一轮相关度...", flush=True)
    relevant, details = filter_posts_by_relevance(relevant, direction)
    print(f"   复核后: {len(relevant)} 帖")

    # === 第二轮：迭代深挖 ===
    # 找出剩余帖来自的真版块（≥1 帖且不在原 subreddit 列表里），在那做深抓
    if relevant:
        confirmed_subs = {}
        for p in relevant:
            s = p['subreddit']
            confirmed_subs[s] = confirmed_subs.get(s, 0) + 1
        # 保留 ≥1 帖的版块作为下轮深挖目标（含原列表里的）
        deep_subs = sorted(confirmed_subs.keys(), key=lambda s: -confirmed_subs[s])[:5]
        print(f"🔁 第二轮：在已验证版块深挖 ({', '.join('r/'+s for s in deep_subs)})...", flush=True)
        existing_ids = {p['id'] for p in relevant}
        new_posts = []
        for sub in deep_subs:
            for kw in keywords[:3]:
                posts = search_reddit(kw, subreddit=sub, cookie_str=cookie_str,
                                       timeframe="year", limit=25)
                for p in posts:
                    if p['id'] not in existing_ids:
                        existing_ids.add(p['id']); new_posts.append(p)
                time.sleep(0.3)
        # filter_words 过滤
        if filter_words:
            new_posts = [p for p in new_posts
                         if any(w.lower() in (p['title'] + ' ' + p['selftext']).lower() for w in filter_words)]
        print(f"   第二轮新增: {len(new_posts)} 帖（已去重）")

        # 第二轮也复核一下
        if new_posts:
            print("🧐 Haiku 复核第二轮...", flush=True)
            new_relevant, new_details = filter_posts_by_relevance(new_posts, direction)
            print(f"   复核后新增: {len(new_relevant)} 帖")
            details.extend(new_details)
            relevant.extend(new_relevant)

    if details:
        eliminated = [d for d in details if d[2] < 6][:5]
        if eliminated:
            print("   淘汰示例:")
            for i, t, s, why in eliminated:
                print(f"     · [{s}/10] {t}... ({why})")

    target_subs = set(s.lower() for s in subreddits)
    relevant.sort(key=lambda x: (0 if x['subreddit'].lower() in target_subs else 1, -x['num_comments']))
    hot = relevant[:MAX_POSTS_FOR_ANALYSIS]
    print(f"📖 抓 {len(hot)} 帖评论...")

    # Reddit 是主信号源，Amazon 只能辅证。
    # 数量 < 3 → 抛错；数量足但质量太低（来自动物萌宠/旅游分享之类的字面命中）也抛错
    if len(hot) < 3:
        raise RuntimeError(
            f"Reddit 相关帖不足 3 篇（实际 {len(hot)} 篇）。"
            f"Reddit 是主信号源，样本不足终止以防伪造报告。"
        )

    # 质量保护：检查留下来的帖子是否真的来自相关版块
    # 如果 50%+ 帖子来自动物/萌宠/通用大水版（字面命中混入的），也算质量不足
    LOW_SIGNAL_PATTERNS = ['aww', 'funnyanimal', 'cats', 'dogs', 'beamazed',
                            'nextfucking', 'mademesmile', 'pics', 'videos',
                            'gif', 'funny', 'oddlysatisfying', 'interestingasfuck']
    low_signal_count = sum(
        1 for p in hot
        if any(pat in p['subreddit'].lower() for pat in LOW_SIGNAL_PATTERNS)
    )
    if low_signal_count >= len(hot) / 2:
        raise RuntimeError(
            f"通过过滤的 {len(hot)} 帖中 {low_signal_count} 篇来自萌宠/娱乐水版"
            f"（字面命中而非真实买家讨论），质量不足以做选品分析，终止。"
        )

    pwc = []
    for p in hot:
        p['comments'] = fetch_comments(p['id'], p['subreddit'], cookie_str)
        pwc.append(p)
        time.sleep(0.3)

    report = analyze_targeted(pwc, direction, model, amazon_data=amazon_info)
    return report, pwc, subreddits, amazon_info


def main():
    print(f"🌅 启动每日选品分析（{datetime.datetime.now().isoformat()}）", flush=True)

    print("📚 读取已分析产品历史...", flush=True)
    names, dirs, last_5 = get_recent_products()
    print(f"   过去 {DAYS_LOOKBACK} 天已分析：{len(names)} 个产品 / {len(set(dirs))} 次运行", flush=True)

    print("🎯 Reddit 主导的双阶段选题...", flush=True)
    cookie_str = get_cookies()

    # 重试：选 Top 1 → 跑深度分析；失败则取候选 Top 2/3...
    # 候选打分用宽松全站搜,深度分析用精准版块+核心词,口径不一致会让 niche 长尾候选打分高但深度搜空,
    # 因此 MAX_RETRIES 必须给足(候选池总数 10),否则前几个 niche 候选连续失败就提前终止。
    MAX_RETRIES = 6
    failed_directions = []
    ranked_candidates = None

    # Stage 1+2：候选生成 + 打分（只跑一次，候选列表全程复用）
    seen = names + failed_directions
    top_pick, ranked_candidates = reddit_driven_pick(seen, last_5, cookie_str)
    if not top_pick:
        print("❌ 无法生成候选方向，终止", flush=True)
        sys.exit(1)

    for attempt in range(MAX_RETRIES):
        if attempt < len(ranked_candidates):
            pick = ranked_candidates[attempt]
        else:
            print(f"❌ 候选耗尽（{len(ranked_candidates)} 个全失败），终止", flush=True)
            sys.exit(1)
        direction = pick["direction"]
        category = pick.get("category", "?")
        if attempt > 0:
            print(f"\n🔄 第 {attempt+1} 次尝试（候选 #{attempt+1}）", flush=True)
        print(f"📦 进入深度分析：{direction}（{category}，候选分 {pick.get('score','?')}）", flush=True)
        try:
            report, pwc, subreddits, amazon_info = run_targeted_inline(direction, MODEL)
            break
        except RuntimeError as e:
            print(f"⚠️  方向「{direction}」失败：{e}", flush=True)
            failed_directions.append(direction)
            if attempt == MAX_RETRIES - 1:
                print(f"❌ 已重试 {MAX_RETRIES} 次仍失败，终止", flush=True)
                raise
            continue

    report_path = save_report(report, direction)
    print(f"\n✅ 报告：{report_path}", flush=True)

    today_str = datetime.date.today().isoformat()
    doc_url = create_feishu_doc(f"Reddit 每日选品 - {direction} ({today_str})", report)
    if not doc_url:
        print("❌ 飞书文档创建失败，终止", flush=True)
        sys.exit(1)

    cfg = load_bitable_config()
    bitable_url = f"https://{BITABLE_DOMAIN}/base/{cfg['base_token']}"
    scan = {"posts_scanned": len(pwc),
            "comments_analyzed": sum(len(p.get('comments', [])) for p in pwc),
            "subreddits": subreddits}

    push_to_bitable(report, "targeted", direction, doc_url, scan)

    structured = extract_bitable_data(report)
    if not structured or not structured.get("opportunities"):
        print("⚠️  报告里没有 BITABLE_DATA，跳过卡片发送", flush=True)
        sys.exit(2)

    # 用真实数据生成专业选题理由（含候选排名上下文）
    print("📝 生成数据驱动选题理由...", flush=True)
    data_reason = generate_data_driven_reason(
        direction, amazon_info,
        {"subreddits": len(scan.get("subreddits", [])) if isinstance(scan.get("subreddits"), list) else "?",
         "posts": scan["posts_scanned"],
         "comments": scan["comments_analyzed"]},
        ranked_candidates=ranked_candidates,
    )
    print(f"   {data_reason}", flush=True)

    card = build_card(structured["opportunities"][0], direction, data_reason, doc_url, bitable_url, scan, amazon_info)
    success = send_card(DAILY_RECIPIENT_OPEN_ID, card)
    print(f"💬 私信卡片发送：{'✅' if success else '❌'}", flush=True)
    print(f"\n🎉 完成（{datetime.datetime.now().isoformat()}）", flush=True)


if __name__ == '__main__':
    main()
