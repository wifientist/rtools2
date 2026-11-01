from sqlalchemy import Column, Integer, String, ForeignKey, Float, DateTime, Enum, Boolean, true
from sqlalchemy.orm import relationship
import datetime
from database import Base
from utils.encryption import encrypt_value, decrypt_value
import enum


class RoleEnum(str, enum.Enum):
    user = "user"
    admin = "admin"
    super = "super"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # hashed_password = Column(String, nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.user, nullable=False)  # Default role is "user"

    otp_code = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    last_authenticated_at = Column(DateTime, nullable=True)

    # Feature flags
    beta_enabled = Column(Boolean, default=False, nullable=False)
    alpha_enabled = Column(Boolean, default=False, nullable=False)
    # Per-user access to the nuclear "Danger Zone" cleanup. Granted only by
    # super admins; independent of role and of beta/alpha.
    danger_enabled = Column(Boolean, default=False, nullable=False)

    # Slack job notifications. The webhook URL is a credential: anyone holding
    # it can post to the channel, so it is encrypted and never returned.
    encrypted_slack_webhook = Column(String, nullable=True)
    slack_notify_completed = Column(Boolean, default=True, server_default=true(), nullable=False)
    slack_notify_failed = Column(Boolean, default=True, server_default=true(), nullable=False)
    slack_notify_confirmation = Column(Boolean, default=True, server_default=true(), nullable=False)

    # 🔹 Add ForeignKey to link Users to Companies
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)

    # 🔹 Track the active controller (RuckusONE or SmartZone)
    active_controller_id = Column(Integer, ForeignKey("controllers.id"), nullable=True)
    secondary_controller_id = Column(Integer, ForeignKey("controllers.id"), nullable=True)

    # Relationships
    #proposals = relationship("Proposal", back_populates="creator", cascade="all, delete")
    #bids = relationship("Bid", back_populates="bidder", cascade="all, delete")
    company = relationship("Company", back_populates="users")
    controllers = relationship("Controller", back_populates="user", cascade="all, delete", foreign_keys="[Controller.user_id]")
    active_controller = relationship("Controller", foreign_keys=[active_controller_id], post_update=True)
    secondary_controller = relationship("Controller", foreign_keys=[secondary_controller_id], post_update=True)

    def set_slack_webhook(self, raw_value: str):
        """Encrypt and store the Slack webhook URL"""
        self.encrypted_slack_webhook = encrypt_value(raw_value)

    def get_slack_webhook(self) -> str:
        """Decrypt and return the Slack webhook URL"""
        if not self.encrypted_slack_webhook:
            return ""
        return decrypt_value(self.encrypted_slack_webhook)

