#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从虫虫钢琴曲谱页解析并下载 .ccmz 文件。

曲谱页源码里包含类似：

    <iframe id='ai-score' src="/sheetplayer/web.html?jianpuMode=0&url=https://.../1054183-xxxx.ccmz"></iframe>

这里只提取 url= 后面的 .ccmz 地址，然后直接下载。
"""

from __future__ import annotations

import html as html_module
import re
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_CCMZ_PATTERN = re.compile(r"url=([^\"'<>\s]+?\.ccmz)", re.IGNORECASE)
_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _decode(response):
    """按响应头 / 实际内容解码，避免中文页面乱码。"""
    encoding = response.encoding
    if not encoding or encoding.lower() in ("iso-8859-1", "ascii"):
        encoding = response.apparent_encoding or "utf-8"
    return response.content.decode(encoding, errors="ignore")


def extract_ccmz_url(page_html):
    """从页面源码中提取 .ccmz 直链，找不到返回 None。"""
    match = _CCMZ_PATTERN.search(page_html or "")
    return match.group(1) if match else None


def extract_title(page_html):
    """提取页面标题，去掉站点后缀，作为默认曲谱名。"""
    match = _TITLE_PATTERN.search(page_html or "")
    if not match:
        return ""
    title = html_module.unescape(re.sub(r"<[^>]+>", "", match.group(1)))
    title = re.split(r"[｜|]", title, maxsplit=1)[0]
    title = title.replace("虫虫钢琴", "").strip()
    return re.sub(r"\s+", " ", title)


def normalize_page_url(page_url):
    """校验并返回曲谱页网址。"""
    url = (page_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入完整的曲谱网址（以 http:// 或 https:// 开头）")
    return url


def fetch_score_page(page_url, timeout=20):
    """请求曲谱页，返回 (ccmz 直链, 页面标题)。"""
    url = normalize_page_url(page_url)
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=timeout,
    )
    response.raise_for_status()

    page_html = _decode(response)
    ccmz_url = extract_ccmz_url(page_html)
    if not ccmz_url:
        raise ValueError(
            "没有在该页面中找到 ccmz 地址。请确认是虫虫钢琴的曲谱详情页，"
            "或该曲谱暂不提供下载。"
        )
    return ccmz_url, extract_title(page_html)


def download_ccmz(ccmz_url, referer=None, timeout=60):
    """下载 ccmz 文件，返回文件内容 bytes。"""
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer

    response = requests.get(ccmz_url, headers=headers, timeout=timeout)
    response.raise_for_status()

    data = response.content
    if len(data) < 2 or data[0] not in (1, 2):
        raise ValueError("下载到的文件不是有效的 ccmz（文件头不匹配）")
    return data
