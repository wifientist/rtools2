from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import schemas
import crud.crud_proposals as crud
from dependencies import get_db, get_current_user

router = APIRouter(prefix="/proposals", tags=["Proposals"])

# # 🚀 Create Proposal
# @router.post("/new", response_model=schemas.ProposalResponse)
# def create_proposal(
#     proposal: schemas.ProposalCreate, 
#     db: Session = Depends(get_db),
#     current_user: schemas.UserResponse = Depends(get_current_user)
# ):
#     created_proposal = crud.create_proposal(db, proposal, user_id=current_user.id)
#     if not created_proposal:
#         return JSONResponse(content={"detail": "Proposal creation failed"}, status_code=400)
#     return created_proposal

# # 🚀 Get All Proposals
# @router.get("", response_model=list[schemas.ProposalResponse])
# def get_proposals(db: Session = Depends(get_db)):
#     return crud.get_proposals(db)

# # 🚀 Get Single Proposal
# @router.get("/{proposal_id}", response_model=schemas.ProposalResponse)
# def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
#     proposal = crud.get_proposal(db, proposal_id)
#     if proposal is None:
#         raise HTTPException(status_code=404, detail="Proposal not found")
#     return proposal
