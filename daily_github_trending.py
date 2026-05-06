import json
from html import escape, unescape

import httpx
from bs4 import BeautifulSoup
import re
import os
import sys
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending?since=daily"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _history_path() -> Path:
    return Path(__file__).resolve().parent / "history.json"


def load_history() -> dict:
    """加载历史数据，文件不存在时返回空 dict"""
    hp = _history_path()
    if hp.exists():
        try:
            with open(hp, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(history: dict):
    """保存历史数据到 JSON 文件"""
    with open(_history_path(), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_history(history: dict, repos: list[dict]) -> dict:
    """更新历史数据，返回更新后的 history"""
    today = datetime.now().strftime("%Y-%m-%d")
    for repo in repos:
        full_name = repo["full_name"]
        if full_name in history:
            history[full_name]["total_days"] += 1
            history[full_name]["last_stars"] = repo["today_stars"]
        else:
            history[full_name] = {
                "first_seen": today,
                "total_days": 1,
                "last_stars": repo["today_stars"],
            }
    return history


def get_repo_status(full_name: str, history: dict) -> str:
    """返回项目状态标识：new / streak{N} / recurring"""
    if full_name not in history:
        return "new"
    days = history[full_name]["total_days"]
    if days == 1:
        return "new"
    if 2 <= days <= 5:
        return f"streak{days}"
    return "recurring"


def fetch_trending() -> list[dict]:
    """抓取 GitHub Trending 页面，返回 Top 10 项目列表"""
    log.info("正在抓取 GitHub Trending...")
    try:
        resp = httpx.get(TRENDING_URL, headers=HEADERS, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.error(f"GitHub Trending 请求失败: {e}")
        return []
    soup = BeautifulSoup(resp.text, "html.parser")

    repos = []
    for article in soup.find_all("article", class_="Box-row"):
        h2 = article.find("h2", class_="h3")
        if not h2:
            continue
        a_tag = h2.find("a")
        if not a_tag:
            continue
        href = a_tag["href"].strip()
        parts = href.strip("/").split("/")
        if len(parts) != 2:
            continue
        owner, name = parts

        # 描述
        desc_p = article.find("p", class_="col-9")
        description = unescape(desc_p.text.strip()) if desc_p else ""

        # 语言
        lang_el = article.find("span", itemprop="programmingLanguage")
        language = lang_el.text.strip() if lang_el else "Unknown"

        # 今日 star 数
        star_el = article.select_one(".d-inline-block.float-sm-right")
        today_stars = star_el.text.strip() if star_el else "N/A"
        if today_stars == "N/A":
            for span in article.find_all("span", class_="d-inline-block"):
                text = span.text.strip()
                if re.search(r'[\d,]+ stars? today', text):
                    today_stars = text
                    break

        repos.append({
            "owner": owner,
            "name": name,
            "full_name": f"{owner}/{name}",
            "url": f"https://github.com{href}",
            "description": description,
            "language": language,
            "today_stars": today_stars,
        })

    log.info(f"抓取完成，获取到 {len(repos)} 个项目")
    return repos[:10]


SYSTEM_PROMPT = """你是一位资深科技分析师兼创业者，每天为读者（主要是产品经理和技术决策者）撰写 GitHub 热门项目深度解读。
你的分析风格：专业但不枯燥，有深度但不啰嗦，锐评时犀利毒舌但言之有理。
请严格按以下格式输出，每个部分用 `###` 标题分隔：

### 项目简介
简要说明这个项目是什么、解决什么核心问题（2-3句话）

### 适合人群
列出这个项目最适合哪些人/角色/场景使用（用 `-` 列表，不超过 5 条）

### 思维发散
从产品经理和创新者视角出发：这个项目或技术能做成什么有趣且用户愿意付费的产品？市场上现有竞品是谁、差距在哪？可以和哪些其他技术组合产生化学反应？（3-5 条具体思路，每条用 `-` 开头，每条需包含商业化价值判断）

### 推荐评估
商业化潜力：X/10 — 一句话理由
落地难度：X/10 — 一句话理由
值得关注度：X/10 — 一句话理由

### 锐评
优点：
- 列出 2-3 个真实优点

缺点：
- 列出 2-3 个真实缺点或潜在风险

总结一句话（犀利风格）"""


def build_analysis_prompt(repo: dict) -> str:
    return f"""请深度分析以下 GitHub Trending 项目：

项目名称：{repo['full_name']}
项目链接：{repo['url']}
编程语言：{repo['language']}
项目描述：{repo['description'] or '（无描述）'}
今日新增 Star：{repo['today_stars']}"""


def analyze_repo(client: OpenAI, repo: dict, model: str) -> dict:
    """调用 DeepSeek API 深度分析单个项目；失败时 analysis 为 None 且 error 字段带错误信息"""
    log.info(f"正在分析: {repo['full_name']} ...")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_analysis_prompt(repo)},
            ],
            temperature=0.8,
            max_tokens=4096,
        )
        content = resp.choices[0].message.content
        if not content:
            log.warning(f"API 返回空内容: {repo['full_name']}")
            return {"repo": repo, "analysis": None, "error": "API returned empty content"}
        usage = getattr(resp, "usage", None)
        token_str = f"tokens: {usage.total_tokens}" if usage else "tokens: N/A"
        log.info(f"分析完成: {repo['full_name']} ({token_str})")
        return {"repo": repo, "analysis": content, "error": None}
    except Exception as e:
        log.error(f"分析失败: {repo['full_name']} — {e}")
        return {"repo": repo, "analysis": None, "error": str(e)}


