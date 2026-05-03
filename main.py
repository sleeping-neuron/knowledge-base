import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import markdown as md
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from pygments.formatters import HtmlFormatter
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from database import Base, engine, get_db
from models import Category, Article, Tag, article_tag
from schemas import (ArticleCreate, ArticleUpdate, ArticleOut, ArticleListItem,
                     CategoryCreate, CategoryOut, TagCreate, TagOut)
from pydantic import BaseModel as PydanticBaseModel
from search import search_articles

# --- 应用初始化 ---

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="知识库管理系统", version="1.0.0")


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    resp = await call_next(request)
    # 禁止缓存 HTML 页面，确保每次拿最新代码
    ct = resp.headers.get("content-type", "")
    if "text/html" in ct:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

Base.metadata.create_all(bind=engine)

# --- Markdown 渲染 ---

md_processor = md.Markdown(
    extensions=["fenced_code", "tables", "codehilite", "toc", "nl2br", "sane_lists"]
)
html_formatter = HtmlFormatter(style="monokai", cssclass="highlight")


def render_markdown(content: str) -> str:
    if not content:
        return ""
    # 保护 LaTeX 公式，避免 markdown 处理器破坏（如下划线变 <em>）
    math_blocks = []

    def save_math(m):
        math_blocks.append(m.group(0))
        return f"\x00MATH{len(math_blocks) - 1}\x00"

    # 保护 \[...\] 块级公式（允许跨行）
    content = re.sub(r'\\\[(.+?)\\\]', save_math, content, flags=re.DOTALL)
    # 保护 $$...$$ 块级公式（允许跨行、包含 $ 符号）
    content = re.sub(r'\$\$(.+?)\$\$', save_math, content, flags=re.DOTALL)
    # 保护 \(...\) 行内公式
    content = re.sub(r'\\\((.+?)\\\)', save_math, content)
    # 保护 $...$ 行内公式
    content = re.sub(r'\$([^$\n]+?)\$', save_math, content)

    body = md_processor.reset().convert(content)

    # 还原公式
    for i, block in enumerate(math_blocks):
        body = body.replace(f"\x00MATH{i}\x00", block)

    return f'<div class="markdown-body">{body}</div>'


# 代码高亮 CSS 在 base.html 的 <head> 中注入
PYGMENTS_CSS = html_formatter.get_style_defs(".highlight")


# --- 模板辅助 ---

