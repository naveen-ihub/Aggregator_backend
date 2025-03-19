import jwt
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import status
from playwright.sync_api import sync_playwright
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta, timezone
import json
import os
import time
from pymongo import MongoClient
from bson import ObjectId
from django.core.mail import send_mail
from django.conf import settings

from .utils import jobs_collection

# JWT setup
JWT_SECRET = "secret"
JWT_ALGORITHM = "HS256"

@api_view(["PATCH"])
def update_job_status(request):
    try:
        # Extract job ID and new status from request data
        job_id = request.data.get("job_id")
        new_status = request.data.get("status", "pending").strip()

        if not job_id or not new_status:
            return Response(
                {"error": "Both 'job_id' and 'status' are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Convert job_id to ObjectId
        try:
            job_object_id = ObjectId(job_id)
        except Exception:
            return Response({"error": "Invalid job ID format."}, status=status.HTTP_400_BAD_REQUEST)

        # Find and update the job
        result = jobs_collection.update_one(
            {"_id": job_object_id},
            {"$set": {"status": new_status, "updated_at": datetime.utcnow()}}
        )

        # Check if a job was actually updated
        if result.matched_count == 0:
            return Response({"error": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"message": "Job status updated successfully."}, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error updating job status: {e}")
        return Response(
            {"error": f"Failed to update job status: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

def get_jobs_by_status(status_filter, username):
    try:
        # Filter jobs by both status and username
        jobs = list(jobs_collection.find({"status": status_filter, "username": username}))
        for job in jobs:
            job["_id"] = str(job["_id"])
            job["inserted_at"] = job.get("inserted_at", "N/A")
        return jobs
    except Exception as e:
        print(f"Error fetching jobs with status {status_filter} for user {username}: {e}")
        return None

@api_view(["GET"])
def get_pending_jobs(request):
    # Extract username from query parameters
    username = request.query_params.get("username")
    if not username:
        return Response({"error": "Username is required."}, status=status.HTTP_400_BAD_REQUEST)

    jobs = get_jobs_by_status("pending", username)
    if jobs is not None:
        return Response({"pending_jobs": jobs}, status=status.HTTP_200_OK)
    return Response({"error": "Failed to fetch pending jobs."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def get_contacted_jobs(request):
    # Extract username from query parameters
    username = request.query_params.get("username")
    if not username:
        return Response({"error": "Username is required."}, status=status.HTTP_400_BAD_REQUEST)

    jobs = get_jobs_by_status("contacted", username)
    if jobs is not None:
        return Response({"contacted_jobs": jobs}, status=status.HTTP_200_OK)
    return Response({"error": "Failed to fetch contacted jobs."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def get_working_jobs(request):
    # Extract username from query parameters
    username = request.query_params.get("username")
    if not username:
        return Response({"error": "Username is required."}, status=status.HTTP_400_BAD_REQUEST)

    jobs = get_jobs_by_status("working", username)
    if jobs is not None:
        return Response({"working_jobs": jobs}, status=status.HTTP_200_OK)
    return Response({"error": "Failed to fetch working jobs."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["GET"])
def get_completed_jobs(request):
    # Extract username from query parameters
    username = request.query_params.get("username")
    if not username:
        return Response({"error": "Username is required."}, status=status.HTTP_400_BAD_REQUEST)

    jobs = get_jobs_by_status("completed", username)
    if jobs is not None:
        return Response({"completed_jobs": jobs}, status=status.HTTP_200_OK)
    return Response({"error": "Failed to fetch completed jobs."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)