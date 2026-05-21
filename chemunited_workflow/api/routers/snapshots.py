"""Routes: GET/POST/DELETE /snapshots."""

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_protocol_service
from ..schemas import SnapshotIn
from ..services.protocol import ProtocolService

read_router = APIRouter(prefix="/snapshots", tags=["snapshots"])
write_router = APIRouter(prefix="/snapshots", tags=["snapshots"])


@read_router.get("/")
async def list_snapshots(svc: ProtocolService = Depends(get_protocol_service)):
    """List all protocol snapshots.

    Returns metadata (filename, last-modified timestamp, size) for every JSON
    file in `protocols_hystoric/`, sorted most-recent first. Use the filename
    from this list to start a run or inspect a snapshot's contents.
    """
    return svc.list_snapshots()


@read_router.get("/{filename}")
async def get_snapshot(
    filename: str,
    svc: ProtocolService = Depends(get_protocol_service),
):
    """Read a single snapshot by filename.

    Returns the full JSON content of the snapshot — `main_parameter` plus one
    key per process step in `{process_name}_{index}` format. The insertion
    order of the process keys defines the execution sequence.
    """
    try:
        return svc.read_snapshot(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@write_router.post("/", status_code=201)
async def create_snapshot(
    body: SnapshotIn,
    svc: ProtocolService = Depends(get_protocol_service),
):
    """Save a new protocol snapshot.

    Validates all process configs and the `main_parameter` block, then writes
    a timestamped JSON file to `protocols_hystoric/`. Every call creates a
    **new** file — snapshots are immutable once written. Returns the generated
    filename, e.g. `suzuki_batch_14_2026-05-15T10-38-00.json`.
    """
    try:
        filename = svc.write_snapshot(body.name, body.data)
        return {"filename": filename}
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@write_router.delete("/{filename}", status_code=204)
async def delete_snapshot(
    filename: str,
    svc: ProtocolService = Depends(get_protocol_service),
):
    """Delete a snapshot file.

    Permanently removes the file from `protocols_hystoric/`. This action is
    irreversible. Only available in builder mode (`enable_builder=True`).
    """
    try:
        svc.delete_snapshot(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
