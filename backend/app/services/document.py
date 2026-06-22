import hashlib
from typing import List, Dict, Any
from pathlib import Path


class DocumentParser:
    @staticmethod
    def parse_file(file_path: str) -> str:
        """Parse file and return text content"""
        ext = Path(file_path).suffix.lower()

        if ext == '.txt':
            return DocumentParser._parse_txt(file_path)
        elif ext == '.pdf':
            return DocumentParser._parse_pdf(file_path)
        elif ext == '.md':
            return DocumentParser._parse_md(file_path)
        elif ext == '.docx':
            return DocumentParser._parse_docx(file_path)
        elif ext == '.xlsx':
            return DocumentParser._parse_xlsx(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    @staticmethod
    def _parse_txt(file_path: str) -> str:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def _parse_pdf(file_path: str) -> str:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text.append(page_text)
        return "\n".join(text)

    @staticmethod
    def _parse_md(file_path: str) -> str:
        return DocumentParser._parse_txt(file_path)

    @staticmethod
    def _parse_docx(file_path: str) -> str:
        from docx import Document
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs if para.text])

    @staticmethod
    def _parse_xlsx(file_path: str) -> str:
        import pandas as pd
        dfs = pd.read_excel(file_path, sheet_name=None)
        text_parts = []
        for sheet_name, df in dfs.items():
            text_parts.append(f"=== Sheet: {sheet_name} ===")
            text_parts.append(df.to_string(index=False))
        return "\n".join(text_parts)


class Chunker:
    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> List[Dict[str, Any]]:
        """Simple character-based chunking"""
        if not text or not text.strip():
            return []

        chunks = []
        start = 0
        chunk_index = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunk = text[start:end]

            if chunk.strip():  # Only add non-empty chunks
                chunks.append({
                    "index": chunk_index,
                    "content": chunk,
                    "start_char": start,
                    "end_char": end
                })

            # If we've reached the end, break
            if end >= text_len:
                break

            start += chunk_size - overlap
            chunk_index += 1

        return chunks

    @staticmethod
    def calculate_file_hash(file_path: str) -> str:
        """Calculate SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
