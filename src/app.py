#!/usr/bin/env python3
import base64
import hashlib
import html
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def format_timestamp(ts):
    if not ts:
        return ""
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        return str(ts)
    except Exception:
        return str(ts)


def escape_html(value):
    return html.escape(str(value or ''))


def badge_style(value):
    value = (value or '').lower()
    palette = {
        'text': '#2563eb',
        'image': '#7c3aed',
        'video': '#db2777',
        'audio': '#0891b2',
        'document': '#ea580c',
        'unknown': '#6b7280',
        'completed': '#15803d',
        'pending': '#ca8a04',
        'failed': '#dc2626',
        'group': '#0f766e',
        'dm': '#1d4ed8',
    }
    bg = palette.get(value, '#374151')
    return f"display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;color:#fff;background:{bg};margin-right:6px"


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MEDIA_DIR = os.path.join(DATA_DIR, 'media')
DB_PATH = os.path.join(DATA_DIR, 'inbox.db')
HOST = '127.0.0.1'
PORT = int(os.environ.get('PORT', '8570'))
IMAGE_CONTEXT_MODE = os.environ.get('IMAGE_CONTEXT_MODE', 'placeholder')

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
    elif 'webp' in mime_type:
        ext = '.webp'
    elif 'mp4' in mime_type:
        ext = '.mp4'
    date_path = datetime.utcnow().strftime('%Y/%m/%d')
    target_dir = os.path.join(MEDIA_DIR, date_path)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f'{message_id}{ext}')
    with urllib.request.urlopen(url, timeout=20) as response:
        content = response.read()
    with open(target_path, 'wb') as file_obj:
        file_obj.write(content)
    return {
        'local_path': target_path,
        'file_size': len(content),
        'sha256': hashlib.sha256(content).hexdigest(),
    }


def placeholder_image_context(local_path, caption=''):
    filename = os.path.basename(local_path)
    summary = f'Image stored as {filename}. Auto context placeholder. Caption: {caption}'.strip()
    return {
        'summary': summary,
        'objects_json': json.dumps([]),
        'ocr_text': '',
        'tags_json': json.dumps(['image', 'placeholder', 'pending-agent-upgrade']),
        'confidence': 0.1,
        'model_name': 'placeholder-analyzer',
        'status': 'completed',
        'error_text': ''
    }


def analyze_image_with_agent(local_path, caption=''):
    raise NotImplementedError('Agent vision is not enabled yet')


def resolve_image_context(local_path, caption=''):
    if IMAGE_CONTEXT_MODE == 'agent':
        try:
            return analyze_image_with_agent(local_path, caption)
        except Exception as error:
            return {
                'summary': '',
                'objects_json': json.dumps([]),
                'ocr_text': '',
                'tags_json': json.dumps(['image', 'agent-failed']),
                'confidence': 0.0,
                'model_name': 'agent-analyzer',
                'status': 'failed',
                'error_text': str(error),
            }
    return placeholder_image_context(local_path, caption)


def validate_payload(payload):
    if not isinstance(payload, dict):
        return ['Payload must be a JSON object']

    errors = []
    message = payload.get('message')
    chat = payload.get('chat')

    if not isinstance(message, dict):
        errors.append('message object is required')
        return errors

    message_id = message.get('id') or message.get('message_id')
    if not message_id:
        errors.append('message.id or message.message_id is required')

    chat_jid = (message.get('chat_jid') or (chat or {}).get('jid') or '').strip()
    if not chat_jid:
        errors.append('chat.jid or message.chat_jid is required')

    message_type = (message.get('msg_type') or message.get('type') or 'unknown').strip()
    if not message_type:
        errors.append('message.msg_type or message.type is required')

    timestamp = message.get('timestamp')
    if timestamp is not None and not isinstance(timestamp, (int, float, str)):
        errors.append('message.timestamp must be int, float, or string')

    media_url = message.get('media_url') or ''
    if media_url and not isinstance(media_url, str):
        errors.append('message.media_url must be a string')

    return errors


