"""Dependency stubs — overridden by create_api() at startup via dependency_overrides."""

from .services.protocol import ProtocolService
from .services.runner import RunnerService


def get_protocol_service() -> ProtocolService:
    raise NotImplementedError("Dependency not wired — was create_api() called?")


def get_runner_service() -> RunnerService:
    raise NotImplementedError("Dependency not wired — was create_api() called?")
