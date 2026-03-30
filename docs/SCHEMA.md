# SQLite Schema Design

## Database file

Suggested path:
- `data/inbox.db`

## Table: messages

Stores one row per inbound message.

```sql
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  event_type TEXT,
  message_id TEXT UNIQUE,
  chat_jid TEXT NOT NULL,
  sender_jid TEXT,
  sender_name TEXT,
  chat_type TEXT,
  message_type TEXT NOT NULL,
  text_content TEXT,
  caption TEXT,
  timestamp INTEGER,
  raw_payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## Table: media_files

Stores media/image file metadata.

```sql
CREATE TABLE IF NOT EXISTS media_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL,
  media_type TEXT NOT NULL,
  mime_type TEXT,
  original_url TEXT,
  local_path TEXT,
  sha256 TEXT,
  file_size INTEGER,
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (message_id) REFERENCES messages(message_id)
);
```

## Table: image_contexts

Stores extracted context from the agent for image messages.

```sql
CREATE TABLE IF NOT EXISTS image_contexts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id TEXT NOT NULL,
  summary TEXT,
  objects_json TEXT,
  ocr_text TEXT,
  tags_json TEXT,
  confidence REAL,
  model_name TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  error_text TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (message_id) REFERENCES messages(message_id)
);
```

## Recommended indexes

```sql
CREATE INDEX IF NOT EXISTS idx_messages_chat_jid ON messages(chat_jid);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type);
CREATE INDEX IF NOT EXISTS idx_media_message_id ON media_files(message_id);
CREATE INDEX IF NOT EXISTS idx_image_contexts_message_id ON image_contexts(message_id);
CREATE INDEX IF NOT EXISTS idx_image_contexts_status ON image_contexts(status);
```
