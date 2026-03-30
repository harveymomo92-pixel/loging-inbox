# Decisions

## Accepted

### Source of messages
- only from Whatsapp Engine

### Storage
- SQLite for structured message logs and extracted image context

### Media handling
- original image files stored locally
- image meaning/context will be extracted through an agent
- extracted context stored in SQLite

### Viewer
- project must include a log viewer

### Delivery order
- all core logging functions must work first
- real agent vision/context extraction is added last, after receiver/storage/viewer are stable
- until then, placeholder context is acceptable for plumbing validation
