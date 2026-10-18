# statline-mcp

🇬🇧 [English version](README.md)

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
| `query_data` | Cijfers ophalen met filters, paginering en labels |
| `refresh_catalog` | Cachestatus bekijken / catalogus verversen (automatisch na 24 uur) |

Typische flow: `search_tables("werkloosheid")` → `get_table_info("85224NED")` →
`list_dimension_values(..., "Perioden")` → `query_data(..., filters={"Perioden": "laatste:4kw"})`.

### Filters in `query_data`

Per dimensie kun je opgeven:

| Invoer | Betekenis |
|---|---|
| `"GM0363"` | een sleutel |
| `"Amsterdam"` | een titel (hoofdletterongevoelig) |
| `"2023*"` | alle waarden die met `2023` beginnen |
| `"laatste"`, `"laatste:5"` | de meest recente 1 resp. 5 waarden |
| `"laatste:4kw"`, `"laatste:3jaar"`, `"laatste:12maand"` | idem, maar alleen kwartalen / jaren / maanden (nuttig bij gemengde perioden) |

Meerdere waarden in een lijst worden met OF gecombineerd, verschillende dimensies met EN.

## Installatie

Je hebt [uv](https://docs.astral.sh/uv/getting-started/installation/) nodig. uv haalt zo nodig zelf
een geschikte Python (≥ 3.10) op.

### Optie 1: direct vanaf GitHub (geen clone nodig)

```bash
uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

Dit start de server over stdio. Normaal gesproken draai je dit niet zelf, maar laat je een
MCP-client het commando starten (zie hieronder).

### Optie 2: lokaal klonen

```bash
git clone https://github.com/pascal-cbs/MCP-server-StatLine.git
cd MCP-server-StatLine
uv sync
uv run statline-mcp   # start de server over stdio
```

## Toevoegen aan Claude Code

Met optie 1 (vanaf GitHub):

```bash
claude mcp add statline -- uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

Met optie 2 (lokale kopie):

```bash
claude mcp add statline -- uv --directory /pad/naar/MCP-server-StatLine run statline-mcp
```

Standaard geldt de server alleen voor het huidige project. Beschikbaar maken in al je projecten:

```bash
claude mcp add --scope user statline -- uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

Controleer daarna met `claude mcp list` of de server verbonden is, of typ `/mcp` in Claude Code.
Start eventueel een nieuwe sessie. Probeer het dan met bijvoorbeeld:

> Zoek een CBS-tabel over werkloosheid en geef de laatste vier kwartaalcijfers.

Verwijderen kan met `claude mcp remove statline`.

## Toevoegen aan andere MCP-clients

Voor Claude Desktop en andere clients met een JSON-configuratie (voor Claude Desktop:
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "statline": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/pascal-cbs/MCP-server-StatLine", "statline-mcp"]
    }
  }
}
```

Gebruik bij een lokale kopie `"command": "uv"` met
`"args": ["--directory", "/pad/naar/MCP-server-StatLine", "run", "statline-mcp"]`.
Als de client `uv`/`uvx` niet vindt, geef dan het volledige pad op (`which uvx`).

## Configuratie (omgevingsvariabelen)

- `STATLINE_CACHE_DIR` – cachemap (standaard `~/.cache/statline-mcp`)
- `STATLINE_CATALOG_MAX_AGE_HOURS` – maximale cacheleeftijd van de catalogus (standaard 24)

De eerste zoekactie haalt de catalogus op bij het CBS (een paar seconden); daarna draait zoeken lokaal.

## Opmerkingen

- Data komt uit `TypedDataSet`; paginering met `offset` gebruikt automatisch de `ODataFeed`-endpoint
  omdat `ODataApi` geen `$skip` ondersteunt.
- CBS-sleutels zijn soms met spaties aangevuld (`'GM0363    '`); de server regelt dit en geeft getrimde waarden terug.
- CBS geeft geen totaal aantal rijen terug; gebruik `next_offset` om verder te pagineren.
