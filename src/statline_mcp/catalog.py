"""Doorzoekbare, lokaal gecachte kopie van de CBS-tabellencatalogus (SQLite FTS5)."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .client import CATALOG_URL, CBSClient

CACHE_DIR = Path(os.environ.get("STATLINE_CACHE_DIR", Path.home() / ".cache" / "statline-mcp"))
DB_PATH = CACHE_DIR / "catalog.db"
MAX_AGE_SECONDS = int(os.environ.get("STATLINE_CATALOG_MAX_AGE_HOURS", "24")) * 3600

# Statussen waarmee een tabel nog actueel wordt bijgehouden.
ACTIVE_STATUSES = ("Regulier", "Regular")

COLUMNS = (
    "Identifier", "Title", "ShortTitle", "Summary", "ShortDescription", "Modified",
    "Language", "Catalog", "Frequency", "Period", "OutputStatus", "ApiUrl",
    "RecordCount", "ColumnCount", "SearchPriority", "Source",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tables (
    identifier TEXT PRIMARY KEY, title TEXT, short_title TEXT, summary TEXT,
    description TEXT, modified TEXT, language TEXT, catalog TEXT, frequency TEXT,
    period TEXT, status TEXT, api_url TEXT, record_count INTEGER, column_count INTEGER,
    priority INTEGER, source TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS tables_fts USING fts5(
    identifier UNINDEXED, title, short_title, summary, description,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class Catalog:
    def __init__(self, client: CBSClient, path: Path = DB_PATH) -> None:
        self.client = client
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- verversen -------------------------------------------------------
    def _meta(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tables").fetchone()[0]

    def age_seconds(self) -> float | None:
        ts = self._meta("fetched_at")
        return time.time() - float(ts) if ts else None

    async def ensure_fresh(self) -> None:
        age = self.age_seconds()
        if self.count() == 0 or age is None:
            await self.refresh()  # zonder cache is er geen alternatief
        elif age > MAX_AGE_SECONDS:
            try:
                await self.refresh()
            except Exception:
                pass  # verouderde cache is beter dan niets

    async def refresh(self) -> int:
        rows = await self.client.get_all(f"{CATALOG_URL}/Tables", {"$select": ",".join(COLUMNS)})
        if not rows:
            raise RuntimeError("Lege catalogus ontvangen van het CBS.")
        with self.db:
            self.db.execute("DELETE FROM tables")
            self.db.execute("DELETE FROM tables_fts")
            for r in rows:
                ident = (r.get("Identifier") or "").strip()
                if not ident:
                    continue
                desc = r.get("ShortDescription") or ""
                self.db.execute(
                    "INSERT OR REPLACE INTO tables VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ident, r.get("Title"), r.get("ShortTitle"), r.get("Summary"), desc,
                        r.get("Modified"), r.get("Language"), r.get("Catalog"),
                        r.get("Frequency"), r.get("Period"), r.get("OutputStatus"),
                        r.get("ApiUrl"), _int(r.get("RecordCount")), _int(r.get("ColumnCount")),
                        _int(r.get("SearchPriority")), (r.get("Source") or "").strip(),
                    ),
                )
                self.db.execute(
                    "INSERT INTO tables_fts VALUES (?,?,?,?,?)",
                    (ident, r.get("Title"), r.get("ShortTitle"), r.get("Summary"), desc),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('fetched_at', ?)", (str(time.time()),)
            )
        return self.count()

    # ---- zoeken ----------------------------------------------------------
    @staticmethod
    def _tokens(query: str) -> list[str]:
        return [t for t in re.findall(r"\w+", query.lower()) if t]

    def search(
        self,
        query: str,
        language: str | None = None,
        include_discontinued: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        toks = self._tokens(query)
        if not toks:
            return []
        where, args = [], []
        if language:
            where.append("t.language = ?")
            args.append(language.lower())
        if not include_discontinued:
            where.append(f"t.status IN ({','.join('?' * len(ACTIVE_STATUSES))})")
            args += list(ACTIVE_STATUSES)
        extra = (" AND " + " AND ".join(where)) if where else ""

        def run(joiner: str) -> list[sqlite3.Row]:
            match = joiner.join(f'"{t}"*' for t in toks)
            sql = f"""
                SELECT t.*, bm25(tables_fts, 0, 10, 6, 3, 1) AS score
                FROM tables_fts JOIN tables t ON t.identifier = tables_fts.identifier
                WHERE tables_fts MATCH ?{extra}
                ORDER BY score LIMIT ? OFFSET ?"""
            return self.db.execute(sql, [match, *args, limit, offset]).fetchall()

        rows = run(" AND ")
        if not rows and len(toks) > 1 and offset == 0:
            rows = run(" OR ")  # val terug op 'minstens één term'
        return [dict(r) for r in rows]

    def get(self, identifier: str) -> dict[str, Any] | None:
        r = self.db.execute(
            "SELECT * FROM tables WHERE identifier = ? COLLATE NOCASE", (identifier.strip(),)
        ).fetchone()
        return dict(r) if r else None

    def facets(self) -> dict[str, Any]:
        q = lambda sql: {r[0]: r[1] for r in self.db.execute(sql)}  # noqa: E731
        return {
            "tables": self.count(),
            "by_language": q("SELECT language, COUNT(*) FROM tables GROUP BY 1"),
            "by_status": q("SELECT status, COUNT(*) FROM tables GROUP BY 1"),
            "by_catalog": q("SELECT catalog, COUNT(*) FROM tables GROUP BY 1"),
        }
