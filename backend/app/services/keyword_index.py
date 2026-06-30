"""SQLite FTS5 keyword index for hybrid retrieval.

Kept separate from the SQLAlchemy ORM: FTS5 virtual tables are managed with
raw sqlite3 against the same database file. Only chunks of the *current*
document version are indexed here.
"""

import sqlite3
from typing import List, Dict
from pathlib import Path

from ..models.database import DB_PATH
from ..core.logging_config import logger


class KeywordIndex:
    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_table(self):
        try:
            with self._connect() as con:
                con.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                    "USING fts5(chunk_id UNINDEXED, doc_id UNINDEXED, version UNINDEXED, content)"
                )
                con.commit()
        except sqlite3.OperationalError as e:
            logger.error(f"FTS5 unavailable, keyword search disabled: {e}")

    def add_chunks(self, doc_id: str, chunks: List[Dict], version: int = 1):
        if not chunks:
            return
        rows = [
            (f"{doc_id}_v{version}_{c['index']}", doc_id, version, c['content'])
            for c in chunks
        ]
        with self._connect() as con:
            con.executemany(
                "INSERT INTO chunks_fts (chunk_id, doc_id, version, content) VALUES (?, ?, ?, ?)",
                rows,
            )
            con.commit()

    def delete_by_doc_id(self, doc_id: str):
        with self._connect() as con:
            con.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
            con.commit()

    def count_by_doc_id(self, doc_id: str) -> int:
        with self._connect() as con:
            cur = con.execute("SELECT COUNT(*) FROM chunks_fts WHERE doc_id = ?", (doc_id,))
            return cur.fetchone()[0]

    @staticmethod
    def _sanitize(query: str) -> str:
        """Turn an arbitrary user string into a safe FTS5 MATCH expression.

        Wrap each token as a quoted phrase to avoid FTS5 syntax errors on
        characters like '-' or ':' and OR the tokens together.
        """
        tokens = [t for t in ''.join(c if c.isalnum() else ' ' for c in query).split() if t]
        if not tokens:
            return ""
        return " OR ".join(f'"{t}"' for t in tokens)

    def search(self, query: str, n_results: int = 20, doc_ids: list = None) -> List[Dict]:
        """Return ranked chunks. Lower bm25() is better; we expose rank order.

        ``doc_ids``: if provided, restrict results to these document IDs.
        """
        match = self._sanitize(query)
        if not match:
            return []
        try:
            with self._connect() as con:
                if doc_ids:
                    placeholders = ",".join("?" for _ in doc_ids)
                    sql = (
                        "SELECT chunk_id, doc_id, version, content, bm25(chunks_fts) AS score "
                        f"FROM chunks_fts WHERE chunks_fts MATCH ? AND doc_id IN ({placeholders}) "
                        "ORDER BY score LIMIT ?"
                    )
                    cur = con.execute(sql, [match, *doc_ids, n_results])
                else:
                    cur = con.execute(
                        "SELECT chunk_id, doc_id, version, content, bm25(chunks_fts) AS score "
                        "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                        (match, n_results),
                    )
                rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            logger.error(f"Keyword search failed: {e}")
            return []

        results = []
        for chunk_id, doc_id, version, content, score in rows:
            results.append({
                "id": chunk_id,
                "content": content,
                "metadata": {"doc_id": doc_id, "version": version},
                "bm25": float(score),
            })
        return results
