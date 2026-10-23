"""Read-only loopback HTTP API serving persisted snapshots to TradeFlipper Overview (v0.3).

GET only, bound to loopback, no authentication: never expose beyond 127.0.0.1/::1. It reads
files written by the scheduled agent; it cannot trigger scans, orders or configuration changes.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .opportunity import to_overview_payload
from .snapshot_store import ID_RE, SnapshotStore

LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
SNAP_PATH = re.compile(r'^/v1/opportunities/snapshots/([^/]+)$')


def make_server(store: SnapshotStore, host: str = '127.0.0.1', port: int = 8766) -> ThreadingHTTPServer:
    if host not in LOOPBACK:
        raise ValueError('Overview API is loopback-only (no authentication); refusing host %r' % host)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'TradeFlipperOverview/1'

        def _send(self, code: int, body: dict) -> None:
            raw = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            path = self.path.split('?', 1)[0].rstrip('/')
            try:
                if path == '/healthz':
                    return self._send(200, {'ok': True, 'read_only': True})
                if path == '/v1/opportunities/latest':
                    return self._send(200, to_overview_payload(store.latest()))
                if path == '/v1/opportunities/snapshots':
                    return self._send(200, {'snapshot_ids': store.ids()[:50]})
                m = SNAP_PATH.fullmatch(path)
                if m:
                    if not ID_RE.fullmatch(m.group(1)):
                        return self._send(400, {'error': 'invalid snapshot id'})
                    try:
                        return self._send(200, store.load(m.group(1)))
                    except FileNotFoundError:
                        return self._send(404, {'error': 'snapshot not found'})
                self._send(404, {'error': 'not found'})
            except (ValueError, OSError) as exc:
                self._send(500, {'error': f'{type(exc).__name__}'})

        def _deny(self):
            self._send(405, {'error': 'read-only API: GET only'})

        do_POST = do_PUT = do_PATCH = do_DELETE = _deny  # noqa: N815

        def log_message(self, *args):  # keep request lines out of stderr
            pass

    return ThreadingHTTPServer((host, port), Handler)
