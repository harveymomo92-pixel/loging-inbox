# ROADMAP

## v0.2 Priorities

### 1. Time Filtering & Navigation
- add viewer filters for today / 24h / 7d / custom range
- add API query parameters for time filtering
- add real pagination with next/prev navigation for large log sets

### 2. Detail & Debug Visibility
- add per-message detail view
- show raw payload JSON, message metadata, media metadata, and processing state
- surface invalid webhook payloads for audit/debug workflows

### 3. Richer Media Handling
- improve support for video/audio/document entries
- show mime type, file info, and any available metadata cleanly in the viewer
- prepare downstream schema so non-image media is first-class

## v0.2 Secondary
- add export helpers (JSON/CSV)
- add storage hygiene/retention tooling
- improve search/highlighting and viewer ergonomics

## v0.3 Direction
- replace placeholder image context with real OCR/vision extraction
- add richer indexing/searchability for extracted context
- add monitoring/ops dashboard for database size, media growth, and pipeline health