def analyze_all(client: OpenAI, repos: list[dict], model: str) -> list[dict]:
    """逐项目分析，单个失败不影响整体"""
    results = []
    for i, repo in enumerate(repos, 1):
        log.info(f"[{i}/{len(repos)}] 开始分析")
        result = analyze_repo(client, repo, model)
        results.append(result)
    return results


TREND_SUMMARY_PROMPT = """你是一位资深技术趋势分析师。以下是今日 GitHub Trending Top 10 项目的分析摘要。
请基于这些信息，用 3-5 句话总结今日的技术风向，包括：
- 今日最值得关注的技术领域/热点是什么
- 有哪些值得关注的新兴方向或变化
- 对产品经理和技术决策者的建议

要求：语言精炼、有洞察力、不罗列项目名称，而是提炼趋势。用纯文本输出，不需要 markdown 格式。"""


def generate_trend_summary(client: OpenAI, results: list[dict], model: str) -> str:
    """汇总所有分析结果，调用 AI 生成今日技术风向总结"""
    log.info("正在生成今日技术风向总结...")
    summaries = []
    for i, r in enumerate(results, 1):
        repo = r["repo"]
        analysis = r.get("analysis", "")
        if analysis:
            # 提取项目简介部分作为摘要
            m = re.search(r"### 项目简介\s*\n(.*?)(?=###|\Z)", analysis, re.DOTALL)
            intro = m.group(1).strip() if m else analysis[:200]
        else:
            intro = f"{repo['full_name']}: {repo.get('description', '无描述')}"
        summaries.append(f"{i}. {repo['full_name']} ({repo['language']}) — {intro}")

    all_summaries = "\n\n".join(summaries)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": TREND_SUMMARY_PROMPT},
                {"role": "user", "content": f"今日 GitHub Trending Top 10 项目摘要：\n\n{all_summaries}"},
            ],
            temperature=0.7,
            max_tokens=1024,
        )
        content = resp.choices[0].message.content
        if content:
            log.info("技术风向总结生成完成")
            return content.strip()
        log.warning("趋势总结 API 返回空内容")
        return "今日技术风向总结暂不可用。"
    except Exception as e:
        log.error(f"趋势总结生成失败: {e}")
        return "今日技术风向总结生成失败，请稍后重试。"


