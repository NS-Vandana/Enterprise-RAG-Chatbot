from pydantic import BaseModel


class UserContext(BaseModel):
    user_id: str
    email: str
    role: str
    allowed_collections: list[str]
    display_name: str = ""