def template_ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "pygments_css": PYGMENTS_CSS, **kwargs}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, category: Optional[int] = None,
          tag: Optional[int] = None, q: Optional[str] = None,
          page: int = 1, db: Session = Depends(get_db)):
    per_page = 20

    # 确定要展示的文章 ID 列表
    article_ids = None
    if q:
        article_ids = search_articles(q)

    query = db.query(Article).options(joinedload(Article.tags))
    if article_ids is not None:
        if not article_ids:
            query = query.filter(Article.id == -1)  # no results
        else:
            query = query.filter(Article.id.in_(article_ids))
    if category:
        query = query.filter(Article.category_id == category)
    if tag:
        query = query.join(article_tag).filter(article_tag.c.tag_id == tag)

    total = query.count()
    articles = (query.order_by(desc(Article.updated_at))
                .offset((page - 1) * per_page).limit(per_page).all())

    items = []
    for a in articles:
        snippet = a.content[:200].replace("\n", " ") if a.content else ""
        items.append(ArticleListItem(
            id=a.id, title=a.title, category_id=a.category_id,
            category_name=a.category.name if a.category else None,
            tags=[TagOut.model_validate(t) for t in a.tags],
            created_at=a.created_at, updated_at=a.updated_at, snippet=snippet,
        ))

    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()

    # 解析当前筛选的名称用于页面展示
    current_category_name = None
    current_tag_name = None
    if category:
        cat = db.query(Category).filter(Category.id == category).first()
        current_category_name = cat.name if cat else None
    if tag:
        t = db.query(Tag).filter(Tag.id == tag).first()
        current_tag_name = t.name if t else None

    return templates.TemplateResponse("index.html", template_ctx(
        request, articles=items, categories=categories, tags=tags,
        current_category=category, current_tag=tag, query=q,
        current_category_name=current_category_name,
        current_tag_name=current_tag_name,
        page=page, total=total, total_pages=max((total - 1) // per_page + 1, 1),
    ))


@app.get("/new", response_class=HTMLResponse)
def new_article_page(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()
    return templates.TemplateResponse("edit.html", template_ctx(
        request, article=None, categories=categories, tags=tags,
        current_category=None, current_tag=None,
    ))


@app.get("/article/{article_id}", response_class=HTMLResponse)
def view_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = db.query(Article).options(joinedload(Article.tags)).filter(
        Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()
    return templates.TemplateResponse("view.html", template_ctx(
        request, article=article, categories=categories, tags=tags,
        current_category=article.category_id, current_tag=None,
    ))


@app.get("/article/{article_id}/edit", response_class=HTMLResponse)
def edit_article_page(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = db.query(Article).options(joinedload(Article.tags)).filter(
        Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()
    return templates.TemplateResponse("edit.html", template_ctx(
        request, article=article, categories=categories, tags=tags,
        current_category=article.category_id, current_tag=None,
    ))


# --- API：文章 ---

@app.post("/api/article", response_model=ArticleOut)
def api_create_article(data: ArticleCreate, db: Session = Depends(get_db)):
    rendered = render_markdown(data.content)
    article = Article(title=data.title, content=data.content,
                      rendered_content=rendered, category_id=data.category_id)
    if data.tag_ids:
        article.tags = db.query(Tag).filter(Tag.id.in_(data.tag_ids)).all()
    db.add(article)
    db.commit()
    db.refresh(article)
    return _article_out(article)


@app.put("/api/article/{article_id}", response_model=ArticleOut)
def api_update_article(article_id: int, data: ArticleUpdate, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)

    if data.title is not None:
        article.title = data.title
    if data.content is not None:
        article.content = data.content
        article.rendered_content = render_markdown(data.content)
    if data.category_id is not None:
        article.category_id = data.category_id
    if data.tag_ids is not None:
        article.tags = db.query(Tag).filter(Tag.id.in_(data.tag_ids)).all()
    article.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(article)
    return _article_out(article)


@app.delete("/api/article/{article_id}")
def api_delete_article(article_id: int, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)
    db.delete(article)
    db.commit()
    return {"ok": True}


class BatchDeleteRequest(PydanticBaseModel):
    ids: list[int]


@app.post("/api/articles/batch-delete")
def api_batch_delete(data: BatchDeleteRequest, db: Session = Depends(get_db)):
    if not data.ids:
        return {"ok": True, "deleted": 0}
    count = db.query(Article).filter(Article.id.in_(data.ids)).delete(synchronize_session="fetch")
    db.commit()
    return {"ok": True, "deleted": count}


class BatchTagRequest(PydanticBaseModel):
    ids: list[int]
    tag_ids: list[int]


@app.post("/api/articles/batch-tag")
def api_batch_tag(data: BatchTagRequest, db: Session = Depends(get_db)):
    if not data.ids or not data.tag_ids:
        return {"ok": True, "updated": 0}
    articles = db.query(Article).filter(Article.id.in_(data.ids)).all()
    new_tags = db.query(Tag).filter(Tag.id.in_(data.tag_ids)).all()
    for a in articles:
        existing_ids = {t.id for t in a.tags}
        for t in new_tags:
            if t.id not in existing_ids:
                a.tags.append(t)
    db.commit()
    return {"ok": True, "updated": len(articles)}


class BatchCategoryRequest(PydanticBaseModel):
    ids: list[int]
    category_id: int | None


@app.post("/api/articles/batch-category")
def api_batch_category(data: BatchCategoryRequest, db: Session = Depends(get_db)):
    if not data.ids:
        return {"ok": True, "updated": 0}
    count = db.query(Article).filter(Article.id.in_(data.ids)).update(
        {"category_id": data.category_id}, synchronize_session="fetch"
    )
    db.commit()
    return {"ok": True, "updated": count}


# --- API：分类 ---

@app.get("/api/categories", response_model=list[CategoryOut])
def api_categories(db: Session = Depends(get_db)):
    return db.query(Category).order_by(Category.name).all()


@app.post("/api/category", response_model=CategoryOut)
def api_create_category(data: CategoryCreate, db: Session = Depends(get_db)):
    cat = Category(name=data.name, parent_id=data.parent_id)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@app.delete("/api/category/{category_id}")
def api_delete_category(category_id: int, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404)
    # 先将该分类下的文章取消分类，避免级联删除文章
    db.query(Article).filter(Article.category_id == category_id).update(
        {"category_id": None})
    db.delete(cat)
    db.commit()
    return {"ok": True}


# --- API：标签 ---

@app.get("/api/tags", response_model=list[TagOut])
def api_tags(db: Session = Depends(get_db)):
    return db.query(Tag).order_by(Tag.name).all()


# --- API：文章列表（轻量，供 AI 选择器等使用）---

@app.get("/api/articles")
def api_articles(q: str = "", limit: int = 50, db: Session = Depends(get_db)):
    query = db.query(Article).options(joinedload(Article.tags), joinedload(Article.category))
    if q:
        article_ids = search_articles(q)
        if not article_ids:
            return []
        query = query.filter(Article.id.in_(article_ids[:limit]))
    articles = query.order_by(desc(Article.updated_at)).limit(limit).all()
    return [{
        "id": a.id,
        "title": a.title,
        "category": a.category.name if a.category else None,
        "tags": [t.name for t in a.tags],
        "snippet": a.content[:200].replace("\n", " ") if a.content else "",
    } for a in articles]


@app.post("/api/tag", response_model=TagOut)
def api_create_tag(data: TagCreate, db: Session = Depends(get_db)):
    tag = Tag(name=data.name, color=data.color)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@app.delete("/api/tag/{tag_id}")
def api_delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404)
    db.delete(tag)
    db.commit()
    return {"ok": True}


# --- API：Markdown 预览 ---


class PreviewRequest(PydanticBaseModel):
    content: str


@app.post("/api/preview")
def api_preview(data: PreviewRequest):
    return {"html": render_markdown(data.content)}


# --- API：图片上传 ---

@app.post("/api/upload")
async def api_upload(file: UploadFile):
    import uuid
    suffix = Path(file.filename or "image.png").suffix or ".png"
    name = f"{uuid.uuid4().hex}{suffix}"
    path = UPLOADS_DIR / name
    content = await file.read()
    path.write_bytes(content)
    return {"url": f"/uploads/{name}"}


# --- AI 页面 ---

@app.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()
    return templates.TemplateResponse("ai.html", template_ctx(
        request, categories=categories, tags=tags,
        current_category=None, current_tag=None,
    ))


# --- API：配置 ---

from pydantic import BaseModel as ConfigModel


class ApiKeySet(ConfigModel):
    api_key: str
    model: str = "deepseek-v4-flash"
    models: dict | None = None


class ConfigSet(ConfigModel):
    language: str | None = None
    theme: str | None = None


class ModelsSet(ConfigModel):
    models: dict


@app.post("/api/config/apikey")
def api_set_apikey(data: ApiKeySet):
    import ai_service
    # 只有提供了非空 key 才更新，避免误清空
    if data.api_key.strip():
        ai_service.set_api_key(data.api_key.strip())
    cfg = ai_service.load_config()
    cfg["model"] = data.model
    if data.models:
        cfg["models"] = data.models
    ai_service.save_config(cfg)
    return {"ok": True}


@app.get("/api/config")
def api_get_config():
    import ai_service
    cfg = ai_service.load_config()
    return {
        "has_api_key": bool(ai_service.get_api_key()),
        "model": cfg.get("model", "deepseek-v4-flash"),
        "models": ai_service.get_all_models(),
        "language": ai_service.get_language(),
        "theme": ai_service.get_theme(),
    }


@app.post("/api/config/models")
def api_set_models(data: ModelsSet):
    import ai_service
    ai_service.set_models(data.models)
    return {"ok": True}


@app.post("/api/config")
def api_set_config(data: ConfigSet):
    import ai_service
    if data.language:
        ai_service.set_language(data.language)
    if data.theme:
        ai_service.set_theme(data.theme)
    return {"ok": True}


# --- API：AI 写作 ---

class AIGenerateRequest(ConfigModel):
    topic: str
    lang: str = "zh"


@app.post("/api/ai/generate")
async def api_ai_generate(data: AIGenerateRequest):
    import ai_service
    result = await ai_service.generate_article(data.topic, data.lang)
    return result


class AISuggestTagsRequest(ConfigModel):
    content: str
    lang: str = "zh"


@app.post("/api/ai/suggest-tags")
async def api_ai_suggest_tags(data: AISuggestTagsRequest):
    import ai_service
    tags = await ai_service.suggest_tags(data.content, data.lang)
    return {"tags": tags}


class AISummarizeRequest(ConfigModel):
    content: str
    lang: str = "zh"


@app.post("/api/ai/summarize")
async def api_ai_summarize(data: AISummarizeRequest):
    import ai_service
    summary = await ai_service.summarize_article(data.content, data.lang)
    return {"summary": summary}


class AIPolishRequest(ConfigModel):
    content: str
    lang: str = "zh"


@app.post("/api/ai/polish")
async def api_ai_polish(data: AIPolishRequest):
    import ai_service
    polished = await ai_service.polish_content(data.content, data.lang)
    return {"content": polished}


class AIRelatedRequest(ConfigModel):
    title: str
    content: str
    lang: str = "zh"


@app.post("/api/ai/related")
async def api_ai_related(data: AIRelatedRequest):
    import ai_service
    topics = await ai_service.suggest_related_topics(data.title, data.content, data.lang)
    return {"topics": topics}


# --- API：AI 知识体系完善 ---

class AIAnalyzeCategoryRequest(ConfigModel):
    category_id: int
    lang: str = "zh"


@app.post("/api/ai/analyze-category")
async def api_ai_analyze_category(data: AIAnalyzeCategoryRequest, db: Session = Depends(get_db)):
    import ai_service
    cat = db.query(Category).filter(Category.id == data.category_id).first()
    if not cat:
        return {"error": "分类不存在"}

    articles = db.query(Article).filter(Article.category_id == data.category_id).all()
    if not articles:
        return {"error": "该分类下没有笔记，无需分析"}

    articles_data = [
        {"id": a.id, "title": a.title, "snippet": a.content[:300]}
        for a in articles
    ]

    result = await ai_service.analyze_category_gaps(cat.name, articles_data, data.lang)
    return result


class AIKnowledgePlanRequest(ConfigModel):
    category_id: int
    gaps: list[dict]
    lang: str = "zh"


@app.post("/api/ai/knowledge-plan")
async def api_ai_knowledge_plan(data: AIKnowledgePlanRequest, db: Session = Depends(get_db)):
    import ai_service
    cat = db.query(Category).filter(Category.id == data.category_id).first()
    if not cat:
        return {"error": "分类不存在"}

    articles = db.query(Article).filter(Article.category_id == data.category_id).all()
    articles_data = [
        {"id": a.id, "title": a.title, "snippet": a.content[:300]}
        for a in articles
    ]

    result = await ai_service.generate_knowledge_plan(
        cat.name, articles_data, data.gaps, data.lang
    )
    return result


# --- API：AI 笔记整理 ---

class AIOrganizeRequest(ConfigModel):
    article_ids: list[int]
    instruction: str = ""
    lang: str = "zh"


@app.post("/api/ai/organize")
async def api_ai_organize(data: AIOrganizeRequest, db: Session = Depends(get_db)):
    import ai_service
    articles = db.query(Article).filter(Article.id.in_(data.article_ids)).all()
    if not articles:
        return {"error": "未找到指定笔记"}

    notes_data = [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content[:4000],
            "category": a.category.name if a.category else None,
            "tags": [t.name for t in a.tags],
        }
        for a in articles
    ]

    result = await ai_service.organize_notes(notes_data, data.instruction, data.lang)
    return result


# --- API：AI 主题拆解 ---

class AIExpandTopicRequest(ConfigModel):
    topic: str
    count: int = 5
    lang: str = "zh"


@app.post("/api/ai/expand-topic")
async def api_ai_expand_topic(data: AIExpandTopicRequest):
    import ai_service
    result = await ai_service.expand_topic(data.topic, data.count, data.lang)
    return result


# --- API：AI 批量保存 ---

class AIBatchSaveItem(ConfigModel):
    title: str
    content: str
    category: str = ""
    tags: list[str] = []


class AIBatchSaveRequest(ConfigModel):
    articles: list[AIBatchSaveItem]


@app.post("/api/ai/batch-save")
def api_ai_batch_save(data: AIBatchSaveRequest, db: Session = Depends(get_db)):
    """批量保存 AI 生成的多篇文章，自动匹配或创建分类和标签"""
    saved = []
    for item in data.articles:
        # 匹配或创建分类
        category_id = None
        if item.category:
            cat = db.query(Category).filter(
                Category.name == item.category
            ).first()
            if not cat:
                cat = Category(name=item.category)
                db.add(cat)
                db.flush()
            category_id = cat.id

        # 匹配或创建标签
        tag_objs = []
        if item.tags:
            for tag_name in item.tags:
                tag = db.query(Tag).filter(Tag.name == tag_name).first()
                if not tag:
                    colors = ["#f87171", "#fb923c", "#fbbf24", "#34d399",
                              "#38bdf8", "#818cf8", "#c084fc", "#f472b6"]
                    import random
                    tag = Tag(name=tag_name, color=random.choice(colors))
                    db.add(tag)
                    db.flush()
                tag_objs.append(tag)

        rendered = render_markdown(item.content)
        article = Article(
            title=item.title,
            content=item.content,
            rendered_content=rendered,
            category_id=category_id,
        )
        article.tags = tag_objs
        db.add(article)
        db.flush()
        saved.append({"id": article.id, "title": article.title})

    db.commit()
    return {"ok": True, "saved": saved}


# --- 辅助函数 ---

def _article_out(article: Article) -> ArticleOut:
    return ArticleOut(
        id=article.id,
        title=article.title,
        content=article.content,
        rendered_content=article.rendered_content,
        category_id=article.category_id,
        category_name=article.category.name if article.category else None,
        tags=[TagOut.model_validate(t) for t in article.tags],
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


# --- 知识图谱 ---

@app.get("/graph", response_class=HTMLResponse)
def graph_page(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(desc(Tag.article_count)).all()
    return templates.TemplateResponse("graph.html", template_ctx(
        request, categories=categories, tags=tags,
        current_category=None, current_tag=None,
    ))


@app.get("/api/graph/data")
def api_graph_data(db: Session = Depends(get_db)):
    articles = db.query(Article).options(joinedload(Article.tags)).all()
    tags = db.query(Tag).all()

    category_colors = {}
    color_palette = [
        "#818cf8", "#34d399", "#fbbf24", "#f87171", "#38bdf8",
        "#c084fc", "#fb923c", "#f472b6", "#a3e635", "#94a3b8",
    ]
    categories = db.query(Category).all()
    for i, cat in enumerate(categories):
        category_colors[cat.id] = color_palette[i % len(color_palette)]

    nodes = []
    for a in articles:
        node_color = "#6366f1"
        if a.category_id and a.category_id in category_colors:
            node_color = category_colors[a.category_id]
        nodes.append({
            "id": a.id,
            "title": a.title,
            "category_id": a.category_id,
            "category_name": a.category.name if a.category else None,
            "tag_count": len(a.tags),
            "color": node_color,
            "size": 8 + len(a.tags) * 1.5,
        })

    edges = []
    article_tag_map = {}
    for a in articles:
        article_tag_map[a.id] = {t.id for t in a.tags}

    article_ids = [a.id for a in articles]
    for i in range(len(article_ids)):
        for j in range(i + 1, len(article_ids)):
            aid, bid = article_ids[i], article_ids[j]
            shared = article_tag_map[aid] & article_tag_map[bid]
            same_cat = False
            a_obj = next((x for x in articles if x.id == aid), None)
            b_obj = next((x for x in articles if x.id == bid), None)
            if a_obj and b_obj and a_obj.category_id and b_obj.category_id:
                if a_obj.category_id == b_obj.category_id:
                    same_cat = True
            weight = len(shared) + (1 if same_cat else 0)
            if weight > 0:
                edges.append({"source": aid, "target": bid, "weight": weight})

    return {"nodes": nodes, "edges": edges}


# --- 启动入口 ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
