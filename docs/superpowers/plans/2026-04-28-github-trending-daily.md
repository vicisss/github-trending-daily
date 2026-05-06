# GitHub Trending 日报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天自动抓取 GitHub Trending Top 10，通过 DeepSeek API 深度分析后生成精美 HTML 日报，飞书提醒

**Architecture:** 单一 Python 脚本，由 macOS launchd 每天定时触发。httpx + BeautifulSoup 抓取 Trending 页面，DeepSeek API 逐项目分析，内嵌 CSS 生成自包含 HTML

**Tech Stack:** Python 3, httpx, beautifulsoup4, openai SDK, python-dotenv

---

## 文件结构

```
工具/
├── daily_github_trending.py          # 主脚本（~350行）
├── .env                               # API key + webhook（不入 git）
├── .env.example                       # 配置模板
├── requirements.txt                   # Python 依赖
├── .gitignore
└── com.github-trending.daily.plist   # launchd 配置
```

---

### Task 1: 项目骨架搭建

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: 创建 requirements.txt**

```bash
cat > requirements.txt << 'DEPS'
httpx>=0.27.0
beautifulsoup4>=4.12.0
openai>=1.30.0
python-dotenv>=1.0.0
DEPS
```

- [ ] **Step 2: 安装依赖**

```bash
pip install -r requirements.txt
```

- [ ] **Step 3: 创建 .env.example 模板**

```
DEEPSEEK_API_KEY=sk-your-key-here
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxx
```

- [ ] **Step 4: 创建 .gitignore**

```
.env
__pycache__/
*.pyc
日报/
error.log
```

- [ ] **Step 5: 创建实际 .env 并填入 API key**

```bash
cat > .env << 'EOF'
DEEPSEEK_API_KEY=sk-b8cca2f448d74a33914f17257f73a749
FEISHU_WEBHOOK_URL=
EOF
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore
git commit -m "chore: project skeleton with dependencies and config template"
```

---

### Task 2: GitHub Trending 抓取模块

**Files:**
- Create: `daily_github_trending.py`

- [ ] **Step 1: 编写 fetch_trending 函数**

```python
import httpx
from bs4 import BeautifulSoup
import re
import json
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


def fetch_trending() -> list[dict]:
    """抓取 GitHub Trending 页面，返回 Top 10 项目列表"""
    log.info("正在抓取 GitHub Trending...")
    resp = httpx.get(TRENDING_URL, headers=HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
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
        description = desc_p.text.strip() if desc_p else ""

        # 语言
        lang_el = article.find("span", itemprop="programmingLanguage")
        language = lang_el.text.strip() if lang_el else "Unknown"

        # 今日 star 数
        star_el = article.select_one(".d-inline-block.float-sm-right")
        today_stars = star_el.text.strip() if star_el else "N/A"
        # 也尝试其他选择器
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
```

- [ ] **Step 2: 验证抓取模块（手动测试）**

```bash
python3 -c "
from daily_github_trending import fetch_trending
import json
repos = fetch_trending()
print(f'获取到 {len(repos)} 个项目')
for r in repos[:3]:
    print(f'{r[\"full_name\"]} | {r[\"language\"]} | {r[\"today_stars\"]}')
"
```

- [ ] **Step 3: Commit**

```bash
git add daily_github_trending.py
git commit -m "feat: add GitHub Trending scraper module"
```

---

### Task 3: DeepSeek API 分析模块

**Files:**
- Modify: `daily_github_trending.py` — 追加分析函数

- [ ] **Step 1: 编写 analyze_repo 函数**

```python
SYSTEM_PROMPT = """你是一位资深科技分析师兼创业者，每天为读者撰写 GitHub 热门项目深度解读。
你的分析风格：专业但不枯燥，有深度但不啰嗦，锐评时犀利毒舌但言之有理。
请严格按以下格式输出，每个部分用 `###` 标题分隔：

### 项目简介
简要说明这个项目是什么、解决什么核心问题（2-3句话）

### 适合人群
列出这个项目最适合哪些人/角色/场景使用（用 `-` 列表，不超过 5 条）

