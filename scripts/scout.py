#!/usr/bin/env python3
"""
Reddit 选品侦察：从真实用户讨论中找产品机会（深度分析版 v2）

用法：
  python3 scout.py                              # 使用默认配置
  python3 scout.py --model claude-opus-4-7     # 指定模型
  python3 scout.py --model claude-haiku-4-5-20251001  # 更便宜/更快

可用模型（按能力排序）：
  claude-opus-4-7            最强，分析最深，成本最高
  claude-sonnet-4-6          默认，质量/成本均衡（推荐）
  claude-haiku-4-5-20251001  最快/最便宜，适合快速验证
"""
import json, subprocess, time, sys, datetime, argparse
import browser_cookie3
import anthropic

# ── 配置（可直接修改，也可通过命令行参数覆盖）────────────────
SUBREDDITS = [
    ("BuyItForLife", "top", "week"),
    ("Frugal", "top", "week"),
    ("HomeImprovement", "hot", None),
    ("malelivingspace", "top", "week"),
    ("Frugal", "hot", None),
    ("dogs", "hot", None),
    ("AskReddit", "hot", None),
]
POSTS_PER_SUB = 15
COMMENTS_PER_POST = 30
TOP_POSTS_FOR_ANALYSIS = 20
DEFAULT_MODEL = "claude-sonnet-4-6"
# ─────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Reddit 选品侦察工具")
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Claude 模型（默认：{DEFAULT_MODEL}）",
    )
    return parser.parse_args()

client = anthropic.Anthropic()

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

def fetch_posts(sub, sort, timeframe, cookie_str):
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={POSTS_PER_SUB}"
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

def fetch_comments(post_id, sub, cookie_str):
    url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json?limit={COMMENTS_PER_POST}&sort=top"
    data = reddit_get(url, cookie_str)
    if not data or not isinstance(data, list):
        return []
    comments = []
    for item in data[1]['data']['children']:
        d = item.get('data', {})
        if d.get('body') and d['body'] not in ('[deleted]', '[removed]'):
            comments.append({
                'body': d['body'][:350],
                'score': d.get('score', 0)
            })
    comments.sort(key=lambda x: x['score'], reverse=True)
    return comments[:COMMENTS_PER_POST]

def analyze_deep(posts_with_comments, model=DEFAULT_MODEL):
    """Single-pass: Claude picks best opportunity AND analyzes deeply from actual data."""
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

    combined = "\n\n---\n\n".join(texts)

    prompt = f"""你是一名资深跨境电商选品分析师。以下是来自多个 Reddit 版块的真实用户讨论（共 {len(posts_with_comments)} 个帖子+评论）。

**你的任务**：
1. 从这些数据中**自主识别**最有价值的产品机会（不限定品类，只看讨论中真实暴露的痛点）
2. 对该机会进行深度分析，风格参考以下结构

**输出格式要求**（请严格按照此格式，不要添加"你想让我"或"选A/B"等交互内容）：

---

## [产品名称]：Reddit 买家痛点深度研究

**品类背景**
（2-3句：为什么这个品类值得研究，市场感知规模，核心用户群）

### 痛点一：[具体痛点标题]
（说明痛点本质 + 具体 Reddit 证据：r/XX，「帖子标题」（X赞 / X评）+ 用户原话引用 + 市场分析）

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
（同上）

③ [具体产品改进方向]（如有）
（同上）

## 竞品现状
（现有主要竞品、定价、缺口在哪里）

## 机会评分：X/10
（需求真实性、市场空间、差异化可行性各X分，综合诚实判断）

## 目标买家画像
（核心人群、购买动机、价格敏感度）

## 本次研究数据
（覆盖版块、扫描帖子数、选出的高质量讨论数）

---

**重要提示**：
- 只分析数据中**真实出现**的讨论和痛点，不要虚构
- 每个痛点必须有数据支撑（具体帖子+赞数+用户原话）
- 机会点要具体可执行（不是笼统建议，要有具体规格/做法）
- 整体长度不少于 1000 字
- 不要在报告中问我问题或让我选择，直接输出完整报告

以下是 Reddit 原始数据：

{combined[:18000]}
"""

    print(f"\n🤖 正在进行深度分析（模型：{model}）...\n")
    full_text = ""
    with client.messages.stream(
        model=model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print("\n")
    return full_text

def main():
    args = parse_args()
    print(f"🔑 获取 Reddit cookies...", flush=True)
    cookie_str = get_cookies()

    all_posts = []
    for sub, sort, timeframe in SUBREDDITS:
        print(f"📥 抓取 r/{sub} ({sort}/{timeframe or 'hot'})...", end=" ", flush=True)
        posts = fetch_posts(sub, sort, timeframe, cookie_str)
        print(f"{len(posts)} 帖", flush=True)
        all_posts.extend(posts)
        time.sleep(1.5)

    # deduplicate by post id
    seen = set()
    unique_posts = []
    for p in all_posts:
        if p['id'] not in seen:
            seen.add(p['id'])
            unique_posts.append(p)
    all_posts = unique_posts

    print(f"\n共 {len(all_posts)} 篇帖子（去重后）", flush=True)

    # Grab comments from top posts by engagement
    hot_posts = sorted(all_posts, key=lambda x: x['num_comments'], reverse=True)[:TOP_POSTS_FOR_ANALYSIS]
    print(f"\n📖 抓取前 {len(hot_posts)} 个高热度帖子的评论...\n", flush=True)

    posts_with_comments = []
    for p in hot_posts:
        print(f"  💬 [{p['num_comments']}评] {p['title'][:65]}...", flush=True)
        comments = fetch_comments(p['id'], p['subreddit'], cookie_str)
        p['comments'] = comments
        posts_with_comments.append(p)
        time.sleep(0.8)

    report = analyze_deep(posts_with_comments, model=args.model)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    output_path = f"/tmp/reddit_deep_{ts}.md"
    with open(output_path, 'w') as f:
        f.write(report)
    print(f"\n✅ 报告已保存：{output_path}", flush=True)

if __name__ == '__main__':
    main()
