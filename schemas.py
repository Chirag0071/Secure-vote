from typing import List, Optional
from pydantic import BaseModel


class RegisterIn(BaseModel):
    voter_id: str
    name: str
    email: Optional[str] = ""
    image: str


class AuthenticateIn(BaseModel):
    voter_id: str
    frames: List[str]


class CastVoteIn(BaseModel):
    candidate_id: int
