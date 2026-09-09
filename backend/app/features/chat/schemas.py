from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=10_000)


class AskRequest(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=10_000,
        description="Pregunta que se enviará al modelo seleccionado.",
        examples=["Explica qué es el streaming de tokens."],
    )
    messages: list[Message] = Field(default_factory=list, max_length=40)
    channel: str = Field(default="chat", pattern="^[a-z0-9-]{1,40}$")
    turn_id: str | None = Field(default=None, max_length=100)
