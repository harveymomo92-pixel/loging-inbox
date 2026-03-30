#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MEDIA_DIR = os.path.join(DATA_DIR, 'media')
DB_PATH = os.path.join(DATA_DIR, 'inbox.db')
HOST = '127.0.0.1'
PORT = int(os.environ.get('PORT', '8570'))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MEDIA_DIR, exist_ok=True)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.executescript('''
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
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

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
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_messages_chat_jid ON messages(chat_jid);
    CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type);
    CREATE INDEX IF NOT EXISTS idx_media_message_id ON media_files(message_id);
    CREATE INDEX IF NOT EXISTS idx_image_contexts_message_id ON image_contexts(message_id);
    CREATE INDEX IF NOT EXISTS idx_image_contexts_status ON image_contexts(status);
    ''')
    conn.commit()
    conn.close()


def now_iso():
    return datetime.utcnow().isoformat() + 'Z'


def read_json(handler):
    length = int(handler.headers.get('Content-Length', '0'))
    raw = handler.rfile.read(length) if length else b''
    return json.loads(raw.decode('utf-8') or '{}')


def write_json(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def download_media(url, message_id, mime_type='image/jpeg'):
    ext = '.jpg'
    if 'png' in mime_type:
        ext = '.png'
    date_path = datetime.utcnow().strftime('%Y/%m/%d')
    target_dir = os.path.join(MEDIA_DIR, date_path)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f'{message_id}{ext}')
    with urllib.request.urlopen(url, timeout=20) as r:
        content = r.read()
    with open(target_path, 'wb') as f:
        f.write(content)
    return {
        'local_path': target_path,
        'file_size': len(content),
        'sha256': hashlib.sha256(content).hexdigest(),
    }


def fake_image_context(local_path, caption=''):
    filename = os.path.basename(local_path)
    summary = f'Image stored as {filename}. Auto context placeholder. Caption: {caption}'.strip()
    return {
        'summary': summary,
        'objects_json': json.dumps([]),
        'ocr_text': '',
        'tags_json': json.dumps(['image', 'pending-agent-upgrade']),
        'confidence': 0.1,
        'model_name': 'placeholder-analyzer',
        'status': 'completed',
        'error_text': ''
    }


