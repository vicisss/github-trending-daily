from html import unescape

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
