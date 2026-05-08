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
import anthropic

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scout import (
    load_bitable_config, get_cookies, plan_search, search_reddit,
    fetch_comments, save_report, create_feishu_doc, push_to_bitable,
    extract_bitable_data, analyze_targeted, discover_relevant_subreddits,
    filter_posts_by_relevance, amazon_validate,
    MAX_POSTS_FOR_ANALYSIS, DEFAULT_MODEL,
)

# ── 用户配置 ──────────────────────────────────────────────
DAILY_RECIPIENT_OPEN_ID = "ou_0df2b09f3185bb15e3c1ea089a80e75e"  # scout-bot 命名空间下的 open_id
LARK_PROFILE = "scout-bot"  # lark-cli profile 名（出海选品客服机器人）
BITABLE_DOMAIN = "ycnm1prsz3tg.feishu.cn"
DAYS_LOOKBACK = 60
MODEL = DEFAULT_MODEL
# ─────────────────────────────────────────────────────────

client = anthropic.Anthropic()


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


def generate_data_driven_reason(direction, amazon_info, reddit_stats, model="claude-haiku-4-5-20251001"):
    """
    基于真实数据生成专业选题理由（用 Haiku 快速生成）。
    优于 pick_fresh_direction 给的"避开 X 品类"机械理由。
    """
    amz_block = ""
    if amazon_info:
        kw = amazon_info.get("keyword_market", [])
        kw_text = " | ".join(
            f"'{k['keyword']}': 月搜{k.get('monthly_searches','?')}, 增长{k.get('growth_pct','?')}%, 供需比{k.get('supply_demand_ratio','?')}, 均价${k.get('avg_price','?')}"
            for k in kw[:2]
        )
        top_brands = list({s.get("brand") for s in amazon_info.get("top_skus", [])[:5] if s.get("brand")})
        top_units = sum((s.get("units_monthly") or 0) for s in amazon_info.get("top_skus", [])[:3])
        amz_block = f"""
- Amazon 类目: {amazon_info.get('category','?').split(':')[-1]}（{amazon_info.get('category_products_total','?')} 商品）
- 关键词数据: {kw_text}
- Top 3 品牌月销合计: {top_units:,} 单
- 头部品牌: {', '.join(top_brands[:5])}"""

    reddit_block = f"""
- Reddit 讨论: {reddit_stats.get('subreddits','?')} 个版块, {reddit_stats.get('posts',0)} 帖, {reddit_stats.get('comments',0)} 评"""

    prompt = f"""你是一名跨境电商选品分析师。请基于下面的真实数据，**用 1-2 句话**说明今天为什么选「{direction}」这个产品方向值得做深度分析。

要求：
- **必须引用具体数据**（搜索量/增长率/月销量/讨论量等数字）
- 从市场规模、增长趋势、竞争格局、需求强度其中 1-2 个角度切入
- 不要说"避开 X 品类"这种轮换理由
- 不要说"季节合适"这种宽泛理由
- 用专业、克制的笔调，不要鸡汤
- 中文 60-100 字

数据：{amz_block}{reddit_block}

直接给理由文本，不要前缀（不要"理由："）。"""
    try:
        resp = client.messages.create(
            model=model, max_tokens=300,
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

    print("🧠 选择今日新方向...", flush=True)
    # 重试机制：当 Reddit 数据不足触发 RuntimeError 时，自动换方向（最多 3 次）
    MAX_RETRIES = 3
    failed_directions = []
    for attempt in range(MAX_RETRIES):
        # 把已失败方向也加到避重列表，避免再选
        pick = pick_fresh_direction(names + failed_directions, dirs + failed_directions, last_5_names=last_5)
        direction = pick["direction"]
        category = pick.get("category", "?")
        reason = pick.get("reason_cn", "")
        if attempt > 0:
            print(f"\n🔄 第 {attempt+1} 次尝试", flush=True)
        print(f"🎯 今日选定：{direction}（{category}）\n   {reason}", flush=True)
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

    # 用真实数据生成专业选题理由（覆盖 pick 阶段的初步理由）
    print("📝 生成数据驱动选题理由...", flush=True)
    data_reason = generate_data_driven_reason(
        direction, amazon_info,
        {"subreddits": len(scan.get("subreddits", [])) if isinstance(scan.get("subreddits"), list) else "?",
         "posts": scan["posts_scanned"],
         "comments": scan["comments_analyzed"]}
    )
    print(f"   {data_reason}", flush=True)

    card = build_card(structured["opportunities"][0], direction, data_reason, doc_url, bitable_url, scan, amazon_info)
    success = send_card(DAILY_RECIPIENT_OPEN_ID, card)
    print(f"💬 私信卡片发送：{'✅' if success else '❌'}", flush=True)
    print(f"\n🎉 完成（{datetime.datetime.now().isoformat()}）", flush=True)


if __name__ == '__main__':
    main()
