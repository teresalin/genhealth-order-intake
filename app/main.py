import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import auth, orders
from app.db import Base, engine, get_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # MVP schema management: create tables on startup (no migrations tool yet).
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="GenHealth DME Order Intake", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(orders.router)


@app.get("/health", tags=["health"])
def health(db: Session = Depends(get_db)):
    # Commit is injected by Render at runtime; dialect proves which database is actually wired up.
    info = {"commit": os.environ.get("RENDER_GIT_COMMIT", "unknown"), "database_backend": engine.dialect.name}
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable", **info})
    return {"status": "ok", "database": "ok", **info}
