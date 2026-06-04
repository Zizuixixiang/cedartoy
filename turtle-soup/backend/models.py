from pydantic import BaseModel, Field

from utils import ANSWER_LIMIT, SURFACE_LIMIT, TAGS_LIMIT, TITLE_LIMIT


class AuthBody(BaseModel):
    username: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, max_length=128)
    source: str = "web"


class RoomCreateBody(BaseModel):
    mode: str = "random"
    puzzle_id: int | None = None
    title: str = Field(default="", max_length=TITLE_LIMIT)
    surface: str | None = Field(default=None, max_length=SURFACE_LIMIT)
    answer: str | None = Field(default=None, max_length=ANSWER_LIMIT)
    tags: str = Field(default="", max_length=TAGS_LIMIT)


class PuzzleBody(BaseModel):
    """管理员题库直写/编辑，使用统一题目长度上限。"""
    title: str = ""
    surface: str
    answer: str
    tags: str = ""


class ContentBody(BaseModel):
    room_id: str
    content: str = Field(min_length=1, max_length=200)


class GuessBody(BaseModel):
    room_id: str
    content: str = Field(min_length=1, max_length=1000)


class HintRequestBody(BaseModel):
    room_id: str


class HintResponseBody(BaseModel):
    room_id: str
    log_id: int
    accept: bool


class NoteBody(BaseModel):
    content: str = Field(min_length=1, max_length=50)


class ReportBody(BaseModel):
    target_player_id: int | None = None
    room_id: str | None = None
    log_id: int | None = None
    reason: str = Field(default="", max_length=300)
