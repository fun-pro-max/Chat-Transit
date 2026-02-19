"""
Chat Transit Backend — Optimized for Render Free Tier
Minimal Chromium footprint to stay within 512MB RAM limit.
"""

import asyncio
import json
import re
import zipfile
import io
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False


# ══════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════

class ConvertRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://(chat\.openai\.com|chatgpt\.com)/(share|c)/[a-zA-Z0-9\-]+", v):
            raise ValueError("Only public ChatGPT share links are supported.")
        return v


# ══════════════════════════════════════════════
# MODULE 1 — RENDERER (memory-optimized)
# ══════════════════════════════════════════════

async def render_page(url: str) -> str:
    """
    Launch Chromium with aggressive memory-saving flags
    to stay within Render's 512MB free tier limit.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",      # Critical for low-memory envs
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--single-process",             # Saves ~100MB RAM (risk: less stable)
                "--memory-pressure-off",
                "--js-flags=--max-old-space-size=256",  # Cap JS heap
            ],
        )

        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1024, "height": 768},  # Smaller viewport = less memory
            java_script_enabled=True,
            bypass_csp=True,
        )

        # Block heavy resources to save memory + speed up load
        await ctx.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3}",
            lambda route: route.abort()
        )
        await ctx.route("**/analytics**", lambda route: route.abort())
        await ctx.route("**/tracking**", lambda route: route.abort())
        await ctx.route("**/hotjar**", lambda route: route.abort())
        await ctx.route("**/sentry**", lambda route: route.abort())

        page = await ctx.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)

            # Wait for message content
            try:
                await page.wait_for_selector(
                    "[data-message-id], [data-message-author-role], article",
                    timeout=25_000,
                )
            except PlaywrightTimeout:
                pass  # Try parsing anyway

            await asyncio.sleep(2)
            html = await page.content()

        except PlaywrightTimeout:
            raise HTTPException(
                status_code=504,
                detail="Page timed out. The link may be private or expired.",
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Renderer error: {str(e)}")
        finally:
            await page.close()
            await ctx.close()
            await browser.close()

    return html


# ══════════════════════════════════════════════
# MODULE 2 — EXTRACTOR
# ══════════════════════════════════════════════

def extract_code_blocks(el) -> tuple[str, list[str]]:
    codes = []
    for pre in el.find_all("pre"):
        code = pre.find("code") or pre
        lang = ""
        if code.get("class"):
            for c in code.get("class", []):
                if c.startswith("language-"):
                    lang = c.replace("language-", "")
        text = code.get_text().strip()
        codes.append(text)
        pre.replace_with(f"\n```{lang}\n{text}\n```\n")

    for tag in el.find_all(["button", "svg", "nav", "header", "footer", "form"]):
        tag.decompose()

    content = el.get_text(separator="\n", strip=True)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content, codes


def extract_messages(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    messages = []

    # Strategy A: modern ChatGPT DOM
    els = soup.find_all(attrs={"data-message-author-role": True})
    if els:
        for el in els:
            role = el.get("data-message-author-role", "").lower()
            if role not in ("user", "assistant"):
                continue
            prose = el.find(class_=re.compile(r"prose|markdown")) or el
            text, codes = extract_code_blocks(prose)
            if text:
                messages.append({"role": role, "content": text, "code_blocks": codes})
        if messages:
            return messages

    # Strategy B: article elements
    articles = soup.find_all("article")
    if articles:
        for i, art in enumerate(articles):
            text, codes = extract_code_blocks(art)
            if text and len(text) > 10:
                messages.append({
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": text,
                    "code_blocks": codes,
                })
        if messages:
            return messages

    # Strategy C: .group divs
    seen = set()
    for g in soup.select(".group.w-full, .group"):
        text = g.get_text(separator="\n", strip=True)
        key = text[:60]
        if len(text) > 20 and key not in seen:
            seen.add(key)
            messages.append({"role": "user", "content": text, "code_blocks": []})

    return messages


# ══════════════════════════════════════════════
# MODULE 3 — NORMALIZATION
# ══════════════════════════════════════════════

_NOISE = re.compile(
    r"(Copy code|Copy|Regenerate|Edit message|Like|Dislike|"
    r"Report|Try again|Stop generating|ChatGPT|GPT-4|GPT-3\.5)",
    re.IGNORECASE,
)

def normalize(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        text = _NOISE.sub("", m["content"])
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if text:
            out.append({**m, "content": text})
    return out


# ══════════════════════════════════════════════
# MODULE 4 — CONTEXT BUILDER
# ══════════════════════════════════════════════

def detect_topic(messages: list[dict]) -> str:
    first = next((m for m in messages if m["role"] == "user"), None)
    if not first:
        return "General Conversation"
    return first["content"].split("\n")[0].strip()[:90]


def build_metadata(url: str, messages: list[dict], topic: str) -> dict:
    user_turns = sum(1 for m in messages if m["role"] == "user")
    code_count = sum(len(m.get("code_blocks", [])) for m in messages)
    word_count = sum(len(m["content"].split()) for m in messages)
    return {
        "source": "ChatGPT Shared Link",
        "source_url": url,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "message_count": len(messages),
        "user_turns": user_turns,
        "assistant_turns": len(messages) - user_turns,
        "code_block_count": code_count,
        "word_count": word_count,
        "transit_version": "1.0.0",
    }


def build_summary(messages: list[dict], meta: dict) -> str:
    users = [m for m in messages if m["role"] == "user"]
    opening = users[0]["content"][:300] if users else "N/A"
    closing = users[-1]["content"][:300] if len(users) > 1 else ""
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║           CHAT TRANSIT — CONTEXT SUMMARY                ║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
        f"Topic      : {meta['topic']}",
        f"Captured   : {meta['captured_at']}",
        f"Source     : {meta['source_url']}",
        f"Messages   : {meta['message_count']} "
        f"({meta['user_turns']} user / {meta['assistant_turns']} assistant)",
        f"Words      : {meta['word_count']:,}",
        f"Code blocks: {meta['code_block_count']}",
        "",
        "── OPENING QUERY ──────────────────────────────────────────",
        "",
        f"  {opening}{'...' if len(opening)==300 else ''}",
    ]
    if closing:
        lines += ["", "── FINAL QUERY ────────────────────────────────────────────",
                  "", f"  {closing}{'...' if len(closing)==300 else ''}"]
    lines += [
        "", "── HOW TO USE ─────────────────────────────────────────────",
        "",
        "  Upload transit.md or transit.json to your target LLM.",
        "  Use this file as a system prompt preamble.",
        "",
        "──────────────────────────────────────────────────────────",
        "Generated by Chat Transit v1.0  •  AI Memory Portability",
    ]
    return "\n".join(lines)


def build_markdown(messages: list[dict], meta: dict) -> str:
    lines = [
        "# Chat Transit Export", "",
        f"> **Source:** {meta['source_url']}  ",
        f"> **Topic:** {meta['topic']}  ",
        f"> **Captured:** {meta['captured_at']}",
        "", "---", "",
    ]
    labels = {"user": "👤 USER", "assistant": "🤖 ASSISTANT"}
    for i, m in enumerate(messages):
        lines += [f"## {labels.get(m['role'], m['role'].upper())}", "", m["content"], ""]
        if i < len(messages) - 1:
            lines += ["---", ""]
    return "\n".join(lines)


def build_transit_json(messages: list[dict], meta: dict) -> dict:
    return {
        "source": meta["source"],
        "source_url": meta["source_url"],
        "captured_at": meta["captured_at"],
        "topic": meta["topic"],
        "messages": messages,
    }


# ══════════════════════════════════════════════
# DEMO DATA
# ══════════════════════════════════════════════

DEMO_MESSAGES = [
    {"role": "user", "content": "How do I implement a Redis-backed rate limiter in Python for a FastAPI app?", "code_blocks": []},
    {"role": "assistant", "content": "A Redis-backed rate limiter is perfect for FastAPI.\n\nHere's a sliding window implementation:\n\n```python\nimport time, redis\nfrom fastapi import Request, HTTPException\n\nredis_client = redis.Redis(host='localhost', port=6379)\n\nasync def rate_limit(request: Request, limit=100, window=60):\n    key = f'rl:{request.client.host}'\n    now = time.time()\n    pipe = redis_client.pipeline()\n    pipe.zremrangebyscore(key, 0, now - window)\n    pipe.zadd(key, {str(now): now})\n    pipe.zcard(key)\n    pipe.expire(key, window)\n    _, _, count, _ = pipe.execute()\n    if count > limit:\n        raise HTTPException(429, 'Rate limit exceeded')\n```", "code_blocks": ["import time, redis..."]},
    {"role": "user", "content": "How do I add this as global middleware?", "code_blocks": []},
    {"role": "assistant", "content": "Wrap it in BaseHTTPMiddleware:\n\n```python\nfrom starlette.middleware.base import BaseHTTPMiddleware\n\nclass RateLimitMiddleware(BaseHTTPMiddleware):\n    async def dispatch(self, request, call_next):\n        await rate_limit(request)\n        return await call_next(request)\n\napp.add_middleware(RateLimitMiddleware)\n```", "code_blocks": ["from starlette.middleware.base import BaseHTTPMiddleware..."]},
]

def get_demo_package(url: str) -> dict:
    topic = "Redis Rate Limiter in FastAPI (Python)"
    meta = build_metadata(url, DEMO_MESSAGES, topic)
    meta["demo_mode"] = True
    tj = build_transit_json(DEMO_MESSAGES, meta)
    md = build_markdown(DEMO_MESSAGES, meta)
    sm = build_summary(DEMO_MESSAGES, meta)
    return {
        "transit_json": tj, "transit_md": md,
        "summary_txt": sm, "metadata_json": meta,
        "stats": {"messages": 4, "turns": 2, "code_blocks": 2, "words": 180, "demo": True},
    }


# ══════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════

app = FastAPI(title="Chat Transit API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"service": "Chat Transit API", "status": "ok", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "full" if PLAYWRIGHT_OK else "demo",
        "playwright": PLAYWRIGHT_OK,
        "bs4": BS4_OK,
    }


@app.post("/api/convert")
async def convert(req: ConvertRequest):
    if not PLAYWRIGHT_OK:
        return get_demo_package(req.url)

    html = await render_page(req.url)
    raw = extract_messages(html)

    if not raw:
        raise HTTPException(
            status_code=422,
            detail="No messages found. Link may be private, expired, or unsupported.",
        )

    messages = normalize(raw)
    topic = detect_topic(messages)
    meta = build_metadata(req.url, messages, topic)
    users = sum(1 for m in messages if m["role"] == "user")
    codes = sum(len(m.get("code_blocks", [])) for m in messages)

    return {
        "transit_json": build_transit_json(messages, meta),
        "transit_md": build_markdown(messages, meta),
        "summary_txt": build_summary(messages, meta),
        "metadata_json": meta,
        "stats": {
            "messages": len(messages),
            "turns": users,
            "code_blocks": codes,
            "words": meta["word_count"],
            "demo": False,
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
