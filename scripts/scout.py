#!/usr/bin/env python3
"""
Reddit 选品侦察（统一版）

三种模式：
  # 宽泛模式：自动从多个版块抓取热帖，让 Claude 自主选出 1 个最有价值的产品机会
  python3 scout.py

  # 定向模式：指定产品方向，让 Claude 规划搜索策略，深度分析买家痛点（输出 1 个产品）
  python3 scout.py --product "女士钱包"
  python3 scout.py -p "women's wallet" --model claude-opus-4-7

  # 周报模式：横扫多个品类，输出 5+ 个不同方向的产品机会
  python3 scout.py --weekly

  # 自动推送到飞书（创建文档 + 多维表入库）
  python3 scout.py --weekly --bitable
  python3 scout.py -p "女士钱包" --bitable
"""
import json, subprocess, time, datetime, argparse, urllib.parse, os, re
import browser_cookie3
import anthropic

# ── 飞书多维表格配置（默认值，可用 ~/.reddit-scout.json 覆盖）────
DEFAULT_BITABLE = {
    "base_token": "Jr25bCOJeaL8gGsqRmVcjbt0njb",
    "table_research": "tblz8whHv2l88kAG",   # 选品记录
    "table_posts": "tblFestqjfCZ2fwE",       # Reddit热帖
}

SELLERSPRITE_MCP_URL = "https://mcp.sellersprite.com/mcp"

# ── 默认配置 ────────────────────────────────────────────────────
DEFAULT_MODEL = "claude-sonnet-4-6"
COMMENTS_PER_POST = 30
MAX_POSTS_FOR_ANALYSIS = 25

# 宽泛模式：固定抓取的 subreddit 列表
BROAD_SUBREDDITS = [
    ("BuyItForLife", "top", "week"),
    ("Frugal", "top", "week"),
    ("HomeImprovement", "hot", None),
    ("malelivingspace", "top", "week"),
    ("femalefashionadvice", "top", "week"),
    ("weddingplanning", "hot", None),
]
BROAD_POSTS_PER_SUB = 15

# 周报模式：横向覆盖 10 个品类
WEEKLY_SUBREDDITS = [
    ("BuyItForLife", "top", "week"),
    ("HomeImprovement", "hot", None),
    ("Frugal", "top", "week"),
    ("Cooking", "hot", None),
    ("Parenting", "hot", None),
    ("dogs", "hot", None),
    ("camping", "top", "week"),
    ("femalefashionadvice", "top", "week"),
    ("malelivingspace", "top", "week"),
    ("declutter", "top", "month"),
]
WEEKLY_POSTS_PER_SUB = 12
WEEKLY_TOP_POSTS = 30

# 定向模式：每个关键词的搜索帖数
TARGETED_POSTS_PER_SEARCH = 10
# ─────────────────────────────────────────────────────────────────

client = anthropic.Anthropic()


def load_bitable_config():
    config_path = os.path.expanduser("~/.reddit-scout.json")
    cfg = dict(DEFAULT_BITABLE)
    if os.path.exists(config_path):
        try:
            cfg.update(json.load(open(config_path)))
        except Exception:
            pass
    return cfg


