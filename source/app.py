"""ReSync localhost web UI. All XML and media processing occurs on this computer."""
from __future__ import annotations
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from engine import Project, report_csv, sync

ROOT = Path(__file__).parent
LOCK = threading.RLock()
STATE = {'project': None, 'filename': 'sequencia', 'result': None,
         'busy': False, 'percent': 0, 'message': 'Pronto para importar o XML',
         'events': [], 'error': ''}


def snapshot():
    with LOCK:
        return {k: STATE[k] for k in ('busy','percent','message','events','error','result')}


def progress(pct, msg):
    with LOCK:
        if pct >= 0:
            STATE['percent'] = pct
        STATE['message'] = msg
        if not STATE['events'] or STATE['events'][-1] != msg:
            STATE['events'].append(msg)
            STATE['events'] = STATE['events'][-60:]


def process(reference, media_folder):
    try:
        with LOCK:
            project = STATE['project']
        result = sync(project, reference, media_folder, progress=progress)
        with LOCK:
            STATE['result'] = result
    except Exception as e:
        with LOCK:
            STATE['error'] = f'{type(e).__name__}: {e}'
    finally:
        with LOCK:
            STATE['busy'] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data, content_type='application/json; charset=utf-8', filename=None):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Access-Control-Allow-Origin', 'http://127.0.0.1:' + str(self.server.server_port))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/':
            self.reply(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif route == '/api/status':
            self.reply(200, snapshot())
        elif route in ('/api/download', '/api/csv'):
            with LOCK:
                if STATE['busy'] or STATE['result'] is None:
                    return self.reply(409, {'error': 'Sincronize antes de exportar.'})
                if route == '/api/download':
                    content = STATE['project'].to_xml()
                    filename = STATE['filename'] + '_ReSync.xml'
                    mime = 'application/xml; charset=utf-8'
                else:
                    content = report_csv(STATE['result'])
                    filename = STATE['filename'] + '_relatorio.csv'
                    mime = 'text/csv; charset=utf-8'
            self.reply(200, content, mime, filename)
        else:
            self.reply(404, {'error': 'Não encontrado.'})

    def do_POST(self):
        route = urlsplit(self.path).path
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 30_000_000:
                return self.reply(413, {'error': 'XML vazio ou maior que 30 MB.'})
            raw = self.rfile.read(size)
            if route == '/api/import':
                with LOCK:
                    if STATE['busy']:
                        return self.reply(409, {'error': 'Sincronização em andamento.'})
                project = Project(raw)
                with LOCK:
                    STATE.update(project=project, filename=re.sub(r'[^A-Za-z0-9_.-]', '_', unquote(self.headers.get('X-Project-Name', 'sequencia')))[:80], result=None, percent=0,
                                 message='XML importado', busy=False, events=[], error='')
                summary=project.summary()
                summary['media_found']=sum(os.path.isfile(g.path) for g in project.groups)
                summary['media_total']=len(project.groups)
                return self.reply(200, summary)
            if route == '/api/sync':
                values = json.loads(raw)
                with LOCK:
                    if STATE['busy']:
                        return self.reply(409, {'error': 'Já existe uma sincronização em andamento.'})
                    if STATE['project'] is None:
                        return self.reply(400, {'error': 'Importe um XML primeiro.'})
                    STATE.update(busy=True, result=None, percent=0, events=[], error='',
                                 message='Preparando análise...')
                reference = str(values.get('reference', 'auto'))
                media_folder = str(values.get('media_folder', '')).strip()
                threading.Thread(target=process, args=(reference, media_folder), daemon=True).start()
                return self.reply(200, {'ok': True})
            return self.reply(404, {'error': 'Não encontrado.'})
        except Exception as e:
            return self.reply(400, {'error': f'{type(e).__name__}: {e}'})


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    print('ReSync — abra:', url, flush=True)
    if os.environ.get('RESYNC_EMBEDDED') != '1':
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nFinalizado.')
    finally:
        server.server_close()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--resync-job':
        from sync_job import run_job
        sys.exit(run_job(sys.argv[2]))
    main()
