from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# --- Category ---
class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None


class CategoryOut(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    article_count: int = 0

    model_config = {"from_attributes": True}


# --- Tag ---
class TagCreate(BaseModel):
    name: str
    color: str = "#6366f1"


class TagOut(BaseModel):
    id: int
    name: str
    color: str

    model_config = {"from_attributes": True}


# --- Article ---
class ArticleCreate(BaseModel):
    title: str
    content: str = ""
    category_id: Optional[int] = None
    tag_ids: list[int] = []


class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category_id: Optional[int] = None
    tag_ids: Optional[list[int]] = None


class ArticleListItem(BaseModel):
    id: int
    title: str
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    tags: list[TagOut] = []
    created_at: datetime
    updated_at: datetime
    snippet: str = ""

    model_config = {"from_attributes": True}


class ArticleOut(BaseModel):
    id: int
    title: str
    content: str
    rendered_content: str = ""
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    tags: list[TagOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
