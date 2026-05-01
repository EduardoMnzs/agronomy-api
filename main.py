from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import auth, documents, knowledge, query, users
from core.config import settings
from db.models import Base
from db.session import engine

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(knowledge.router)
app.include_router(documents.router)
app.include_router(query.router)
app.include_router(users.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}
