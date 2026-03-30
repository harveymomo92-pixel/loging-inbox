# Roadmap

## Phase 1
- define incoming webhook schema
- create SQLite schema
- create receiver endpoint
- persist text messages
- persist image metadata
- save image files locally
- create viewer foundation

## Phase 2
- build log viewer endpoint/UI
- add filters and pagination
- show image preview
- show extracted image context field
- verify end-to-end logging from Whatsapp Engine

## Phase 3
- systemd service
- retention policy
- export/search improvements

## Final phase
- replace placeholder image analysis with real agent vision/context extraction
- store structured image context from the agent into SQLite
- add error handling / retry policy for agent failures
- make activation configurable so fallback stays available