HTML_CSS = """<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #ffffff; color: #24292f; font-family: -apple-system, BlinkMacSystemFont,
      'Segoe UI', 'Noto Sans SC', 'PingFang SC', sans-serif;
    line-height: 1.7;
  }
  .main-content { margin-left: 260px; padding: 40px 20px; }
  .container { max-width: 900px; margin: 0 auto; }
  .header {
    text-align: center; padding: 60px 0 40px;
    border-bottom: 1px solid #d0d7de; margin-bottom: 48px;
  }
  .header h1 {
    font-size: 2.2em; background: linear-gradient(135deg, #0969da, #8250df);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .header .date { color: #656d76; margin-top: 8px; font-size: 1.05em; }
  .header .summary {
    margin-top: 20px; padding: 16px 24px; background: #f6f8fa;
    border-radius: 8px; border-left: 3px solid #0969da;
    text-align: left; color: #656d76; font-size: 0.95em;
  }
  .card {
    background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 12px;
    padding: 32px; margin-bottom: 28px;
    transition: border-color 0.2s, box-shadow 0.2s; scroll-margin-top: 24px;
  }
  .card:hover { border-color: #0969da; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .card-header {
    display: flex; align-items: center; gap: 14px; margin-bottom: 18px;
    flex-wrap: wrap;
  }
  .rank {
    display: inline-flex; align-items: center; justify-content: center;
    width: 36px; height: 36px; border-radius: 50%;
    font-weight: 700; font-size: 0.95em; flex-shrink: 0;
  }
  .rank-1 { background: #f0c419; color: #fff; }
  .rank-2 { background: #a3b4c2; color: #fff; }
  .rank-3 { background: #cd7f32; color: #fff; }
  .rank-other { background: #eaeef2; color: #656d76; }
  .repo-name { font-size: 1.15em; font-weight: 600; }
  .repo-name a { color: #0969da; text-decoration: none; }
  .repo-name a:hover { text-decoration: underline; }
  .meta { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.85em; color: #656d76; }
  .meta span { display: inline-flex; align-items: center; gap: 4px; }
  .lang-dot {
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  }
  .desc { color: #656d76; font-size: 0.93em; margin-bottom: 20px; line-height: 1.6; }
  .section { margin-top: 20px; }
  .section h3 { font-size: 1em; margin-bottom: 10px; color: #24292f; }
  .section ul { padding-left: 20px; color: #24292f; font-size: 0.93em; }
  .section ul li { margin-bottom: 6px; }
  .pros-cons { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .pros { background: #dafbe1; border-radius: 8px; padding: 16px; }
  .cons { background: #ffebe9; border-radius: 8px; padding: 16px; }
  .pros h4 { color: #1a7f37; font-size: 0.93em; margin-bottom: 8px; }
  .cons h4 { color: #cf222e; font-size: 0.93em; margin-bottom: 8px; }
  .pros ul, .cons ul { color: #24292f; font-size: 0.9em; }
  .verdict { margin-top: 16px; padding: 12px 16px; background: #f3e8ff;
    border-radius: 6px; font-style: italic; color: #8250df; font-size: 0.93em; }
  .footer {
    text-align: center; padding: 40px 0; color: #656d76; font-size: 0.85em;
    border-top: 1px solid #d0d7de; margin-top: 20px;
  }
  .footer a { color: #0969da; }

  /* 左侧目录 */
  #sidebar {
    position: fixed; left: 0; top: 0; width: 250px; height: 100vh;
    background: #f6f8fa; border-right: 1px solid #d0d7de;
    overflow-y: auto; padding: 24px 0; z-index: 100;
  }
  #sidebar .toc-title {
    padding: 0 20px 16px; font-size: 0.85em; font-weight: 700;
    color: #656d76; text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid #d0d7de; margin-bottom: 8px;
  }
  #sidebar a {
    display: block; padding: 8px 20px; color: #656d76;
    text-decoration: none; font-size: 0.87em;
    border-left: 2px solid transparent; transition: all 0.15s;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #sidebar a:hover { color: #24292f; background: #eaeef2; }
  #sidebar a.active {
    color: #0969da; background: #eaeef2;
    border-left-color: #0969da;
  }
  #sidebar a .toc-rank {
    display: inline-block; width: 22px; font-weight: 600; color: #0969da;
  }
  #sidebar a .toc-dot-new {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #ff9800; margin-left: 4px; vertical-align: middle;
  }

  /* 技术风向总结 */
  .trend-summary {
    background: linear-gradient(135deg, #f6f8fa 0%, #e8f0fe 50%, #f6f8fa 100%);
    background-size: 200% 200%;
    animation: gradientShift 8s ease infinite;
    border: 1px solid #d0d7de; border-radius: 12px;
    padding: 28px 32px; margin-bottom: 40px;
    border-left: 4px solid #0969da;
  }
  .trend-summary h2 {
    font-size: 1.15em; color: #0969da; margin-bottom: 12px;
  }
  .trend-summary p {
    font-size: 0.95em; color: #24292f; line-height: 1.8;
  }
  @keyframes gradientShift {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
  }

  /* 卡片入场动画 */
  .card {
    opacity: 0; transform: translateY(30px);
    transition: opacity 0.5s ease, transform 0.5s ease, border-color 0.2s, box-shadow 0.2s;
  }
  .card.visible { opacity: 1; transform: translateY(0); }

  /* NEW/STREAK 徽章 */
  .badge-new {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75em; font-weight: 700; color: #fff; background: #2da44e;
    animation: badgePulse 2s ease-in-out infinite;
  }
  .badge-streak {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75em; font-weight: 700; color: #fff; background: #8250df;
  }
  .badge-recurring {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.75em; font-weight: 700; color: #656d76; background: #eaeef2;
  }
  @keyframes badgePulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(45, 164, 78, 0.4); }
    50% { box-shadow: 0 0 0 6px rgba(45, 164, 78, 0); }
  }

  /* 评分进度条 */
  .score-row { margin-bottom: 14px; }
  .score-label { font-size: 0.9em; font-weight: 600; color: #24292f; margin-bottom: 4px; }
  .score-num { font-weight: 700; color: #0969da; margin-left: 4px; }
  .score-bar {
    height: 8px; background: #eaeef2; border-radius: 4px; overflow: hidden;
    margin-bottom: 2px;
  }
  .score-bar-fill {
    height: 100%; border-radius: 4px;
    transition: width 1s ease;
  }
  .score-reason {
    font-size: 0.82em; color: #656d76;
  }

  /* 今日 Star 高亮 */
  .today-stars-count {
    font-weight: 700; color: #0969da; font-size: 0.9em;
  }

  @media (max-width: 768px) {
    #sidebar { display: none; }
    .main-content { margin-left: 0; padding: 20px 12px; }
    .card { padding: 20px; }
    .pros-cons { grid-template-columns: 1fr; }
    .header h1 { font-size: 1.5em; }
  }
</style>"""


