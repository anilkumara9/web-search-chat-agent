#!/usr/bin/env python3
"""Local-only web interface for the same Agent. No additional model tools."""
import argparse
import json
import os
import re
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent.core import Agent, professional_error
from agent.memory import Memory

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
TOKEN = secrets.token_urlsafe(32)
SESSION = re.compile(r'^[a-zA-Z0-9_-]{1,80}$')


def db_path():
    return os.environ.get('AGENT_DB', 'memory.db')


def user_id():
    return os.environ.get('AGENT_USER', 'local')


def configuration():
    return {'model': os.environ.get('LLM_MODEL', 'openai/gpt-oss-120b'),
            'model_configured': bool(os.environ.get('LLM_API_KEY')),
            'search_backend': 'Tavily' if os.environ.get('TAVILY_API_KEY') else 'Brave' if os.environ.get('BRAVE_API_KEY') else 'DuckDuckGo (best effort)',
            'search_configured': bool(os.environ.get('TAVILY_API_KEY') or os.environ.get('BRAVE_API_KEY')),
            'user': user_id(), 'streaming': False, 'token': TOKEN}


def history(session=None):
    m = Memory(db_path())
    try:
        if session:
            rows = m.db.execute('SELECT role,content,ts FROM turns WHERE user_id=? AND session=? ORDER BY id', (user_id(), session)).fetchall()
            return [{'role': r[0], 'content': professional_error(r[1]) if r[0] == 'assistant' and r[1].startswith('Sorry, the language model call failed (') else r[1], 'ts': r[2]} for r in rows]
        rows = m.db.execute('SELECT session,MIN(ts),MAX(ts),COUNT(*) FROM turns WHERE user_id=? GROUP BY session ORDER BY MAX(ts) DESC LIMIT 100', (user_id(),)).fetchall()
        out = []
        for sess, start, end, count in rows:
            first = m.db.execute("SELECT content FROM turns WHERE user_id=? AND session=? AND role='user' ORDER BY id LIMIT 1", (user_id(), sess)).fetchone()
            out.append({'session': sess, 'title': first[0][:100] if first else 'Conversation', 'started': start, 'updated': end, 'turns': count})
        return out
    finally:
        m.db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalChat/1.0'

    def log_message(self, fmt, *args):
        # Do not write question content or API credentials to access logs.
        pass

    def respond(self, status, body, kind='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {'error': 'Local host required.'})
        path = urlparse(self.path).path
        try:
            if path == '/api/config':
                return self.respond(200, configuration())
            if path == '/api/sessions':
                return self.respond(200, {'sessions': history()})
            if path.startswith('/api/session/'):
                session = path.rsplit('/', 1)[-1]
                if not SESSION.fullmatch(session):
                    return self.respond(400, {'error': 'Invalid session.'})
                return self.respond(200, {'messages': history(session)})
            static = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in static:
                name, kind = static[path]
                return self.respond(200, (ROOT / 'web' / name).read_bytes(), kind)
            return self.respond(404, {'error': 'Not found.'})
        except (sqlite3.Error, OSError):
            return self.respond(500, {'error': 'Cannot access local storage. Check AGENT_DB and directory permissions.'})

    def do_POST(self):
        if not self.allowed_host():
            return self.respond(403, {'error': 'Local host required.'})
        if self.headers.get('X-Local-Token') != TOKEN:
            return self.respond(403, {'error': 'Refresh the page before trying again.'})
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}:
            return self.respond(403, {'error': 'Cross-origin requests are not allowed.'})
        if self.path != '/api/chat':
            return self.respond(404, {'error': 'Not found.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000:
                return self.respond(413, {'error': 'Request too large or empty.'})
            data = json.loads(self.rfile.read(length))
            question = data.get('question', '')
            session = data.get('session', '')
            if not isinstance(question, str) or not question.strip() or len(question) > 6000:
                return self.respond(400, {'error': 'Enter a question of 1-6000 characters.'})
            if not isinstance(session, str) or not SESSION.fullmatch(session):
                return self.respond(400, {'error': 'Invalid session.'})
        except (ValueError, AttributeError):
            return self.respond(400, {'error': 'Invalid JSON request.'})
        if not os.environ.get('LLM_API_KEY'):
            return self.respond(503, {'error': 'Model key missing. Set LLM_API_KEY in the server terminal, restart, and refresh.'})
        if not LOCK.acquire(blocking=False):
            return self.respond(409, {'error': 'Another question is running. Wait for it to finish.'})
        m = None
        try:
            m = Memory(db_path())
            agent = Agent(user_id=user_id(), memory=m, session=session)
            answer = agent.ask(question.strip())
            return self.respond(200, {'answer': answer, 'stats': {'total_ms': agent.last_stats.get('total_ms'), 'tool_calls': len(agent.last_stats.get('searches', [])), 'error': bool(agent.last_stats.get('error'))}, 'session': session})
        except Exception:
            return self.respond(500, {'error': 'The request could not finish. Check local storage and provider configuration. Reload history before retrying to avoid duplicate questions.'})
        finally:
            if m:
                m.db.close()
            LOCK.release()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        ap.exit(1, f'Cannot open local port {args.port}: {exc}. Try --port 8765.\n')
    print(f'Web-search chat: http://127.0.0.1:{server.server_port}', flush=True)
    print('Local access only. Keys stay in the server environment. Ctrl-C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
