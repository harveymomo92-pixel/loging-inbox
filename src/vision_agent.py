#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_NAME = 'openclaw-agent-vision'

PROMPT_TEMPLATE = """
You are analyzing a single image for a WhatsApp logging pipeline.
Return JSON only. No markdown. No commentary.

Rules:
- Describe only what is visually present.
- Do not hallucinate unreadable text.
- OCR may be partial.
- Keep tags short and useful.
- confidence must be a number from 0 to 1.

Input:
- image_path: {image_path}
- caption: {caption}

Return exactly this JSON schema:
{{
  "summary": "short visual summary",
  "ocr_text": "visible text if any, else empty string",
  "tags": ["tag1", "tag2"],
  "confidence": 0.0
}}
""".strip()


class VisionAgentError(RuntimeError):
    pass


def _extract_json(text):
    text = (text or '').strip()
    if not text:
        raise VisionAgentError('empty agent response')

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        raise VisionAgentError('agent response did not contain JSON object')
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except Exception as error:
        raise VisionAgentError(f'failed to parse agent JSON: {error}')


def _normalize_result(obj):
    if not isinstance(obj, dict):
        raise VisionAgentError('agent result is not an object')

    summary = str(obj.get('summary') or '').strip()
    ocr_text = str(obj.get('ocr_text') or '').strip()
    tags = obj.get('tags') or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(tag).strip() for tag in tags if str(tag).strip()]

    try:
        confidence = float(obj.get('confidence', 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        'summary': summary,
        'objects_json': json.dumps([]),
        'ocr_text': ocr_text,
        'tags_json': json.dumps(tags, ensure_ascii=False),
        'confidence': confidence,
        'model_name': DEFAULT_MODEL_NAME,
        'status': 'completed',
        'error_text': ''
    }


def analyze_image(local_path, caption=''):
    image_path = str(Path(local_path).resolve())
    if not os.path.exists(image_path):
        raise VisionAgentError(f'image file does not exist: {image_path}')

    prompt = PROMPT_TEMPLATE.format(
        image_path=image_path,
        caption=(caption or '').strip(),
    )

    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt') as prompt_file:
        prompt_file.write(prompt)
        prompt_path = prompt_file.name

    try:
        # Use the local OpenClaw agent runtime through Codex CLI in one-shot mode.
        # We keep the contract strict: JSON only.
        cmd = [
            'codex',
            'exec',
            '--skip-git-repo-check',
            '--sandbox', 'workspace-write',
            '-',
        ]
        full_prompt = f"{prompt}\n\nImportant: analyze the image at path {image_path}. Return JSON only."
        proc = subprocess.run(
            cmd,
            input=full_prompt,
            text=True,
            capture_output=True,
            cwd=str(BASE_DIR),
            timeout=120,
        )
        if proc.returncode != 0:
            raise VisionAgentError((proc.stderr or proc.stdout or 'agent command failed').strip())

        parsed = _extract_json(proc.stdout)
        return _normalize_result(parsed)
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass
