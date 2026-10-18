# statline-mcp

MCP-server voor de [CBS StatLine OData API](https://opendata.cbs.nl). De volledige tabellencatalogus
(~6000 tabellen) wordt lokaal gecachet in SQLite (FTS5) en is doorzoekbaar; vervolgens kan een
MCP-gebruiker door een tabel navigeren en de cijfers opvragen.

## Tools

| Tool | Doel |
|---|---|
| `search_tables` | Zoek in de catalogus op titel/omschrijving (voorvoegsel-matching, nl/en, actuele tabellen standaard) |
| `get_table_info` | Beschrijving, periode, status en dimensies van een tabel |
| `get_table_measures` | Meetwaarden (kolommen) met eenheid en onderwerpgroep, doorzoekbaar |
| `list_dimension_values` | Geldige waarden van een dimensie (regio's, perioden, categorieën), doorzoekbaar |
| `query_data` | Cijfers ophalen met filters (sleutel, titel, `2023*`, `laatste:5`), paginering, labels |
| `refresh_catalog` | Cachestatus bekijken / catalogus verversen (automatisch na 24 uur) |

Typische flow: `search_tables("werkloosheid")` → `get_table_info("85312NED")` →
`list_dimension_values(..., "Perioden")` → `query_data(..., filters={"Perioden": "laatste:4"})`.

## Installatie

Vereist Python ≥ 3.10.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

## Gebruik met Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "statline": { "command": "/Volumes/Data/MCP-Statline/.venv/bin/statline-mcp" }
  }
}
```

Claude Code: `claude mcp add statline -- /Volumes/Data/MCP-Statline/.venv/bin/statline-mcp`

## Configuratie (omgevingsvariabelen)

- `STATLINE_CACHE_DIR` – cachemap (standaard `~/.cache/statline-mcp`)
- `STATLINE_CATALOG_MAX_AGE_HOURS` – maximale cacheleeftijd (standaard 24)

## Opmerkingen

- Data komt uit `TypedDataSet`; paginering met `offset` gebruikt automatisch de `ODataFeed`-endpoint
  omdat `ODataApi` geen `$skip` ondersteunt.
- CBS-sleutels zijn soms met spaties aangevuld (`'GM0363    '`); de server regelt dit en geeft getrimde waarden terug.