def parse_args():
    parser = argparse.ArgumentParser(description="Reddit 选品侦察工具")
    parser.add_argument(
        "--product", "-p",
        default=None,
        help="定向模式：指定产品方向，如 '女士钱包'。不填且不带 --weekly 则进入宽泛模式。",
    )
    parser.add_argument(
        "--weekly", "-w",
        action="store_true",
        default=False,
        help="周报模式：横扫多品类，输出 5+ 个不同方向的产品机会。",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Claude 模型（默认：{DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="报告保存路径（默认：~/reddit-scout-reports/）",
    )
    parser.add_argument(
        "--bitable", "-b",
        action="store_true",
        default=False,
        help="自动创建飞书文档 + 推送结果到多维表格（需已配置 lark-cli）",
    )
    return parser.parse_args()


def get_cookies():
    cookies = browser_cookie3.chrome(domain_name='.reddit.com')
    return '; '.join(f'{c.name}={c.value}' for c in cookies)


def reddit_get(url, cookie_str):
    try:
        result = subprocess.run([
            'curl', '-s', '-L',
            '-A', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            '-H', 'Accept: application/json',
            '-H', f'Cookie: {cookie_str}',
            url
        ], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None
    if not result.stdout or (not result.stdout.startswith('{') and not result.stdout.startswith('[')):
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def fetch_posts_from_sub(sub, sort, timeframe, cookie_str, limit=None):
    """按版块抓取热帖"""
    if limit is None:
        limit = BROAD_POSTS_PER_SUB
    sub_clean = sub.lstrip('/').lstrip('r/').lstrip('R/').strip('/')
    url = f"https://www.reddit.com/r/{sub_clean}/{sort}.json?limit={limit}"
    if timeframe:
        url += f"&t={timeframe}"
    data = reddit_get(url, cookie_str)
    if not data or not isinstance(data, dict):
        return []
    children = data.get('data', {}).get('children', []) if isinstance(data.get('data'), dict) else []
    posts = []
    for item in children:
        d = item.get('data', {})
        if not d.get('id'):
            continue
        posts.append({
            'id': d['id'],
            'title': d.get('title', ''),
            'score': d.get('score', 0),
            'num_comments': d.get('num_comments', 0),
            'selftext': (d.get('selftext') or '')[:600],
            'subreddit': sub_clean,
            'created_utc': d.get('created_utc'),
        })
    return posts


def search_reddit(keyword, subreddit=None, cookie_str="", limit=TARGETED_POSTS_PER_SEARCH, timeframe="year"):
    """关键词搜索帖子，可限定 subreddit + 时间窗口（hour/day/week/month/year/all）"""
    q = urllib.parse.quote(keyword)
    if subreddit:
        # 兼容 Claude 偶尔输出带 'r/' 或 '/r/' 前缀的 sub 名
        sub_clean = subreddit.lstrip('/').lstrip('r/').lstrip('R/').strip('/')
        url = f"https://www.reddit.com/r/{sub_clean}/search.json?q={q}&sort=top&t={timeframe}&limit={limit}&restrict_sr=1"
    else:
        url = f"https://www.reddit.com/search.json?q={q}&sort=top&t={timeframe}&limit={limit}"
    data = reddit_get(url, cookie_str)
    if not data or not isinstance(data, dict):
        return []
    children = data.get('data', {}).get('children', []) if isinstance(data.get('data'), dict) else []
    posts = []
    for item in children:
        d = item.get('data', {})
        if not d.get('id'):
            continue
        posts.append({
            'id': d['id'],
            'title': d.get('title', ''),
            'score': d.get('score', 0),
            'num_comments': d.get('num_comments', 0),
            'selftext': (d.get('selftext') or '')[:600],
            'subreddit': d.get('subreddit', ''),
            'created_utc': d.get('created_utc'),
        })
    return posts


def fetch_comments(post_id, sub, cookie_str):
    url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json?limit={COMMENTS_PER_POST}&sort=top"
    data = reddit_get(url, cookie_str)
    if not data or not isinstance(data, list):
        return []
    comments = []
    for item in data[1]['data']['children']:
        d = item.get('data', {})
        if d.get('body') and d['body'] not in ('[deleted]', '[removed]'):
            comments.append({'body': d['body'][:350], 'score': d.get('score', 0)})
    comments.sort(key=lambda x: x['score'], reverse=True)
    return comments[:COMMENTS_PER_POST]


def discover_subreddits_by_name(keyword, cookie_str, top_n=10):
    """
    用 Reddit 自带的 /subreddits/search 找版块名直接匹配的真相关 sub。
    比从帖子聚合更准——返回的是按 sub 名/描述匹配的，不是被噪音帖污染的。
    """
    q = urllib.parse.quote(keyword)
    url = f"https://www.reddit.com/subreddits/search.json?q={q}&limit={top_n*2}&sort=relevance"
    data = reddit_get(url, cookie_str)
    if not data or 'data' not in data:
        return []
    out = []
    for item in data['data']['children']:
        d = item['data']
        # 过滤掉用户太少的死版块（兼容 subscribers 为 None 的情况）
        if (d.get('subscribers') or 0) < 1000:
            continue
        out.append({
            "subreddit": d.get('display_name'),
            "subscribers": d.get('subscribers', 0),
            "description": (d.get('public_description') or '')[:80],
        })
        if len(out) >= top_n:
            break
    return out


def discover_relevant_subreddits(product, cookie_str, top_n=10):
    """
    数据驱动的子版块发现（双源合并）：
    1. /subreddits/search：按版块名匹配（最准）
    2. /search.json 全站搜索 → 聚合帖子来自的版块（覆盖广）
    过滤噪音黑名单后取 top_n。
    """
    NOISE_SUBREDDITS = {
        # 大型综合/娱乐
        'nba', 'nfl', 'soccer', 'baseball', 'hockey', 'mma', 'formula1',
        'politics', 'worldnews', 'news', 'AskReddit', 'funny', 'pics',
        'memes', 'wallstreetbets', 'Superstonk', 'CryptoCurrency',
        'todayilearned', 'mildlyinteresting', 'unpopularopinion',
        'TwoXChromosomes', 'gaming', 'pcmasterrace',
        'movies', 'television', 'music', 'KpopFap', 'kpop', 'popheads',
        'taylorswift', 'beatles', 'leagueoflegends', 'DotA2',
        'GTA6', 'marvelrivals', 'Helldivers',
        # 故事/吐槽八卦类（容易被任意关键词命中）
        'SubredditDrama', 'BestofRedditorUpdates', 'BORUpdates',
        'tifu', 'AmItheAsshole', 'AITAH', 'AmIOverreacting',
        'pettyrevenge', 'MaliciousCompliance', 'weddingshaming',
        'relationship_advice', 'TopCharacterTropes',
        'nosleep', 'TrueCrimeDiscussion', 'HFY', 'SipsTea',
        # 时尚/美妆/动物可爱
        'MadeMeSmile', 'MapPorn', 'bald', 'cats', 'dogs', 'aww',
        'RedditLaqueristas', 'MakeupAddiction', 'AnimalCrossing',
        'FinalFantasyIX', 'StarWars', 'ClosetWeed',
        # 城市/旅游/综合区域（除非产品就是地理相关）
        'LosAngeles', 'Defeat_Project_2025', 'architecture',
    }
    NOISE_LOWER = {s.lower() for s in NOISE_SUBREDDITS}

    # Source 1: 版块名匹配
    name_matches = discover_subreddits_by_name(product, cookie_str, top_n=8)

    # Source 2: 全站帖子聚合
    q = urllib.parse.quote(product)
    url = f"https://www.reddit.com/search.json?q={q}&sort=top&t=year&limit=100"
    data = reddit_get(url, cookie_str)
    counts = {}
    if data and 'data' in data:
        for item in data['data']['children']:
            d = item['data']
            sub = d.get('subreddit')
            if not sub or sub.lower() in NOISE_LOWER:
                continue
            c = counts.setdefault(sub, {"posts": 0, "score": 0})
            c["posts"] += 1
            c["score"] += d.get('score', 0)

    # 合并：name_matches 放最前（更准），然后是 post-aggregated 排前 N
    seen = set()
    out = []
    for nm in name_matches:
        sub = nm["subreddit"]
        if not sub or sub.lower() in NOISE_LOWER or sub.lower() in seen:
            continue
        seen.add(sub.lower())
        out.append({
            "subreddit": sub, "posts": "N/A",
            "total_score": f"{nm['subscribers']:,} 订阅",
            "source": "name-match",
        })
    ranked = sorted(counts.items(), key=lambda kv: (kv[1]["posts"], kv[1]["score"]), reverse=True)
    for sub, v in ranked:
        if sub.lower() in seen:
            continue
        seen.add(sub.lower())
        out.append({
            "subreddit": sub, "posts": v["posts"],
            "total_score": v["score"], "source": "post-aggregate",
        })
        if len(out) >= top_n:
            break
    return out[:top_n]


# ── Sellersprite 亚马逊数据验证 ─────────────────────────────

def _sellersprite_secret_key():
    """从 ~/.claude.json 读取 sellersprite secret-key"""
    cfg_path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(cfg_path):
        return None
    try:
        cfg = json.load(open(cfg_path))
        url = cfg.get("mcpServers", {}).get("sellersprite", {}).get("url", "")
        if "?" not in url:
            return None
        qs = urllib.parse.parse_qs(url.split("?", 1)[1])
        for v in qs.values():
            if v and v[0]:
                return v[0]
    except Exception:
        return None
    return None


def sellersprite_call(tool_name, args, secret_key=None, timeout=30):
    """通过 HTTP JSON-RPC 调 sellersprite MCP（不依赖 Claude session 的 MCP 集成）"""
    if secret_key is None:
        secret_key = _sellersprite_secret_key()
    if not secret_key:
        return None
    body = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args}
    }
    try:
        r = subprocess.run([
            "curl", "-s", "-m", str(timeout), "-X", "POST",
            f"{SELLERSPRITE_MCP_URL}?secret-key={secret_key}",
            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json, text/event-stream",
            "-d", json.dumps(body, ensure_ascii=False)
        ], capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return None
    if not r.stdout.strip().startswith("{"):
        return None
    try:
        resp = json.loads(r.stdout)
        if "error" in resp:
            return None
        text = resp["result"]["content"][0]["text"]
        return json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def amazon_validate(direction, max_skus=10, max_review_brands=3):
    """
    用 sellersprite 做亚马逊验证：
    - 找最相关类目（避开手机屏保/食品等字面巧合误命中）
    - 抓 Top 销量 SKU（按月销）
    - 抓 Top 2-3 品牌的差评（避免同品牌多变体重复）
    - 抓关键词市场数据（搜索量/增长/供需比/平均价/头部品牌）

    返回 dict 或 None（失败时）
    """
    print(f"🛒 Sellersprite Amazon 验证（方向: {direction}）...", flush=True)

    # Step 1: 找类目
    nodes_resp = sellersprite_call("product_node", {
        "request": {"marketplace": "US", "keyword": direction}
    })
    nodes = []
    if nodes_resp and isinstance(nodes_resp.get("data"), list):
        nodes = nodes_resp["data"]
    if not nodes:
        print("   ⚠️  未找到匹配类目，跳过 Amazon 验证")
        return None

    # 类目选择：先初筛候选，让 Haiku 在候选里挑最匹配的
    # 步骤 1：先把明显大杂烩水类目 + 字面命中歧义的过滤掉，留下 30 个候选
    GENERIC_WORDS = {
        'portable', 'outdoor', 'indoor', 'home', 'best', 'pro', 'mini',
        'large', 'small', 'medium', 'mens', 'womens', 'kids', 'baby',
        'electric', 'manual', 'wireless', 'rechargeable', 'foldable',
        'compact', 'lightweight', 'heavy', 'duty', 'professional',
        'premium', 'cheap', 'travel', 'camping', 'car',
    }
    direction_words_all = [w.lower() for w in direction.split() if len(w) >= 3]
    direction_core = [w for w in direction_words_all if w not in GENERIC_WORDS]

    # 初筛：含核心词的优先；否则含任意非通用词；否则含通用词；最后兜底
    matched_core = [n for n in nodes
                     if direction_core and any(w in n.get("nodeLabelPath", "").lower() for w in direction_core)]
    matched_any = [n for n in nodes
                    if any(w in n.get("nodeLabelPath", "").lower() for w in direction_words_all)]
    candidates = matched_core if matched_core else (matched_any if matched_any else nodes)
    # 排除明显超大杂烩（>50000 通常是装饰/配件大类目）和过小（<200 数据稀疏）
    filtered = [n for n in candidates if 200 <= (n.get("products") or 0) <= 50000]
    if not filtered:
        filtered = candidates
    # 取 top 25 候选给 Haiku 选
    filtered.sort(key=lambda x: x.get("products", 0), reverse=True)
    candidates_top = filtered[:25]

    # 步骤 2：让 Haiku 看候选挑最匹配的
    cand_lines = "\n".join(
        f"{i+1}. {n['nodeLabelPath']} ({n.get('products', '?')} 商品)"
        for i, n in enumerate(candidates_top)
    )
    pick_prompt = f"""从以下亚马逊类目候选中，选出最适合分析「{direction}」这个产品的类目。

判断标准：
- **类目语义必须真的就是这个产品**（如 portable hammock stand 应选含 hammock 的类目，不是站立桨板 Stand-Up Paddleboarding 这种字面巧合）
- 优先选**叶子层级深、语义精准**的类目，而不是含混大类目
- 商品数量在 500-15000 之间最理想；过大数据多杂噪音，过小数据稀疏
- 如果**没有真正匹配**的类目（产品在亚马逊还是新品类），返回 0

候选：
{cand_lines}

只返回 1 个数字（候选序号 1-{len(candidates_top)}，或 0 表示都不匹配）。不要任何其他文字。"""
    try:
        pick_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": pick_prompt}]
        )
        pick_idx = int(pick_resp.content[0].text.strip()) - 1
        if 0 <= pick_idx < len(candidates_top):
            top_node = candidates_top[pick_idx]
        else:
            print("   ⚠️  Haiku 判断无真匹配类目，跳过 Amazon 验证")
            return None
    except (ValueError, Exception) as e:
        print(f"   ⚠️  Haiku 选类目失败 ({e})，回退到规模排序")
        top_node = candidates_top[0] if candidates_top else None
        if not top_node:
            return None
    print(f"   📂 类目（Haiku 挑选）: {top_node['nodeLabelPath']} ({top_node.get('products', '?')} 商品)")

    # Step 2: 抓 Top SKU（用上个月数据）
    # 关键：按"最具识别性的核心词"过滤标题，避免父类目下其他爆款（如门垫、储粮桶）混入
    last_month = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m")

    # 选最具识别性的核心词作 keyword（产品方向最后一个非通用词，通常是产品类型本身）
    # 例如 "pet water fountain" → "fountain"; "outdoor solar lantern" → "lantern"
    if direction_core:
        identifier_word = direction_core[-1]  # 最后一个核心词通常是产品类型
    elif direction_words_all:
        identifier_word = direction_words_all[-1]
    else:
        identifier_word = direction

    skus_resp = sellersprite_call("product_research", {
        "request": {
            "marketplace": "US",
            "nodeIdPath": top_node["nodeIdPath"],
            "nodeIdPathEqual": False,
            "keyword": identifier_word,
            "matchType": 2,  # 2: 模糊匹配
            "month": last_month,
            "minUnits": 50,
            "size": max_skus * 4,  # 拿很多，去重后取 Top
            "order": {"field": "total_units", "desc": True}
        }
    })
    raw_items = []
    if skus_resp and isinstance(skus_resp.get("data"), dict):
        raw_items = skus_resp["data"].get("items", []) or []
    # 客户端二次校验：标题必须含识别词
    raw_items = [
        it for it in raw_items
        if identifier_word.lower() in (it.get("title") or "").lower()
    ]
    raw_items.sort(key=lambda x: x.get("units") or 0, reverse=True)

    # 关键：按品牌去重，让 Top N 是 N 个**不同品牌**的产品
    # （同一品牌多个 listing 会让头部看起来"分散"，实际还是一家）
    items = []
    seen_brands = set()
    for it in raw_items:
        brand = (it.get("brand") or "").strip().lower()
        if brand and brand in seen_brands:
            continue
        if brand:
            seen_brands.add(brand)
        items.append(it)
        if len(items) >= max_skus:
            break
    print(f"   🏆 Top {len(items)} SKU（{last_month} 月销，标题必含 '{identifier_word}'，每品牌只取销量最高款）")

    if not items:
        return {
            "category": top_node["nodeLabelPath"],
            "category_products_total": top_node.get("products"),
            "month": last_month,
            "top_skus": [],
            "negative_reviews": [],
            "keyword_market": []
        }

    # Step 3: 抓 Top 品牌差评（同品牌只取一个变体，避免重复）
    category_id_root = top_node["nodeIdPath"].split(":")[0]
    reviews_by_brand = []
    seen_brands = set()
    for it in items:
        if len(reviews_by_brand) >= max_review_brands:
            break
        brand = it.get("brand", "?") or "?"
        if brand in seen_brands:
            continue
        seen_brands.add(brand)
        rev = sellersprite_call("review", {
            "marketplace": "US",
            "asin": it["asin"],
            "categoryId": category_id_root,
            "starList": [1, 2, 3],
            "size": 8
        })
        review_items = []
        if rev and isinstance(rev.get("data"), dict):
            review_items = rev["data"].get("items", []) or []
        if review_items:
            reviews_by_brand.append({
                "asin": it["asin"],
                "brand": brand,
                "title": (it.get("title") or "")[:80],
                "samples": [
                    {
                        "star": r.get("star"),
                        "title": (r.get("title") or "")[:80],
                        "content": (r.get("content") or "").replace("<br>", " ")[:280]
                    }
                    for r in review_items[:6]
                ]
            })
    total_reviews = sum(len(b["samples"]) for b in reviews_by_brand)
    print(f"   💬 抓到 {total_reviews} 条差评 / {len(reviews_by_brand)} 个 Top 品牌")

    # Step 4: 关键词市场分析
    kw_resp = sellersprite_call("keyword_research", {
        "request": {"keywords": direction, "marketplace": "US"}
    })
    keyword_market = []
    if kw_resp and isinstance(kw_resp.get("data"), dict):
        kws = kw_resp["data"].get("items", []) or []
        for k in kws[:3]:
            keyword_market.append({
                "keyword": k.get("keywords"),
                "monthly_searches": k.get("searches"),
                "growth_pct": round((k.get("growth") or 0) * 100, 1),
                "supply_demand_ratio": k.get("supplyDemandRatio"),
                "avg_price": k.get("avgPrice"),
                "bid_avg": k.get("bid"),
                "top_brands": ", ".join((k.get("brands") or [])[:3]),
            })
    if keyword_market:
        print(f"   🔍 关键词市场: {len(keyword_market)} 词数据")

    return {
        "category": top_node["nodeLabelPath"],
        "category_products_total": top_node.get("products"),
        "month": last_month,
        "top_skus": [
            {
                "asin": it["asin"],
                "brand": it.get("brand"),
                "title": (it.get("title") or "")[:80],
                "price": it.get("price"),
                "units_monthly": it.get("units"),
                "revenue_monthly": it.get("revenue"),
                "rating": it.get("rating"),
                "ratings_count": it.get("ratings"),
                "bsr": it.get("bsr"),
                "is_amazon_self": it.get("sellerName") == "Amazon",
            }
            for it in items[:max_skus]
        ],
        "negative_reviews": reviews_by_brand,
        "keyword_market": keyword_market,
    }


