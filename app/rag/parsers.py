"""RAG 文本解析：把 Markdown / HTML 原始文本解析成干净正文 + 元数据。

设计目标：
- 返回统一的 `Document`，后续切分/入库只依赖 Document；
- 元数据与正文分离；
- 空正文、非法格式、非法 source 一律 fail-closed。
"""

from __future__ import annotations

import html.parser
import re
from hashlib import sha256
from typing import Any, Literal, Mapping

from app.rag.documents import Document

DocumentFormat = Literal["markdown", "html"]

# Markdown 常用块级标记
_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_MD_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_MD_UL = re.compile(r"^\s*[-*+]\s+(.*)$")
_MD_OL = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


class DocumentParseError(ValueError):
    """解析失败、空正文或非法输入时抛出的异常。"""


def _validate_source(source: str) -> None:
    if not isinstance(source, str) or not source.strip():
        raise DocumentParseError("source must be a non-empty string")


def _validate_raw_text(raw_text: str) -> None:
    if not isinstance(raw_text, str):
        raise DocumentParseError("raw_text must be a string")
    if not raw_text.strip():
        raise DocumentParseError("raw_text must not be empty")


def _make_document_id(source: str, content: str) -> str:
    """生成稳定的文档 ID：同一来源 + 同一正文永远得到同一 ID。"""
    digest = sha256(f"{source}\u0000{content}".encode("utf-8")).hexdigest()
    return f"doc-{digest}"


def _build_document(
    *,
    raw_text: str,
    source: str,
    format_name: DocumentFormat,
    document_id: str | None,
    content: str,
    title: str | None,
    lang: str | None,
    metadata: Mapping[str, Any] | None,
) -> Document:
    _validate_source(source)
    _validate_raw_text(raw_text)
    if not content.strip():
        raise DocumentParseError(f"{format_name} content is empty after parsing")

    merged = dict(metadata or {})
    merged["format"] = format_name
    if title:
        merged.setdefault("title", title)
    if lang:
        merged.setdefault("lang", lang)

    final_id = document_id or _make_document_id(source, content)
    return Document(
        id=final_id,
        source=source,
        content=content.strip(),
        metadata=merged,
    )


def _strip_markdown(raw_text: str) -> tuple[str, str | None]:
    """去除 Markdown 标记，返回 (正文, 标题)。

    保持简单可预测：去掉标题/列表/引用/围栏标记、图片与链接语法、
    行内代码反引号和星号强调；代码块内容原样保留。
    """
    title: str | None = None
    text_without_comments = _HTML_COMMENT.sub("", raw_text)
    lines: list[str] = []
    in_fence = False

    for line in text_without_comments.splitlines():
        # 围栏代码块：只去标记，内容保留
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(line)
            continue

        # 标题：第一个 H1 作为文档标题，标题行本身仍作为正文第一行保留
        heading_match = _MD_HEADING.match(line)
        if heading_match:
            heading_text = heading_match.group(2).strip()
            if heading_match.group(1) == "#" and title is None and heading_text:
                title = heading_text
            lines.append(heading_text)
            continue

        # 分隔线
        if _MD_HR.match(line):
            continue

        # 引用/无序列表/有序列表：去掉前缀
        for pattern in (_MD_BLOCKQUOTE, _MD_UL, _MD_OL):
            match = pattern.match(line)
            if match:
                line = match.group(1)
                break

        # 图片与链接：保留可见文本
        line = _MD_IMAGE.sub(r"\1", line)
        line = _MD_LINK.sub(r"\1", line)

        # 行内代码与粗斜体：去掉标记，保留内容
        line = line.replace("`", "")
        line = line.replace("**", "")
        line = line.replace("~~", "")
        line = line.replace("*", "")

        # 剩余的裸 HTML 标签也去掉
        line = re.sub(r"<[^>]+>", "", line)

        lines.append(line)

    content = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return content, title


class _HTMLTextExtractor(html.parser.HTMLParser):
    """提取 HTML 正文、标题和 lang；跳过 script/style。"""

    # 这些标签在解析结果里代表“换行边界”
    _BLOCK_TAGS = {
        "p", "div", "section", "article", "main", "header", "footer",
        "li", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5",
        "h6", "pre", "blockquote", "br", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self.first_heading: str | None = None
        self.html_lang: str | None = None

        self._skip_depth = 0
        self._capture_title = False
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def _append_newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = {name: value or "" for name, value in attrs}

        if tag == "html" and "lang" in attr_dict:
            self.html_lang = attr_dict["lang"]

        if tag in {"script", "style"}:
            self._skip_depth += 1
            return

        if tag == "title":
            self._capture_title = True
            return

        if (
            tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
            and self.first_heading is None
        ):
            self._heading_tag = tag
            self._heading_parts = []

        if tag == "br":
            self._append_newline()
        elif tag in self._BLOCK_TAGS:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if tag == "title":
            self._capture_title = False
            return

        if tag == self._heading_tag and self._heading_tag is not None:
            text = "".join(self._heading_parts).strip()
            if text:
                self.first_heading = text
            self._heading_tag = None
            self._heading_parts = []

        if tag in self._BLOCK_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._capture_title:
            self.title = (self.title or "") + data
            return
        if self._heading_tag is not None:
            self._heading_parts.append(data)
        self.parts.append(data)


def parse_markdown(
    raw_text: str,
    source: str,
    *,
    document_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Document:
    """把 Markdown 原始文本解析成 Document。"""
    _validate_source(source)
    _validate_raw_text(raw_text)
    content, title = _strip_markdown(raw_text)
    return _build_document(
        raw_text=raw_text,
        source=source,
        format_name="markdown",
        document_id=document_id,
        content=content,
        title=title,
        lang=None,
        metadata=metadata,
    )


def parse_html(
    raw_text: str,
    source: str,
    *,
    document_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Document:
    """把 HTML 原始文本解析成 Document。"""
    _validate_source(source)
    _validate_raw_text(raw_text)

    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(raw_text)
    except Exception as exc:  # html.parser 可能对畸形 HTML 抛异常
        raise DocumentParseError(f"invalid HTML: {exc}") from exc
    extractor.close()

    parts = "".join(extractor.parts)
    # 折叠行内多余空格与连续空行，保留段落边界
    content = re.sub(r"[ \t]+", " ", parts)
    content = re.sub(r"\n{2,}", "\n\n", content).strip()
    title = extractor.title.strip() if extractor.title else None
    if not title:
        title = extractor.first_heading

    return _build_document(
        raw_text=raw_text,
        source=source,
        format_name="html",
        document_id=document_id,
        content=content,
        title=title,
        lang=extractor.html_lang,
        metadata=metadata,
    )


def parse_document(
    raw_text: str,
    source: str,
    format_name: DocumentFormat,
    *,
    document_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Document:
    """按格式分发解析；不支持的格式直接 fail-closed。"""
    if format_name == "markdown":
        return parse_markdown(
            raw_text,
            source,
            document_id=document_id,
            metadata=metadata,
        )
    if format_name == "html":
        return parse_html(
            raw_text,
            source,
            document_id=document_id,
            metadata=metadata,
        )
    raise DocumentParseError(f"unsupported format: {format_name!r}")
