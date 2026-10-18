"""MCP-server voor CBS StatLine: zoek tabellen, verken de structuur en vraag data op."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .catalog import Catalog
from .client import DEFAULT_API, CBSClient, CBSError

mcp = FastMCP(
    "statline",
    instructions=(
        "Toegang tot de CBS StatLine OData API (opendata.cbs.nl). Werkwijze: "
        "1) search_tables om een tabel te vinden; 2) get_table_info om de dimensies en "
        "meetwaarden te zien; 3) list_dimension_values om geldige waarden (regio's, perioden, "
        "categorieën) te vinden; 4) query_data om de cijfers op te halen. "
        "Filter in query_data altijd op alle dimensies die je kent, anders krijg je alle combinaties."
    ),
)

_client: CBSClient | None = None
_catalog: Catalog | None = None
_structures: dict[str, "Structure"] = {}
_dim_values: dict[tuple[str, str], list[dict[str, Any]]] = {}

# Perioden-sleutels: 2024KW03, 2024JJ00, 2024MM05, 2024HJ01 (halfjaar), 2024W1 (week) enz.
PERIOD_ALIASES = {
    "kw": "KW", "q": "KW", "kwartaal": "KW", "kwartalen": "KW",
    "jj": "JJ", "j": "JJ", "jaar": "JJ", "jaren": "JJ", "year": "JJ",
    "mm": "MM", "m": "MM", "maand": "MM", "maanden": "MM", "month": "MM",
    "hj": "HJ", "halfjaar": "HJ",
}

DIM_TYPES = {"Dimension", "TimeDimension", "GeoDimension", "GeoDetail"}


# ---------------------------------------------------------------- helpers
def _cat() -> Catalog:
    global _client, _catalog
    if _catalog is None:
        _client = CBSClient()
        _catalog = Catalog(_client)
    return _catalog


def _cli() -> CBSClient:
    _cat()
    assert _client is not None
    return _client


def _clip(text: str | None, n: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _clean(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def _q(s: str) -> str:
    return s.replace("'", "''")


class Structure:
    """Dimensies en meetwaarden van één tabel (uit DataProperties)."""

    def __init__(self, table_id: str, base: str, props: list[dict[str, Any]]) -> None:
        self.table_id, self.base = table_id, base
        by_id = {p["ID"]: p for p in props}
        self.dimensions: dict[str, dict[str, Any]] = {}
        self.measures: dict[str, dict[str, Any]] = {}
        for p in props:
            t, key = p.get("Type"), (p.get("Key") or "").strip()
            if t in DIM_TYPES and key and key not in self.dimensions:
                self.dimensions[key] = {
                    "key": key, "title": _clean(p.get("Title")), "type": t,
                    "description": _clip(p.get("Description"), 300),
                }
            elif t == "Topic" and key:
                path, parent = [], by_id.get(p.get("ParentID"))
                while parent:
                    path.append(_clean(parent.get("Title")))
                    parent = by_id.get(parent.get("ParentID"))
                self.measures[key] = {
                    "key": key, "title": _clean(p.get("Title")), "unit": _clean(p.get("Unit")),
                    "datatype": p.get("Datatype"), "decimals": p.get("Decimals"),
                    "group": " > ".join(reversed(path)) or None,
                    "description": _clip(p.get("Description"), 300),
                }

    def dimension(self, name: str) -> dict[str, Any]:
        for k, d in self.dimensions.items():
            if k.lower() == name.strip().lower() or (d["title"] or "").lower() == name.strip().lower():
                return d
        raise ValueError(
            f"Dimensie '{name}' bestaat niet in {self.table_id}. "
            f"Beschikbaar: {', '.join(self.dimensions)}"
        )

    def resolve_measure(self, name: str) -> str:
        n = name.strip().lower()
        for k, m in self.measures.items():
            if k.lower() == n or (m["title"] or "").lower() == n:
                return k
        hits = [k for k, m in self.measures.items() if n in (m["title"] or "").lower() or n in k.lower()]
        if len(hits) == 1:
            return hits[0]
        hint = f" Mogelijk bedoeld: {', '.join(hits[:8])}" if hits else ""
        raise ValueError(f"Meetwaarde '{name}' niet eenduidig gevonden in {self.table_id}.{hint}")


async def _structure(table_id: str) -> Structure:
    table_id = table_id.strip().upper() if table_id.strip().isalnum() else table_id.strip()
    if table_id in _structures:
        return _structures[table_id]
    await _cat().ensure_fresh()
    meta = _cat().get(table_id)
    base = (meta["api_url"] if meta and meta["api_url"] else f"{DEFAULT_API}/{table_id}").rstrip("/")
    if not meta:
        base = f"{DEFAULT_API}/{table_id}"
    props = await _cli().get_all(f"{base}/DataProperties")
    s = Structure(meta["identifier"] if meta else table_id, base, props)
    _structures[table_id] = s
    return s


async def _values(s: Structure, dim: str) -> list[dict[str, Any]]:
    cache_key = (s.table_id, dim)
    if cache_key not in _dim_values:
        rows = await _cli().get_all(f"{s.base}/{dim}")
        groups: dict[Any, str] = {}
        if any("CategoryGroupID" in r for r in rows[:1]):
            try:
                g = await _cli().get_all(f"{s.base}/CategoryGroups", {"$filter": f"DimensionKey eq '{_q(dim)}'"})
                groups = {x["ID"]: _clean(x.get("Title")) for x in g}
            except CBSError:
                pass
        _dim_values[cache_key] = [
            {
                "raw": r["Key"], "key": r["Key"].strip(), "title": _clean(r.get("Title")) or "",
                "description": _clip(r.get("Description"), 160),
                "group": groups.get(r.get("CategoryGroupID")),
                "status": r.get("Status"),
            }
            for r in rows
        ]
    return _dim_values[cache_key]


def _suggest(values: list[dict[str, Any]], spec: str, n: int = 8) -> str:
    s = spec.lower()
    hits = [v for v in values if s in v["title"].lower() or s in v["key"].lower()][:n]
    if not hits:
        return ""
    return " Bedoelde je: " + "; ".join(f"{v['key']} = {v['title']}" for v in hits) + "?"


async def _dim_clause(s: Structure, dim: str, specs: list[str]) -> str:
    """Zet gebruikersinvoer (sleutel, titel, 'prefix*', 'laatste[:N]') om in een OData-voorwaarde."""
    values = await _values(s, dim)
    by_key = {v["key"]: v for v in values}
    by_key_ci = {v["key"].lower(): v for v in values}
    by_title = {v["title"].lower(): v for v in values}
    terms: list[str] = []
    for spec in specs:
        spec = str(spec).strip()
        m = re.fullmatch(r"(?:latest|laatste)(?::?(\d+))?(?::?([a-z]+))?", spec, re.I)
        if m:
            n = int(m.group(1) or 1)
            pool = values
            if m.group(2):
                code = PERIOD_ALIASES.get(m.group(2).lower())
                if not code:
                    raise ValueError(
                        f"Onbekend periodetype '{m.group(2)}'. Gebruik: {', '.join(sorted(set(PERIOD_ALIASES)))}"
                    )
                pool = sorted(
                    (v for v in values if re.fullmatch(rf"\d{{4}}{code}\d+", v["key"])),
                    key=lambda v: v["key"],
                )
                if not pool:
                    raise ValueError(f"Dimensie {dim} bevat geen waarden van type '{m.group(2)}'.")
            terms += [f"{dim} eq '{_q(v['raw'])}'" for v in pool[-n:]]
        elif spec.endswith("*") and len(spec) > 1:
            terms.append(f"startswith({dim},'{_q(spec[:-1])}')")
        elif spec in by_key:
            terms.append(f"{dim} eq '{_q(by_key[spec]['raw'])}'")
        elif spec.lower() in by_key_ci:
            terms.append(f"{dim} eq '{_q(by_key_ci[spec.lower()]['raw'])}'")
        elif spec.lower() in by_title:
            terms.append(f"{dim} eq '{_q(by_title[spec.lower()]['raw'])}'")
        else:
            raise ValueError(f"Waarde '{spec}' niet gevonden in dimensie {dim}.{_suggest(values, spec)}")
    if len(terms) > 80:
        raise ValueError(f"Te veel waarden voor {dim} ({len(terms)}); gebruik een 'prefix*' of selecteer minder.")
    return "(" + " or ".join(terms) + ")"


def _record(r: dict[str, Any]) -> dict[str, Any]:
    return {k: _clean(v) for k, v in r.items() if k != "ID"}


# ------------------------------------------------------------------ tools
@mcp.tool()
async def search_tables(
    query: str,
    language: Literal["nl", "en"] | None = None,
    include_discontinued: bool = False,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Doorzoek de CBS-catalogus (ca. 6000 tabellen) op titel en beschrijving.

    Zoektermen worden als voorvoegsel gematcht (bv. 'werkloos' vindt 'werkloosheid'); alle
    termen moeten voorkomen, anders valt de zoekactie terug op 'minstens één term'.
    Een tabel-ID (bv. '83765NED') kan ook direct worden opgegeven.

    Args:
        query: Zoektekst, bv. 'huizenprijzen gemeente' of 'unemployment'.
        language: Beperk tot Nederlandstalige ('nl') of Engelstalige ('en') tabellen.
        include_discontinued: Neem ook stopgezette tabellen mee (standaard alleen actuele).
        limit: Maximaal aantal resultaten (1-50).
        offset: Aantal resultaten om over te slaan (voor volgende pagina).
    """
    cat = _cat()
    await cat.ensure_fresh()
    limit = max(1, min(limit, 50))
    direct = cat.get(query) if re.fullmatch(r"\w{4,12}", query.strip()) else None
    rows = cat.search(query, language, include_discontinued, limit, offset)
    if direct and offset == 0 and all(r["identifier"] != direct["identifier"] for r in rows):
        rows.insert(0, direct)
    return {
        "query": query,
        "results": [
            {
                "id": r["identifier"], "title": _clip(r["title"], 200), "language": r["language"],
                "period": r["period"], "frequency": r["frequency"], "status": r["status"],
                "records": r["record_count"], "columns": r["column_count"],
                "summary": _clip(r["summary"], 300),
            }
            for r in rows
        ],
        "next_offset": offset + limit if len(rows) == limit else None,
        "hint": "Gebruik get_table_info(id) om de structuur van een tabel te zien.",
    }