### 思维发散
从创新角度出发，这个项目或技术可以如何延伸应用？有没有变现/商业化的可能？可以和哪些其他技术组合产生化学反应？（3-5 条具体思路，每条用 `-` 开头）

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
    """调用 DeepSeek API 深度分析单个项目，返回分析结果（失败返回 None）"""
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
        log.info(f"分析完成: {repo['full_name']} (tokens: {resp.usage.total_tokens})")
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
```

- [ ] **Step 2: 验证分析模块**

```bash
python3 -c "
from daily_github_trending import fetch_trending, get_client, analyze_repo
repos = fetch_trending()
client = get_client()
result = analyze_repo(client, repos[0], 'deepseek-chat')
print(result['analysis'][:500])
"
```

- [ ] **Step 3: Commit**

```bash
git add daily_github_trending.py
git commit -m "feat: add DeepSeek API analysis module"
```

---

### Task 4: HTML 日报生成模块

**Files:**
- Modify: `daily_github_trending.py` — 追加 HTML 生成函数

- [ ] **Step 1: 编写 generate_html 函数**

```python
HTML_CSS = """<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont,
      'Segoe UI', 'Noto Sans SC', 'PingFang SC', sans-serif;
    line-height: 1.7; padding: 40px 20px;
  }
  .container { max-width: 900px; margin: 0 auto; }
  .header {
    text-align: center; padding: 60px 0 40px;
    border-bottom: 1px solid #21262d; margin-bottom: 48px;
  }
  .header h1 {
    font-size: 2.2em; background: linear-gradient(135deg, #58a6ff, #bc8cff);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .date { color: #8b949e; margin-top: 8px; font-size: 1.05em; }
  .header .summary {
    margin-top: 20px; padding: 16px 24px; background: #161b22;
    border-radius: 8px; border-left: 3px solid #58a6ff;
    text-align: left; color: #8b949e; font-size: 0.95em;
  }
  .card {
    background: #161b22; border: 1px solid #21262d; border-radius: 12px;
    padding: 32px; margin-bottom: 28px;
    transition: border-color 0.2s;
  }
  .card:hover { border-color: #30363d; }
  .card-header {
    display: flex; align-items: center; gap: 14px; margin-bottom: 18px;
    flex-wrap: wrap;
  }
  .rank {
    display: inline-flex; align-items: center; justify-content: center;
    width: 36px; height: 36px; border-radius: 50%;
    font-weight: 700; font-size: 0.95em; flex-shrink: 0;
  }
  .rank-1 { background: #f0c419; color: #0d1117; }
  .rank-2 { background: #a3b4c2; color: #0d1117; }
  .rank-3 { background: #cd7f32; color: #0d1117; }
  .rank-other { background: #21262d; color: #8b949e; }
  .repo-name { font-size: 1.15em; font-weight: 600; }
  .repo-name a { color: #58a6ff; text-decoration: none; }
  .repo-name a:hover { text-decoration: underline; }
  .meta { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.85em; color: #8b949e; }
  .meta span { display: inline-flex; align-items: center; gap: 4px; }
  .lang-dot {
    display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  }
  .desc { color: #8b949e; font-size: 0.93em; margin-bottom: 20px; line-height: 1.6; }
  .section { margin-top: 20px; }
  .section h3 { font-size: 1em; margin-bottom: 10px; color: #e6edf3; }
  .section ul { padding-left: 20px; color: #8b949e; font-size: 0.93em; }
  .section ul li { margin-bottom: 6px; }
  .pros-cons { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .pros { background: #0d2b1e; border-radius: 8px; padding: 16px; }
  .cons { background: #2b0d0d; border-radius: 8px; padding: 16px; }
  .pros h4 { color: #3fb950; font-size: 0.93em; margin-bottom: 8px; }
  .cons h4 { color: #f85149; font-size: 0.93em; margin-bottom: 8px; }
  .pros ul, .cons ul { color: #c9d1d9; font-size: 0.9em; }
  .verdict { margin-top: 16px; padding: 12px 16px; background: #1a1a2e;
    border-radius: 6px; font-style: italic; color: #bc8cff; font-size: 0.93em; }
  .footer {
    text-align: center; padding: 40px 0; color: #484f58; font-size: 0.85em;
    border-top: 1px solid #21262d; margin-top: 20px;
  }
  .footer a { color: #58a6ff; }
  @media (max-width: 640px) {
    body { padding: 20px 12px; }
    .card { padding: 20px; }
    .pros-cons { grid-template-columns: 1fr; }
    .header h1 { font-size: 1.5em; }
  }
</style>"""