def format_analysis_html(analysis: str) -> str:
    """把 AI 返回的 markdown 分析转为 HTML"""
    analysis = escape(analysis)
    sections = re.split(r'(?=### )', analysis.strip())
    html_parts = []
    verdict_text = ""

    for sec in sections:
        sec = sec.strip()
        if sec.startswith("### 项目简介"):
            content = sec.replace("### 项目简介", "").strip()
            content = _inline_md_to_html(content)
            html_parts.append(f'<div class="section"><h3>📖 项目简介</h3><p style="color:#8b949e;font-size:0.93em;">{content}</p></div>')
        elif sec.startswith("### 适合人群"):
            content = sec.replace("### 适合人群", "").strip()
            html_parts.append(f'<div class="section"><h3>🎯 适合人群</h3><ul>{_md_list_to_html(content)}</ul></div>')
        elif sec.startswith("### 思维发散"):
            content = sec.replace("### 思维发散", "").strip()
            html_parts.append(f'<div class="section"><h3>💡 思维发散</h3><ul>{_md_list_to_html(content)}</ul></div>')
        elif sec.startswith("### 推荐评估"):
            content = sec.replace("### 推荐评估", "").strip()
            scores = _parse_recommendation(content)
            html_parts.append(f'<div class="section"><h3>📊 推荐评估</h3>{_render_score_bars(scores)}</div>')
        elif sec.startswith("### 锐评"):
            content = sec.replace("### 锐评", "").strip()
            pros_html, cons_html, verdict_text = _parse_critique(content)
            html_parts.append(f'''<div class="section"><h3>⚔️ 锐评</h3>
<div class="pros-cons">
  <div class="pros"><h4>✅ 优点</h4><ul>{pros_html}</ul></div>
  <div class="cons"><h4>❌ 缺点</h4><ul>{cons_html}</ul></div>
</div>
<div class="verdict">💬 {verdict_text}</div></div>''')
        else:
            html_parts.append(f'<div class="section">{sec}</div>')

    return "\n".join(html_parts)


