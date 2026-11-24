from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from local_video_site.identity.permissions.account import ROLE_USER


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = ROLE_USER
    must_change_password: bool = True


class UserUpdate(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserPasswordReset(BaseModel):
    password: str
    must_change_password: bool = True


class OwnPasswordChange(BaseModel):
    current_password: str
    new_password: str
