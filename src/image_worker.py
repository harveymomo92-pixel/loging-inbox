#!/usr/bin/env python3
import os
import sqlite3
import time
from datetime import datetime

from vision_agent import analyze_image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'inbox.db')
WORKER_ID = os.environ.get('IMAGE_WORKER_ID', f'image-worker:{os.uname().nodename}')
POLL_INTERVAL_SECONDS = int(os.environ.get('IMAGE_WORKER_POLL_INTERVAL', '10'))


def now_iso():
    return datetime.utcnow().isoformat() + 'Z'


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def claim_next_image_job(worker_id):
    conn = db()
    cur = conn.cursor()
    cur.execute('''
      SELECT ic.message_id, mf.local_path, m.caption, m.text_content, m.chat_jid, m.sender_jid, m.sender_name
      FROM image_contexts ic
      JOIN media_files mf ON mf.message_id = ic.message_id
      JOIN messages m ON m.message_id = ic.message_id
      WHERE ic.status = 'pending'
      ORDER BY COALESCE(ic.created_at, ic.updated_at) ASC, ic.id ASC
      LIMIT 1
    ''')
    row = cur.fetchone()
    if not row:
      conn.close()
      return None

    cur.execute('''
      UPDATE image_contexts
      SET status = 'processing',
          locked_at = ?,
          worker_id = ?,
          attempt_count = COALESCE(attempt_count, 0) + 1,
          updated_at = ?
      WHERE message_id = ?
        AND status = 'pending'
    ''', (now_iso(), worker_id, now_iso(), row['message_id']))
    conn.commit()
    claimed = cur.rowcount == 1
    conn.close()
    return dict(row) if claimed else None


def mark_completed(message_id, result):
    conn = db()
    cur = conn.cursor()
    cur.execute('''
      UPDATE image_contexts
      SET summary=?,
          objects_json=?,
          ocr_text=?,
          tags_json=?,
          confidence=?,
          model_name=?,
          status='completed',
          error_text='',
          analysis_source='agent',
          updated_at=?
      WHERE message_id=?
    ''', (
      result.get('summary', ''),
      result.get('objects_json', '[]'),
      result.get('ocr_text', ''),
      result.get('tags_json', '[]'),
      result.get('confidence', 0.0),
      result.get('model_name', 'unknown-worker'),
      now_iso(),
      message_id,
    ))
    conn.commit()
    conn.close()


def mark_failed(message_id, error_text):
    conn = db()
    cur = conn.cursor()
    cur.execute('''
      UPDATE image_contexts
      SET status='failed',
          error_text=?,
          analysis_source='agent',
          updated_at=?
      WHERE message_id=?
    ''', (str(error_text), now_iso(), message_id))
    conn.commit()
    conn.close()


def process_once():
    job = claim_next_image_job(WORKER_ID)
    if not job:
        return False

    message_id = job['message_id']
    local_path = job['local_path'] or ''
    caption = job['caption'] or ''

    if not local_path or not os.path.exists(local_path):
        mark_failed(message_id, 'media file missing')
        return True

    try:
        result = analyze_image(local_path, caption)
        mark_completed(message_id, result)
    except Exception as error:
        mark_failed(message_id, error)
    return True


def main():
    print(f'[image-worker] starting worker_id={WORKER_ID} db={DB_PATH}')
    while True:
        worked = process_once()
        if not worked:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