def normalize_payload(payload):
    source = payload.get('source', 'unknown')
    event_type = payload.get('type', '')
    chat = payload.get('chat', {}) or {}
    message = payload.get('message', {}) or {}

    normalized_chat = {
        'jid': chat.get('jid') or message.get('chat_jid') or '',
        'name': chat.get('name') or message.get('sender_name') or message.get('name') or '',
        'type': chat.get('type') or ('group' if message.get('is_group') else 'dm') if 'is_group' in message else '',
    }

    normalized_message = {
        'id': message.get('id') or message.get('message_id') or f"generated-{int(datetime.utcnow().timestamp())}",
        'from': message.get('from') or message.get('sender_jid') or '',
        'name': message.get('name') or message.get('sender_name') or '',
        'type': message.get('type') or message.get('msg_type') or 'unknown',
        'content': message.get('content') or '',
        'caption': message.get('caption') or '',
        'timestamp': message.get('timestamp'),
        'mime_type': message.get('mime_type') or '',
        'media_url': message.get('media_url') or '',
        'file_name': message.get('file_name') or '',
        'width': message.get('width'),
        'height': message.get('height'),
        'is_group': message.get('is_group'),
        'chat_jid': message.get('chat_jid') or normalized_chat['jid'],
        'sender_jid': message.get('sender_jid') or '',
        'sender_name': message.get('sender_name') or message.get('name') or '',
    }

    return source, event_type, normalized_chat, normalized_message


def save_event(payload):
    source, event_type, chat, message = normalize_payload(payload)

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
      message.get('from') or message.get('sender_jid', ''),
      message.get('name') or message.get('sender_name', ''),
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
            image_context = resolve_image_context(local_path, caption)
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


