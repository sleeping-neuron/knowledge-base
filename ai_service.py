"""AI 笔记服务 — 调用 DeepSeek V4 API 生成文章、摘要、标签建议"""
import json
import os
from pathlib import Path

import httpx

CONFIG_FILE = Path(__file__).parent / "config.json"
BASE_URL = "https://api.deepseek.com/chat/completions"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def get_api_key() -> str | None:
    cfg = load_config()
    return cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY")


def set_api_key(key: str):
    cfg = load_config()
    cfg["api_key"] = key
    save_config(cfg)


def get_model() -> str:
    cfg = load_config()
    return cfg.get("model", "deepseek-v4-flash")


def get_language() -> str:
    cfg = load_config()
    return cfg.get("language", "zh")


def set_language(lang: str):
    cfg = load_config()
    cfg["language"] = lang
    save_config(cfg)


def get_theme() -> str:
    cfg = load_config()
    return cfg.get("theme", "dark")


def set_theme(theme: str):
    cfg = load_config()
    cfg["theme"] = theme
    save_config(cfg)


SYSTEM_PROMPT = """You are a professional knowledge management assistant.
When writing notes, follow these rules:
1. Use Markdown format with proper headings, code blocks, tables, and lists
2. Structure content logically with clear hierarchy
3. Include practical examples when relevant
4. Write in a clear, concise, educational style
5. For Chinese content, use proper technical terms

Output ONLY the article content — no explanations, no "here is the article", just the Markdown."""


async def _call_deepseek(messages: list[dict], max_tokens: int = 4096, timeout: int = 120) -> dict:
    """统一封装 DeepSeek API 调用"""
    api_key = get_api_key()
    if not api_key:
        return {"error": "请先配置 API Key"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                BASE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": get_model(),
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
            )
            data = resp.json()

        if resp.status_code != 200:
            error_msg = data.get("error", {}).get("message", "未知错误")
            return {"error": f"API 错误 ({resp.status_code}): {error_msg}"}

        return {"text": data["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"error": f"请求失败: {str(e)}"}


async def generate_article(topic: str, lang: str = "zh") -> dict:
    """根据话题生成一篇知识笔记，返回标题 + Markdown 正文 + 分类/标签建议"""
    lang_instruction = "请用中文撰写。" if lang == "zh" else "Please write in English."
    user_prompt = (
        f"Write a comprehensive knowledge base article about:\n\n"
        f"{topic}\n\n"
        f"{lang_instruction}\n"
        f"Include 3-6 tags (comma-separated) and a suggested category name.\n"
        f"Format your response exactly like this:\n"
        f"---\n"
        f"TITLE: <article title>\n"
        f"CATEGORY: <category name>\n"
        f"TAGS: <tag1>, <tag2>, <tag3>\n"
        f"---\n"
        f"<markdown content>\n"
    )

    result = await _call_deepseek(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4096,
        timeout=120,
    )

    if "error" in result:
        return result

    text = result["text"]

    # 解析结构化响应 — 提取标题、分类、标签
    title = ""
    category = ""
    tags = []
    body = text

    if "---" in text:
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta_block = parts[1].strip()
            body = parts[2].strip()
            for line in meta_block.split("\n"):
                line = line.strip()
                if line.upper().startswith("TITLE:"):
                    title = line[6:].strip()
                elif line.upper().startswith("CATEGORY:"):
                    category = line[9:].strip()
                elif line.upper().startswith("TAGS:"):
                    tags = [t.strip() for t in line[5:].split(",") if t.strip()]

    if not title:
        first_line = body.split("\n")[0].strip().lstrip("#").strip()
        title = first_line or topic

    return {
        "title": title,
        "content": body,
        "category": category,
        "tags": tags,
    }


async def suggest_tags(content: str, lang: str = "zh") -> list[str]:
    """根据文章内容推荐标签"""
    lang_instruction = "输出中文标签。" if lang == "zh" else "Output English tags."
    prompt = (
        f"Given this article, suggest 3-6 concise tags.\n"
        f"{lang_instruction}\n"
        f"Output only comma-separated tags, nothing else.\n\n"
        f"{content[:3000]}"
    )

    result = await _call_deepseek(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        timeout=30,
    )

    if "error" in result:
        return []

    text = result["text"].strip()
    return [t.strip().strip("#") for t in text.split(",") if t.strip()][:6]
