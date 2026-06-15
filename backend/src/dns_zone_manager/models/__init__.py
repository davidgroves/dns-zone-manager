"""Pydantic models for DNS API."""

from dns_zone_manager.models.records import RRsetData
from dns_zone_manager.models.requests import (
    AddRRsetRequest,
    DeleteRRsetRequest,
    HistoryBatchResponse,
    HistoryChangeResponse,
    ReplaceRRsetRequest,
    RollbackPreviewResponse,
    RollbackRequest,
    RollbackResponse,
    RRsetResponse,
    ZoneHistoryResponse,
    ZoneResponse,
)

__all__ = [
    "RRsetData",
    "AddRRsetRequest",
    "DeleteRRsetRequest",
    "ReplaceRRsetRequest",
    "RRsetResponse",
    "ZoneResponse",
    "HistoryChangeResponse",
    "HistoryBatchResponse",
    "ZoneHistoryResponse",
    "RollbackRequest",
    "RollbackPreviewResponse",
    "RollbackResponse",
]
