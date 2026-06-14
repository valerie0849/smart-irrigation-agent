import re
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class TextSplitter:

    def split_text(self, text: str) -> List[str]:
        if not text or not text.strip():
            return []

        sections = self._split_by_headings(text)
        if len(sections) <= 1:
            sections = self._split_by_paragraphs(text)

        return [s.strip() for s in sections if s.strip()]

    def _split_by_headings(self, text: str) -> List[str]:
        pattern = r'(?:^|\n)(#{1,6}\s+[^#\n]+|第[一二三四五六七八九十百\d]+[章节条段部篇][\s：:：][^\n]+)'
        parts = re.split(pattern, text, flags=re.MULTILINE)
        sections = []
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            is_heading = bool(
                re.match(r'^#{1,6}\s+', part) or
                re.match(r'^(?:第[一二三四五六七八九十百\d]+)?[章节条段部篇][\s：:：]', part)
            )
            if is_heading and current:
                sections.append(current.strip())
                current = part
            else:
                current += ("\n" if current else "") + part
        if current.strip():
            sections.append(current.strip())
        return sections if sections else [text]

    def _split_by_paragraphs(self, text: str) -> List[str]:
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        return paragraphs if paragraphs else [text]

    def split_documents(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        all_chunks = []
        for doc in documents:
            content = doc.get("content", "")
            if not content.strip():
                continue
            raw_chunks = self.split_text(content)
            for i, chunk in enumerate(raw_chunks):
                chunk_id = f"{doc.get('chunk_id', 'doc')}_c{i}"
                prev_chunk_id = f"{doc.get('chunk_id', 'doc')}_c{i - 1}" if i > 0 else ""
                next_chunk_id = f"{doc.get('chunk_id', 'doc')}_c{i + 1}" if i + 1 < len(raw_chunks) else ""
                all_chunks.append({
                    "source": doc.get("source", ""),
                    "page": doc.get("page", 0),
                    "chunk_id": chunk_id,
                    "content": chunk,
                    "raw_content": chunk,
                    "position": i,
                    "prev_chunk_id": prev_chunk_id,
                    "next_chunk_id": next_chunk_id,
                    "parent_source": doc.get("source", ""),
                })
        logger.info(f"[Splitter] 切分完成: {len(documents)}篇原始文档 -> {len(all_chunks)}个文本块(纯净切分,无上下文注入)")
        return all_chunks