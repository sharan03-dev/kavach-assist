# The classroom sample "FastAPI backend with MongoDB for a SaaS product", copied VERBATIM (Exercise 15 handout).
# Kept unchanged so run_lab15.py can MEASURE its problems against tasktracker/app.py (see README, "The sample, tested").
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel
from datetime import datetime
import os

app = FastAPI(title="TaskTracker API")

# MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client["tasktracker"]

# CORS middleware
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"]
)

class TaskCreate(BaseModel):
    title: str
    description: str
    priority: str = "medium"

class TaskResponse(TaskCreate):
    id: str
    tenant_id: str
    status: str
    created_at: str

@app.post("/tasks/")
async def create_task(task: TaskCreate, tenant_id: str = "default"):
    doc = {
        "tenant_id": tenant_id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "status": "open",
        "created_at": datetime.utcnow().isoformat()
    }
    result = db.tasks.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return doc

@app.get("/tasks/")
async def list_tasks(tenant_id: str = "default"):
    tasks = []
    for t in db.tasks.find({"tenant_id": tenant_id}):
        t["id"] = str(t.pop("_id"))
        tasks.append(t)
    return tasks