def format_amazon_section_for_prompt(amazon_data):
    """把 Amazon 数据格式化成文本片段，塞到 Claude 分析 prompt 里"""
    if not amazon_data:
        return ""
    skus = amazon_data.get("top_skus", [])
    skus_lines = "\n".join(
        f"  · {s['asin']} | {s.get('brand','?')} | ${s.get('price','?')} | "
        f"月销 {s.get('units_monthly','?')} 单 / ${(s.get('revenue_monthly') or 0):,.0f} | "
        f"{s.get('rating','?')}★({s.get('ratings_count','?')}评) | BSR {s.get('bsr','?')} | "
        f"{(s.get('title') or '')[:55]}"
        for s in skus[:8]
    ) or "  （无数据）"

    review_blocks = []
    for nr in amazon_data.get("negative_reviews", []):
        samples = "\n".join(
            f"      [{s.get('star','?')}★] {s.get('title','')}: {(s.get('content') or '')[:200]}"
            for s in nr["samples"][:5]
        )
        review_blocks.append(f"    {nr['brand']} ({nr['asin']}):\n{samples}")
    reviews_text = "\n".join(review_blocks) or "  （无数据）"

    kw = amazon_data.get("keyword_market", [])
    kw_lines = "\n".join(
        f"  · {k['keyword']}: 月搜 {k.get('monthly_searches','?')} | "
        f"近期增长 {k.get('growth_pct','?')}% | 供需比 {k.get('supply_demand_ratio','?')} | "
        f"均价 ${k.get('avg_price','?')} | PPC ${k.get('bid_avg','?')} | 头部 {k.get('top_brands','')}"
        for k in kw
    ) or "  （无数据）"

    return f"""

---

**【亚马逊市场实证数据 — Sellersprite {amazon_data.get('month','')}】**

**类目**：{amazon_data.get('category','?')}（共 {amazon_data.get('category_products_total','?')} 个商品）

**Top {len(skus)} 销量 SKU（按月销量降序）**:
{skus_lines}

**Top 品牌的差评样本（与 Reddit 痛点对比，找差异化空间）**:
{reviews_text}

**关键词市场**:
{kw_lines}

---

**重要要求**：你的报告**必须把上述亚马逊数据融入分析**，不能只看 Reddit：
- 「双重验证」：Reddit 痛点中哪些在 Top SKU 差评里也出现了？
- 头部 SKU 的品牌集中度、定价区间、评分分布说明什么？
- 关键词搜索量 + 增长率 + 供需比 → 市场是热是冷？是上升还是下行？
- 你的产品方向相比 Top SKU 的**具体差异化点**是什么？是否解决了它们差评里的问题？
- **机会评分必须把市场规模（关键词月搜量 × 头部 SKU 月销额）+ 竞争程度（供需比 + 品牌集中度）+ 差异化空间（差评指向的具体改进）三者都纳入**
"""


