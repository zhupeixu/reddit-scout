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
    """关键词搜索帖子，可限定 subreddit"""
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

    scoring_block = SCORING_INSTRUCTIONS.format(
        post_count=len(posts_with_comments),
        comment_count=total_comments
    )

    prompt = f"""你是一名资深跨境电商选品分析师。以下是来自多个 Reddit 版块的真实用户讨论（共 {len(posts_with_comments)} 个帖子，约 {total_comments} 条评论）。

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
    full_text = ""
    with client.messages.stream(
        model=model, max_tokens=8000,
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

**品类背景**（2-3句）

### 痛点一/二/三/四：[标题]
（痛点本质 + Reddit 证据：r/XX，「帖子标题」（X赞/X评）+ 用户原话引用 + 市场分析）

## 机会点 ①②③（具体规格/材质/做法/定价）

## 竞品现状

{scoring_block}

## 目标买家画像

## 本次研究数据

---

要求：
- 只分析数据中真实出现的痛点，不要虚构
- 每个痛点必须有具体 Reddit 帖子作为证据
- 机会评分必须用上方表格中的数据支撑
- 机会点要具体可执行（有具体规格/材质/做法）
- 整体不少于 1000 字
{bitable_json_instruction(1)}

以下是 Reddit 原始数据：

{combined[:18000]}
"""

    print(f"\n🤖 深度分析中（模型：{model}）...\n")
    full_text = ""
    with client.messages.stream(
        model=model, max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print("\n")
    return full_text


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
    full_text = ""
    with client.messages.stream(
        model=model, max_tokens=10000,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print("\n")
    return full_text


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
    """通过 lark-cli 创建飞书文档，返回 doc_url"""
    print(f"📄 创建飞书文档：{title}", flush=True)
    r = subprocess.run(
        ["lark-cli", "docs", "+create", "--title", title, "--markdown", markdown_content],
        capture_output=True, text=True
    )
    raw = r.stdout
    # docs +create 可能输出 [deprecated] 警告行，从首个 { 开始截取
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
            return None
        return d["data"]["record"].get("record_id") or record_id
    except (json.JSONDecodeError, KeyError):
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
        time.sleep(0.8)

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

    plan = plan_search(product, args.model)
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
            time.sleep(1.2)
    for kw in keywords[2:]:
        print(f"  🌐 全站 ← \"{kw}\"...", end=" ", flush=True)
        posts = search_reddit(kw, cookie_str=cookie_str)
        new = [p for p in posts if p['id'] not in seen]
        for p in new: seen.add(p['id'])
        all_posts.extend(new)
        print(f"{len(new)} 帖", flush=True)
        time.sleep(1.2)
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
        time.sleep(0.8)

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
        time.sleep(1.2)

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
