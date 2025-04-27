from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import crud.crud_proposals as crud
import schemas
from dependencies import get_db

router = APIRouter(prefix="/bids", tags=["Bids"])

# # 🚀 Create Bid
# @router.post("/new", response_model=schemas.BidResponse)
# def create_bid(bid: schemas.BidCreate, db: Session = Depends(get_db)):
#     return crud.create_bid(db, bid)

# # 🚀 Get Bids for a Proposal
# @router.get("/{proposal_id}", response_model=list[schemas.BidResponse])
# def get_bids_for_proposal(proposal_id: int, db: Session = Depends(get_db)):
#     return crud.get_bids_for_proposal(db, proposal_id)


# # 🚀 Get All Bids
# @router.get("/all", response_model=list[schemas.BidResponse])
# def get_all_bids(db: Session = Depends(get_db)):
#     return crud.get_all_bids(db)
