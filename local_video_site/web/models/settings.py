from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SettingUpdate(BaseModel):
    value: Any
    expected_version: int = Field(default=0, ge=0)
    note: str = ""


class SettingReset(BaseModel):
    expected_version: int = Field(ge=1)
    note: str = ""


class DeleteVerificationUpdate(BaseModel):
    current_password: str = ""
    new_password: str
    confirmation: str
    note: str = ""
