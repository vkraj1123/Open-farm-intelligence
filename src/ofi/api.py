from fastapi import FastAPI

from ofi.domain.models import FarmCase, ReasoningResult
from ofi.intelligence.orchestrator import Orchestrator

app = FastAPI(
    title="Open Farm Intelligence",
    version="0.1.0",
    description="Farm-level intelligence and orchestration layer for Indian agriculture.",
)

orchestrator = Orchestrator()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/cases/{case_id}/reason", response_model=ReasoningResult)
def reason(case_id: str, case: FarmCase) -> ReasoningResult:
    if case.id != case_id:
        case.id = case_id
    return orchestrator.reason(case)