# ── Reddit 帖子相关度过滤（Haiku） ─────────────────────────────

def filter_posts_by_relevance(posts, product, model="claude-haiku-4-5-20251001"):
    """
    用 Haiku 给每帖打相关度分，去掉与产品无关的噪音。
    返回 (相关帖列表, 评分明细) — 用 ≥ 7 分作为阈值（更严，避免松鼠视频这种边缘命中混入）。
    """
    if not posts:
        return [], []
    items = "\n".join(
        f"{i+1}. r/{p['subreddit']} | {p['title'][:120]}"
        + (f" || {p['selftext'][:80]}" if p['selftext'] else "")
        for i, p in enumerate(posts)
    )
    prompt = f"""判断以下 Reddit 帖子是否**真正讨论「{product}」这个产品/品类的真实买家选购或使用经验**。

**关键判断标准**（这是给跨境电商选品分析用的，要的是消费者购买决策的信号）：
- 10/10：买家咨询推荐、抱怨产品缺陷、对比品牌型号、分享使用心得
- 7-9：评论里有真实购买/使用讨论，即使主帖是分享照片
- 4-6：模糊涉及该品类但不是消费者讨论（如评测视频、户外活动顺带提及）
- 0-3：**字面命中但完全不是该产品的消费讨论**——例如：
  - 萌宠/动物视频里有产品作为道具（"松鼠坐在小桌子上"虽然提到 camping table，但不是买家讨论）
  - 旅游/事件分享里只是顺带提到产品名
  - 玩游戏/电影/小说虚构场景里出现该词

对每帖**严格按格式**输出 1 行：`序号|相关度|理由`
- 相关度：0-10 整数
- 理由：1 短句（≤15 字）

示例（产品 = portable camping fan）：
1|9|露营风扇购买推荐讨论
2|0|NBA 球迷帖 fan 字面命中
3|2|宠物视频道具非消费讨论
4|7|户外帖评论区有人推荐风扇

只输出 N 行，不要任何其他内容。

待判断帖子：
{items}"""
    try:
        resp = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        lines = resp.content[0].text.strip().split("\n")
        scores = {}
        for ln in lines:
            parts = ln.strip().split("|", 2)
            if len(parts) >= 2:
                try:
                    idx = int(parts[0].strip()) - 1
                    score = int(parts[1].strip())
                    reason = parts[2].strip() if len(parts) > 2 else ""
                    scores[idx] = (score, reason)
                except ValueError:
                    continue
        kept = []
        details = []
        for i, p in enumerate(posts):
            sc, why = scores.get(i, (5, "未评分"))
            details.append((i, p['title'][:50], sc, why))
            if sc >= 7:  # 提高阈值 6→7，更严格
                kept.append(p)
        return kept, details
    except Exception as e:
        print(f"⚠️  相关度过滤失败（{e}），使用原始全部帖子")
        return posts, []


