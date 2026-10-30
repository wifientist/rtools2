"""
The customer's captive portal key, stored on the RuckusONE controller.

SZ's API never returns a hotspot portal's signature signing key, so a WISPr
network created from SZ would get no R1 integrationKey. The user saves the
customer's key once on the R1 EC controller the networks are created in
(encrypted, write-only), and WLAN Grabber and the SZ -> R1 migration read it
here, at the moment they create the network -- it is never copied into a
plan, job or cache.
"""

from typing import Optional


def controller_portal_key(controller_id: Optional[int]) -> Optional[str]:
    """The integration key saved on this R1 controller, or None."""
    if not controller_id:
        return None
    from database import SessionLocal
    from models.controller import Controller
    db = SessionLocal()
    try:
        controller = db.query(Controller).filter_by(id=controller_id).first()
        # EC only: a key is one customer's, and an MSP controller spans tenants.
        if controller is None or not controller.is_ec():
            return None
        return controller.get_portal_key() or None
    finally:
        db.close()


def controller_has_portal_key(controller_id: Optional[int]) -> bool:
    return bool(controller_portal_key(controller_id))


def tenant_portal_key(controller_id: Optional[int], tenant_id: Optional[str],
                      user_id: Optional[int]) -> Optional[str]:
    """
    The key this tool would put on networks in the controller's tenant: the
    controller's own when it is an EC; through an MSP controller, the key
    saved on the user's EC controller for that same R1 tenant.
    """
    key = controller_portal_key(controller_id)
    if key or not tenant_id or not user_id:
        return key
    from database import SessionLocal
    from models.controller import Controller
    db = SessionLocal()
    try:
        for ec in db.query(Controller).filter_by(user_id=user_id, controller_type="RuckusONE",
                                                 r1_tenant_id=tenant_id).all():
            if ec.is_ec() and ec.encrypted_portal_key:
                return ec.get_portal_key() or None
        return None
    finally:
        db.close()
