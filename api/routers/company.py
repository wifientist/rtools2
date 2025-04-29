from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models.user import User
from models.company import Company
from schemas.auth import CompanyCreate, CompanyResponse
from dependencies import get_db, get_current_user
from security import require_same_company

router = APIRouter(prefix="/companies", tags=["Companies"])

# 🚀 Create a new company
@router.post("/new", response_model=CompanyResponse)
def create_company(company_data: CompanyCreate, db: Session = Depends(get_db)):
    existing_company = db.query(Company).filter(Company.name == company_data.name).first()
    if existing_company:
        raise HTTPException(status_code=400, detail="Company name already exists")

    new_company = Company(name=company_data.name)
    db.add(new_company)
    db.commit()
    db.refresh(new_company)
    return new_company

# 🏢 Get the authenticated user's company details
@router.get("/my", response_model=CompanyResponse)
def get_my_company(user: User = Depends(get_current_user)):
    if not user.company:
        raise HTTPException(status_code=404, detail="User is not associated with any company")
    return user.company

@router.get("/data")
def get_company_data(user: User = Depends(require_same_company)):
    return {"message": f"Data for company: {user.company.name}"}

