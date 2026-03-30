# Webhook Event Schema

## Source

Incoming webhook source:
- Whatsapp Engine

## Suggested normalized payload

```json
{
  "type": "whatsapp.message",
  "source": "whatsapp-engine",
  "chat": {
    "jid": "6281234567890@s.whatsapp.net",
    "name": "Bima"
  },
  "message": {
    "id": "MSG123",
    "from": "6281234567890@s.whatsapp.net",
    "name": "Bima",
    "type": "text",
    "content": "hello",
    "caption": "",
    "timestamp": 1710000000,
    "mime_type": "",
    "media_url": "",
    "file_name": "",
    "width": null,
    "height": null
  },
  "seenAt": "2026-03-30T00:00:00.000Z"
}
```

## Minimal required fields

- `type`
- `source`
- `chat.jid`
- `message.id`
- `message.type`
- `message.timestamp`

## Message types to support first

- `text`
- `image`

## Receiver behavior

### For `text`
- save metadata to `messages`

### For `image`
- save metadata to `messages`
- save media metadata to `media_files`
- download/store image locally if media URL available
- create pending row in `image_contexts`
- send image to agent for context extraction
- update `image_contexts`
