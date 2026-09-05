import pytest

from app.rag.documents import Document, compute_content_hash
from app.rag.parsers import (
    DocumentParseError,
    parse_document,
    parse_html,
    parse_markdown,
)


def test_parse_markdown_extracts_title_and_clean_content() -> None:
    document = parse_markdown(
        "# RAG 入门\n\nRAG 是**检索增强生成**，参考[官网](https://example.com)。",
        source="docs/rag-intro.md",
    )

    assert isinstance(document, Document)
    assert document.source == "docs/rag-intro.md"
    assert document.metadata["format"] == "markdown"
    assert document.metadata["title"] == "RAG 入门"
    assert "RAG 入门" in document.content
    assert "检索增强生成" in document.content
    assert "**" not in document.content
    assert "[官网](https://example.com)" not in document.content
    assert document.content_hash == compute_content_hash(document.content)


def test_parse_markdown_keeps_code_block_content() -> None:
    document = parse_markdown(
        "# 示例\n\n```python\nprint('hi')\n```",
        source="docs/code.md",
    )

    assert "print('hi')" in document.content
    assert "```" not in document.content


def test_parse_html_extracts_title_lang_and_skips_script_style() -> None:
    raw_html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
      <head><title>HTML 标题</title></head>
      <body>
        <h1>Hello</h1>
        <p>RAG 是 <b>检索增强生成</b>。</p>
        <script>var secret = "should not appear";</script>
        <style>.a { color: red; }</style>
      </body>
    </html>
    """
    document = parse_html(raw_html, source="https://example.com/page")

    assert document.source == "https://example.com/page"
    assert document.metadata["format"] == "html"
    assert document.metadata["title"] == "HTML 标题"
    assert document.metadata["lang"] == "zh-CN"
    assert "Hello" in document.content
    assert "检索增强生成" in document.content
    assert "should not appear" not in document.content
    assert ".a" not in document.content


def test_parse_html_uses_first_h1_as_title_when_title_missing() -> None:
    raw_html = '<html lang="en"><body><h1>Fallback Title</h1><p>body</p></body></html>'
    document = parse_html(raw_html, source="s")

    assert document.metadata["title"] == "Fallback Title"
    assert document.metadata["lang"] == "en"


def test_parse_document_dispatches_by_format() -> None:
    markdown_doc = parse_document(
        "# 标题\n\n正文",
        source="s",
        format_name="markdown",
    )
    html_doc = parse_document(
        "<html><body><p>正文</p></body></html>",
        source="s",
        format_name="html",
    )

    assert markdown_doc.metadata["format"] == "markdown"
    assert html_doc.metadata["format"] == "html"


def test_metadata_is_merged_and_external_dict_is_copied() -> None:
    metadata = {"crawled_at": "2025-01-01", "title": "外部标题"}
    document = parse_markdown(
        "# 内部标题\n\n正文",
        source="s",
        metadata=metadata,
    )

    metadata["crawled_at"] = "已修改"
    assert document.metadata["crawled_at"] == "2025-01-01"
    # 调用方提供的 title 优先于从正文提取的 title
    assert document.metadata["title"] == "外部标题"
    with pytest.raises(TypeError):
        document.metadata["new_key"] = "x"  # type: ignore[index]


def test_parse_document_id_is_deterministic() -> None:
    first = parse_markdown("# 标题\n\n正文", source="s")
    second = parse_markdown("# 标题\n\n正文", source="s")
    changed = parse_markdown("# 标题\n\n正文改了", source="s")

    assert first.id == second.id
    assert first.id != changed.id


def test_parse_document_accepts_explicit_id() -> None:
    document = parse_markdown("# 标题\n\n正文", source="s", document_id="my-id")

    assert document.id == "my-id"


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: parse_markdown("   ", source="s"),
            "raw_text",
        ),
        (
            lambda: parse_html("<html><body>   </body></html>", source="s"),
            "empty after parsing",
        ),
        (
            lambda: parse_markdown("# 标题\n\n正文", source=""),
            "source",
        ),
        (
            lambda: parse_document("# 标题\n\n正文", source="s", format_name="pdf"),  # type: ignore[arg-type]
            "unsupported format",
        ),
    ],
)
def test_parser_fails_closed_on_bad_input(factory, match) -> None:
    with pytest.raises(DocumentParseError, match=match):
        factory()
