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


DEFAULT_MODEL = "deepseek-v4-flash"

SCENARIO_MODELS = {
    "generate": "deepseek-v4-pro",
    "summarize": "deepseek-v4-flash",
    "polish": "deepseek-v4-flash",
    "tags": "deepseek-v4-flash",
    "related": "deepseek-v4-flash",
    "analyze": "deepseek-v4-pro",
    "plan": "deepseek-v4-pro",
    "organize": "deepseek-v4-pro",
    "expand": "deepseek-v4-pro",
}


def get_model() -> str:
    cfg = load_config()
    return cfg.get("model", DEFAULT_MODEL)


def get_model_for(scenario: str) -> str:
    """获取指定场景的模型，支持全局默认覆盖"""
    cfg = load_config()
    models = cfg.get("models", {})
    if scenario in models:
        return models[scenario]
    return cfg.get("model", DEFAULT_MODEL)


def get_all_models() -> dict:
    """返回所有场景的模型配置"""
    cfg = load_config()
    result = {}
    for scenario, default in SCENARIO_MODELS.items():
        result[scenario] = cfg.get("models", {}).get(scenario, default)
    return result


def set_models(models: dict):
    """保存场景模型配置"""
    cfg = load_config()
    if "models" not in cfg:
        cfg["models"] = {}
    cfg["models"].update(models)
    save_config(cfg)


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


async def _call_deepseek(messages: list[dict], max_tokens: int = 0, timeout: int = 120,
                        scenario: str = "", continue_on_truncation: bool = False) -> dict:
    """统一封装 DeepSeek API 调用。

    - max_tokens=0 表示不设置限制，由模型自行决定
    - continue_on_truncation=True 时，若 finish_reason="length"，自动续写直到完整
    """
    api_key = get_api_key()
    if not api_key:
        return {"error": "请先配置 API Key"}

    model = get_model_for(scenario) if scenario else get_model()
    current_messages = [m.copy() for m in messages]

    try:
        full_text = ""
        max_iterations = 10  # 安全上限，防止无限循环

        for iteration in range(max_iterations):
            body = {
                "model": model,
                "messages": current_messages,
            }
            if max_tokens > 0:
                body["max_tokens"] = max_tokens

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    BASE_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                data = resp.json()

            if resp.status_code != 200:
                error_msg = data.get("error", {}).get("message", "未知错误")
                return {"error": f"API 错误 ({resp.status_code}): {error_msg}"}

            choice = data["choices"][0]
            chunk = choice["message"]["content"]
            finish_reason = choice.get("finish_reason", "stop")
            full_text += chunk

            if not continue_on_truncation or finish_reason != "length":
                return {"text": full_text}

            # 被截断：让模型继续
            current_messages.append({"role": "assistant", "content": chunk})
            current_messages.append({"role": "user", "content": "请继续完成上面的内容，从截断处接着写，不要重复已写的内容。"})

        return {"text": full_text, "warning": "达到最大续写次数，内容可能仍不完整"}
    except Exception as e:
        if full_text:
            return {"text": full_text, "warning": f"续写中断: {str(e)}"}
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
        max_tokens=0,
        timeout=300,
        scenario="generate",
        continue_on_truncation=True,
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
        scenario="tags",
    )

    if "error" in result:
        return []

    text = result["text"].strip()
    return [t.strip().strip("#") for t in text.split(",") if t.strip()][:6]


async def summarize_article(content: str, lang: str = "zh") -> str:
    """对文章内容生成简短摘要"""
    lang_instruction = "用中文输出摘要。" if lang == "zh" else "Output summary in English."
    result = await _call_deepseek(
        messages=[{
            "role": "user",
            "content": (
                f"Write a concise summary (3-5 sentences) of the following article.\n"
                f"Focus on the key points and main takeaways.\n"
                f"{lang_instruction}\n"
                f"Output only the summary, nothing else.\n\n{content[:6000]}"
            ),
        }],
        max_tokens=500,
        timeout=60,
        scenario="summarize",
    )
    if "error" in result:
        return ""
    return result["text"].strip()


