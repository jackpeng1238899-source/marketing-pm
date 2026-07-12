#!/usr/bin/env python3
"""抓取国内外医药行业新闻,生成简报并通过 Gmail 发送邮件。

新闻来源:
    - 国内:Google News RSS 搜索(医药/创新药/集采等关键词)
    - 国际:FiercePharma、STAT News 的 RSS,以及 Google News 英文搜索兜底

摘要直接使用新闻源自带的简介文字(不调用额外的 AI 接口)。RSS 用标准库
xml.etree 手动解析,不依赖 feedparser(它的 sgmllib3k 依赖在现代
setuptools 下编译不过)。

所需环境变量:
    GMAIL_ADDRESS        发件 Gmail 地址
    GMAIL_APP_PASSWORD   Gmail 应用专用密码(不是登录密码)
    EMAIL_RECIPIENT       收件邮箱地址

可选环境变量:
    HOURS_LOOKBACK           只保留最近多少小时内发布的新闻,默认 26
    MAX_ITEMS_PER_SECTION    国内/国际各最多保留多少条,默认 15
"""

import html
import os
import re
import smtplib
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

DOMESTIC_QUERY = "医药 OR 创新药 OR 集采 OR 药监局 OR 医保谈判 OR 生物医药"
INTL_QUERY = 'pharma OR biotech OR "drug approval" OR "clinical trial"'

DOMESTIC_FEEDS = [
    f"https://news.google.com/rss/search?q={DOMESTIC_QUERY}&hl=zh-CN&gl=CN&ceid=CN:zh",
]
INTL_FEEDS = [
    "https://www.fiercepharma.com/rss.xml",
    "https://www.statnews.com/feed/",
    f"https://news.google.com/rss/search?q={INTL_QUERY}&hl=en-US&gl=US&ceid=US:en",
]

TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    summary: str
    published: Optional[datetime]


def clean_html(text: Optional[str]) -> str:
    if not text:
        return ""
    text = TAG_RE.sub("", text)
    return html.unescape(text).strip()


def is_redundant_summary(title: str, summary: str) -> bool:
    """Google News 的 description 往往就是"标题+来源名"拼接,不算真正的摘要。

    标题常见形式是"核心标题 - 来源名",而 description 里是"核心标题<来源名>"
    (中间用空白分隔,没有连字符),所以只拿标题里连字符/竖线前的"核心"部分
    去匹配 description 开头,避免连字符导致误判。
    """
    normalize = lambda s: re.sub(r"\s+", "", s).strip()
    core_title = re.split(r"[-|｜–—]", normalize(title))[0]
    if not core_title:
        return False
    return normalize(summary).startswith(core_title[:15])


def parse_pubdate(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_feed(url: str) -> list[NewsItem]:
    """解析标准 RSS 2.0 feed(Google News / FiercePharma / STAT News 都是这个格式)。"""
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; NewsDigestBot/1.0)"})
    resp.raise_for_status()
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    items = []
    for item_el in root.iter("item"):
        title = clean_html(item_el.findtext("title"))
        if not title:
            continue
        link = (item_el.findtext("link") or "").strip()
        description = clean_html(item_el.findtext("description"))
        if description and is_redundant_summary(title, description):
            description = ""

        source_el = item_el.find("source")
        source = clean_html(source_el.text) if source_el is not None and source_el.text else ""
        if not source:
            creator = item_el.findtext("{http://purl.org/dc/elements/1.1/}creator")
            source = clean_html(creator)

        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source,
                summary=description,
                published=parse_pubdate(item_el.findtext("pubDate")),
            )
        )
    return items


def dedup_and_filter(items: list[NewsItem], hours_lookback: int, max_items: int) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
    seen_titles: set[str] = set()
    filtered: list[NewsItem] = []
    for item in items:
        key = item.title.strip().lower()
        if key in seen_titles:
            continue
        if item.published is not None and item.published < cutoff:
            continue
        seen_titles.add(key)
        filtered.append(item)
    filtered.sort(key=lambda x: x.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return filtered[:max_items]


def render_section_html(title: str, items: list[NewsItem]) -> str:
    if not items:
        return f"<h2>{title}</h2><p>今天没有抓到相关新闻。</p>"
    rows = []
    for item in items:
        summary = item.summary if item.summary and item.summary != item.title else ""
        summary_html = f"<div style='color:#555;font-size:13px;margin-top:2px;'>{summary}</div>" if summary else ""
        source_html = f"<span style='color:#888;font-size:12px;'>({item.source})</span>" if item.source else ""
        rows.append(
            f"<li style='margin-bottom:10px;'>"
            f"<a href='{item.link}' style='font-weight:bold;text-decoration:none;color:#1a73e8;'>{item.title}</a> "
            f"{source_html}"
            f"{summary_html}"
            f"</li>"
        )
    return f"<h2>{title}</h2><ul style='padding-left:20px;'>{''.join(rows)}</ul>"


def render_email(domestic: list[NewsItem], intl: list[NewsItem]) -> str:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    parts = [
        f"<h1>医药行业新闻简报 - {today}</h1>",
        render_section_html("国内动态", domestic),
        render_section_html("国际动态", intl),
    ]
    return "".join(parts)


def send_email(html_body: str, sender: str, app_password: str, recipient: str) -> None:
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"医药行业新闻简报 - {today}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())


def main() -> int:
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("EMAIL_RECIPIENT")
    hours_lookback = int(os.environ.get("HOURS_LOOKBACK") or "26")
    max_items = int(os.environ.get("MAX_ITEMS_PER_SECTION") or "15")

    missing = [
        name
        for name, val in [
            ("GMAIL_ADDRESS", sender),
            ("GMAIL_APP_PASSWORD", app_password),
            ("EMAIL_RECIPIENT", recipient),
        ]
        if not val
    ]
    if missing:
        print(f"缺少必要的环境变量: {', '.join(missing)}", file=sys.stderr)
        return 1

    domestic_raw: list[NewsItem] = []
    for url in DOMESTIC_FEEDS:
        domestic_raw.extend(fetch_feed(url))
    intl_raw: list[NewsItem] = []
    for url in INTL_FEEDS:
        intl_raw.extend(fetch_feed(url))

    domestic = dedup_and_filter(domestic_raw, hours_lookback, max_items)
    intl = dedup_and_filter(intl_raw, hours_lookback, max_items)

    print(f"国内新闻 {len(domestic)} 条,国际新闻 {len(intl)} 条")

    html_body = render_email(domestic, intl)
    send_email(html_body, sender, app_password, recipient)
    print(f"邮件已发送至 {recipient}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
