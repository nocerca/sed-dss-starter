from app.models import KnowledgeDocument
from app.retrieval import retrieve


def test_only_relevant_records_returned():
    records = [
        KnowledgeDocument(id="a", filename="1.txt", kind="good", source_ref="A",
            content="Срок предоставления документов должен быть точно указан"),
        KnowledgeDocument(id="b", filename="2.txt", kind="bad", source_ref="B",
            content="Оформление таблиц и заголовков"),
    ]
    result = retrieve("Указан срок предоставления документов", records)
    assert len(result) == 1
    assert result[0].source_ref == "A"
