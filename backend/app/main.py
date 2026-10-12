from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.api import router
from app.db.init_db import init_db

app = FastAPI(title="AI Music Recommendation Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173","http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
@app.on_event("startup")
def startup(): init_db()
