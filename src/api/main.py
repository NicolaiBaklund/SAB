from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.review import router as review_router
from src.api.sentiment import router as sentiment_router
from src.settings import get_settings


app = FastAPI(title="SAB Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(review_router)
app.include_router(sentiment_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

