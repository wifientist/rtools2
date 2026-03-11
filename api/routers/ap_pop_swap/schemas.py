"""
Pydantic schemas for Pop and Swap AP replacement tool.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum


class CleanupAction(str, Enum):
    NONE = "none"
    UNASSIGN = "unassign"
    REMOVE = "remove"


class SwapMapping(BaseModel):
    """A single old→new AP swap pair."""
    old_serial: str = Field(description="Serial number of the AP being replaced")
    new_serial: str = Field(description="Serial number of the replacement AP")


class PopSwapOptions(BaseModel):
    """Options for a Pop and Swap job."""
    copy_name: bool = Field(default=True, description="Copy old AP's name to new AP")
    cleanup_action: CleanupAction = Field(default=CleanupAction.NONE, description="What to do with the old AP after swap")


class PopSwapPreviewRequest(BaseModel):
    """Request body for preview endpoint."""
    mappings: List[SwapMapping] = Field(description="List of old→new AP swap pairs")
    options: PopSwapOptions = Field(default_factory=PopSwapOptions)


class PopSwapApplyRequest(BaseModel):
    """Request body for apply endpoint."""
    mappings: List[SwapMapping] = Field(description="List of old→new AP swap pairs")
    options: PopSwapOptions = Field(default_factory=PopSwapOptions)


class SwapPairPreview(BaseModel):
    """Preview result for a single swap pair."""
    old_serial: str
    new_serial: str
    old_ap_name: Optional[str] = None
    old_ap_group_id: Optional[str] = None
    old_ap_group_name: Optional[str] = None
    old_ap_model: Optional[str] = None
    old_ap_status: Optional[str] = None
    settings_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    valid: bool = True


class PopSwapPreviewResponse(BaseModel):
    """Response from preview endpoint."""
    pairs: List[SwapPairPreview]
    total_valid: int
    total_invalid: int
    warnings: List[str] = Field(default_factory=list)


class SwapRecordSummary(BaseModel):
    """Summary view of a swap record for the pending swaps dashboard."""
    swap_id: str
    controller_id: int
    venue_id: str
    old_serial: str
    new_serial: str
    ap_name: Optional[str] = None
    ap_group_id: Optional[str] = None
    ap_group_name: Optional[str] = None
    status: str
    created_at: str
    expires_at: str
    sync_attempts: int = 0
    last_attempt_at: Optional[str] = None
    applied_at: Optional[str] = None
    cleanup_action: str = "none"


class SwapRecordDetail(SwapRecordSummary):
    """Full swap record including config snapshot and apply results."""
    config_data: Optional[Dict[str, Any]] = None
    apply_results: Optional[Dict[str, str]] = None


class SyncNowResponse(BaseModel):
    """Response from the sync-now endpoint."""
    swap_id: str
    status: str
    message: str
    apply_results: Optional[Dict[str, str]] = None


class ExtendResponse(BaseModel):
    """Response from the extend endpoint."""
    swap_id: str
    new_expires_at: str
    message: str
