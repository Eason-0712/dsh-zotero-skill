# Zotero local API and connector routes

Base URL: `http://127.0.0.1:23119`.

## Desktop local API

The local API is under `/api/`. It implements Zotero Web API v3 for the local logged-in desktop user.

Important constraints:

- Use `/api/users/0/...` for the local user by default.
- Local API reads do not require an API key.
- Local API **writes** (POST / PUT / PATCH / DELETE) require a local API key plus a `Zotero-Server-ID` header; see below.
- Atom output is not supported locally.
- Attachment file URLs and full text can expose local file paths or document text; only retrieve them when the user asks.

Safe read routes:

```text
/api/
/api/schema
/api/itemTypes
/api/itemFields
/api/itemTypeFields?itemType=journalArticle
/api/itemTypeCreatorTypes?itemType=journalArticle
/api/creatorFields
/api/users/0/collections
/api/users/0/collections/top
/api/users/0/items
/api/users/0/items/top
/api/users/0/items/trash
/api/users/0/items/<itemKey>
/api/users/0/items/<itemKey>/children
/api/users/0/items?format=keys
/api/users/0/items?format=versions
/api/users/0/items?format=bibtex
/api/users/0/items?include=data,citation&style=apa
/api/users/0/items?q=<query>
/api/users/0/tags
/api/users/0/searches
/api/users/0/searches/<searchKey>/items
/api/users/0/groups
/api/users/0/fulltext?since=0
/api/users/0/items/<attachmentKey>/fulltext
/api/users/0/items/<attachmentKey>/file/view/url
```

## Local API writes (tags, collections, duplicate cleanup)

Write requests are supported but gated. The observed flow on Zotero 10.0.7:

1. Every response carries `Zotero-Server-ID`. Writes must echo it back in a `Zotero-Server-ID` header, otherwise the server answers `428 Zotero-Server-ID not provided` (mismatch: `412`).
2. Without a key: `401 API key required -- POST /api/local/authorize to obtain one`.
3. `POST /api/local/authorize` with `{"appName": "<name>"}` opens a Zotero dialog with **Allow** (single-use key, consumed by the first successful write), **Always Allow** (`remember: true`, persistent) and **Deny**. The response is `{"key": "...", "remember": false|true}`.
4. Send the key as `Zotero-API-Key: <key>` on PATCH/POST/DELETE. Include `If-Unmodified-Since-Version: <item version>` to avoid clobbering concurrent edits.
5. Send JSON bodies from a file (`curl --data-binary "@body.json"`); shell-quoted JSON is easily mangled into `Invalid JSON provided`.

`zotero.py authorize` performs step 3, caches `{key, serverId}` in `~/.zotero-skill/local-key.json`, and every write command (`tag`, `collect`, `classify --apply`, `dupes --trash`) handles steps 1, 2 and 4 automatically, re-authorizing on 401. Never edit `zotero.sqlite` directly.

## Connector server

The Zotero Connector server shares port `23119` and is used for desktop writes/imports.

Useful routes:

```text
GET  /connector/ping
POST /connector/ping
POST /connector/getSelectedCollection
POST /connector/import?session=<uuid>
POST /connector/saveItems
POST /connector/saveSnapshot
```

Use `/connector/import` for importing BibTeX/RIS strings into the currently selected Zotero library or collection. Treat connector writes as Zotero library modifications and confirm with the user before doing them.
