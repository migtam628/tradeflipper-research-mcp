"""Local, atomic persistence for research snapshots (v0.3). Stdlib only; never stores secrets.

Files live OUTSIDE the repository by default (``TRADEFLIPPER_OPPORTUNITY_DIR`` or
``~/.local/share/tradeflipper-research/snapshots``) and are written 0600 in a 0700 directory.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .opportunity import validate_snapshot

ID_RE = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{10}$')
FILE_RE = re.compile(r'^snapshot_(\d{8}T\d{6}Z-[0-9a-f]{10})\.json$')


def default_dir() -> Path:
    configured = os.environ.get('TRADEFLIPPER_OPPORTUNITY_DIR')
    return Path(configured).expanduser() if configured else Path.home() / '.local/share/tradeflipper-research/snapshots'


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=1, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class SnapshotStore:
    def __init__(self, directory: Path | str | None = None):
        self.dir = Path(directory).expanduser() if directory else default_dir()

    def _ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, snapshot: dict, *, keep: int = 200) -> Path:
        validate_snapshot(snapshot)
        if not ID_RE.fullmatch(snapshot['snapshot_id']):
            raise ValueError('invalid snapshot_id')
        self._ensure()
        path = self.dir / f'snapshot_{snapshot["snapshot_id"]}.json'
        _atomic_write(path, snapshot)
        _atomic_write(self.dir / 'latest.json', snapshot)
        self.prune(keep)
        return path

    def ids(self) -> list[str]:
        """Snapshot ids, newest first (ids sort chronologically)."""
        if not self.dir.is_dir():
            return []
        found = [m.group(1) for p in self.dir.iterdir() if (m := FILE_RE.fullmatch(p.name))]
        return sorted(found, reverse=True)

    def load(self, snapshot_id: str) -> dict:
        if not ID_RE.fullmatch(snapshot_id or ''):
            raise ValueError('invalid snapshot_id')
        return self._read(self.dir / f'snapshot_{snapshot_id}.json')

    def latest(self) -> dict | None:
        ids = self.ids()
        return self.load(ids[0]) if ids else None

    def _read(self, path: Path) -> dict:
        snap = json.loads(path.read_text(encoding='utf-8'))
        validate_snapshot(snap)
        return snap

    def prune(self, keep: int) -> int:
        removed = 0
        for sid in self.ids()[max(1, keep):]:
            (self.dir / f'snapshot_{sid}.json').unlink(missing_ok=True)
            removed += 1
        return removed

    # Scheduler state (universe rotation only).
    def load_state(self) -> dict:
        try:
            data = json.loads((self.dir / 'state.json').read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, state: dict) -> None:
        self._ensure()
        _atomic_write(self.dir / 'state.json', state)
