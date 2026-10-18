# statline-mcp

🇳🇱 [Nederlandse versie](README-nl.md)

MCP server for the [CBS StatLine OData API](https://opendata.cbs.nl) (Statistics Netherlands). The full
table catalog (~6,000 tables) is cached locally in SQLite (FTS5) and is searchable; an MCP user can then
navigate through a table and retrieve the figures.

## Tools

| Tool | Purpose |
|---|---|
| `search_tables` | Search the catalog by title/description (prefix matching, nl/en, current tables by default) |
| `get_table_info` | Description, period, status and dimensions of a table |
| `get_table_measures` | Measures (columns) with unit and topic group, searchable |
| `list_dimension_values` | Valid values of a dimension (regions, periods, categories), searchable |
| `query_data` | Retrieve figures with filters, pagination and labels |
| `refresh_catalog` | Show cache status / refresh the catalog (automatically after 24 hours) |

Typical flow: `search_tables("werkloosheid")` → `get_table_info("85224NED")` →
`list_dimension_values(..., "Perioden")` → `query_data(..., filters={"Perioden": "laatste:4kw"})`.

> Note: most CBS tables are in Dutch. English versions exist for part of the catalog (table IDs ending
> in `ENG`); use `language="en"` in `search_tables` to restrict results to those.

### Filters in `query_data`

For each dimension you can pass:

| Input | Meaning |
|---|---|
| `"GM0363"` | a key |
| `"Amsterdam"` | a title (case-insensitive) |
| `"2023*"` | all values starting with `2023` |
| `"laatste"`, `"laatste:5"` | the most recent 1 or 5 values ("laatste" = latest) |
| `"laatste:4kw"`, `"laatste:3jaar"`, `"laatste:12maand"` | same, but counting only quarters / years / months (useful for mixed periods) |

Multiple values in a list are combined with OR, different dimensions with AND.

## Installation

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). uv fetches a suitable Python
(≥ 3.10) itself if needed.

### Option 1: straight from GitHub (no clone needed)

```bash
uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

This starts the server over stdio. You normally won't run this yourself; instead, let an MCP client
launch the command (see below).

### Option 2: clone locally

```bash
git clone https://github.com/pascal-cbs/MCP-server-StatLine.git
cd MCP-server-StatLine
uv sync
uv run statline-mcp   # starts the server over stdio
```

## Adding to Claude Code

With option 1 (from GitHub):

```bash
claude mcp add statline -- uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

With option 2 (local copy):

```bash
claude mcp add statline -- uv --directory /path/to/MCP-server-StatLine run statline-mcp
```

By default the server applies to the current project only. To make it available in all your projects:

```bash
claude mcp add --scope user statline -- uvx --from git+https://github.com/pascal-cbs/MCP-server-StatLine statline-mcp
```

Then check with `claude mcp list` that the server is connected, or type `/mcp` in Claude Code.
Start a new session if needed, then try for example:

> Find a CBS table about unemployment and give me the last four quarterly figures.

Remove it with `claude mcp remove statline`.

## Adding to other MCP clients

For Claude Desktop and other clients with a JSON configuration (for Claude Desktop:
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

For a local copy use `"command": "uv"` with
`"args": ["--directory", "/path/to/MCP-server-StatLine", "run", "statline-mcp"]`.
If the client cannot find `uv`/`uvx`, give the full path (`which uvx`).

## Configuration (environment variables)

- `STATLINE_CACHE_DIR` – cache directory (default `~/.cache/statline-mcp`)
- `STATLINE_CATALOG_MAX_AGE_HOURS` – maximum age of the catalog cache (default 24)

The first search fetches the catalog from CBS (a few seconds); after that, searching runs locally.

## Notes

- Data comes from `TypedDataSet`; pagination with `offset` automatically uses the `ODataFeed` endpoint
  because `ODataApi` does not support `$skip`.
- CBS keys are sometimes padded with spaces (`'GM0363    '`); the server handles this and returns trimmed values.
- CBS does not return a total row count; use `next_offset` to keep paginating.
