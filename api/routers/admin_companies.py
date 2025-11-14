from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from models.user import User, RoleEnum
from models.company import Company
from dependencies import get_db, get_current_user
from decorators import require_role
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/admin/companies", tags=["Admin - Companies"])


# Pydantic schemas
class CompanyListResponse(BaseModel):
    id: int
    name: str
    domain: str
    is_approved: bool
    created_at: datetime | None
    user_count: int

    class Config:
        from_attributes = True


class CompanyCreateRequest(BaseModel):
    name: str
    domain: str
    is_approved: bool = True  # Manually created companies are approved by default


class CompanyUpdateRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    is_approved: bool | None = None


# List all companies (admin only)
@router.get("", response_model=List[CompanyListResponse])
@require_role(RoleEnum.admin)
def list_all_companies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all companies with their approval status and user count.
    Requires admin role.
    """
    companies = db.query(Company).all()

    result = []
    for company in companies:
        result.append(CompanyListResponse(
            id=company.id,
            name=company.name,
            domain=company.domain,
            is_approved=company.is_approved,
            created_at=company.created_at,
            user_count=len(company.users)
        ))

    return result


# Get specific company details (admin only)
@router.get("/{company_id}", response_model=CompanyListResponse)
@require_role(RoleEnum.admin)
def get_company(
    company_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get details of a specific company.
    Requires admin role.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    return CompanyListResponse(
        id=company.id,
        name=company.name,
        domain=company.domain,
        is_approved=company.is_approved,
        created_at=company.created_at,
        user_count=len(company.users)
    )


# Create a new company (admin only)
@router.post("", response_model=CompanyListResponse, status_code=status.HTTP_201_CREATED)
@require_role(RoleEnum.admin)
def create_company(
    company_data: CompanyCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Manually create a new company (approved by default).
    Requires admin role.
    """
    # Check if domain already exists
    existing = db.query(Company).filter(Company.domain == company_data.domain).first()
    if existing:
        raise HTTPException(status_code=400, detail="Company with this domain already exists")

    # Check if name already exists
    existing_name = db.query(Company).filter(Company.name == company_data.name).first()
    if existing_name:
        raise HTTPException(status_code=400, detail="Company with this name already exists")

    new_company = Company(
        name=company_data.name,
        domain=company_data.domain,
        is_approved=company_data.is_approved
    )
    db.add(new_company)
    db.commit()
    db.refresh(new_company)

    return CompanyListResponse(
        id=new_company.id,
        name=new_company.name,
        domain=new_company.domain,
        is_approved=new_company.is_approved,
        created_at=new_company.created_at,
        user_count=0
    )


# Update company (admin only)
@router.patch("/{company_id}", response_model=CompanyListResponse)
@require_role(RoleEnum.admin)
def update_company(
    company_id: int,
    company_data: CompanyUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update company details including approval status.
    Requires admin role.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Prevent modifying the Unassigned company's approval status
    if company.id == -1 and company_data.is_approved is False:
        raise HTTPException(
            status_code=400,
            detail="Cannot unapprove the 'Unassigned' company"
        )

    # Update fields if provided
    if company_data.name is not None:
        # Check name uniqueness
        existing = db.query(Company).filter(
            Company.name == company_data.name,
            Company.id != company_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Company name already exists")
        company.name = company_data.name

    if company_data.domain is not None:
        # Check domain uniqueness
        existing = db.query(Company).filter(
            Company.domain == company_data.domain,
            Company.id != company_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Company domain already exists")
        company.domain = company_data.domain

    if company_data.is_approved is not None:
        company.is_approved = company_data.is_approved

    db.commit()
    db.refresh(company)

    return CompanyListResponse(
        id=company.id,
        name=company.name,
        domain=company.domain,
        is_approved=company.is_approved,
        created_at=company.created_at,
        user_count=len(company.users)
    )


# Approve a company (admin only)
@router.post("/{company_id}/approve", response_model=CompanyListResponse)
@require_role(RoleEnum.admin)
def approve_company(
    company_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Approve a company to allow user signups from this domain.
    Requires admin role.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    company.is_approved = True
    db.commit()
    db.refresh(company)

    return CompanyListResponse(
        id=company.id,
        name=company.name,
        domain=company.domain,
        is_approved=company.is_approved,
        created_at=company.created_at,
        user_count=len(company.users)
    )


# Unapprove a company (admin only)
@router.post("/{company_id}/unapprove", response_model=CompanyListResponse)
@require_role(RoleEnum.admin)
def unapprove_company(
    company_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Revoke approval for a company. Existing users remain but new signups are blocked.
    Requires admin role.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Prevent unapproving the Unassigned company
    if company.id == -1:
        raise HTTPException(
            status_code=400,
            detail="Cannot unapprove the 'Unassigned' company"
        )

    company.is_approved = False
    db.commit()
    db.refresh(company)

    return CompanyListResponse(
        id=company.id,
        name=company.name,
        domain=company.domain,
        is_approved=company.is_approved,
        created_at=company.created_at,
        user_count=len(company.users)
    )


# Delete a company (admin only) - with safety checks
@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.admin)
def delete_company(
    company_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a company. Only allowed if no users are associated with it.
    Requires admin role.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Prevent deleting the Unassigned company
    if company.id == -1:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the 'Unassigned' company"
        )

    # Check if company has users
    if len(company.users) > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete company with {len(company.users)} users. Reassign users first."
        )

    db.delete(company)
    db.commit()

    return None
