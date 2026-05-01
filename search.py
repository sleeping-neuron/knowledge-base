from sqlalchemy import text
from database import SessionLocal


def search_articles(query: str, limit: int = 50) -> list[int]:
    """Search articles via FTS5, returns matching article IDs ordered by rank."""
    if not query.strip():
        return []

    safe_query = " OR ".join(f'"{term}"' for term in query.strip().split())

    sql = text("""
        SELECT rowid AS id, rank
        FROM articles_fts
        WHERE articles_fts MATCH :q
        ORDER BY rank
        LIMIT :limit
    """)

    db = SessionLocal()
    try:
        result = db.execute(sql, {"q": safe_query, "limit": limit})
        return [row.id for row in result.fetchall()]
    finally:
        db.close()