def format_analysis_html(analysis: str) -> str:
    """把 AI 返回的 markdown 分析转为 HTML"""
    import re

    # 分割各段
    sections = re.split(r'(?=### )', analysis.strip())

    html_parts = []
    verdict_text = ""

    for sec in sections:
        sec = sec.strip()
        if sec.startswith("### 项目简介"):
            content = sec.replace("### 项目简介", "").strip()
            html_parts.append(f'<div class="section"><h3>📖 项目简介</h3><p style="color:#8b949e;font-size:0.93em;">{content}</p></div>')

        elif sec.startswith("### 适合人群"):
            content = sec.replace("### 适合人群", "").strip()
            html_parts.append(f'<div class="section"><h3>🎯 适合人群</h3><ul>{_md_list_to_html(content)}</ul></div>')

        elif sec.startswith("### 思维发散"):
            content = sec.replace("### 思维发散", "").strip()
            html_parts.append(f'<div class="section"><h3>💡 思维发散</h3><ul>{_md_list_to_html(content)}</ul></div>')

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
            # 未识别的 section，原样显示
            html_parts.append(f'<div class="section">{sec}</div>')

    return "\n".join(html_parts)


def _md_list_to_html(text: str) -> str:
    """把 markdown 列表转为 <li> 拼接"""
    items = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("-"):
            items.append(f"<li>{stripped[1:].strip()}</li>")
        elif stripped and not stripped.startswith("#"):
            items.append(f"<li>{stripped}</li>")
    return "\n".join(items)


def _parse_critique(text: str) -> tuple[str, str, str]:
    """解析锐评中的优/缺点和总结句"""
    pros_items = []
    cons_items = []
    verdict = ""
    current = None

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("优点") or stripped.startswith("**优点"):
            current = "pros"
            continue
        elif stripped.startswith("缺点") or stripped.startswith("**缺点"):
            current = "cons"
            continue
        elif stripped.startswith("总结") or stripped.startswith("**总结"):
            current = "verdict"
            continue

        if current == "pros" and stripped.startswith("-"):
            pros_items.append(f"<li>{stripped[1:].strip()}</li>")
        elif current == "cons" and stripped.startswith("-"):
            cons_items.append(f"<li>{stripped[1:].strip()}</li>")
        elif current == "verdict" and stripped:
            verdict = stripped

    return "\n".join(pros_items), "\n".join(cons_items), verdict


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


