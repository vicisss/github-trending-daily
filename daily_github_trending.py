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
