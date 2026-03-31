#!/usr/bin/env python3
import base64
import hashlib
import html
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse


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


def parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def parse_date_to_timestamp(value, end_of_day=False):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, '%Y-%m-%d')
        if end_of_day:
            dt = dt + timedelta(days=1) - timedelta(seconds=1)
        return int(dt.timestamp())
    except ValueError:
        return None


def resolve_time_range(time_range, start_date='', end_date=''):
    now = datetime.now()
    if time_range == 'today':
        start = datetime(now.year, now.month, now.day)
        return int(start.timestamp()), int(now.timestamp())
    if time_range == '24h':
        return int((now - timedelta(hours=24)).timestamp()), int(now.timestamp())
    if time_range == '7d':
        return int((now - timedelta(days=7)).timestamp()), int(now.timestamp())
    if time_range == 'custom':
        start_ts = parse_date_to_timestamp(start_date)
        end_ts = parse_date_to_timestamp(end_date, end_of_day=True)
        return start_ts, end_ts
    return None, None


def build_query(params):
    cleaned = {key: value for key, value in params.items() if value not in ('', None)}
    return urlencode(cleaned)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MEDIA_DIR = os.path.join(DATA_DIR, 'media')
DB_PATH = os.path.join(DATA_DIR, 'inbox.db')
HOST = '0.0.0.0'
PORT = int(os.environ.get('PORT', '8570'))
IMAGE_CONTEXT_MODE = os.environ.get('IMAGE_CONTEXT_MODE', 'placeholder')
IMAGE_ANALYSIS_WHITELIST_PATH = os.environ.get('IMAGE_ANALYSIS_WHITELIST_PATH', os.path.join(BASE_DIR, 'config', 'image-analysis-whitelist.json'))

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
      attempt_count INTEGER NOT NULL DEFAULT 0,
      locked_at TEXT,
      worker_id TEXT,
      analysis_source TEXT,
      decision_reason TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_messages_chat_jid ON messages(chat_jid);
    CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type);
    CREATE INDEX IF NOT EXISTS idx_media_message_id ON media_files(message_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_media_unique_message_id ON media_files(message_id);
    CREATE INDEX IF NOT EXISTS idx_image_contexts_message_id ON image_contexts(message_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_image_contexts_unique_message_id ON image_contexts(message_id);
    CREATE INDEX IF NOT EXISTS idx_image_contexts_status ON image_contexts(status);
    ''')
    for sql in [
        "ALTER TABLE image_contexts ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE image_contexts ADD COLUMN locked_at TEXT",
        "ALTER TABLE image_contexts ADD COLUMN worker_id TEXT",
        "ALTER TABLE image_contexts ADD COLUMN analysis_source TEXT",
        "ALTER TABLE image_contexts ADD COLUMN decision_reason TEXT"
    ]:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def now_iso():
    return datetime.utcnow().isoformat() + 'Z'


def load_image_analysis_whitelist():
    try:
        with open(IMAGE_ANALYSIS_WHITELIST_PATH, 'r', encoding='utf-8') as file_obj:
            data = json.load(file_obj)
        if not isinstance(data, dict):
            raise ValueError('Whitelist config must be an object')
        return {
            'enabled': bool(data.get('enabled', True)),
            'groups': [str(x).strip() for x in data.get('groups', []) if str(x).strip()],
            'users': [str(x).strip() for x in data.get('users', []) if str(x).strip()],
            'senderNames': [str(x).strip() for x in data.get('senderNames', []) if str(x).strip()],
        }
    except Exception:
        return {'enabled': True, 'groups': [], 'users': [], 'senderNames': []}


def image_analysis_decision(chat_jid, sender_jid, sender_name):
    cfg = load_image_analysis_whitelist()
    if not cfg.get('enabled', True):
        return True, 'whitelist_disabled'

    chat_jid = (chat_jid or '').strip()
    sender_jid = (sender_jid or '').strip()
    sender_name = (sender_name or '').strip()

    if chat_jid and chat_jid in cfg.get('groups', []):
        return True, 'whitelist_group'
    if sender_jid and sender_jid in cfg.get('users', []):
        return True, 'whitelist_user'
    if sender_name and sender_name in cfg.get('senderNames', []):
        return True, 'whitelist_sender_name'
    return False, 'not_in_whitelist'


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
        'timestamp': parse_int(message.get('timestamp'), message.get('timestamp')),
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

        cur.execute('SELECT id, local_path, sha256, file_size FROM media_files WHERE message_id=? LIMIT 1', (message_id,))
        existing_media = cur.fetchone()
        cur.execute('SELECT id, status FROM image_contexts WHERE message_id=? LIMIT 1', (message_id,))
        existing_context = cur.fetchone()

        if media_url and not existing_media:
            media_saved = download_media(media_url, message_id, mime_type)
            local_path = media_saved['local_path']
            sha256 = media_saved['sha256']
            file_size = media_saved['file_size']
        elif existing_media:
            local_path = existing_media['local_path'] or ''
            sha256 = existing_media['sha256'] or ''
            file_size = existing_media['file_size']

        if not existing_media:
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

        should_analyze, decision_reason = image_analysis_decision(
            chat.get('jid', ''),
            message.get('from') or message.get('sender_jid', ''),
            message.get('name') or message.get('sender_name', ''),
        )

        if not existing_context:
            cur.execute('''
              INSERT INTO image_contexts (
                message_id, status, error_text, analysis_source, decision_reason, created_at, updated_at
              ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
              message_id,
              'pending' if should_analyze else 'skipped',
              '' if should_analyze else 'not in analysis whitelist',
              None,
              decision_reason,
              now_iso(),
              now_iso(),
            ))

        if local_path and should_analyze:
            image_context = resolve_image_context(local_path, caption)
            cur.execute('''
              UPDATE image_contexts
              SET summary=?, objects_json=?, ocr_text=?, tags_json=?, confidence=?, model_name=?, status=?, error_text=?, analysis_source=?, updated_at=?
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
              'placeholder' if IMAGE_CONTEXT_MODE != 'agent' else 'agent',
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


def fetch_logs(limit=50, offset=0, search='', message_type='', chat_type='', time_range='', start_date='', end_date='', sender_filter='', group_filter=''):
    conn = db()
    cur = conn.cursor()
    clauses = ["NOT (m.message_type = 'unknown' AND COALESCE(m.text_content, '') = '' AND COALESCE(m.caption, '') = '')"]
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

    if sender_filter:
        clauses.append('(LOWER(COALESCE(m.sender_name, \'\')) LIKE ? OR LOWER(COALESCE(m.sender_jid, \'\')) LIKE ?)')
        sender_needle = f"%{sender_filter.lower()}%"
        params.extend([sender_needle, sender_needle])

    if group_filter:
        clauses.append('LOWER(COALESCE(m.chat_jid, \'\')) LIKE ?')
        group_needle = f"%{group_filter.lower()}%"
        params.append(group_needle)

    start_ts, end_ts = resolve_time_range(time_range, start_date, end_date)
    if start_ts is not None:
        clauses.append('COALESCE(m.timestamp, 0) >= ?')
        params.append(start_ts)
    if end_ts is not None:
        clauses.append('COALESCE(m.timestamp, 0) <= ?')
        params.append(end_ts)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''

    count_query = f'SELECT COUNT(*) FROM messages m {where_sql}'
    cur.execute(count_query, tuple(params))
    total = cur.fetchone()[0]

    query = f'''
      SELECT m.id, m.message_id, m.chat_jid, m.sender_jid, m.sender_name, m.chat_type, m.message_type,
             m.text_content, m.caption, m.timestamp, ic.status AS image_context_status, ic.decision_reason, ic.analysis_source
      FROM messages m
      LEFT JOIN image_contexts ic ON ic.message_id = m.message_id
      {where_sql}
      ORDER BY COALESCE(m.timestamp, 0) DESC, m.id DESC
      LIMIT ? OFFSET ?
    '''
    query_params = params + [limit, offset]
    cur.execute(query, tuple(query_params))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows, total


def fetch_log_detail(message_row_id):
    conn = db()
    cur = conn.cursor()
    cur.execute('''
      SELECT m.*, mf.local_path, mf.mime_type, mf.original_url, mf.sha256, mf.file_size, mf.width, mf.height,
             ic.summary, ic.ocr_text, ic.tags_json, ic.status AS image_context_status, ic.error_text, ic.model_name, ic.confidence,
             ic.attempt_count, ic.locked_at, ic.worker_id, ic.analysis_source, ic.decision_reason
      FROM messages m
      LEFT JOIN media_files mf ON mf.message_id = m.message_id
      LEFT JOIN image_contexts ic ON ic.message_id = m.message_id
      WHERE m.id = ?
      LIMIT 1
    ''', (message_row_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_log_record(message_row_id):
    conn = db()
    cur = conn.cursor()
    cur.execute('SELECT message_id FROM messages WHERE id = ? LIMIT 1', (message_row_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, None

    message_id = row['message_id']
    cur.execute('DELETE FROM image_contexts WHERE message_id = ?', (message_id,))
    cur.execute('DELETE FROM media_files WHERE message_id = ?', (message_id,))
    cur.execute('DELETE FROM messages WHERE id = ?', (message_row_id,))
    conn.commit()
    conn.close()
    return True, message_id


def render_html(rows, total, search='', message_type='', chat_type='', limit=100, offset=0, time_range='', start_date='', end_date='', form_action='/loging-inbox', detail_base='', sender_filter='', group_filter=''):
    items = []
    for row in rows:
        sender = escape_html(row.get('sender_name') or row.get('sender_jid') or '-')
        chat_jid = escape_html(row.get('chat_jid') or '-')
        text_raw = row.get('text_content') or ''
        caption_raw = row.get('caption') or ''
        summary_text = text_raw or caption_raw or ''
        summary_text = summary_text.replace('\n', ' ').strip()
        if len(summary_text) > 180:
            summary_text = summary_text[:177] + '...'
        is_fallback_unknown = text_raw == '[unknown message payload from bridge]'
        summary = escape_html(summary_text or '-')
        msg_type = escape_html(row.get('message_type') or 'unknown')
        chat_badge = escape_html(row.get('chat_type') or 'unknown')
        image_status = escape_html(row.get('image_context_status') or 'n/a')
        decision_reason = escape_html(row.get('decision_reason') or '')
        analysis_source = escape_html(row.get('analysis_source') or '')
        fallback_badge = f"<span style='{badge_style('fallback')}'>{escape_html('fallback')}</span>" if is_fallback_unknown else ''

        items.append(f"""
        <div style='border:1px solid #ddd;padding:14px;border-radius:12px;margin-bottom:14px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.04)'>
          <div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap'>
            <div><b>{sender}</b> → <span style='color:#555'>{chat_jid}</span></div>
            <div>
              <span style='{badge_style(msg_type)}'>{msg_type}</span>
              <span style='{badge_style(chat_badge)}'>{chat_badge}</span>
              <span style='{badge_style(image_status)}'>{image_status}</span>
              {fallback_badge}
            </div>
          </div>
          <div style='margin-top:8px;color:#333'><b>Summary:</b> {summary}</div>
          <div style='margin-top:4px;color:#555'><b>Timestamp:</b> {escape_html(format_timestamp(row.get('timestamp')) or '-')}</div>
          <div style='margin-top:4px;color:#6b7280'><b>Decision:</b> {decision_reason or '-'} · <b>Source:</b> {analysis_source or '-'}</div>
          <div style='margin-top:10px'><a href='{escape_html(detail_base)}/message/{escape_html(str(row.get('id')))}' style='color:#2563eb;text-decoration:none'><b>Open detail →</b></a></div>
        </div>
        """)

    selected_msg_type = message_type or ''
    selected_chat_type = chat_type or ''
    selected_time_range = time_range or ''

    def selected(current, expected):
        return 'selected' if current == expected else ''

    page = (offset // limit) + 1 if limit else 1
    total_pages = max(1, ((total - 1) // limit) + 1) if limit else 1
    base_params = {
        'q': search,
        'message_type': message_type,
        'chat_type': chat_type,
        'sender': sender_filter,
        'group': group_filter,
        'limit': str(limit),
        'time_range': time_range,
        'start_date': start_date,
        'end_date': end_date,
    }

    prev_link = ''
    next_link = ''
    if offset > 0:
        prev_params = dict(base_params)
        prev_params['offset'] = str(max(0, offset - limit))
        prev_link = f"<a href='{escape_html(form_action)}?{escape_html(build_query(prev_params))}' style='padding:10px 16px;border-radius:8px;background:#e5e7eb;color:#111827;text-decoration:none'>← Prev</a>"
    if offset + limit < total:
        next_params = dict(base_params)
        next_params['offset'] = str(offset + limit)
        next_link = f"<a href='{escape_html(form_action)}?{escape_html(build_query(next_params))}' style='padding:10px 16px;border-radius:8px;background:#2563eb;color:#fff;text-decoration:none'>Next →</a>"

    return f"""
    <html>
      <head>
        <title>Loging Inbox Viewer</title>
      </head>
      <body style='font-family:sans-serif;max-width:1100px;margin:32px auto;background:#f8fafc;color:#111827'>
        <h1 style='margin-bottom:8px'>Loging Inbox Viewer</h1>
        <p style='margin-top:0;color:#4b5563'>Recent inbound messages from Whatsapp Engine.</p>

        <form method='get' action='{escape_html(form_action)}' style='display:flex;gap:10px;flex-wrap:wrap;background:#fff;padding:14px;border-radius:12px;border:1px solid #e5e7eb;margin-bottom:18px'>
          <input type='text' name='q' placeholder='Search sender, chat, text, caption...' value='{escape_html(search)}' style='flex:1;min-width:220px;padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
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
          <input type='text' name='sender' placeholder='Filter user/sender...' value='{escape_html(sender_filter)}' style='min-width:180px;padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
          <input type='text' name='group' placeholder='Filter group/chat...' value='{escape_html(group_filter)}' style='min-width:180px;padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
          <select name='time_range' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'>
            <option value='' {selected(selected_time_range, '')}>Any time</option>
            <option value='today' {selected(selected_time_range, 'today')}>Today</option>
            <option value='24h' {selected(selected_time_range, '24h')}>Last 24h</option>
            <option value='7d' {selected(selected_time_range, '7d')}>Last 7d</option>
            <option value='custom' {selected(selected_time_range, 'custom')}>Custom</option>
          </select>
          <input type='date' name='start_date' value='{escape_html(start_date)}' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
          <input type='date' name='end_date' value='{escape_html(end_date)}' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'/>
          <select name='limit' style='padding:10px;border:1px solid #d1d5db;border-radius:8px'>
            <option value='25' {selected(str(limit), '25')}>25</option>
            <option value='50' {selected(str(limit), '50')}>50</option>
            <option value='100' {selected(str(limit), '100')}>100</option>
            <option value='200' {selected(str(limit), '200')}>200</option>
          </select>
          <input type='hidden' name='offset' value='0'/>
          <button type='submit' style='padding:10px 16px;border:0;border-radius:8px;background:#2563eb;color:#fff'>Apply</button>
          <a href='{escape_html(form_action)}' style='padding:10px 16px;border-radius:8px;background:#e5e7eb;color:#111827;text-decoration:none'>Reset</a>
        </form>

        <div style='display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;background:#fff;padding:12px 14px;border-radius:12px;border:1px solid #e5e7eb;margin-bottom:16px'>
          <div><b>Total:</b> {total} logs · <b>Page:</b> {page}/{total_pages}</div>
          <div style='display:flex;gap:8px'>{prev_link}{next_link}</div>
        </div>

        {''.join(items) if items else '<div style="background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px">No logs found.</div>'}
      </body>
    </html>
    """


def render_detail_html(row, back_href='/loging-inbox'):
    if not row:
        return "<html><body><h1>Message not found</h1></body></html>"

    preview = ''
    local_path = row.get('local_path') or ''
    if local_path and os.path.exists(local_path):
        with open(local_path, 'rb') as file_obj:
            b64 = base64.b64encode(file_obj.read()).decode('ascii')
        mime = row.get('mime_type') or 'image/jpeg'
        preview = f'<div style="margin-top:12px"><img src="data:{escape_html(mime)};base64,{b64}" style="max-width:320px;border-radius:8px;border:1px solid #ddd"/></div>'

    raw_payload = escape_html(row.get('raw_payload_json') or '{}')
    return f"""
    <html>
      <head><title>Log Detail</title></head>
      <body style='font-family:sans-serif;max-width:1100px;margin:32px auto;background:#f8fafc;color:#111827'>
        <a href='{escape_html(back_href)}' style='color:#2563eb;text-decoration:none'>← Back to viewer</a>
        <h1>Message Detail</h1>
        <div style='background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px;margin-bottom:16px'>
          <div><b>ID:</b> {escape_html(str(row.get('id') or '-'))}</div>
          <div><b>Message ID:</b> {escape_html(row.get('message_id') or '-')}</div>
          <div><b>Sender:</b> {escape_html(row.get('sender_name') or row.get('sender_jid') or '-')}</div>
          <div><b>Chat:</b> {escape_html(row.get('chat_jid') or '-')}</div>
          <div><b>Type:</b> {escape_html(row.get('message_type') or '-')}</div>
          <div><b>Chat Type:</b> {escape_html(row.get('chat_type') or '-')}</div>
          <div><b>Timestamp:</b> {escape_html(format_timestamp(row.get('timestamp')) or '-')}</div>
          <div><b>Text:</b> {escape_html(row.get('text_content') or '-')}</div>
          <div><b>Caption:</b> {escape_html(row.get('caption') or '-')}</div>
          {preview}
        </div>
        <div style='background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px;margin-bottom:16px'>
          <h2>Media metadata</h2>
          <div><b>MIME:</b> {escape_html(row.get('mime_type') or '-')}</div>
          <div><b>Original URL:</b> {escape_html(row.get('original_url') or '-')}</div>
          <div><b>Local path:</b> {escape_html(row.get('local_path') or '-')}</div>
          <div><b>SHA256:</b> {escape_html(row.get('sha256') or '-')}</div>
          <div><b>File size:</b> {escape_html(str(row.get('file_size') or '-'))}</div>
          <div><b>Width:</b> {escape_html(str(row.get('width') or '-'))}</div>
          <div><b>Height:</b> {escape_html(str(row.get('height') or '-'))}</div>
        </div>
        <div style='background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px;margin-bottom:16px'>
          <h2>Processing status</h2>
          <div><b>Status:</b> {escape_html(row.get('image_context_status') or '-')}</div>
          <div><b>Decision:</b> {escape_html(row.get('decision_reason') or '-')}</div>
          <div><b>Source:</b> {escape_html(row.get('analysis_source') or '-')}</div>
          <div><b>Worker ID:</b> {escape_html(row.get('worker_id') or '-')}</div>
          <div><b>Locked at:</b> {escape_html(row.get('locked_at') or '-')}</div>
          <div><b>Attempt count:</b> {escape_html(str(row.get('attempt_count') or '0'))}</div>
          <div><b>Summary:</b> {escape_html(row.get('summary') or '-')}</div>
          <div><b>OCR:</b> {escape_html(row.get('ocr_text') or '-')}</div>
          <div><b>Tags:</b> {escape_html(row.get('tags_json') or '-')}</div>
          <div><b>Model:</b> {escape_html(row.get('model_name') or '-')}</div>
          <div><b>Confidence:</b> {escape_html(str(row.get('confidence') or '-'))}</div>
          <div><b>Error:</b> {escape_html(row.get('error_text') or '-')}</div>
        </div>
        <div style='background:#fff;border:1px solid #e5e7eb;padding:18px;border-radius:12px;margin-bottom:16px'>
          <h2>Raw payload JSON</h2>
          <pre style='white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:8px'>{raw_payload}</pre>
        </div>
        <div style='background:#fff3f2;border:1px solid #fecaca;padding:18px;border-radius:12px'>
          <h2 style='color:#991b1b;margin-top:0'>Danger zone</h2>
          <p style='color:#7f1d1d'>Delete this record from the database manually. Related <code>media_files</code> and <code>image_contexts</code> rows will also be removed. Physical media files are kept.</p>
          <form method='post' action='/loging-inbox/message/{escape_html(str(row.get('id') or '0'))}/delete' onsubmit="return confirm('Delete this record from database? This cannot be undone from the viewer.');">
            <button type='submit' style='padding:10px 16px;border:0;border-radius:8px;background:#dc2626;color:#fff;cursor:pointer'>Delete record</button>
          </form>
        </div>
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
            limit = clamp(parse_int((query.get('limit') or ['50'])[0], 50), 1, 500)
            offset = max(0, parse_int((query.get('offset') or ['0'])[0], 0))
            search = (query.get('q') or [''])[0].strip()
            message_type = (query.get('message_type') or [''])[0].strip()
            chat_type = (query.get('chat_type') or [''])[0].strip()
            sender_filter = (query.get('sender') or [''])[0].strip()
            group_filter = (query.get('group') or [''])[0].strip()
            time_range = (query.get('time_range') or [''])[0].strip()
            start_date = (query.get('start_date') or [''])[0].strip()
            end_date = (query.get('end_date') or [''])[0].strip()
            rows, total = fetch_logs(limit, offset, search, message_type, chat_type, time_range, start_date, end_date, sender_filter, group_filter)
            return write_json(self, 200, {
                'items': rows,
                'limit': limit,
                'offset': offset,
                'total': total,
            })
        if parsed.path == '/viewer':
            query = parse_qs(parsed.query)
            search = (query.get('q') or [''])[0].strip()
            message_type = (query.get('message_type') or [''])[0].strip()
            chat_type = (query.get('chat_type') or [''])[0].strip()
            sender_filter = (query.get('sender') or [''])[0].strip()
            group_filter = (query.get('group') or [''])[0].strip()
            time_range = (query.get('time_range') or [''])[0].strip()
            start_date = (query.get('start_date') or [''])[0].strip()
            end_date = (query.get('end_date') or [''])[0].strip()
            limit = clamp(parse_int((query.get('limit') or ['100'])[0], 100), 1, 500)
            offset = max(0, parse_int((query.get('offset') or ['0'])[0], 0))
            rows, total = fetch_logs(limit, offset, search, message_type, chat_type, time_range, start_date, end_date, sender_filter, group_filter)
            form_action = os.environ.get('VIEWER_FORM_ACTION', '/loging-inbox')
            detail_base = os.environ.get('VIEWER_DETAIL_BASE') or form_action
            body = render_html(rows, total, search, message_type, chat_type, limit, offset, time_range, start_date, end_date, form_action, detail_base, sender_filter, group_filter).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith('/message/') or parsed.path.startswith('/loging-inbox/message/'):
            prefix = '/loging-inbox/message/' if parsed.path.startswith('/loging-inbox/message/') else '/message/'
            message_row_id = parse_int(parsed.path.replace(prefix, ''), 0)
            row = fetch_log_detail(message_row_id)
            back_href = os.environ.get('VIEWER_FORM_ACTION', '/loging-inbox')
            body = render_detail_html(row, back_href).encode('utf-8')
            self.send_response(200 if row else 404)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return write_json(self, 404, {'error': 'Not found'})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith('/loging-inbox/message/') and parsed.path.endswith('/delete'):
            message_row_id = parse_int(parsed.path.replace('/loging-inbox/message/', '').replace('/delete', '').strip('/'), 0)
            ok, message_id = delete_log_record(message_row_id)
            if ok:
                self.send_response(303)
                self.send_header('Location', '/loging-inbox')
                self.end_headers()
                return
            return write_json(self, 404, {'error': 'Not found'})
        if parsed.path.startswith('/message/') and parsed.path.endswith('/delete'):
            message_row_id = parse_int(parsed.path.replace('/message/', '').replace('/delete', '').strip('/'), 0)
            ok, message_id = delete_log_record(message_row_id)
            if ok:
                self.send_response(303)
                self.send_header('Location', '/loging-inbox')
                self.end_headers()
                return
            return write_json(self, 404, {'error': 'Not found'})
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