def generate_html(results: list[dict], date_str: str) -> str:
    """生成完整的自包含 HTML 日报"""
    cards_html = []
    for i, result in enumerate(results, 1):
        repo = result["repo"]
        analysis = result.get("analysis") or "### 项目简介\n分析暂不可用\n\n### 锐评\n分析过程中出现错误"

        cards_html.append(f"""
<div class="card">
  <div class="card-header">
    <span class="rank {rank_class(i)}">#{i}</span>
    <span class="repo-name">
      <a href="{repo['url']}" target="_blank">{repo['full_name']}</a>
    </span>
    <div class="meta">
      <span><span class="lang-dot" style="background:{lang_color(repo['language'])}"></span> {repo['language']}</span>
      <span>⭐ {repo['today_stars']}</span>
    </div>
  </div>
  <div class="desc">{repo['description'] or '（无描述）'}</div>
  {format_analysis_html(analysis)}
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub Trending 日报 — {date_str}</title>
{HTML_CSS}
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🔥 GitHub Trending 日报</h1>
    <div class="date">{date_str}</div>
    <div class="summary">
      本日报由 AI 自动生成，每日精选 GitHub Trending Top 10 项目进行深度解读，
      涵盖项目简介、适合人群、创新思维发散与犀利锐评，助你快速把握技术脉搏。
    </div>
  </div>
  {''.join(cards_html)}
  <div class="footer">
    数据来源：<a href="https://github.com/trending" target="_blank">GitHub Trending</a> ·
    分析引擎：DeepSeek AI · 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</div>
</body>
</html>"""
    return html
```

- [ ] **Step 2: 验证 HTML 生成**

```bash
python3 -c "
from daily_github_trending import generate_html
# 模拟数据验证
mock = [{'repo': {'full_name':'test/repo','url':'#','description':'test','language':'Python','today_stars':'100'}, 'analysis': '### 项目简介\n这是一个测试项目\n\n### 适合人群\n- 开发者\n\n### 思维发散\n- 可做xxx\n\n### 锐评\n优点：\n- 好用\n\n缺点：\n- 文档差\n\n总结：值得关注'}]
html = generate_html(mock, '2026-04-28')
print(html[:500])
"
```

- [ ] **Step 3: Commit**

```bash
git add daily_github_trending.py
git commit -m "feat: add HTML report generator with dark theme"
```

---

### Task 5: 飞书通知 + 主流程编排

**Files:**
- Modify: `daily_github_trending.py` — 追加通知 + main 函数

- [ ] **Step 1: 编写飞书通知函数**

```python
def send_feishu_notification(webhook_url: str, html_path: str, date_str: str, count: int):
    """向飞书 Webhook 发送日报已生成的通知"""
    if not webhook_url:
        log.warning("未配置飞书 Webhook URL，跳过通知")
        return

    message = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 GitHub Trending 日报已生成"},
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**日期：{date_str}**\n已分析 {count} 个热门项目\n\n日报已保存至本地，请打开浏览器查看完整内容。"
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
```

- [ ] **Step 2: 编写 get_client 和 main 函数**

```python
def get_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        log.error("未设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    return OpenAI(api_key=api_key, base_url=base_url)


def main():
    log.info("=== GitHub Trending 日报生成开始 ===")

    # 1. 抓取
    try:
        repos = fetch_trending()
    except Exception as e:
        log.error(f"抓取失败: {e}")
        sys.exit(1)

    if not repos:
        log.error("未获取到任何项目")
        sys.exit(1)

    # 2. 分析
    client = get_client()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    results = analyze_all(client, repos, model)

    # 3. 生成 HTML
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path.home() / "GitHub日报"
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"{today}.html"

    html_content = generate_html(results, today)
    html_path.write_text(html_content, encoding="utf-8")
    log.info(f"日报已保存: {html_path}")

    # 4. 飞书通知
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    send_feishu_notification(webhook_url, str(html_path), today, len(results))

    # 5. 打开浏览器
    open_browser = os.getenv("OPEN_BROWSER", "true").lower() == "true"
    if open_browser:
        open_in_browser(str(html_path))

    log.info("=== GitHub Trending 日报生成完成 ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add daily_github_trending.py
git commit -m "feat: add Feishu notification and orchestration main"
```

---

### Task 6: launchd 定时任务配置

**Files:**
- Create: `com.github-trending.daily.plist`

- [ ] **Step 1: 编写 plist 文件**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.github-trending.daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/env</string>
        <string>python3</string>
        <string>/Users/Zhuanz/Desktop/我的电脑/D盘/claudecode/工具/daily_github_trending.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DEEPSEEK_API_KEY</key>
        <string>sk-b8cca2f448d74a33914f17257f73a749</string>
        <key>FEISHU_WEBHOOK_URL</key>
        <string>填你的飞书webhook地址</string>
        <key>DEEPSEEK_MODEL</key>
        <string>deepseek-chat</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>/Users/Zhuanz/Desktop/我的电脑/D盘/claudecode/工具</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/tmp/github-trending-daily.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/github-trending-daily.err</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 2: 安装 launchd 任务**

```bash
cp com.github-trending.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.github-trending.daily.plist
```

- [ ] **Step 3: 验证已加载**

```bash
launchctl list | grep github-trending
```
Expected: 看到 `com.github-trending.daily` 在列表中

- [ ] **Step 4: Commit**

```bash
git add com.github-trending.daily.plist
git commit -m "chore: add launchd plist for daily 9am scheduling"
```

---

### Task 7: 端到端测试

- [ ] **Step 1: 完整运行一次脚本**

```bash
cd /Users/Zhuanz/Desktop/我的电脑/D盘/claudecode/工具
python3 daily_github_trending.py
```

预期输出：
- 抓取到 10 个 Trending 项目
- 逐个调用 DeepSeek 分析（约 2-3 分钟）
- 生成 `~/GitHub日报/2026-04-28.html`
- 浏览器自动打开

- [ ] **Step 2: 检查生成的 HTML**

打开 `~/GitHub日报/2026-04-28.html`，确认：
- 深色主题渲染正常
- 10 个项目卡片完整
- 每个卡片有：简介、适合人群、思维发散、锐评
- 链接可点击

- [ ] **Step 3: 手动触发 launchd 测试（可选）**

```bash
launchctl start com.github-trending.daily
# 检查日志
tail -f /tmp/github-trending-daily.log
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test: end-to-end verification complete"
```
