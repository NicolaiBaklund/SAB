from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.review import router as review_router


app = FastAPI(title="SAB Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(review_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