def fetch_logs(limit=50, search='', message_type='', chat_type=''):
    conn = db()
    cur = conn.cursor()
    clauses = []
    params = []

    if search:
        clauses.append('(LOWER(m.sender_name) LIKE ? OR LOWER(m.sender_jid) LIKE ? OR LOWER(m.chat_jid) LIKE ? OR LOWER(m.text_content) LIKE ? OR LOWER(m.caption) LIKE ?)')
        needle = f"%{search.lower()}%"
        params.extend([needle, needle, needle, needle, needle])

    if message_type:
        clauses.append('m.message_type = ?')
        params.append(message_type)

    if chat_type:
        clauses.append('m.chat_type = ?')
        params.append(chat_type)

    where_sql = ''
    if clauses:
        where_sql = 'WHERE ' + ' AND '.join(clauses)

    query = f'''
      SELECT m.*, mf.local_path, mf.mime_type, ic.summary, ic.ocr_text, ic.tags_json, ic.status AS image_context_status
      FROM messages m
      LEFT JOIN media_files mf ON mf.message_id = m.message_id
      LEFT JOIN image_contexts ic ON ic.message_id = m.message_id
      {where_sql}
      ORDER BY COALESCE(m.timestamp, 0) DESC, m.id DESC
      LIMIT ?
    '''
    params.append(limit)
    cur.execute(query, tuple(params))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def render_html(rows, search='', message_type='', chat_type='', limit=100, form_action='viewer'):
    items = []
    for row in rows:
        preview = ''
        local_path = row.get('local_path') or ''
        if local_path and os.path.exists(local_path):
            with open(local_path, 'rb') as file_obj:
                b64 = base64.b64encode(file_obj.read()).decode('ascii')
            mime = row.get('mime_type') or 'image/jpeg'
            preview = f'<div style="margin-top:10px"><img src="data:{escape_html(mime)};base64,{b64}" style="max-width:240px;border-radius:8px;border:1px solid #ddd"/></div>'

        sender = escape_html(row.get('sender_name') or row.get('sender_jid') or '-')
        chat_jid = escape_html(row.get('chat_jid') or '-')
        text_content = escape_html(row.get('text_content') or '')
        caption = escape_html(row.get('caption') or '')
        summary = escape_html(row.get('summary') or '-')
        ocr_text = escape_html(row.get('ocr_text') or '-')
        tags_json = escape_html(row.get('tags_json') or '-')
        msg_type = escape_html(row.get('message_type') or 'unknown')
        chat_badge = escape_html(row.get('chat_type') or 'unknown')
        image_status = escape_html(row.get('image_context_status') or 'n/a')

        items.append(f"""
        <div style='border:1px solid #ddd;padding:14px;border-radius:12px;margin-bottom:14px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.04)'>
          <div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap'>
            <div><b>{sender}</b> → <span style='color:#555'>{chat_jid}</span></div>
            <div>
              <span style='{badge_style(msg_type)}'>{msg_type}</span>
              <span style='{badge_style(chat_badge)}'>{chat_badge}</span>
              <span style='{badge_style(image_status)}'>{image_status}</span>
            </div>
          </div>
          <div style='margin-top:8px;color:#333'><b>Text:</b> {text_content or '-'}</div>
          <div style='margin-top:4px;color:#333'><b>Caption:</b> {caption or '-'}</div>
          <div style='margin-top:4px;color:#555'><b>Timestamp:</b> {escape_html(format_timestamp(row.get('timestamp')) or '-')}</div>
          {preview}
          <div style='margin-top:8px'><b>Image context:</b> {summary}</div>
          <div style='margin-top:4px'><b>OCR:</b> {ocr_text}</div>
          <div style='margin-top:4px'><b>Tags:</b> {tags_json}</div>
        </div>
        """)

    search_value = escape_html(search)
    selected_msg_type = message_type or ''
    selected_chat_type = chat_type or ''

    def selected(current, expected):
        return 'selected' if current == expected else ''

    return f"""
    <html>
      <head>
        <title>Loging Inbox Viewer</title>
      </head>
      <body style='font-family:sans-serif;max-width:1100px;margin:32px auto;background:#f8fafc;color:#111827'>
        <h1 style='margin-bottom:8px'>Loging Inbox Viewer</h1>
        <p style='margin-top:0;color:#4b5563'>Recent inbound messages from Whatsapp Engine.</p>

        <form method='get' action='{escape_html(form_action)}' style='display:flex;gap:10px;flex-wrap:wrap;background:#fff;padding:14px;border-radius:12px;border:1px solid #e5e7eb;margin-bottom:18px'>
          <input type='text' name='q' placeholder='Search sender, chat, text, caption...' value='{search_value}' style='flex:1;min-width:260px;padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
          <select name='message_type' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'>
            <option value='' {selected(selected_msg_type, '')}>All types</option>
            <option value='text' {selected(selected_msg_type, 'text')}>Text</option>
            <option value='image' {selected(selected_msg_type, 'image')}>Image</option>
            <option value='video' {selected(selected_msg_type, 'video')}>Video</option>
            <option value='audio' {selected(selected_msg_type, 'audio')}>Audio</option>
            <option value='document' {selected(selected_msg_type, 'document')}>Document</option>
            <option value='unknown' {selected(selected_msg_type, 'unknown')}>Unknown</option>
          </select>
          <select name='chat_type' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'>
            <option value='' {selected(selected_chat_type, '')}>All chats</option>
            <option value='dm' {selected(selected_chat_type, 'dm')}>DM</option>
            <option value='group' {selected(selected_chat_type, 'group')}>Group</option>
          </select>
          <select name='limit' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'>
            <option value='25' {selected(str(limit), '25')}>25</option>
            <option value='50' {selected(str(limit), '50')}>50</option>
            <option value='100' {selected(str(limit), '100')}>100</option>
            <option value='200' {selected(str(limit), '200')}>200</option>
          </select>
          <button type='submit' style='padding:10px 16px;border:0;border-radius:8px;background:#2563eb;color:#fff'>Apply</button>
          <a href='{escape_html(form_action)}' style='padding:10px 16px;border-radius:8px;background:#e5e7eb;color:#111827;text-decoration:none'>Reset</a>
        </form>

        {''.join(items) if items else '<div style="background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px">No logs found.</div>'}
      </body>
    </html>
    """


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            return write_json(self, 200, {'ok': True, 'service': 'loging-inbox'})
        if parsed.path == '/logs':
            query = parse_qs(parsed.query)
            limit = max(1, min(500, int((query.get('limit') or ['50'])[0])))
            search = (query.get('q') or [''])[0].strip()
            message_type = (query.get('message_type') or [''])[0].strip()
            chat_type = (query.get('chat_type') or [''])[0].strip()
            return write_json(self, 200, {
                'items': fetch_logs(limit, search, message_type, chat_type)
            })
        if parsed.path == '/viewer':
            query = parse_qs(parsed.query)
            search = (query.get('q') or [''])[0].strip()
            message_type = (query.get('message_type') or [''])[0].strip()
            chat_type = (query.get('chat_type') or [''])[0].strip()
            limit = max(1, min(500, int((query.get('limit') or ['100'])[0])))
            rows = fetch_logs(limit, search, message_type, chat_type)
            form_action = os.environ.get('VIEWER_FORM_ACTION', 'viewer')
            body = render_html(rows, search, message_type, chat_type, limit, form_action).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return write_json(self, 404, {'error': 'Not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/webhook/whatsapp':
            try:
                payload = read_json(self)
            except json.JSONDecodeError as error:
                return write_json(self, 400, {'error': 'Invalid JSON payload', 'detail': str(error)})

            errors = validate_payload(payload)
            if errors:
                return write_json(self, 400, {'error': 'Invalid webhook payload', 'details': errors})

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
