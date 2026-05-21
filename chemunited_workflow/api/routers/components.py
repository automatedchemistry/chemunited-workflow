"""Routes: GET /components."""

from fastapi import APIRouter, Depends

from ..dependencies import get_protocol_service
from ..schemas import ComponentStatus
from ..services.protocol import ProtocolService

router = APIRouter(prefix="/components", tags=["components"])


@router.get("/ping", response_model=list[ComponentStatus])
async def ping_components(
    timeout: float = 2.0,
    svc: ProtocolService = Depends(get_protocol_service),
):
    """Verify that all device URLs in ``associations.json`` are reachable."""
    return svc.ping_components(timeout=timeout)


@router.get("/")
async def get_components(svc: ProtocolService = Depends(get_protocol_service)):
    """Return the device connectivity map.

    Returns the full contents of `connectivity/associations.json` — the
    mapping of component names to their device-server URLs. Entries with an
    empty `component_url` are included as-is; they represent devices that are
    physically present but not yet wired to a server endpoint.
    """
    return svc.read_components()
