"""The admin audit log (audit_store.py): read it, export it, check its hash chain.
Admin only. Filters: actor (part of their username), action (exact, or every
action under "member." etc.), from / to (Unix seconds)."""

import csv
import io
import json
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

import audit_store
from kb.audit import audited
from kb.deps import require_admin, ws_of

router = APIRouter()

COLUMNS = ("id", "at", "workspace", "actor", "actor_type", "action", "target_type",
           "target_id", "ip", "user_agent", "details", "prev_hash", "hash")
FORMULA = ("=", "+", "-", "@", "\t", "\r")       # a spreadsheet would run these


class Filters:
    def __init__(self, actor: str = Query("", max_length=200),
                 action: str = Query("", max_length=100),
                 since: float | None = Query(None, alias="from"),
                 until: float | None = Query(None, alias="to")):
        self.kw = {"actor": actor.strip() or None, "action": action.strip() or None,
                   "since": since, "until": until}


@router.get("/api/admin/audit")
def list_audit(filters: Filters = Depends(), cursor: int | None = None,
               limit: int = Query(audit_store.PAGE, ge=1, le=audit_store.MAX_PAGE),
               user=Depends(require_admin)):
    """A page of events, newest first. Pass next_cursor back as cursor for the next."""
    events, next_cursor = audit_store.list_events(ws_of(user), cursor=cursor, limit=limit,
                                                  **filters.kw)
    return {"events": events, "next_cursor": next_cursor}


def _cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA) else text


def _csv(events):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    yield out.getvalue()
    for e in events:
        out.seek(0)
        out.truncate()
        row = {**e, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e["at"])),
               "details": json.dumps(e["details"], sort_keys=True)}
        writer.writerow([_cell(row[c]) for c in COLUMNS])
        yield out.getvalue()


def _jsonl(events):
    for e in events:
        yield json.dumps({c: e[c] for c in COLUMNS}) + "\n"


@router.get("/api/admin/audit/export")
def export_audit(filters: Filters = Depends(),
                 format: str = Query("csv", pattern=r"^(csv|jsonl)$"),
                 user=Depends(require_admin), audit=audited("audit.export")):
    """Every matching event, oldest first, streamed. jsonl keeps each row exactly
    as hashed (so the chain can be checked from it); csv is for spreadsheets
    (a cell that would start a formula gets a leading ')."""
    ws = ws_of(user)
    audit.record("audit", ws, {"format": format,
                               **{k: v for k, v in filters.kw.items() if v is not None}},
                 user=user)
    events = audit_store.iter_events(ws, **filters.kw)
    name = f"audit-{ws}-{time.strftime('%Y%m%d', time.gmtime())}.{format}"
    return StreamingResponse(
        _csv(events) if format == "csv" else _jsonl(events),
        media_type="text/csv; charset=utf-8" if format == "csv" else "application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/admin/audit/verify")
def verify_audit(user=Depends(require_admin)):
    """Check the workspace's hash chain: {ok, count, head, broken_at}. Keep `head`
    somewhere else to tell later if rows were cut off the end."""
    return audit_store.verify(ws_of(user))
