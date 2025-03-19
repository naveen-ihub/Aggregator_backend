from django.urls import path
from .views import *
from .job_management_views import *

urlpatterns = [
    path("home", home, name="home"),
    path("scrape_jobs", scrape_jobs, name="scrape_jobs"),
    path("login", login_user, name="login"),
    path("get_existing_jobs", get_existing_jobs, name="get_existing_jobs"),
    path(
        "get_existing_jobs_count",
        get_existing_jobs_count,
        name="get_existing_jobs_count",
    ),
    path("get_not_fit_jobs", get_not_fit_jobs, name="get_not_fit_jobs"),
    path("sendotp", send_otp, name="send_otp"),
    path("verifyotp", verify_otp, name="verify_otp"),
    path("resetpassword", reset_password, name="reset_password"),
    path("createaccount/", createaccount, name="create_account"),
    path("forgot-opt", forgot_send_otp, name="forgot-opt"),
    path("update_status", update_job_status, name="update_job_status"),
    path("generate_proposal/", generate_proposal, name="generate_proposal"),
    path("save_user_settings/", save_user_settings, name="save_user_settings"),
    path("get_user_settings/", get_user_settings, name="get_user_settings"),
    path("get-admins/", fetch_admins, name="fetch_admins"),
    path("delete-admin/<str:admin_id>/", delete_admin, name="delete_admin"),
    path("edit-admin/<str:admin_id>/", edit_admin, name="edit_admin"),
    path("get-stats/", get_stats, name="get_stats"),
    
    # New routes for filtering jobs by status
    path("get_pending_jobs", get_pending_jobs, name="get_pending_jobs"),
    path("get_contacted_jobs", get_contacted_jobs, name="get_contacted_jobs"),
    path("get_working_jobs", get_working_jobs, name="get_working_jobs"),
    path("get_completed_jobs", get_completed_jobs, name="get_completed_jobs"),
    path("add_job_note", add_job_note, name="add_job_note"),
    path("get_job_notes", get_job_notes, name="get_job_notes"),
    path("get_user_noted_jobs", get_user_noted_jobs, name="get_user_noted_jobs"),
    path(
        "delete_job_note/<str:note_id>", delete_job_note, name="delete_job_note"
    ),  # New endpoint
    path("save_job", save_job, name="save_job"),
    path("get_saved_jobs", get_saved_jobs, name="get_saved_jobs"),
    path("remove_saved_job", remove_saved_job, name="remove_saved_job"),
    path(
        "update_Savedjob_status_to_pending/<str:job_id>",
        update_Savedjob_status_to_pending,
        name="update_Savedjob_status_to_pending",
    ),
    path("delete_job/<str:job_id>", delete_job, name="delete_job"),
    path("restore_job/<str:job_id>", restore_job, name="restore_job"),
]
