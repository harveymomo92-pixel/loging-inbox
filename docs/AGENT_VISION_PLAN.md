# Agent Vision Plan

## Purpose

This document defines how to replace the current placeholder image context generator with a real agent-based image understanding flow.

## Important rule

This is the **last-stage feature**.

Do not make agent vision mandatory until these are already stable:
- webhook receiver
- SQLite persistence
- local image storage
- log viewer
- end-to-end inbound logging

## Proposed approach

For each stored image:
1. create an `image_contexts` row with `status = pending`
2. run an analyzer step asynchronously
3. analyzer sends image to an agent capable of image understanding
4. agent returns structured context
5. update the row in SQLite with:
   - summary
   - OCR text if available
   - objects/tags
   - confidence
   - model name
   - status = completed

If analyzer fails:
- keep original file and metadata
- set status to `failed`
- store failure reason in `error_text`

## Suggested interface

Implement a function like:

```python
def analyze_image_with_agent(local_path: str, caption: str = '') -> dict:
    ...
```

Expected return shape:

```json
{
  "summary": "Short description of the image",
  "objects_json": "[\"object1\", \"object2\"]",
  "ocr_text": "Detected text if any",
  "tags_json": "[\"invoice\", \"receipt\", \"document\"]",
  "confidence": 0.87,
  "model_name": "agent-vision-model",
  "status": "completed",
  "error_text": ""
}
```

## Activation strategy

Use an environment flag later, for example:
- `IMAGE_CONTEXT_MODE=placeholder`
- `IMAGE_CONTEXT_MODE=agent`

Default should remain:
- `placeholder`

This keeps the system safe and debuggable while core features stabilize.

## Future implementation ideas

Possible execution modes:
- direct local model call
- OpenClaw agent invocation
- external multimodal API

Preferred behavior:
- asynchronous job
- queue-based if volume grows
- retry with backoff on transient failures
