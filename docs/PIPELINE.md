# Image Processing Pipeline

## Goal

For each incoming image message:
- keep original file locally
- extract useful context using an agent
- store extracted context in SQLite

## Pipeline

```text
Whatsapp Engine webhook
  -> receive image event
  -> write message metadata to SQLite
  -> download/store image locally
  -> create pending image_context row
  -> call agent/image analyzer
  -> receive structured context
  -> update SQLite
  -> expose through viewer
```

## Stored outputs

### Original file
- stored in local media folder, e.g. `data/media/YYYY/MM/DD/...`

### Metadata in SQLite
- message metadata
- media metadata
- extraction summary
- tags
- OCR text if present
- status / errors

## Suggested extracted context fields

- `summary`: short natural-language description
- `objects_json`: list of detected objects/visual entities
- `ocr_text`: text read from the image, if any
- `tags_json`: labels for search/filtering
- `confidence`: analyzer confidence score
- `status`: pending / completed / failed

## Failure handling

If analysis fails:
- keep original message and file
- mark `image_contexts.status = failed`
- store reason in `error_text`

## Viewer expectations

Viewer should show:
- sender
- message time
- text/caption
- image preview
- extracted summary
- OCR text
- tags