def _inline_md_to_html(text: str) -> str:
    """把行内 markdown（粗体、斜体、代码）转为 HTML"""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = text.replace('**', '')
    text = re.sub(r'(?<!\w)\*(?!\w)', '', text)
    return text


def _md_list_to_html(text: str) -> str:
    """把 markdown 列表转为 li 标签"""
    items = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("-"):
            items.append(f"<li>{_inline_md_to_html(stripped[1:].strip())}</li>")
        elif stripped and not stripped.startswith("#"):
            items.append(f"<li>{_inline_md_to_html(stripped)}</li>")
    return "\n".join(items)


def _parse_critique(text: str) -> tuple[str, str, str]:
    """解析锐评中的优/缺点和总结句"""
    pros_items = []
    cons_items = []
    verdict = ""
    current = None

    def _extract_remainder(line: str) -> str:
        """Extract content after colon in a header line like 优点：xxx"""
        for sep in ("：", ":"):
            if sep in line:
                remainder = line.split(sep, 1)[1].strip()
                if remainder:
                    return remainder
        return ""

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("优点") or stripped.startswith("**优点"):
            current = "pros"
            remainder = _extract_remainder(stripped)
            if remainder:
                pros_items.append(f"<li>{_inline_md_to_html(remainder)}</li>")
            continue
        elif stripped.startswith("缺点") or stripped.startswith("**缺点"):
            current = "cons"
            remainder = _extract_remainder(stripped)
            if remainder:
                cons_items.append(f"<li>{_inline_md_to_html(remainder)}</li>")
            continue
        elif stripped.startswith("总结") or stripped.startswith("**总结"):
            current = "verdict"
            remainder = _extract_remainder(stripped)
            if remainder:
                verdict = _inline_md_to_html(remainder)
            continue

        if current == "pros" and stripped.startswith("-"):
            pros_items.append(f"<li>{_inline_md_to_html(stripped[1:].strip())}</li>")
        elif current == "cons" and stripped.startswith("-"):
            cons_items.append(f"<li>{_inline_md_to_html(stripped[1:].strip())}</li>")
        elif current == "verdict" and stripped:
            part = _inline_md_to_html(stripped)
            verdict = verdict + part if verdict else part

    return "\n".join(pros_items), "\n".join(cons_items), verdict


def _parse_recommendation(text: str) -> dict:
    """解析推荐评估中的三维评分，返回 {label, score, reason} 列表"""
    scores = []
    patterns = [
        (r"商业化潜力[：:]\s*(\d+)/10\s*[—\-—]?\s*(.*)", "商业化潜力"),
        (r"落地难度[：:]\s*(\d+)/10\s*[—\-—]?\s*(.*)", "落地难度"),
        (r"值得关注度[：:]\s*(\d+)/10\s*[—\-—]?\s*(.*)", "值得关注度"),
    ]
    for pattern, label in patterns:
        m = re.search(pattern, text)
        if m:
            scores.append({
                "label": label,
                "score": int(m.group(1)),
                "reason": m.group(2).strip(),
            })
    return scores


def _render_score_bars(scores: list[dict]) -> str:
    """生成三维评分进度条 HTML"""
    if not scores:
        return '<p style="color:#8b949e;">评分数据暂不可用</p>'

    colors = {"商业化潜力": "#0969da", "落地难度": "#cf222e", "值得关注度": "#8250df"}
    icons = {"商业化潜力": "💰", "落地难度": "🔧", "值得关注度": "⭐"}

    bars = []
    for s in scores:
        label = s["label"]
        color = colors.get(label, "#0969da")
        icon = icons.get(label, "")
        score = s["score"]
        reason = _inline_md_to_html(s["reason"])
        bars.append(f"""<div class="score-row">
  <div class="score-label">{icon} {label} <span class="score-num">{score}/10</span></div>
  <div class="score-bar"><div class="score-bar-fill" style="background:{color};width:0" data-width="{score * 10}%"></div></div>
  <div class="score-reason">{reason}</div>
</div>""")
    return "\n".join(bars)


def rank_class(rank: int) -> str:
    if rank == 1: return "rank-1"
    if rank == 2: return "rank-2"
    if rank == 3: return "rank-3"
    return "rank-other"


LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Go": "#00ADD8", "Rust": "#dea584", "Java": "#b07219",
    "C++": "#f34b7d", "C": "#555555", "Ruby": "#701516",
    "Swift": "#F05138", "Kotlin": "#A97BFF", "Zig": "#ec915c",
}