async def polish_content(content: str, lang: str = "zh") -> str:
    """润色 Markdown 内容，改善表达和结构"""
    lang_instruction = "用中文输出。" if lang == "zh" else "Output in English."
    result = await _call_deepseek(
        messages=[{
            "role": "user",
            "content": (
                f"Polish and improve the following Markdown article.\n"
                f"- Fix grammar and awkward phrasing\n"
                f"- Improve clarity and readability\n"
                f"- Keep all original headings, code blocks, and formatting\n"
                f"- Do NOT add new sections or content, only improve existing writing\n"
                f"{lang_instruction}\n"
                f"Output the polished Markdown directly, nothing else.\n\n{content[:8000]}"
            ),
        }],
        max_tokens=0,
        timeout=180,
        scenario="polish",
        continue_on_truncation=True,
    )
    if "error" in result:
        return ""
    return result["text"].strip()


async def suggest_related_topics(title: str, content: str, lang: str = "zh") -> list[str]:
    """根据当前笔记推荐相关话题"""
    lang_instruction = "用中文输出话题。" if lang == "zh" else "Output topics in English."
    result = await _call_deepseek(
        messages=[{
            "role": "user",
            "content": (
                f"Based on this article titled \"{title}\", suggest 5 related topics for further study.\n"
                f"Each topic should be a specific, actionable subject that extends or complements the material.\n"
                f"{lang_instruction}\n"
                f"Output one topic per line, no numbers or bullets.\n\n{content[:3000]}"
            ),
        }],
        max_tokens=400,
        timeout=30,
        scenario="related",
    )
    if "error" in result:
        return []
    lines = [l.strip().lstrip("-#0123456789. ").strip() for l in result["text"].strip().split("\n")]
    return [l for l in lines if l and len(l) > 2][:5]