@mcp.tool()
async def get_table_info(table_id: str, full_description: bool = False) -> dict[str, Any]:
    """Beschrijving van één tabel plus zijn dimensies (waar je op filtert) en aantal meetwaarden.

    Dit is het startpunt om door een tabel te navigeren. Gebruik daarna get_table_measures voor
    de kolommen en list_dimension_values voor de waarden van een dimensie.

    Args:
        table_id: Tabel-ID zoals '83765NED' of '85874ENG'.
        full_description: Voeg de lange officiële toelichting toe (kan veel tekst zijn).
    """
    s = await _structure(table_id)
    info = (await _cli().get_json(f"{s.base}/TableInfos"))["value"][0]
    out: dict[str, Any] = {
        "id": s.table_id,
        "title": _clean(info.get("Title")),
        "summary": _clean(info.get("Summary")),
        "description": _clean(info.get("ShortDescription")),
        "period": info.get("Period"), "frequency": info.get("Frequency"),
        "status": info.get("OutputStatus"), "language": info.get("Language"),
        "modified": info.get("Modified"), "source": _clean(info.get("Source")),
        "explanatory_text": _clean(info.get("ExplanatoryText")) or None,
        "dimensions": list(s.dimensions.values()),
        "measure_count": len(s.measures),
        "default_measures": [m["key"] for m in list(s.measures.values())[:5]],
    }
    if full_description:
        out["full_description"] = _clip(info.get("Description"), 8000)
    return out


