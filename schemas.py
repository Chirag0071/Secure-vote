import re
from typing import List, Optional
from pydantic import BaseModel, field_validator

# UP Vidhan Sabha voter ID format, e.g. UP/23/142/0001234
VOTER_ID_PATTERN = re.compile(r"^UP/\d{2}/\d{3}/\d{7}$")


class RegisterIn(BaseModel):
    voter_id: str
    name: str
    email: Optional[str] = ""
    image: str
    constituency: str

    @field_validator("voter_id")
    @classmethod
    def validate_voter_id(cls, v: str) -> str:
        v = v.strip()
        if not VOTER_ID_PATTERN.match(v):
            raise ValueError(
                "Voter ID must match the format UP/23/142/0001234 (state/year/constituency code/7-digit number)."
            )
        return v


class AuthenticateIn(BaseModel):
    voter_id: str
    frames: List[str]


class CastVoteIn(BaseModel):
    candidate_id: int