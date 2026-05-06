#!/usr/bin/env python3
"""
Reddit 选品侦察（统一版）

两种模式：
  # 宽泛模式：自动从多个版块抓取热帖，让 Claude 自主选出最有价值的产品机会
  python3 scout.py

  # 定向模式：指定产品方向，让 Claude 规划搜索策略，深度分析买家痛点
  python3 scout.py --product "女士钱包"
  python3 scout.py -p "women's wallet" --model claude-opus-4-7
"""
import json, subprocess, time, datetime, argparse, urllib.parse, os
import browser_cookie3
import anthropic

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

# 定向模式：每个关键词的搜索帖数
TARGETED_POSTS_PER_SEARCH = 10
# ─────────────────────────────────────────────────────────────────

client = anthropic.Anthropic()


def parse_args():
    parser = argparse.ArgumentParser(description="Reddit 选品侦察工具")
    parser.add_argument(
        "--product", "-p",
        default=None,
        help="定向模式：指定产品方向，如 '女士钱包' 或 \"women's wallet\"。不填则进入宽泛模式。",
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


def fetch_posts_from_sub(sub, sort, timeframe, cookie_str, limit=BROAD_POSTS_PER_SUB):
    """宽泛模式：按版块抓取热帖"""
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}"
    if timeframe:
        url += f"&t={timeframe}"
    data = reddit_get(url, cookie_str)
    if not data:
        return []
    posts = []
    for item in data['data']['children']:
        d = item['data']
        posts.append({
            'id': d['id'],
            'title': d['title'],
            'score': d['score'],
            'num_comments': d['num_comments'],
            'selftext': d['selftext'][:600] if d.get('selftext') else '',
            'subreddit': sub,
        })
    return posts


