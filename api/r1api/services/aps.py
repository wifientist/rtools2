import logging

logger = logging.getLogger(__name__)


class ApService:
    """
    Placeholder: R1Client still builds `client.aps`. AP calls live in
    VenueService. get_ap_overrides (GET /apconfig) was removed 2026-11-11:
    R1 answers 404, it was never a real route, and nothing called it.
    """

    def __init__(self, client):
        self.client = client  # back-reference to main R1Client
