import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserBase(BaseModel):
    email: EmailStr

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, v: EmailStr) -> str:
        return str(v).strip().lower()


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128, description="User password")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v.strip()) < 8:
            raise ValueError("Password must not be whitespace only or shorter than 8 characters")
        return v


class UserLogin(UserBase):
    password: str = Field(..., min_length=1, max_length=128)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
