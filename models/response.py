from typing import Optional
from pydantic import BaseModel, Field


class Response(BaseModel):
    """
    Response - Simple response containing a message.

        message: Descriptive message [Optional].
    """

    message: Optional[str] = Field(alias="message", default=None)


Response.update_forward_refs()
