from fastapi import FastAPI, HTTPException
from ofi.domain.models import CaseOutcome, FarmCase, Observation, CaseRecord
from ofi.services.case_manager import CaseManager

app = FastAPI(title="Open Farm Intelligence", version="0.2.0", description="Farm-level intelligence and orchestration layer for Indian agriculture.")
case_manager = CaseManager()

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/cases", response_model=CaseRecord)
def create_case(case: FarmCase):
    try: return case_manager.create(case)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc

@app.get("/cases/{case_id}", response_model=CaseRecord)
def get_case(case_id: str):
    try: return case_manager.store.get(case_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="case not found") from exc

@app.post("/cases/{case_id}/observations", response_model=CaseRecord)
def add_observation(case_id: str, observation: Observation):
    try: return case_manager.add_observation(case_id, observation, actor="api")
    except KeyError as exc: raise HTTPException(status_code=404, detail="case not found") from exc

@app.post("/cases/{case_id}/reason", response_model=CaseRecord)
def reason(case_id: str):
    try: return case_manager.reason(case_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="case not found") from exc

@app.post("/cases/{case_id}/outcome", response_model=CaseRecord)
def record_outcome(case_id: str, outcome: CaseOutcome):
    try: return case_manager.record_outcome(case_id, outcome)
    except KeyError as exc: raise HTTPException(status_code=404, detail="case not found") from exc

@app.post("/cases/{case_id}/escalate", response_model=CaseRecord)
def escalate(case_id: str):
    try: return case_manager.escalate(case_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="case not found") from exc
