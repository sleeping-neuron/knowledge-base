from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import markdown as md
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pygments.formatters import HtmlFormatter
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from database import Base, engine, get_db
from models import Category, Article, Tag, article_tag
from schemas import (ArticleCreate, ArticleUpdate, ArticleOut, ArticleListItem,
                     CategoryCreate, CategoryOut, TagCreate, TagOut)
from search import search_articles

# --- 应用初始化 ---

BASE_DIR = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="知识库管理系统", version="1.0.0")

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
    body = md_processor.reset().convert(content)
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
    tags = db.query(Tag).order_by(Tag.name).all()

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
    tags = db.query(Tag).order_by(Tag.name).all()
    return templates.TemplateResponse("edit.html", template_ctx(
        request, article=None, categories=categories, tags=tags,
    ))


@app.get("/article/{article_id}", response_class=HTMLResponse)
def view_article(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = db.query(Article).options(joinedload(Article.tags)).filter(
        Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("view.html", template_ctx(
        request, article=article,
    ))


@app.get("/article/{article_id}/edit", response_class=HTMLResponse)
def edit_article_page(article_id: int, request: Request, db: Session = Depends(get_db)):
    article = db.query(Article).options(joinedload(Article.tags)).filter(
        Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404)
    categories = db.query(Category).order_by(Category.name).all()
    tags = db.query(Tag).order_by(Tag.name).all()
    return templates.TemplateResponse("edit.html", template_ctx(
        request, article=article, categories=categories, tags=tags,
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
    db.delete(cat)
    db.commit()
    return {"ok": True}


# --- API：标签 ---

@app.get("/api/tags", response_model=list[TagOut])
def api_tags(db: Session = Depends(get_db)):
    return db.query(Tag).order_by(Tag.name).all()


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

from pydantic import BaseModel as PydanticBaseModel


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
    tags = db.query(Tag).order_by(Tag.name).all()
    return templates.TemplateResponse("ai.html", template_ctx(
        request, categories=categories, tags=tags,
    ))


# --- API：配置 ---

from pydantic import BaseModel as ConfigModel


class ApiKeySet(ConfigModel):
    api_key: str
    model: str = "deepseek-v4-flash"


class ConfigSet(ConfigModel):
    language: str | None = None
    theme: str | None = None


@app.post("/api/config/apikey")
def api_set_apikey(data: ApiKeySet):
    import ai_service
    ai_service.set_api_key(data.api_key)
    cfg = ai_service.load_config()
    cfg["model"] = data.model
    ai_service.save_config(cfg)
    return {"ok": True}


@app.get("/api/config")
def api_get_config():
    import ai_service
    cfg = ai_service.load_config()
    return {
        "has_api_key": bool(ai_service.get_api_key()),
        "model": cfg.get("model", "deepseek-v4-flash"),
        "language": ai_service.get_language(),
        "theme": ai_service.get_theme(),
    }


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


# --- 启动入口 ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