def plan_search(product, model, candidate_subs=None):
    """
    定向模式 Step 1：让 Claude 规划搜索策略
    candidate_subs: 来自 discover_relevant_subreddits 的数据驱动候选列表（强制 Claude 在里面选）
    """
    print(f"\n🧠 规划搜索策略（产品：{product}）...\n")

    if candidate_subs:
        cand_lines = "\n".join(
            f"- r/{c['subreddit']}（{c['posts']} 帖 / {c['total_score']} 累计赞）"
            for c in candidate_subs
        )
        sub_instruction = f"""**候选 subreddit 列表**（来自 Reddit 实际搜索数据，按相关性降序）：
{cand_lines}

**选 subreddit 的指南**：
1. **优先从候选列表挑**真正与该产品/品类强相关的 3-5 个版块
2. **如果候选明显不相关**（全是综合水版/字面巧合），允许补充 1-3 个**已知该品类专属的版块**（比如露营产品可补 r/CampingGear, r/CarCamping, r/Overlanding；母婴可补 r/beyondthebump 等）
3. 排除字面巧合（产品名 "picnic blanket" 时，不要选 r/pickling）
4. 排除综合大水版（pettyrevenge / AITAH / mildlyinfuriating 等不能反映买家讨论）
5. 加入候选列表外的版块时，必须确信该版块**真的存在**且**真的与产品品类强相关**"""
    else:
        sub_instruction = """- subreddits：3-5 个，选择最可能有该产品**真实买家**讨论的版块（英文，不带 r/）
- 不要选男性时尚版块给女性产品；避免字面巧合（picnic vs pickling）"""

    prompt = f"""你是一名跨境电商选品研究员，正在研究「{product}」这个产品品类在 Reddit 上的买家讨论。

请给出搜索策略，返回 JSON 格式：

{{
  "subreddits": ["subreddit1", "subreddit2", "subreddit3"],
  "keywords": ["search phrase 1", "search phrase 2", "search phrase 3", "search phrase 4"],
  "filter_words": ["word1", "word2"]
}}

要求：
{sub_instruction}
- subreddit 名**不要带 "r/" 前缀**（直接写 `camping`，不要 `r/camping`）
- keywords：3-5 个英文搜索词，覆盖"问题/抱怨/推荐/购买建议"等角度
- filter_words：2-3 个**简单英文单词**（非短语），用于过滤帖子相关性，如 ["wallet", "purse"]
- 只返回 JSON，不要其他内容"""

    resp = client.messages.create(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip()
    return json.loads(raw)


def build_post_texts(posts_with_comments):
    texts = []
    for p in posts_with_comments:
        # 把发帖日期展示给 Claude
        date_str = ""
        if p.get('created_utc'):
            try:
                date_str = " · " + datetime.datetime.utcfromtimestamp(p['created_utc']).strftime("%Y-%m-%d")
            except Exception:
                pass
        text = f"【r/{p['subreddit']}】{p['score']}赞 / {p['num_comments']}评{date_str}\n标题: {p['title']}\n"
        if p['selftext']:
            text += f"内容: {p['selftext']}\n"
        if p['comments']:
            text += "高赞评论:\n"
            for c in p['comments'][:10]:
                text += f"  ({c['score']}赞) \"{c['body'][:250]}\"\n"
        texts.append(text)
    return "\n\n---\n\n".join(texts)


def post_date_distribution(posts):
    """计算帖子的时间分布，给 Claude 引用用"""
    if not posts:
        return ""
    today = datetime.datetime.utcnow()
    buckets = {"近 30 天": 0, "30-90 天": 0, "90-180 天": 0, "180 天以上": 0, "未知日期": 0}
    for p in posts:
        ts = p.get('created_utc')
        if not ts:
            buckets["未知日期"] += 1
            continue
        days = (today - datetime.datetime.utcfromtimestamp(ts)).days
        if days <= 30:
            buckets["近 30 天"] += 1
        elif days <= 90:
            buckets["30-90 天"] += 1
        elif days <= 180:
            buckets["90-180 天"] += 1
        else:
            buckets["180 天以上"] += 1
    parts = [f"{k}: {v}" for k, v in buckets.items() if v > 0]
    return " | ".join(parts)


SCORING_INSTRUCTIONS = """
## 机会评分：X/10

**评分依据**（必须用实际数据支撑，不允许凭感觉打分）：

| 维度 | 得分 | 数据依据 |
|------|------|---------|
| 需求真实性 | X/3 | 本次数据中明确表达该痛点的帖子数、评论数、总赞数（如:3篇帖子 + 47条评论提及，累计 312赞） |
| 市场空间 | X/3 | 搜索结果密度、版块规模（如:r/BuyItForLife 月活300万+，该话题帖子 top 帖平均 150赞） |
| 差异化可行性 | X/4 | 现有竞品的具体缺陷（引用评论）vs 你的改进方案是否可落地 |

**诚实声明**：本次样本为 {post_count} 帖 / {comment_count} 条评论，Reddit 讨论量不等于市场规模，以上评分仅反映 Reddit 上的声音，不构成市场验证。
"""


def stream_with_retry(model, max_tokens, prompt, max_retries=3):
    """
    流式调用 Claude；网络瞬断时自动重试，最后一次重试改用非流式（更稳定）。
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            full_text = ""
            # 最后一次重试：用非流式调用避免 chunk 断流
            if attempt == max_retries - 1:
                print(f"\n  [重试 {attempt+1}/{max_retries}] 改用非流式...", flush=True)
                resp = client.messages.create(
                    model=model, max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}]
                )
                full_text = resp.content[0].text
                print(full_text)
                return full_text

            with client.messages.stream(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                    full_text += text
            print()
            return full_text
        except (anthropic.APIConnectionError, anthropic.APIError, Exception) as e:
            err_name = type(e).__name__
            # 只重试网络/流相关的错误，业务错误立即失败
            if not any(s in err_name for s in ('RemoteProtocol', 'APIConnection', 'Timeout', 'APIError')):
                # 检查 message 看是否流式相关
                msg = str(e).lower()
                if 'peer closed' not in msg and 'incomplete' not in msg and 'connection' not in msg:
                    raise
            last_err = e
            wait = 2 ** attempt
            print(f"\n⚠️  Claude 流式调用失败（{err_name}: {str(e)[:100]}），{wait}s 后重试...", flush=True)
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("stream_with_retry exhausted")


def bitable_json_instruction(num_opportunities):
    """生成"在报告末尾追加 BITABLE_DATA JSON 块"的 prompt 段落"""
    return f"""

---

**最重要：报告最末尾必须追加一个 BITABLE_DATA JSON 块**（用 HTML 注释包裹，markdown 渲染时不显示，但程序可解析），用于自动入库飞书多维表格。格式严格如下：

<!--BITABLE_DATA
{{
  "opportunities": [
    {{
      "product_name": "产品中文名",
      "category": "品类（如 厨房/家居/服饰）",
      "score": 8.5,
      "demand_score": 2.5,
      "market_score": 3,
      "diff_score": 3,
      "pain_summary": "核心痛点 1-2 句话总结",
      "opportunity_summary": "机会点 1-2 句话总结（具体规格/材质/做法/定价）",
      "competition_summary": "竞品现状 1-2 句话",
      "buyer_persona": "目标用户 1-2 句话",
      "subreddits": "覆盖版块（如 r/Cooking, r/BuyItForLife）",
      "selected_posts": 1,
      "notes": "评分依据：本次数据 N 帖 / M 评论 / 累计 K 赞讨论；样本规模声明",
      "amazon_validation": "如有亚马逊数据则填这里：类目+Top SKU 销量/品牌集中度+关键词市场+差评中与 Reddit 共振的痛点+建议定价。无亚马逊数据则留空字符串。",
      "evidence_posts": [
        {{
          "title": "Reddit 帖子标题原文",
          "subreddit": "r/X",
          "score": 100,
          "num_comments": 50,
          "top_comments": "(高赞数)评论原文1 | (高赞数)评论原文2",
          "summary": "帖子主题 1 句话摘要"
        }}
      ]
    }}
  ]
}}
-->

JSON 严格要求：
- 顶层 opportunities 数组**必须包含 {num_opportunities} 个对象**
- 所有数字字段（score / demand_score / market_score / diff_score / score / num_comments / selected_posts）**不带引号**
- 每个 opportunity 至少 1 条 evidence_posts，最多 3 条
- product_name 和 category 用中文，title 用英文原文
- JSON 必须能被 json.loads 解析（注意双引号转义、不能有多余逗号）"""


def analyze_broad(posts_with_comments, model):
    """宽泛模式：Claude 自主选出最有价值的产品机会并深度分析"""
    combined = build_post_texts(posts_with_comments)
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_comments)
    today = datetime.date.today().isoformat()

    scoring_block = SCORING_INSTRUCTIONS.format(
        post_count=len(posts_with_comments),
        comment_count=total_comments
    )

    prompt = f"""你是一名资深跨境电商选品分析师。今天是 {today}。

以下是来自多个 Reddit 版块的真实用户讨论（共 {len(posts_with_comments)} 个帖子，约 {total_comments} 条评论），数据采集时间为今天（{today}）。

**你的任务**：
1. 从这些数据中**自主识别** 1 个最有价值的产品机会
2. 对该机会进行深度分析

**输出格式**（严格按此格式）：

---

## [产品名称]：Reddit 买家痛点深度研究

**品类背景**（2-3句）

### 痛点一：[标题]
（痛点本质 + Reddit 证据：r/XX，「帖子标题」（X赞 / X评）+ 用户原话引用 + 市场分析）

### 痛点二/三/四（如有）

## 机会点
① 具体方向（材质/尺寸/制造可行性/卖点）
② ...
③ ...

## 竞品现状

{scoring_block}

## 目标买家画像

## 本次研究数据
（必须包含：覆盖版块、扫描帖数、精选讨论数、关键词范围。**数据采集时间必须写当天日期 {today}，不要凭印象写成其他年份**）

---

**重要约束**：
- 只分析数据中真实出现的痛点，不要虚构
- 每个痛点必须有具体帖子+赞数+用户原话作为证据
- 机会评分必须用上方表格中的数据支撑
- 整体不少于 1000 字
{bitable_json_instruction(1)}

以下是 Reddit 原始数据：

{combined[:18000]}
"""

    print(f"\n🤖 正在进行深度分析（模型：{model}）...\n")
    return stream_with_retry(model, 8000, prompt)


def analyze_targeted(posts_with_comments, product, model, amazon_data=None):
    """定向模式：深度分析指定产品的买家痛点。amazon_data 可选，传入则融合 Amazon 实证。"""
    combined = build_post_texts(posts_with_comments)
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_comments)
    today = datetime.date.today().isoformat()
    date_dist = post_date_distribution(posts_with_comments)

    scoring_block = SCORING_INSTRUCTIONS.format(
        post_count=len(posts_with_comments),
        comment_count=total_comments
    )

    amazon_section = format_amazon_section_for_prompt(amazon_data)
    has_amazon = bool(amazon_data and amazon_data.get("top_skus"))

    prompt = f"""你是一名资深跨境电商选品分析师，正在研究「{product}」这个品类的 Reddit 买家真实讨论。今天是 {today}。

以下是从 Reddit 相关版块收集的帖子和评论数据（共 {len(posts_with_comments)} 个帖子，约 {total_comments} 条评论），数据采集时间为今天（{today}）。

**帖子时间分布**：{date_dist}
（每个帖子在原始数据里都标注了发帖日期，请在分析中诚实指出"信号是近期的还是历史性的"——如果某痛点引用的帖子大多在 180 天前，要在评分中体现"信号陈旧风险"）
{amazon_section}
请对「{product}」进行深度买家痛点研究，严格按以下格式输出完整报告：

---

## {product}：Reddit + Amazon 买家痛点深度研究

**品类背景**（2-3句）

### 痛点一/二/三/四：[标题]
（痛点本质 + Reddit 证据：r/XX，「帖子标题」（X赞/X评）+ 用户原话引用 + 市场分析）
{"（**注意：每个痛点请尽量结合 Amazon Top SKU 差评做'双重验证'**——如果 Reddit 抱怨的问题在亚马逊差评里也出现，单独标注「Amazon 差评同源」并引用具体差评原话）" if has_amazon else ""}

## 机会点 ①②③（具体规格/材质/做法/定价）
{"（定价区间必须参考 Amazon Top SKU 实际价位段，差异化点必须解决至少 1 条 Top SKU 差评中的问题）" if has_amazon else ""}

## 竞品现状
{"（**必须用上方亚马逊数据**：Top 销量品牌、定价区间、品牌集中度、自营 vs 第三方占比）" if has_amazon else ""}

{scoring_block}
{"（**机会评分维度必须改用 Amazon 数据**：市场规模 = 头部 SKU 月销额累计；竞争程度 = 品牌集中度 + 供需比；差异化可行性 = 差评指向的可解决问题）" if has_amazon else ""}

## 目标买家画像

## 本次研究数据
（必须包含：覆盖版块 / 搜索关键词数 / 扫描帖数 / 精选讨论数 / **Amazon 类目 / Top SKU 数 / 关键词月搜量**。**数据采集时间必须写当天日期 {today}**）

---

要求：
- 只分析数据中真实出现的痛点，不要虚构
- 每个痛点必须有具体 Reddit 帖子作为证据；如果 Amazon 数据可用，优先做"Reddit 痛点 + Amazon 差评"双重验证
- 机会评分必须用上方表格中的数据支撑
- 机会点要具体可执行（有具体规格/材质/做法）
- 整体不少于 1200 字（融合 Amazon 数据后内容更丰富）
{bitable_json_instruction(1)}

以下是 Reddit 原始数据：

{combined[:14000]}
"""

    print(f"\n🤖 深度分析中（模型：{model}{'，含 Amazon 数据' if has_amazon else ''}）...\n")
    return stream_with_retry(model, 8000, prompt)


def analyze_weekly(posts_with_comments, model):
    """周报模式：识别 5+ 个不同方向的产品机会"""
    combined = build_post_texts(posts_with_comments)
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_comments)
    today = datetime.date.today().isoformat()

    prompt = f"""你是一名资深跨境电商选品分析师，正在做本周的 Reddit 选品周报。

以下是从 10 个不同品类版块收集的 {len(posts_with_comments)} 篇帖子和约 {total_comments} 条高赞评论。

**你的任务**：识别出 **5 个不同方向、不同品类**的产品机会（不要 5 个都是家居或厨房）。

**严格要求**：
- 每个机会必须有具体 Reddit 帖子作为证据（标题/赞数/评论数/原话）
- 评分基于实际数据（多少帖子讨论、多少赞）
- 不允许虚构数据里没出现的内容
- 5 个机会必须横跨不同品类

**输出格式**：

# Reddit 选品周报（{today}）

**本周扫描**：10 个版块 / {len(posts_with_comments)} 帖 / {total_comments} 条评论

**核心发现**：1-2 句总结本周最值得关注的趋势

---

## 机会 1：[产品名]（品类：xx）
**痛点核心**（1 句话）
**Reddit 证据**（r/X「标题」（X赞/X评）+ 原话引用）
**产品方向**（具体规格/材质/做法/定价）
**机会评分**：X/10（数据依据：N 篇帖子 / M 条评论 / 累计 K 赞；竞品定价；差异化空间）

---

## 机会 2-5：[同结构]

---

## 本周次级信号（值得继续观察）
- 信号一：简短描述 + 来源
- 信号二：...
- 信号三（如有）

---

## 数据声明
本次样本为 {len(posts_with_comments)} 帖 / {total_comments} 条评论；产品立项前必须做亚马逊/阿里巴巴竞品对比、目标用户访谈等市场验证。
{bitable_json_instruction(5)}

以下是 Reddit 原始数据：

{combined[:22000]}
"""

    print(f"\n🤖 生成周报中（模型：{model}）...\n")
    return stream_with_retry(model, 10000, prompt)


# ── 飞书多维表格自动推送 ────────────────────────────────────────

def extract_bitable_data(report_text):
    """从报告末尾的 HTML 注释块解析 BITABLE_DATA JSON"""
    match = re.search(r'<!--BITABLE_DATA\s*(\{.*?\})\s*-->', report_text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"⚠️  BITABLE_DATA JSON 解析失败：{e}")
        return None


def create_feishu_doc(title, markdown_content):
    """通过 lark-cli 创建飞书文档，返回 doc_url。
    自动剥掉文末的 <!--BITABLE_DATA--> JSON 块（飞书 docx 渲染不会隐藏 HTML 注释）。
    """
    print(f"📄 创建飞书文档：{title}", flush=True)
    # 剥掉 BITABLE_DATA 块（含包裹的换行/分隔线）
    cleaned = re.sub(
        r'\n*-{3,}\n*<!--BITABLE_DATA.*?-->\n*',
        '\n',
        markdown_content,
        flags=re.DOTALL
    )
    cleaned = re.sub(r'\n*<!--BITABLE_DATA.*?-->\n*', '\n', cleaned, flags=re.DOTALL).rstrip() + "\n"
    r = subprocess.run(
        ["lark-cli", "docs", "+create", "--title", title, "--markdown", cleaned],
        capture_output=True, text=True
    )
    raw = r.stdout
    start = raw.find('{')
    if start < 0:
        print(f"❌ 飞书文档创建失败：{raw[:300]}")
        return None
    try:
        d = json.loads(raw[start:])
        if d.get("ok"):
            url = d["data"]["doc_url"]
            print(f"   → {url}", flush=True)
            return url
    except json.JSONDecodeError:
        pass
    print(f"❌ 飞书文档创建失败：{raw[:300]}")
    return None


def push_record(base_token, table_id, payload, record_id=None):
    """通过 lark-cli 创建/更新一条多维表记录，返回 record_id"""
    cmd = ["lark-cli", "base", "+record-upsert",
           "--base-token", base_token, "--table-id", table_id,
           "--json", json.dumps(payload, ensure_ascii=False)]
    if record_id:
        cmd[3:3] = ["--record-id", record_id]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        if not d.get("ok"):
            print(f"     lark-cli 返回错误: {d.get('error', d)}")
            return None
        rec = d["data"].get("record", {})
        # +record-upsert 响应中 record_id 在 record_id_list[0]
        rid_list = rec.get("record_id_list") or []
        if rid_list:
            return rid_list[0]
        return rec.get("record_id") or record_id
    except (json.JSONDecodeError, KeyError) as e:
        print(f"     解析响应失败: {e}; stdout 前 200 字: {r.stdout[:200]}")
        return None


def push_to_bitable(report_text, mode, input_direction, doc_url, scan_summary):
    """解析报告末尾 JSON，推送 N 条机会到选品记录表，关联证据帖到 Reddit热帖表"""
    data = extract_bitable_data(report_text)
    if not data or "opportunities" not in data:
        print("⚠️  报告里没有可解析的 BITABLE_DATA 数据，跳过推送")
        return None

    cfg = load_bitable_config()
    base = cfg["base_token"]
    t1 = cfg["table_research"]
    t2 = cfg["table_posts"]

    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_label = {"weekly": "周报", "broad": "宽泛", "targeted": "定向"}.get(mode, "宽泛")

    opps = data["opportunities"]
    print(f"\n📤 推送 {len(opps)} 个机会到飞书多维表格…", flush=True)
    pushed_records = []

    for i, opp in enumerate(opps, 1):
        record = {
            "产品名称": opp.get("product_name", "?"),
            "分析日期": today,
            "分析模式": mode_label,
            "输入方向": input_direction,
            "机会评分": opp.get("score"),
            "需求真实性": opp.get("demand_score"),
            "市场空间": opp.get("market_score"),
            "差异化可行性": opp.get("diff_score"),
            "核心痛点": opp.get("pain_summary"),
            "机会点": opp.get("opportunity_summary"),
            "竞品现状": opp.get("competition_summary"),
            "覆盖版块": opp.get("subreddits"),
            "扫描帖数": scan_summary.get("posts_scanned"),
            "精选帖数": opp.get("selected_posts"),
            "跟进状态": "待评估",
            "飞书文档": doc_url,
            "备注": opp.get("notes"),
        }
        if opp.get("amazon_validation"):
            record["亚马逊验证"] = opp["amazon_validation"]
        rid = push_record(base, t1, record)
        if rid:
            print(f"  ✅ [{i}/{len(opps)}] 选品记录: {opp.get('product_name')} → {rid}")
            pushed_records.append((rid, opp))
        else:
            print(f"  ❌ [{i}/{len(opps)}] 选品记录失败: {opp.get('product_name')}")

    # 推送证据帖到 Reddit热帖 表
    post_count = 0
    for rid, opp in pushed_records:
        for ep in opp.get("evidence_posts", []) or []:
            post_payload = {
                "帖子标题": ep.get("title"),
                "版块": ep.get("subreddit"),
                "赞数": ep.get("score"),
                "评论数": ep.get("num_comments"),
                "高赞评论": ep.get("top_comments"),
                "帖子摘要": ep.get("summary"),
                "发现日期": today,
                "关联研究": [{"id": rid}],
            }
            if push_record(base, t2, post_payload):
                post_count += 1

    print(f"📤 完成：{len(pushed_records)} 条选品记录 + {post_count} 条证据帖", flush=True)
    return pushed_records


# ── 主流程 ───────────────────────────────────────────────────────

def save_report(report, label, output_dir=None):
    if output_dir is None:
        output_dir = os.path.expanduser("~/reddit-scout-reports")
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    safe_label = label.replace(' ', '_').replace('/', '_')[:30]
    path = os.path.join(output_dir, f"reddit_{safe_label}_{ts}.md")
    with open(path, 'w') as f:
        f.write(report)
    return path


def maybe_push_to_lark(report_text, args, mode, input_direction, label, scan_summary):
    """如果 --bitable 开启：创建飞书文档 + 推送多维表"""
    if not args.bitable:
        return
    today_str = datetime.date.today().isoformat()
    title_map = {
        "weekly": f"Reddit 选品周报 ({today_str})",
        "broad": f"Reddit 选品分析 - 宽泛模式 ({today_str})",
        "targeted": f"Reddit 选品分析 - {input_direction} ({today_str})",
    }
    doc_url = create_feishu_doc(title_map.get(mode, label), report_text)
    if not doc_url:
        print("⚠️  跳过多维表推送（飞书文档创建失败）")
        return
    push_to_bitable(report_text, mode, input_direction, doc_url, scan_summary)


def run_broad_mode(cookie_str, args):
    print("🌐 宽泛模式：自动发现产品机会", flush=True)
    all_posts = []
    for sub, sort, timeframe in BROAD_SUBREDDITS:
        print(f"📥 抓取 r/{sub} ({sort}/{timeframe or 'hot'})...", end=" ", flush=True)
        posts = fetch_posts_from_sub(sub, sort, timeframe, cookie_str)
        print(f"{len(posts)} 帖", flush=True)
        all_posts.extend(posts)
        time.sleep(1.5)

    seen, unique = set(), []
    for p in all_posts:
        if p['id'] not in seen:
            seen.add(p['id']); unique.append(p)
    all_posts = unique
    print(f"\n共 {len(all_posts)} 篇帖子（去重后）")

    SKIP_TITLE_PATTERNS = ['what is', 'who is', 'what are', 'am i', 'eli5', 'megathread', 'weekly']
    filtered = [p for p in all_posts
                if not any(pat in p['title'].lower() for pat in SKIP_TITLE_PATTERNS)]
    hot_posts = sorted(filtered, key=lambda x: x['num_comments'], reverse=True)[:MAX_POSTS_FOR_ANALYSIS]
    print(f"\n📖 抓取前 {len(hot_posts)} 个高热度帖子的评论...\n")

    pwc = []
    for p in hot_posts:
        print(f"  💬 [{p['num_comments']}评] r/{p['subreddit']} — {p['title'][:65]}", flush=True)
        p['comments'] = fetch_comments(p['id'], p['subreddit'], cookie_str)
        pwc.append(p)
        time.sleep(0.3)

    report = analyze_broad(pwc, args.model)
    path = save_report(report, "broad", args.output)
    print(f"✅ 报告已保存：{path}")

    scan = {"posts_scanned": len(pwc),
            "comments_analyzed": sum(len(p.get('comments', [])) for p in pwc),
            "subreddits": [s[0] for s in BROAD_SUBREDDITS]}
    maybe_push_to_lark(report, args, "broad", "宽泛-自动发现", "broad", scan)


def run_targeted_mode(cookie_str, args):
    product = args.product
    print(f"🎯 目标产品：{product}")

    # 数据驱动：先用 Reddit 自带搜索找出真实有讨论的候选版块
    print("🔎 数据驱动发现候选版块...", flush=True)
    candidates = discover_relevant_subreddits(product, cookie_str)
    if candidates:
        print(f"   候选 {len(candidates)} 个版块（按真实帖数排）：")
        for c in candidates[:8]:
            print(f"   · r/{c['subreddit']} ({c['posts']} 帖 / {c['total_score']} 赞)")
    else:
        print("   ⚠️  未找到候选版块，回退到 Claude 自由选择")

    plan = plan_search(product, args.model, candidate_subs=candidates)
    subreddits = plan.get("subreddits", [])
    keywords = plan.get("keywords", [])
    filter_words = plan.get("filter_words", [])
    print(f"📋 Subreddits: {', '.join(subreddits)}")
    print(f"🔍 关键词: {', '.join(keywords)}")
    print(f"🏷️  过滤词: {', '.join(filter_words)}\n")

    all_posts, seen = [], set()
    for sub in subreddits:
        for kw in keywords[:2]:
            print(f"  🔍 r/{sub} ← \"{kw}\"...", end=" ", flush=True)
            posts = search_reddit(kw, subreddit=sub, cookie_str=cookie_str)
            new = [p for p in posts if p['id'] not in seen]
            for p in new: seen.add(p['id'])
            all_posts.extend(new)
            print(f"{len(new)} 帖", flush=True)
            time.sleep(0.5)
    for kw in keywords[2:]:
        print(f"  🌐 全站 ← \"{kw}\"...", end=" ", flush=True)
        posts = search_reddit(kw, cookie_str=cookie_str)
        new = [p for p in posts if p['id'] not in seen]
        for p in new: seen.add(p['id'])
        all_posts.extend(new)
        print(f"{len(new)} 帖", flush=True)
        time.sleep(0.5)
    print(f"\n共找到 {len(all_posts)} 篇帖子（去重后）")

    if filter_words:
        relevant = [p for p in all_posts
                    if any(w.lower() in (p['title'] + ' ' + p['selftext']).lower() for w in filter_words)]
        print(f"相关帖（含核心词 {filter_words}）：{len(relevant)} / {len(all_posts)}")
    else:
        relevant = all_posts
    target_subs = set(s.lower() for s in subreddits)
    relevant.sort(key=lambda x: (0 if x['subreddit'].lower() in target_subs else 1, -x['num_comments']))

    hot = relevant[:MAX_POSTS_FOR_ANALYSIS]
    print(f"\n📖 抓取前 {len(hot)} 帖评论...\n")
    pwc = []
    for p in hot:
        print(f"  💬 [{p['num_comments']}评] r/{p['subreddit']} — {p['title'][:60]}", flush=True)
        p['comments'] = fetch_comments(p['id'], p['subreddit'], cookie_str)
        pwc.append(p)
        time.sleep(0.3)

    report = analyze_targeted(pwc, product, args.model)
    path = save_report(report, product, args.output)
    print(f"✅ 报告已保存：{path}")

    scan = {"posts_scanned": len(pwc),
            "comments_analyzed": sum(len(p.get('comments', [])) for p in pwc),
            "subreddits": subreddits}
    maybe_push_to_lark(report, args, "targeted", product, product, scan)


def run_weekly_mode(cookie_str, args):
    print("📰 周报模式：横扫多品类，输出 5+ 个产品机会\n")
    all_posts = []
    for sub, sort, tf in WEEKLY_SUBREDDITS:
        print(f"📥 r/{sub} ({sort}/{tf or 'hot'})...", end=" ", flush=True)
        posts = fetch_posts_from_sub(sub, sort, tf, cookie_str, limit=WEEKLY_POSTS_PER_SUB)
        print(f"{len(posts)} 帖", flush=True)
        all_posts.extend(posts)
        time.sleep(0.5)

    seen, uniq = set(), []
    for p in all_posts:
        if p['id'] not in seen:
            seen.add(p['id']); uniq.append(p)
    SKIP = ['what is', 'who is', 'am i', 'eli5', 'megathread', 'weekly thread', 'daily']
    all_posts = [p for p in uniq if not any(pat in p['title'].lower() for pat in SKIP)]
    print(f"\n共 {len(all_posts)} 篇有效帖子（去重+过滤后）")

    hot = sorted(all_posts, key=lambda x: x['num_comments'], reverse=True)[:WEEKLY_TOP_POSTS]
    print(f"📖 抓取前 {len(hot)} 帖的高赞评论...\n")
    pwc = []
    for p in hot:
        print(f"  💬 [{p['num_comments']}评] r/{p['subreddit']} — {p['title'][:60]}", flush=True)
        p['comments'] = fetch_comments(p['id'], p['subreddit'], cookie_str)
        pwc.append(p)
        time.sleep(0.7)

    report = analyze_weekly(pwc, args.model)
    today_str = datetime.date.today().isoformat()
    path = save_report(report, f"weekly_{today_str}", args.output)
    print(f"✅ 报告已保存：{path}")

    scan = {"posts_scanned": len(pwc),
            "comments_analyzed": sum(len(p.get('comments', [])) for p in pwc),
            "subreddits": [s[0] for s in WEEKLY_SUBREDDITS]}
    maybe_push_to_lark(report, args, "weekly", f"周报-{today_str}", "weekly", scan)


def main():
    args = parse_args()
    cookie_str = get_cookies()

    if args.weekly:
        run_weekly_mode(cookie_str, args)
    elif args.product:
        run_targeted_mode(cookie_str, args)
    else:
        run_broad_mode(cookie_str, args)


if __name__ == '__main__':
    main()
