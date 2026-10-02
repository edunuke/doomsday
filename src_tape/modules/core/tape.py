"""
DECISION TAPE — append-only JSON Lines event log.

The tape is the *only* interface between the decision engine (walk-forward) and
every downstream consumer (metrics, execution, reporting, audit). It is
human-readable, streamable, versioned and deterministic.

Event types
-----------
HEADER        run metadata (first line)
REBALANCE     a rebalance decision (cause + pre-trade book stats)
ENTRY         a pair entered the book (audit view)
EXIT          a pair left the book (audit view)
PAIR_BOOK     per-pair target leg weights at a rebalance (execution view)
BOOK          full live target row, sparse {ticker: weight} (execution view)
GHOST_BOOK    sparse ghost-benchmark target row
PROMOTED_BOOK sparse promoted-benchmark target row
BARRIER       circuit-breaker / early-stop event
GATE          regime entry gate state

Sparse books: a BOOK carries only non-zero targets. A missing ticker means the
target is ZERO — the engine rebuilds the full row from scratch each rebalance,
so this is exact and keeps the tape compact.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Iterator, List, Optional, Set

import numpy as np
import pandas as pd

TAPE_VERSION = 1
_EVENT_TYPES = {
    "HEADER", "REBALANCE", "ENTRY", "EXIT", "PAIR_BOOK",
    "BOOK", "GHOST_BOOK", "PROMOTED_BOOK", "BARRIER", "GATE",
    "OVERRIDE_BOOK", "OVERRIDE_ENTRY", "OVERRIDE_EXIT",
}


def config_sha(params: dict) -> str:
    payload = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


class TapeWriter:
    """Append-only, schema-validated JSONL writer."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def header(self, meta: dict) -> None:
        self.event({**meta, "type": "HEADER"})

    def event(self, obj: dict) -> None:
        if obj.get("type") not in _EVENT_TYPES:
            raise ValueError(f"tape: unknown event type {obj.get('type')!r}")
        obj.setdefault("v", TAPE_VERSION)
        self._fh.write(json.dumps(obj, separators=(",", ":"), default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "TapeWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class TapeReader:
    """Typed iterator over a tape."""

    def __init__(self, path: str):
        self.path = path
        self._header: Optional[dict] = None

    def events(self, types: Optional[Set[str]] = None) -> Iterator[dict]:
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("type") == "HEADER" and self._header is None:
                    self._header = obj
                if types is None or obj.get("type") in types:
                    yield obj

    def header(self) -> dict:
        if self._header is None:
            for _ in self.events({"HEADER"}):
                break
        return self._header or {}


def lint(path: str) -> List[str]:
    """Validate tape invariants; returns a list of violations (empty = clean)."""
    r = TapeReader(path)
    problems: List[str] = []
    open_pairs: Dict[tuple, dict] = {}
    n_entries = n_exits = 0
    rebalance_ts: Optional[str] = None
    book_ts_seen = set()
    version = None

    for ev in r.events():
        if version is None:
            version = ev.get("v")
        t = ev.get("type")
        if t == "REBALANCE":
            rebalance_ts = str(ev.get("ts"))
            book_ts_seen = set()
        elif t == "BOOK":
            if rebalance_ts is not None and str(ev.get("ts")) != rebalance_ts:
                problems.append(f"BOOK ts {ev.get('ts')} != REBALANCE ts {rebalance_ts}")
            if rebalance_ts is not None:
                book_ts_seen.add(rebalance_ts)
        elif t == "ENTRY":
            n_entries += 1
            key = tuple(ev.get("pair", []))
            open_pairs[key] = ev
        elif t == "EXIT":
            n_exits += 1
            key = tuple(ev.get("pair", []))
            if key in open_pairs:
                del open_pairs[key]

    if version != TAPE_VERSION:
        problems.append(f"tape version {version} != {TAPE_VERSION}")
    # every ENTRY eventually EXITs (open at tape end is allowed)
    # (left open -> fine; duplicates would be a bug)
    return problems


def reconstruct_book(path: str, index: pd.DatetimeIndex, columns,
                     etype: str = "BOOK") -> pd.DataFrame:
    """Rebuild the full target matrix from sparse book events of type ``etype``.

    Non-emitted rows stay NaN (hold), exactly like the engine's sparse
    ``master_weights``.
    """
    columns = list(columns)
    books = {}
    for ev in TapeReader(path).events({etype}):
        ts = pd.Timestamp(ev["ts"])
        row = pd.Series(0.0, index=columns)
        for k, v in (ev.get("targets") or {}).items():
            if k in row.index:
                row[k] = float(v)
        books[ts] = row
    mat = pd.DataFrame(index=index, columns=columns, dtype=float)
    for ts, row in books.items():
        if ts in mat.index:
            mat.loc[ts] = row.values
    return mat


def reconstruct_stream(path: str, index: pd.DatetimeIndex, columns,
                       etype: str) -> pd.DataFrame:
    """Rebuild a held-weight matrix from change-only events of type ``etype``.

    Unlike :func:`reconstruct_book` (where each event is a FULL row and missing
    means zero), a stream event carries only the row at the moment it changes;
    the previous row is held forward until the next event. Used for the override
    sleeve, whose targets change on entry/exit rather than on rebalances.
    """
    columns = list(columns)
    mat = pd.DataFrame(np.nan, index=index, columns=columns, dtype=float)
    for ev in TapeReader(path).events({etype}):
        ts = pd.Timestamp(ev["ts"])
        if ts not in mat.index:
            continue
        row = pd.Series(0.0, index=columns)
        for k, v in (ev.get("targets") or {}).items():
            if k in row.index:
                row[k] = float(v)
        mat.loc[ts] = row.values
    return mat.ffill().fillna(0.0)


def reconstruct_pair_books(path: str, index: pd.DatetimeIndex, columns) -> Dict[tuple, pd.DataFrame]:
    """Rebuild per-pair target weight matrices from PAIR_BOOK events."""
    columns = list(columns)
    mats: Dict[tuple, pd.DataFrame] = {}
    for ev in TapeReader(path).events({"PAIR_BOOK"}):
        pair = tuple(ev["pair"])
        ts = pd.Timestamp(ev["ts"])
        if pair not in mats:
            mats[pair] = pd.DataFrame(np.nan, index=index, columns=pair, dtype=float)
        w = ev.get("weights") or {}
        for k, v in w.items():
            if k in mats[pair].columns:
                mats[pair].loc[ts, k] = float(v)
    return mats