def search_reddit(keyword, subreddit=None, cookie_str="", limit=TARGETED_POSTS_PER_SEARCH):
    """定向模式：关键词搜索帖子，可限定 subreddit"""
    q = urllib.parse.quote(keyword)
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/search.json?q={q}&sort=top&t=year&limit={limit}&restrict_sr=1"
    else:
        url = f"https://www.reddit.com/search.json?q={q}&sort=top&t=year&limit={limit}"
    data = reddit_get(url, cookie_str)
    if not data:
        return []
    posts = []
    for item in data['data']['children']:
        d = item['data']
        posts.append({
            'id': d['id'],
            'title': d['title'],
            'score': d['score'],
            'num_comments': d['num_comments'],
            'selftext': d['selftext'][:600] if d.get('selftext') else '',
            'subreddit': d['subreddit'],
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


def plan_search(product, model):
    """定向模式 Step 1：让 Claude 规划搜索策略"""
    print(f"\n🧠 规划搜索策略（产品：{product}）...\n")
    prompt = f"""你是一名跨境电商选品研究员，正在研究「{product}」这个产品品类在 Reddit 上的买家讨论。

请给出搜索策略，返回 JSON 格式：

{{
  "subreddits": ["subreddit1", "subreddit2", "subreddit3"],
  "keywords": ["search phrase 1", "search phrase 2", "search phrase 3", "search phrase 4"],
  "filter_words": ["word1", "word2"]
}}

要求：
- subreddits：3-5 个，选择最可能有该产品**真实买家**讨论的版块（英文，不带 r/），不要选男性时尚版块给女性产品
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
        text = f"【r/{p['subreddit']}】{p['score']}赞 / {p['num_comments']}评\n标题: {p['title']}\n"
        if p['selftext']:
            text += f"内容: {p['selftext']}\n"
        if p['comments']:
            text += "高赞评论:\n"
            for c in p['comments'][:10]:
                text += f"  ({c['score']}赞) \"{c['body'][:250]}\"\n"
        texts.append(text)
    return "\n\n---\n\n".join(texts)


SCORING_INSTRUCTIONS = """
## 机会评分：X/10

**评分依据**（必须用实际数据支撑，不允许凭感觉打分）：

| 维度 | 得分 | 数据依据 |
|------|------|---------|
| 需求真实性 | X/3 | 本次数据中明确表达该痛点的帖子数、评论数、总赞数（如：3篇帖子 + 47条评论提及，累计 312赞） |
| 市场空间 | X/3 | 搜索结果密度、版块规模（如：r/BuyItForLife 月活300万+，该话题帖子 top 帖平均 150赞） |
| 差异化可行性 | X/4 | 现有竞品的具体缺陷（引用评论）vs 你的改进方案是否可落地 |

**诚实声明**：本次样本为 {post_count} 帖 / {comment_count} 条评论，Reddit 讨论量不等于市场规模，以上评分仅反映 Reddit 上的声音，不构成市场验证。
"""


def analyze_broad(posts_with_comments, model):
    """宽泛模式：Claude 自主选出最有价值的产品机会并深度分析"""
    combined = build_post_texts(posts_with_comments)
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_comments)

    scoring_block = SCORING_INSTRUCTIONS.format(
        post_count=len(posts_with_comments),
        comment_count=total_comments
    )

    prompt = f"""你是一名资深跨境电商选品分析师。以下是来自多个 Reddit 版块的真实用户讨论（共 {len(posts_with_comments)} 个帖子，约 {total_comments} 条评论）。

**你的任务**：
1. 从这些数据中**自主识别**最有价值的产品机会（不限定品类，只看讨论中真实暴露的痛点）
2. 对该机会进行深度分析

**输出格式**（严格按此格式，不要添加"你想让我"或"选A/B"等交互内容）：

---

## [产品名称]：Reddit 买家痛点深度研究

**品类背景**
（2-3句：为什么这个品类值得研究，市场感知规模，核心用户群）

### 痛点一：[具体痛点标题]
（痛点本质 + 具体 Reddit 证据：r/XX，「帖子标题」（X赞 / X评）+ 用户原话引用 + 市场分析）

### 痛点二：[具体痛点标题]
（同上）

### 痛点三：[具体痛点标题]
（同上）

### 痛点四：[具体痛点标题]（如有）
（同上）

## 机会点

① [具体产品改进方向]
（具体材质/尺寸/设计细节 + 制造可行性 + 产品页面卖点文案方向）

② [具体产品改进方向]

③ [具体产品改进方向]（如有）

## 竞品现状
（现有主要竞品、定价区间、市场缺口）

{scoring_block}

## 目标买家画像
（核心人群、购买动机、价格敏感度、触达渠道）

## 本次研究数据
（覆盖版块、扫描帖子数、精选讨论数、关键词范围）

---

**重要约束**：
- 只分析数据中**真实出现**的讨论和痛点，不要虚构
- 每个痛点必须有具体帖子+赞数+用户原话作为证据
- 机会评分必须用上方表格中的数据支撑，不允许"感觉很强烈"式的打分
- 机会点要有具体规格/材质/做法，不要笼统建议
- 整体不少于 1000 字

以下是 Reddit 原始数据：

{combined[:18000]}
"""

    print(f"\n🤖 正在进行深度分析（模型：{model}）...\n")
    full_text = ""
    with client.messages.stream(
        model=model, max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print("\n")
    return full_text


def analyze_targeted(posts_with_comments, product, model):
    """定向模式：深度分析指定产品的买家痛点"""
    combined = build_post_texts(posts_with_comments)
    total_comments = sum(len(p.get('comments', [])) for p in posts_with_comments)

    scoring_block = SCORING_INSTRUCTIONS.format(
        post_count=len(posts_with_comments),
        comment_count=total_comments
    )

    prompt = f"""你是一名资深跨境电商选品分析师，正在研究「{product}」这个品类的 Reddit 买家真实讨论。

以下是从 Reddit 相关版块收集的帖子和评论数据（共 {len(posts_with_comments)} 个帖子，约 {total_comments} 条评论）。

请对「{product}」进行深度买家痛点研究，严格按以下格式输出完整报告：

---

## {product}：Reddit 买家痛点深度研究

**品类背景**
（2-3句：市场规模感知、核心用户群、为什么值得研究）

### 痛点一：[具体痛点标题]
（痛点本质 + Reddit 证据：r/XX，「帖子标题」（X赞/X评）+ 用户原话引用 + 市场分析）

### 痛点二：[具体痛点标题]
（同上）

### 痛点三：[具体痛点标题]
（同上）

### 痛点四：[具体痛点标题]（如有）
（同上）

## 机会点

① [具体改进方向]
（材质/尺寸/设计细节 + 制造可行性 + 产品页面卖点方向）

② [具体改进方向]

③ [具体改进方向]（如有）

## 竞品现状
（现有主要竞品、定价区间、市场缺口）

{scoring_block}

## 目标买家画像
（人群/购买动机/价格敏感度/触达渠道）

## 本次研究数据
（覆盖版块 / 搜索关键词数 / 扫描帖数 / 精选讨论数）

---

要求：
- 只分析数据中真实出现的痛点，不要虚构
- 每个痛点必须有具体 Reddit 帖子作为证据（帖子标题+赞数+评论数+用户原话）
- 机会评分必须用上方表格中的数据支撑——明确列出本次数据中有多少帖子、多少评论、多少赞在讨论该痛点，不允许用模糊的"讨论热烈"代替数字
- 机会点要具体可执行（有具体规格/材质/做法）
- 整体不少于 1000 字

以下是 Reddit 原始数据：

{combined[:18000]}
"""

    print(f"\n🤖 深度分析中（模型：{model}）...\n")
    full_text = ""
    with client.messages.stream(
        model=model, max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print("\n")
    return full_text


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


def run_broad_mode(cookie_str, model, output_dir):
    """宽泛模式主流程"""
    all_posts = []
    for sub, sort, timeframe in BROAD_SUBREDDITS:
        print(f"📥 抓取 r/{sub} ({sort}/{timeframe or 'hot'})...", end=" ", flush=True)
        posts = fetch_posts_from_sub(sub, sort, timeframe, cookie_str)
        print(f"{len(posts)} 帖", flush=True)
        all_posts.extend(posts)
        time.sleep(1.5)

    # 去重
    seen, unique = set(), []
    for p in all_posts:
        if p['id'] not in seen:
            seen.add(p['id'])
            unique.append(p)
    all_posts = unique
    print(f"\n共 {len(all_posts)} 篇帖子（去重后）", flush=True)

    # 过滤：跳过通用闲聊帖（AskReddit 风格，无具体产品痛点迹象）
    # 取评论数前 N，但排除纯问答/无产品内容的帖子
    SKIP_TITLE_PATTERNS = ['what is', 'who is', 'what are', 'am i', 'eli5', 'megathread', 'weekly']
    def likely_product_discussion(p):
        title_lower = p['title'].lower()
        if any(pat in title_lower for pat in SKIP_TITLE_PATTERNS):
            return False
        return True

    filtered = [p for p in all_posts if likely_product_discussion(p)]
    hot_posts = sorted(filtered, key=lambda x: x['num_comments'], reverse=True)[:MAX_POSTS_FOR_ANALYSIS]
    print(f"\n📖 抓取前 {len(hot_posts)} 个高热度帖子的评论...\n", flush=True)

    posts_with_comments = []
    for p in hot_posts:
        print(f"  💬 [{p['num_comments']}评] r/{p['subreddit']} — {p['title'][:65]}...", flush=True)
        comments = fetch_comments(p['id'], p['subreddit'], cookie_str)
        p['comments'] = comments
        posts_with_comments.append(p)
        time.sleep(0.8)

    report = analyze_broad(posts_with_comments, model)
    path = save_report(report, "broad", output_dir)
    print(f"✅ 报告已保存：{path}", flush=True)


def run_targeted_mode(product, cookie_str, model, output_dir):
    """定向模式主流程"""
    print(f"🎯 目标产品：{product}", flush=True)

    # Step 1: 规划搜索策略
    plan = plan_search(product, model)
    subreddits = plan.get("subreddits", [])
    keywords = plan.get("keywords", [])
    filter_words = plan.get("filter_words", [])
    print(f"📋 Subreddits: {', '.join(subreddits)}")
    print(f"🔍 关键词: {', '.join(keywords)}")
    print(f"🏷️  过滤词: {', '.join(filter_words)}\n")

    # Step 2: 搜索帖子
    all_posts, seen = [], set()

    # 在每个 subreddit 内搜索前两个关键词
    for sub in subreddits:
        for kw in keywords[:2]:
            print(f"  🔍 r/{sub} ← \"{kw}\"...", end=" ", flush=True)
            posts = search_reddit(kw, subreddit=sub, cookie_str=cookie_str)
            new = [p for p in posts if p['id'] not in seen]
            for p in new:
                seen.add(p['id'])
            all_posts.extend(new)
            print(f"{len(new)} 帖", flush=True)
            time.sleep(1.2)

    # 全站搜索剩余关键词
    for kw in keywords[2:]:
        print(f"  🌐 全站 ← \"{kw}\"...", end=" ", flush=True)
        posts = search_reddit(kw, cookie_str=cookie_str)
        new = [p for p in posts if p['id'] not in seen]
        for p in new:
            seen.add(p['id'])
        all_posts.extend(new)
        print(f"{len(new)} 帖", flush=True)
        time.sleep(1.2)

    print(f"\n共找到 {len(all_posts)} 篇帖子（去重后）")

    # Step 3: 过滤不相关帖
    if filter_words:
        def is_relevant(p):
            text = (p['title'] + ' ' + p['selftext']).lower()
            return any(w.lower() in text for w in filter_words)
        relevant = [p for p in all_posts if is_relevant(p)]
        print(f"相关帖（含核心词 {filter_words}）：{len(relevant)} 篇 / 共 {len(all_posts)} 篇")
    else:
        relevant = all_posts
        print("（未设置过滤词，使用全部帖子）")

    # 优先用目标 subreddit 内的帖子，再按评论数排序
    target_subs = set(s.lower() for s in subreddits)
    relevant.sort(key=lambda x: (
        0 if x['subreddit'].lower() in target_subs else 1,
        -x['num_comments']
    ))

    hot_posts = relevant[:MAX_POSTS_FOR_ANALYSIS]
    print(f"\n📖 抓取前 {len(hot_posts)} 帖评论...\n")

    posts_with_comments = []
    for p in hot_posts:
        print(f"  💬 [{p['num_comments']}评] r/{p['subreddit']} — {p['title'][:60]}...", flush=True)
        comments = fetch_comments(p['id'], p['subreddit'], cookie_str)
        p['comments'] = comments
        posts_with_comments.append(p)
        time.sleep(0.8)

    # Step 4: 深度分析
    report = analyze_targeted(posts_with_comments, product, model)
    path = save_report(report, product, output_dir)
    print(f"✅ 报告已保存：{path}", flush=True)


def main():
    args = parse_args()
    cookie_str = get_cookies()

    if args.product:
        run_targeted_mode(args.product, cookie_str, args.model, args.output)
    else:
        print("🌐 宽泛模式：自动发现产品机会", flush=True)
        run_broad_mode(cookie_str, args.model, args.output)


if __name__ == '__main__':
    main()
