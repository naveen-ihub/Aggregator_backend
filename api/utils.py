
import os
from pymongo import MongoClient

# MongoDB setup
client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("MONGO_DB_NAME")]
meetings_collection = db[os.getenv("MONGO_ACCOUNTS_COLLECTION")]
jobs_collection = db[os.getenv("MONGO_JOBS_COLLECTION")]
settings_collection = db["user_settings"]
notes_collection = db["job_notes"]
saved_jobs_collection = db["saved_jobs"]