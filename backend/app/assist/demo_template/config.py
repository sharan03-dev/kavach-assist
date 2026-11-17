"""Settings of the demo web shop."""
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_PASSWORD = "Sup3rSecret!2024"          # planted problem: a hard-coded password
API_KEY = os.getenv("SHOP_API_KEY", "")
DEBUG = True                               # planted problem: debug mode in production
