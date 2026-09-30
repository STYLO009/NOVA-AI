"""NOVA backend — pydantic request/response models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., description="Free-form user message")
    session_id: Optional[str] = Field(None, description="Client generated session id")


class DecisionRequest(BaseModel):
    approved: bool = Field(..., description="true = allow the destructive step")


class MemoryCreate(BaseModel):
    key: str = Field(..., min_length=1)
    value: str = ""


class MemorySearchRequest(BaseModel):
    query: str = Field("", description="Natural language query for semantic recall")
    limit: int = Field(10, ge=1, le=100)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=200)


class AuthRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

LLMMode = Literal["huggingface", "local", "fallback"]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    llm_mode: LLMMode
    model: str
    workspace: str
    features: dict[str, bool]


class ChatResponse(BaseModel):
    task_id: str
    status: str = "queued"


class DecisionResponse(BaseModel):
    ok: bool = True
    status: str


class TaskStep(BaseModel):
    id: int
    title: str
    detail: str = ""
    tool: str = ""
    destructive: bool = False
    status: str = "pending"


class TaskResponse(BaseModel):
    id: str
    input: str
    status: str
    phase: str
    progress: int
    steps: list[TaskStep]
    summary: str
    created_at: str
    duration_ms: int


class HistoryItem(BaseModel):
    id: str
    input: str
    summary: str
    status: str
    steps: int
    phase: str
    created_at: str
    duration_ms: int


class HistoryResponse(BaseModel):
    tasks: list[HistoryItem]


class MemoryItem(BaseModel):
    id: int
    key: str
    value: str
    created_at: str


class MemoryListResponse(BaseModel):
    items: list[MemoryItem]


class MemoryCreateResponse(BaseModel):
    ok: bool = True
    item: MemoryItem


class OkResponse(BaseModel):
    ok: bool = True


class SystemResponse(BaseModel):
    cpu: float
    ram: float
    ssd: float
    network_mbps: float
    network_display: str
    gpu_temp: Optional[float]
    os: str
    hostname: str
    user: str
    python: str
    cores: int
    ram_total_gb: float
    uptime_s: float


class AppItem(BaseModel):
    name: str
    abbr: str
    color: str
    command: str


class AppsResponse(BaseModel):
    apps: list[AppItem]


class RecentFile(BaseModel):
    name: str
    ext: str
    time: str
    color: str
    path: str


class FilesResponse(BaseModel):
    files: list[RecentFile]


class TranscribeResponse(BaseModel):
    text: str


class ErrorResponse(BaseModel):
    detail: Optional[str] = None
    error: Optional[str] = None
    extra: dict[str, Any] = {}


__all__ = [name for name in dir() if not name.startswith("_")]
