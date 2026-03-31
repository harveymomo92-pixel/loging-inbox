#!/usr/bin/env python3
import json
import os


def analyze_image(local_path, caption=''):
    filename = os.path.basename(local_path or '')
    summary = f'Image stored as {filename}. Async context placeholder. Caption: {caption}'.strip()
    return {
        'summary': summary,
        'objects_json': json.dumps([]),
        'ocr_text': '',
        'tags_json': json.dumps(['image', 'placeholder', 'async-worker']),
        'confidence': 0.1,
        'model_name': 'placeholder-async-worker',
        'status': 'completed',
        'error_text': ''
    }