@mcp.tool()
async def get_table_measures(
    table_id: str, search: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Lijst de meetwaarden (kolommen met cijfers) van een tabel, met eenheid en onderwerpgroep.

    Args:
        table_id: Tabel-ID.
        search: Filter op tekst in titel, sleutel, groep of omschrijving.
        limit: Maximaal aantal meetwaarden (1-200).
        offset: Aantal over te slaan (paginering).
    """
    s = await _structure(table_id)
    ms = list(s.measures.values())
    if search:
        q = search.lower()
        ms = [m for m in ms if q in " ".join(str(m.get(f) or "") for f in ("key", "title", "group", "description")).lower()]
    limit = max(1, min(limit, 200))
    page = ms[offset : offset + limit]
    return {
        "table": s.table_id, "total": len(ms), "measures": page,
        "next_offset": offset + limit if offset + limit < len(ms) else None,
    }


@mcp.tool()
async def list_dimension_values(
    table_id: str, dimension: str, search: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Lijst de geldige waarden van een dimensie (bv. regio's, perioden, geslacht, bedrijfstakken).

    De 'key' kun je gebruiken in query_data.filters. Perioden-sleutels hebben vorm als
    '2023JJ00' (jaar), '2023KW01' (kwartaal), '2023MM01' (maand).

    Args:
        table_id: Tabel-ID.
        dimension: Dimensie-sleutel of -titel uit get_table_info (bv. 'Perioden', 'RegioS').
        search: Filter op tekst in sleutel, titel of groep (bv. 'amsterdam').
        limit: Maximaal aantal waarden (1-500).
        offset: Aantal over te slaan (paginering).
    """
    s = await _structure(table_id)
    d = s.dimension(dimension)
    vals = await _values(s, d["key"])
    if search:
        q = search.lower()
        vals = [v for v in vals if q in f"{v['key']} {v['title']} {v['group'] or ''}".lower()]
    limit = max(1, min(limit, 500))
    page = [{k: v for k, v in x.items() if k != "raw" and v} for x in vals[offset : offset + limit]]
    return {
        "table": s.table_id, "dimension": d["key"], "total": len(vals), "values": page,
        "next_offset": offset + limit if offset + limit < len(vals) else None,
    }


@mcp.tool()
async def query_data(
    table_id: str,
    filters: dict[str, list[str] | str] | None = None,
    measures: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    labels: Literal["title", "key", "both"] = "title",
    odata_filter: str | None = None,
) -> dict[str, Any]:
    """Haal cijfers op uit een CBS-tabel.

    Filters per dimensie accepteren: een sleutel ('GM0363'), een titel ('Amsterdam'),
    een voorvoegsel met sterretje ('2023*' = alle perioden van 2023), of 'laatste' / 'laatste:5'
    voor de meest recente 1 resp. 5 waarden. Bij een tijddimensie met gemengde perioden (jaar +
    kwartaal) voeg je een type toe: 'laatste:4kw' (4 kwartalen), 'laatste:3jaar', 'laatste:12maand'. Meerdere waarden in een lijst worden met OF gecombineerd,
    verschillende dimensies met EN. Een dimensie zonder filter levert alle waarden op.

    Voorbeeld: filters={"RegioS": ["Amsterdam", "Utrecht"], "Perioden": "laatste:3"},
               measures=["Werkloosheid"].

    Args:
        table_id: Tabel-ID.
        filters: {dimensie: waarde(n)}. Zie list_dimension_values voor geldige waarden.
        measures: Meetwaarden (sleutel of (deel van) titel). Standaard de eerste 10.
        limit: Maximaal aantal rijen per aanroep (1-1000).
        offset: Aantal rijen om over te slaan (paginering).
        labels: Toon dimensiewaarden als titel (standaard), sleutel of beide.
        odata_filter: Extra ruwe OData $filter-expressie (voor gevorderd gebruik), met AND toegevoegd.
    """
    s = await _structure(table_id)
    limit = max(1, min(limit, 1000))

    clauses = []
    for name, specs in (filters or {}).items():
        d = s.dimension(name)
        clauses.append(await _dim_clause(s, d["key"], [specs] if isinstance(specs, str) else list(specs)))
    if odata_filter:
        clauses.append(f"({odata_filter})")

    notes = []
    if measures:
        mkeys = [s.resolve_measure(m) for m in measures]
    else:
        mkeys = list(s.measures)[:10]
        if len(s.measures) > 10:
            notes.append(
                f"Tabel heeft {len(s.measures)} meetwaarden; alleen de eerste 10 getoond. "
                "Geef 'measures' op voor andere (zie get_table_measures)."
            )
    dims = list(s.dimensions)
    params = {"$select": ",".join(dims + mkeys), "$top": str(limit + 1)}
    data_base = s.base
    if offset:
        # ODataApi ondersteunt geen $skip; ODataFeed wel (zelfde data).
        params["$skip"] = str(offset)
        data_base = s.base.replace("/ODataApi/", "/ODataFeed/")
    if clauses:
        params["$filter"] = " and ".join(clauses)

    try:
        data = await _cli().get_json(f"{data_base}/TypedDataSet", params)
    except CBSError as e:
        raise CBSError(f"{e}. Controleer filters/odata_filter.") from e
    rows = [_record(r) for r in data.get("value", [])]
    more = len(rows) > limit
    rows = rows[:limit]

    if labels != "key":
        for dim in dims:
            if not any(dim in r for r in rows):
                continue
            lookup = {v["key"]: v["title"] for v in await _values(s, dim)}
            for r in rows:
                k = r.get(dim)
                if k is not None:
                    t = lookup.get(k, k)
                    r[dim] = t if labels == "title" else f"{t} ({k})"

    if not rows:
        notes.append("Geen rijen gevonden voor deze selectie; controleer de filters (bv. beschikbare perioden).")
    return {
        "table": s.table_id,
        "columns": {k: {"title": s.measures[k]["title"], "unit": s.measures[k]["unit"]} for k in mkeys},
        "dimensions": {k: s.dimensions[k]["title"] for k in dims},
        "rows": rows,
        "returned": len(rows),
        "next_offset": offset + limit if more else None,
        "notes": notes,
        "source_url": f"{s.base}/TypedDataSet",
    }


@mcp.tool()
async def refresh_catalog(force: bool = False) -> dict[str, Any]:
    """Toon de status van de lokale cataloguscache en ververs hem desgewenst.

    De catalogus wordt automatisch ververst als hij ouder is dan 24 uur.

    Args:
        force: Haal de catalogus nu opnieuw op bij het CBS.
    """
    cat = _cat()
    if force:
        await cat.refresh()
    else:
        await cat.ensure_fresh()
    age = cat.age_seconds()
    return {
        **cat.facets(),
        "cache_age_hours": round(age / 3600, 2) if age is not None else None,
        "fetched_at": datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - (age or 0), timezone.utc
        ).isoformat(timespec="seconds"),
        "cache_file": str(cat.path),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
