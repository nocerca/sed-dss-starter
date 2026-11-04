"""Детерминированный baseline поиска; это НЕ семантический RAG.

На реальном корпусе сравнить BM25/pgvector и выбрать на основе метрик поиска.
"""
import re
from dataclasses import dataclass
from app.models import KnowledgeDocument


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z0-9]{4,}", text.lower())
            if w not in {"этого", "также", "который", "письма", "документ", "необходимо"}}


@dataclass(frozen=True)
class Context:
    id: str
    source_ref: str
    kind: str
    text: str
    score: float


def retrieve(query: str, records: list[KnowledgeDocument], limit: int = 3) -> list[Context]:
    q = terms(query)
    if not q:
        return []
    ranked = []
    for record in records:
        match = len(q & terms(record.content))
        if match == 0:
            continue
        score = match / max(1, len(q))
        # Контекст ограничен по длине ради стоимости и приватности.
        ranked.append(Context(str(record.id), record.source_ref or str(record.id),
                              record.kind, record.content[:1300], score))
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]