def lang_color(lang: str) -> str:
    return LANG_COLORS.get(lang, "#8b949e")


def generate_html(results: list[dict], date_str: str, history: dict, trend_summary: str) -> str:
    """生成完整的自包含 HTML 日报"""
    cards_html = []
    toc_items = []
    for i, result in enumerate(results, 1):
        repo = result["repo"]
        analysis = result.get("analysis") or "### 项目简介\n分析暂不可用\n\n### 锐评\n优点：\n- 暂无\n\n缺点：\n- 暂无\n\n总结：分析过程出现问题"
        repo_name = escape(repo["full_name"])
        repo_url = escape(repo["url"], quote=True)
        repo_desc = escape(repo["description"]) if repo["description"] else "（无描述）"
        repo_lang = escape(repo["language"])
        repo_stars = escape(repo["today_stars"])

        # 历史状态徽章
        status = get_repo_status(repo["full_name"], history)
        if status == "new":
            badge_html = '<span class="badge-new">NEW</span>'
        elif status.startswith("streak"):
            badge_html = f'<span class="badge-streak">连续{status[6:]}天</span>'
        else:
            badge_html = '<span class="badge-recurring">常客</span>'

        # 提取今日 star 数用于计数动画
        star_match = re.search(r'[\d,]+', repo["today_stars"])
        star_count = star_match.group().replace(",", "") if star_match else "0"

        cards_html.append(f"""
<div class="card" id="card-{i}">
  <div class="card-header">
    <span class="rank {rank_class(i)}">#{i}</span>
    <span class="repo-name">
      <a href="{repo_url}" target="_blank">{repo_name}</a>
      {badge_html}
    </span>
    <div class="meta">
      <span><span class="lang-dot" style="background:{lang_color(repo['language'])}"></span> {repo_lang}</span>
      <span class="today-stars-count" data-count="{star_count}">⭐ {repo_stars}</span>
    </div>
  </div>
  <div class="desc">{repo_desc}</div>
  {format_analysis_html(analysis)}
</div>""")

        short_name = escape(repo["full_name"])
        toc_dot = '<span class="toc-dot-new"></span>' if status == "new" else ""
        toc_items.append(f'      <a href="#card-{i}"><span class="toc-rank">#{i}</span>{short_name}{toc_dot}</a>')

    generate_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub Trending 日报 — {date_str}</title>
{HTML_CSS}
</head>
<body>
<nav id="sidebar">
  <div class="toc-title">📋 今日目录</div>
{chr(10).join(toc_items)}
</nav>
<div class="main-content">
<div class="container">
  <div class="header">
    <h1>🔥 GitHub Trending 日报</h1>
    <div class="date">{date_str}</div>
    <div class="summary">
      本日报由 AI 自动生成，每日精选 GitHub Trending Top 10 项目进行深度解读，
      涵盖项目简介、适合人群、PM推荐评估、思维发散与犀利锐评，助你快速把握技术脉搏。
    </div>
  </div>
  <div class="trend-summary">
    <h2>🌊 今日技术风向</h2>
    <p>{_inline_md_to_html(escape(trend_summary))}</p>
  </div>
  {''.join(cards_html)}
  <div class="footer">
    数据来源：<a href="https://github.com/trending" target="_blank">GitHub Trending</a> ·
    分析引擎：DeepSeek AI · 生成时间：{generate_time}
  </div>
