from datetime import datetime, timezone
from sqlalchemy import (Column, Integer, String, Text, DateTime, ForeignKey,
                        Table, event, text, select, func)
from sqlalchemy.orm import relationship, column_property
from database import Base, engine


article_tag = Table(
    "article_tag", Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    parent_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)

    parent = relationship("Category", remote_side=[id], backref="children")
    articles = relationship("Article", back_populates="category", cascade="all, delete-orphan")

    @property
    def article_count(self):
        return len(self.articles)


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(300), nullable=False)
    content = Column(Text, default="")
    rendered_content = Column(Text, default="")
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    category = relationship("Category", back_populates="articles")
    tags = relationship("Tag", secondary=article_tag, back_populates="articles",
                        lazy="joined")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)
    color = Column(String(7), default="#6366f1")

    articles = relationship("Article", secondary=article_tag, back_populates="tags")

    article_count = column_property(
        select(func.count(article_tag.c.article_id))
        .where(article_tag.c.tag_id == id)
        .correlate_except(article_tag)
        .scalar_subquery()
    )


# --- FTS5 full-text search virtual table (created via raw SQL) ---

FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, content, content='articles', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
    INSERT INTO articles_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""


def init_fts5(*args, **kwargs):
    """Create FTS5 virtual table and triggers for full-text search."""
    with engine.connect() as conn:
        # executescript is needed for multi-statement DDL (FTS5 + triggers)
        raw = conn.connection
        raw.executescript(FTS5_DDL)
        conn.commit()


# Initialize FTS5 after all tables are created
event.listen(Base.metadata, "after_create", init_fts5)