async def analyze_category_gaps(category_name: str, articles: list[dict], lang: str = "zh") -> dict:
    """分析某个分类下的知识缺口 - 识别缺失的重要子主题"""
    import json as _json
    lang_instruction = "用中文输出。" if lang == "zh" else "Output in English."

    articles_summary = _json.dumps(articles, ensure_ascii=False, indent=2)

    prompt = (
        f"Analyze the knowledge coverage for the category \"{category_name}\".\n\n"
        f"Existing articles in this category:\n{articles_summary}\n\n"
        f"Task:\n"
        f"1. Identify important sub-topics that are MISSING from this category\n"
        f"2. Rate each gap by importance (high/medium/low)\n"
        f"3. For each gap, explain briefly why it's important to cover\n"
        f"4. Give a one-paragraph overall assessment of the category's knowledge completeness\n\n"
        f"{lang_instruction}\n\n"
        f"Respond in strict JSON format:\n"
        f'{{"overall_assessment": "...", "gaps": [{{"topic": "...", "importance": "high/medium/low", '
        f'"reason": "..."}}]}}\n\n'
        f"Output only the JSON, nothing else."
    )

    result = await _call_deepseek(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        timeout=180,
        scenario="analyze",
    )

    if "error" in result:
        return result

    try:
        text = result["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            elif text.endswith("\n```"):
                text = text[:-4]
        return _json.loads(text)
    except (ValueError, _json.JSONDecodeError):
        return {"error": "解析 AI 返回结果失败", "raw": result["text"]}


async def generate_knowledge_plan(category_name: str, articles: list[dict],
                                  gaps: list[dict], lang: str = "zh") -> dict:
    """基于知识缺口生成知识体系完善计划 - 规划 3~8 篇待写笔记"""
    import json as _json
    lang_instruction = "用中文输出。" if lang == "zh" else "Output in English."

    articles_summary = _json.dumps(articles, ensure_ascii=False, indent=2)
    gaps_summary = _json.dumps(gaps, ensure_ascii=False, indent=2)

    prompt = (
        f"Create a knowledge completion plan for the category \"{category_name}\".\n\n"
        f"Existing articles:\n{articles_summary}\n\n"
        f"Identified knowledge gaps:\n{gaps_summary}\n\n"
        f"Task: Plan 3-8 new articles to fill the most important gaps and complete the knowledge system.\n"
        f"{lang_instruction}\n\n"
        f"Respond in strict JSON format:\n"
        f'{{"plan_title": "...", "articles": ['
        f'{{"title": "...", "description": "...", "key_points": ["...", "..."]}}'
        f']}}\n\n'
        f"Output only the JSON, nothing else."
    )

    result = await _call_deepseek(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=0,
        timeout=180,
        scenario="plan",
    )

    if "error" in result:
        return result

    try:
        text = result["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            elif text.endswith("\n```"):
                text = text[:-4]
        return _json.loads(text)
    except (ValueError, _json.JSONDecodeError):
        return {"error": "解析 AI 返回结果失败", "raw": result["text"]}


async def organize_notes(notes: list[dict], instruction: str = "", lang: str = "zh") -> dict:
    """整理多篇笔记 — 交叉比对、去重、合并、补充关联"""
    import json as _json
    lang_instruction = "用中文输出。" if lang == "zh" else "Output in English."

    notes_json = _json.dumps(notes, ensure_ascii=False, indent=2)
    instruction_text = f"\nAdditional user instruction: {instruction}" if instruction else ""

    prompt = (
        f"Organize and reconcile the following knowledge base notes.{instruction_text}\n\n"
        f"Notes:\n{notes_json}\n\n"
        f"Task:\n"
        f"1. Identify overlapping or duplicate content across notes\n"
        f"2. Suggest merges where multiple notes cover the same topic\n"
        f"3. Identify notes that should be updated (outdated, incomplete, or inconsistent)\n"
        f"4. Suggest a better organization structure (category, tags)\n"
        f"5. Write a brief analysis summary\n\n"
        f"{lang_instruction}\n\n"
        f"Respond in strict JSON format:\n"
        f'{{"analysis": "...", "actions": ['
        f'{{"action": "keep|merge|update|delete", "note_ids": [1, 2], '
        f'"title": "...", "reason": "...", "suggested_content": "..."}}'
        f']}}\n\n'
        f"Output only the JSON, nothing else."
    )

    result = await _call_deepseek(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=0,
        timeout=300,
        scenario="organize",
        continue_on_truncation=True,
    )

    if "error" in result:
        return result

    try:
        text = result["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            elif text.endswith("\n```"):
                text = text[:-4]
        return _json.loads(text)
    except (ValueError, _json.JSONDecodeError):
        return {"error": "解析 AI 返回结果失败", "raw": result["text"]}


async def expand_topic(topic: str, count: int = 5, lang: str = "zh") -> dict:
    """将一个大主题拆解为多个子主题，用于批量生成"""
    import json as _json
    lang_instruction = "用中文输出。" if lang == "zh" else "Output in English."

    prompt = (
        f"Break down the topic \"{topic}\" into {count} specific sub-topics for creating "
        f"a comprehensive knowledge base article series.\n\n"
        f"{lang_instruction}\n"
        f"Each sub-topic should be a focused, self-contained subject that together "
        f"form a complete understanding of the main topic.\n\n"
        f"Respond in strict JSON format:\n"
        f'{{"main_topic": "...", "sub_topics": ['
        f'{{"title": "...", "description": "...", "category": "..."}}'
        f']}}\n\n'
        f"Output only the JSON, nothing else."
    )

    result = await _call_deepseek(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
        timeout=90,
        scenario="expand",
    )

    if "error" in result:
        return result

    try:
        text = result["text"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            elif text.endswith("\n```"):
                text = text[:-4]
        return _json.loads(text)
    except (ValueError, _json.JSONDecodeError):
        return {"error": "解析 AI 返回结果失败", "raw": result["text"]}