</div>
</div>
<script>
(function() {{
  const links = document.querySelectorAll('#sidebar a');
  const cards = document.querySelectorAll('.card[id]');

  // 1. 卡片入场 + 评分条动画 + 侧边栏高亮
  const sectionObserver = new IntersectionObserver(function(entries) {{
    entries.forEach(function(entry) {{
      if (entry.isIntersecting) {{
        // 卡片入场
        entry.target.classList.add('visible');

        // 侧边栏高亮
        links.forEach(function(a) {{
          a.classList.toggle('active', a.getAttribute('href') === '#' + entry.target.id);
        }});

        // 评分条动画
        var bars = entry.target.querySelectorAll('.score-bar-fill');
        bars.forEach(function(bar) {{
          bar.style.width = bar.getAttribute('data-width');
        }});
      }}
    }});
  }}, {{ rootMargin: '-10% 0px -15% 0px', threshold: 0.1 }});

  cards.forEach(function(c) {{ sectionObserver.observe(c); }});

  // 2. Star 计数动画
  document.querySelectorAll('.today-stars-count').forEach(function(el) {{
    var target = parseInt(el.getAttribute('data-count')) || 0;
    var duration = 1000;
    var start = performance.now();
    function update(now) {{
      var elapsed = now - start;
      var progress = Math.min(elapsed / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(target * eased).toLocaleString();
      if (progress < 1) requestAnimationFrame(update);
    }}
    requestAnimationFrame(update);
  }});
}})();
</script>
</body>
</html>"""
    return html


PUBLIC_BASE_URL = "https://vicisss.github.io/github-trending-daily"


def publish_to_github_pages(output_dir: Path, today: str) -> str:
    """将 HTML 推送到 GitHub Pages，返回公开访问 URL"""
    public_url = f"{PUBLIC_BASE_URL}/{today}.html"
    try:
        subprocess.run(
            ["git", "-C", str(output_dir), "pull", "--rebase", "origin", "main"],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except subprocess.CalledProcessError:
        log.warning("git pull 失败，尝试继续推送")
    try:
        subprocess.run(
            ["git", "-C", str(output_dir), "add", f"{today}.html"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(output_dir), "commit", "-m", f"add: {today} 日报"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(output_dir), "push", "origin", "main"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        log.info(f"已发布到 GitHub Pages: {public_url}")
        return public_url
    except subprocess.CalledProcessError as e:
        log.error(f"GitHub Pages 发布失败: {e.stderr}")
        return ""


def send_feishu_notification(webhook_url: str, public_url: str, date_str: str, count: int):
    """向飞书 Webhook 发送日报已生成的通知"""
    if not webhook_url:
        log.warning("未配置飞书 Webhook URL，跳过通知")
        return

    url_md = f"[📖 打开日报]({public_url})" if public_url else "日报已保存至本地"
    message = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "📊 GitHub Trending 日报已生成"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**日期：{date_str}**\n已分析 {count} 个热门项目\n\n{url_md}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": "🤖 由 AI 自动生成 · DeepSeek 驱动"}]
                }
            ]
        }
    }

    try:
        resp = httpx.post(webhook_url, json=message, timeout=15)
        resp.raise_for_status()
        log.info("飞书通知发送成功")
    except Exception as e:
        log.error(f"飞书通知发送失败: {e}")


def open_in_browser(html_path: str):
    """在默认浏览器中打开 HTML 文件"""
    try:
        subprocess.run(["open", html_path], check=True)
        log.info(f"已在浏览器中打开: {html_path}")
    except Exception as e:
        log.error(f"无法打开浏览器: {e}")


def get_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        log.error("未设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


def main():
    log.info("=== GitHub Trending 日报生成开始 ===")

    # 1. 抓取
    repos = fetch_trending()
    if not repos:
        log.error("未获取到任何项目，退出")
        sys.exit(1)

    # 2. 历史追踪
    history = load_history()
    update_history(history, repos)

    # 3. 分析
    client = get_client()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    results = analyze_all(client, repos, model)

    # 4. 技术风向总结
    trend_summary = generate_trend_summary(client, results, model)

    # 5. 生成 HTML
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path(__file__).resolve().parent
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / f"{today}.html"
        html_content = generate_html(results, today, history, trend_summary)
        html_path.write_text(html_content, encoding="utf-8")
        log.info(f"日报已保存: {html_path}")
        save_history(history)
        log.info("历史数据已更新")
    except OSError as e:
        log.error(f"无法写入日报文件: {e}")
        sys.exit(1)

    # 6. 发布到 GitHub Pages
    public_url = publish_to_github_pages(output_dir, today)

    # 7. 飞书通知
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    send_feishu_notification(webhook_url, public_url, today, len(results))

    # 8. 打开浏览器
    should_open = os.getenv("OPEN_BROWSER", "true").lower() == "true"
    if should_open:
        open_in_browser(str(html_path))

    log.info("=== GitHub Trending 日报生成完成 ===")


if __name__ == "__main__":
    main()
