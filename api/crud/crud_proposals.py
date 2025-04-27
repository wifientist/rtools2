from sqlalchemy.orm import Session
import models, schemas
from datetime import datetime


# 🚀 Create Proposal
def create_proposal(db: Session, proposal: schemas.ProposalCreate, user_id: int):
    try:
        db_proposal = models.Proposal(
            title=proposal.title,
            description=proposal.description,
            budget=proposal.budget,
            location=proposal.location,
            deadline=proposal.deadline,
            created_by=user_id,
        )
        db.add(db_proposal)
        db.commit()
        db.refresh(db_proposal)
        return db_proposal
    except Exception as e:
        db.rollback()
        print(f"Error creating proposal: {e}")
        return None


# 🚀 Get All Proposals
def get_proposals(db: Session):
    return db.query(models.Proposal).all()

# 🚀 Get Single Proposal
def get_proposal(db: Session, proposal_id: int):
    return db.query(models.Proposal).filter(models.Proposal.id == proposal_id).first()



# 🚀 Create a Bid
def create_bid(db: Session, bid: schemas.BidCreate):
    #submitted_at=datetime.fromisoformat(bid.submitted_at.replace("Z", "")),  # Convert ISO string to datetime
    db_bid = models.Bid(**bid.dict()) #, submitted_at=submitted_at)
    db.add(db_bid)
    db.commit()
    db.refresh(db_bid)
    return db_bid

# 🚀 Get Bids for a Proposal
def get_bids_for_proposal(db: Session, proposal_id: int):
    return db.query(models.Bid).filter(models.Bid.proposal_id == proposal_id).all()

# 🚀 Get All Bids
def get_all_bids(db: Session):
    return db.query(models.Bid).all()