def save_event(payload):
    source = payload.get('source', 'unknown')
    event_type = payload.get('type', '')
    chat = payload.get('chat', {}) or {}
    message = payload.get('message', {}) or {}

    message_id = message.get('id') or f"generated-{int(datetime.utcnow().timestamp())}"
    message_type = message.get('type', 'unknown')
    text_content = message.get('content', '')
    caption = message.get('caption', '')

    conn = db()
    cur = conn.cursor()
    cur.execute('''
      INSERT OR IGNORE INTO messages (
        source, event_type, message_id, chat_jid, sender_jid, sender_name,
        chat_type, message_type, text_content, caption, timestamp, raw_payload_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
      source,
      event_type,
      message_id,
      chat.get('jid', ''),
      message.get('from', ''),
      message.get('name', ''),
      chat.get('type', ''),
      message_type,
      text_content,
      caption,
      message.get('timestamp'),
      json.dumps(payload, ensure_ascii=False),
    ))

    media_saved = None
    image_context = None

    if message_type == 'image':
      media_url = message.get('media_url', '')
      mime_type = message.get('mime_type', 'image/jpeg')
      local_path = ''
      sha256 = ''
      file_size = None

      if media_url:
          media_saved = download_media(media_url, message_id, mime_type)
          local_path = media_saved['local_path']
          sha256 = media_saved['sha256']
          file_size = media_saved['file_size']

      cur.execute('''
        INSERT INTO media_files (
          message_id, media_type, mime_type, original_url, local_path, sha256,
          file_size, width, height
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ''', (
        message_id,
        'image',
        mime_type,
        media_url,
        local_path,
        sha256,
        file_size,
        message.get('width'),
        message.get('height'),
      ))

      cur.execute('''
        INSERT INTO image_contexts (
          message_id, status, updated_at
        ) VALUES (?, ?, ?)
      ''', (message_id, 'pending', now_iso()))

      if local_path:
          image_context = fake_image_context(local_path, caption)
          cur.execute('''
            UPDATE image_contexts
            SET summary=?, objects_json=?, ocr_text=?, tags_json=?, confidence=?, model_name=?, status=?, error_text=?, updated_at=?
            WHERE message_id=?
          ''', (
            image_context['summary'],
            image_context['objects_json'],
            image_context['ocr_text'],
            image_context['tags_json'],
            image_context['confidence'],
            image_context['model_name'],
            image_context['status'],
            image_context['error_text'],
            now_iso(),
            message_id,
          ))

    conn.commit()
    conn.close()

    return {
      'message_id': message_id,
      'message_type': message_type,
      'media_saved': media_saved,
      'image_context': image_context,
    }


def fetch_logs(limit=50):
    conn = db()
    cur = conn.cursor()
    cur.execute('''
      SELECT m.*, mf.local_path, mf.mime_type, ic.summary, ic.ocr_text, ic.tags_json, ic.status AS image_context_status
      FROM messages m
      LEFT JOIN media_files mf ON mf.message_id = m.message_id
      LEFT JOIN image_contexts ic ON ic.message_id = m.message_id
      ORDER BY COALESCE(m.timestamp, 0) DESC, m.id DESC
      LIMIT ?
    ''', (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def render_html(rows):
    items = []
    for row in rows:
        preview = ''
        local_path = row.get('local_path') or ''
        if local_path and os.path.exists(local_path):
            with open(local_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            mime = row.get('mime_type') or 'image/jpeg'
            preview = f'<div><img src="data:{mime};base64,{b64}" style="max-width:240px;border-radius:8px"/></div>'
        items.append(f"""
        <div style='border:1px solid #ddd;padding:12px;border-radius:10px;margin-bottom:12px'>
          <div><b>{row.get('sender_name') or row.get('sender_jid') or '-'}</b> → {row.get('chat_jid') or '-'}</div>
          <div>Type: {row.get('message_type')}</div>
          <div>Text: {row.get('text_content') or ''}</div>
          <div>Caption: {row.get('caption') or ''}</div>
          <div>Timestamp: {row.get('timestamp') or ''}</div>
          {preview}
          <div><b>Image context:</b> {row.get('summary') or '-'}</div>
          <div><b>OCR:</b> {row.get('ocr_text') or '-'}</div>
          <div><b>Tags:</b> {row.get('tags_json') or '-'}</div>
        </div>
        """)
    return f"""
    <html><head><title>Loging Inbox Viewer</title></head>
    <body style='font-family:sans-serif;max-width:960px;margin:40px auto'>
      <h1>Loging Inbox Viewer</h1>
      <p>Recent inbound messages from Whatsapp Engine.</p>
      {''.join(items) if items else '<p>No logs yet.</p>'}
    </body></html>
    """


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            return write_json(self, 200, {'ok': True, 'service': 'loging-inbox'})
        if parsed.path == '/logs':
            qs = parse_qs(parsed.query)
            limit = int((qs.get('limit') or ['50'])[0])
            return write_json(self, 200, {'items': fetch_logs(limit)})
        if parsed.path == '/viewer':
            rows = fetch_logs(100)
            html = render_html(rows).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        return write_json(self, 404, {'error': 'Not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/webhook/whatsapp':
            payload = read_json(self)
            result = save_event(payload)
            return write_json(self, 200, {'ok': True, 'saved': result})
        return write_json(self, 404, {'error': 'Not found'})


def main():
    init_db()
    server = HTTPServer((HOST, PORT), Handler)
    print(f'loging-inbox listening on http://{HOST}:{PORT}')
    server.serve_forever()


if __name__ == '__main__':
    main()
