from fastapi import APIRouter

router = APIRouter(prefix="", tags=["Status"])

@router.get("/status")
def status():
    return {"status": "ok"}
