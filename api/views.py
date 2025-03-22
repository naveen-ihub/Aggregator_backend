from arrow import now
import jwt
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import status
# from playwright.sync_api import sync_playwright
from django.http import JsonResponse,HttpResponse
from django.views.decorators.csrf import csrf_exempt
from datetime import datetime, timedelta, timezone
import json
import os
import time
from pymongo import MongoClient
from bson import ObjectId
from django.core.mail import send_mail
from django.conf import settings
from .utils import jobs_collection, meetings_collection, settings_collection , notes_collection , saved_jobs_collection
import random
import string
import bcrypt 
from django.contrib.auth.hashers import make_password, check_password
import email
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
import json
import random
import asyncio
import aiohttp
from playwright.async_api import async_playwright



logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the scheduler
scheduler = BackgroundScheduler()
# scheduler.start()

# JWT setup
JWT_SECRET = "secret"
JWT_ALGORITHM = "HS256"

def generate_tokens(id, username):
    access_payload = {
        "id": str(id),
        "username": str(username),
        "exp": (datetime.utcnow() + timedelta(minutes=600)).timestamp(),  # Expiration in 600 minutes
        "iat": datetime.utcnow().timestamp(),  # Issued at current time
    }
    token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"jwt": token}

# In-memory storage for OTPs
otp_storage = {}

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def extract_tech_stack(description):
    tech_keywords = [
        'python', 'javascript', 'java', 'react', 'node', 'aws', 'docker',
        'kubernetes', 'sql', 'nosql', 'mongodb', 'angular', 'vue', 'django',
        'flask', 'express', 'git', 'github', 'html', 'css', 'php', 'c++',
        'swift', 'kotlin', 'flutter', 'fastapi', 'airflow'
    ]
    found_tech = [word for word in tech_keywords if word.lower() in description.lower()]
    return ", ".join(found_tech)

@api_view(["GET"])
def home(request):
    data = {"status": "working"}
    return Response(data, status=status.HTTP_200_OK)


def remove_duplicates(jobs):
    seen = set()
    unique_jobs = []

    for job in jobs:
        # Create a unique identifier for each job based on username, title, and link
        job_id = (job["username"], job["title"], job["link"])

        if job_id not in seen:
            seen.add(job_id)
            unique_jobs.append(job)

    return unique_jobs

class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return json.JSONEncoder.default(self, o)

@api_view(["GET"])
def get_existing_jobs_count(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch all jobs from the database for the specified user
        all_jobs = list(jobs_collection.find({"username": username}))

        # Calculate counts based on status
        stats = {
            "total": len(all_jobs),
            "pending": len([job for job in all_jobs if job.get("status", "").lower() == "pending"]),
            "contacted": len([job for job in all_jobs if job.get("status", "").lower() == "contacted"]),
            "working": len([job for job in all_jobs if job.get("status", "").lower() == "working"]),
            "completed": len([job for job in all_jobs if job.get("status", "").lower() == "completed"]),
            "notFit": len([job for job in all_jobs if job.get("status", "").lower() == "notfit"]),
        }

        # New Metric 1: Total Saved Jobs
        saved_jobs_count = saved_jobs_collection.count_documents({"username": username})
        stats["saved"] = saved_jobs_count

        # New Metric 2: Noted Jobs Count (unique jobs with notes)
        noted_job_ids = set(note["job_id"] for note in notes_collection.find({"username": username}))
        stats["noted"] = len(noted_job_ids)

        # New Metric 3: Total Jobs Scraped Today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        jobs_scraped_today = jobs_collection.count_documents({
            "username": username,
            "inserted_at": {"$gte": today_start, "$lt": today_end}
        })
        stats["scrapedToday"] = jobs_scraped_today

        response_data = {
            "counts": stats
        }

        print("Returning response:", response_data)  # Debugging
        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching job counts: {e}")
        return Response(
            {"error": f"Failed to fetch job counts: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_existing_jobs(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch all jobs from the database for the specified user
        all_jobs = list(jobs_collection.find({"username": username, "status": "Open"}))

        print(f"Found {len(all_jobs)} open jobs for user {username}")  # Debugging

        # Define 12-hour threshold for Latest Jobs
        twelve_hours_ago = datetime.utcnow() - timedelta(hours=24)

        # Latest Jobs: Jobs inserted within the last 12 hours
        new_jobs = [
            job for job in all_jobs
            if "inserted_at" in job and job["inserted_at"] >= twelve_hours_ago
        ]

        # All Jobs: Jobs older than 12 hours
        existing_jobs = [job for job in all_jobs if job not in new_jobs]

        for job in new_jobs + existing_jobs:
            job["_id"] = str(job["_id"])
            job["inserted_at"] = job.get("inserted_at", "N/A")

        response_data = {
            "new_jobs": new_jobs,
            "all_jobs": existing_jobs,
            "new_job_found": False  # Set in scrape_jobs
        }

        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching existing jobs: {e}")
        return Response(
            {"error": f"Failed to fetch jobs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_not_fit_jobs(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch all jobs from the database for the specified user with status "notFit"
        all_jobs = list(jobs_collection.find({"username": username, "status": "notFit"}))

        print(f"Found {len(all_jobs)} not fit jobs for user {username}")  # Debugging

        for job in all_jobs:
            job["_id"] = str(job["_id"])
            job["inserted_at"] = job.get("inserted_at", "N/A")

        response_data = {
            "notFitJobs": all_jobs
        }
        print("Returning response:", response_data)  # Debugging
        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching not fit jobs: {e}")
        return Response(
            {"error": f"Failed to fetch jobs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

# proxy_list = [{'server': 'dc.oxylabs.io:8000', 'username': 'naveen_kY5lG', 'password': 'ea8vWq+NiqjurjkQ'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Gudbadugly_0cqzZ', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'ihubsns_zQVFs', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Testing_LoNLf', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'HarisankarJ_a5Nj2', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'akash_tNgmM', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Akilihub_Gy3Z1', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'mugilan_eGYDD', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'RNS_Sanjay_MZyzH', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'lathees_6pZh3', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'shruthi_1vvlk', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'kavin_bakyaraj_5cQ3F', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Ajay_Chakravarthi_B5RJy', 'password': 'SNS+ihub=123'}]


# async def get_random_proxy(proxy_list):
#     proxy_now = random.choice(proxy_list)
#     try:
#         username = proxy_now["username"]
#         password = proxy_now["password"]
#         proxy = proxy_now["server"]

#         proxies = {
#             "https": f'https://user-{username}:{password}@{proxy}'
#         }

#         response = requests.get("https://ip.oxylabs.io/location", proxies=proxies)
#         if response.status_code == 200:
#             return proxy_now
#         else:
#             proxy_list.pop(proxy_list.index(proxy_now))
#             return await get_random_proxy(proxy_list)
#     except Exception as e:
#         print(e)
#         proxy_list.pop(proxy_list.index(proxy_now))
#         return await get_random_proxy(proxy_list)


# async def setup_browser():
#     playwright = await async_playwright().start()
#     #proxy = await get_random_proxy(proxy_list)
    
#    # browser = await playwright.chromium.launch(
#     #    headless=False,
#      #   proxy={"server": proxy["server"], "username": proxy["username"], "password": proxy["password"]}
#    # )

#     browser = await playwright.chromium.launch(
#         headless=True
#     )
#     page = await browser.new_page()
#     return playwright, browser, page



# async def freelancer_scrapper(search_query):
#     playwright, browser, page = await setup_browser()
#     jobs = []

#     try:
#         await page.goto(f"https://www.freelancer.com/jobs/?keyword={search_query}&results=20", timeout=60000)
#         if "freelancer.com" not in page.url:
#             raise Exception("Failed to load Freelancer.com jobs page")

#         await asyncio.sleep(3)

#         await page.wait_for_selector("//input[@id='keyword-input']", timeout=60000)
#         search_box = page.locator("//input[@id='keyword-input']")

#         if not await search_box.is_visible() or not await search_box.is_enabled():
#             raise Exception("Search input field not interactable")

#         await search_box.press("Enter")
#         await asyncio.sleep(3)

#         while True:
#             job_cards = await page.locator(".JobSearchCard-item").all()
#             if not job_cards:
#                 print("No job listings found on the page")
#                 break

#             for card in job_cards:
#                 try:
#                     title_elem = card.locator(".JobSearchCard-primary-heading-link")
#                     title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"
#                     link = await title_elem.get_attribute("href") if await title_elem.count() > 0 else None
#                     if link and not link.startswith("http"):
#                         link = f"https://www.freelancer.com{link}"

#                     desc_elem = card.locator(".JobSearchCard-primary-description")
#                     description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
#                     truncated_description = description.replace("\n", " ")

#                     budget_elem = card.locator(".JobSearchCard-primary-price")
#                     budget = (await budget_elem.inner_text()).strip().replace(" ", "") if await budget_elem.count() > 0 else "N/A"

#                     proposal_elem = card.locator(".JobSearchCard-secondary-entry")
#                     proposals = (await proposal_elem.inner_text()).strip().replace(" ", "") if await budget_elem.count() > 0 else "N/A"

#                     skills_elem = card.locator(".JobSearchCard-primary-tags")
#                     skills = await skills_elem.inner_text() if await skills_elem.count() > 0 else "N/A"

#                     post_ends = card.locator(".JobSearchCard-primary-heading-days")
#                     post_ends = await post_ends.inner_text() if await post_ends.count() > 0 else "N/A"

#                     job_data = {
#                         "title": title,
#                         "link": link,
#                         "description": truncated_description,
#                         "full_description": truncated_description,
#                         "budget": budget,
#                         "posted_time": "N/A",
#                         'proposals': proposals,
#                         "status": "Open",
#                         "post_ends": post_ends,
#                         "skills": skills,
#                         "keyword": search_query,
#                         "platform": "freelancer.com"
#                     }
#                     if title != "N/A" :
#                         jobs.append(job_data)

#                 except Exception as e:
#                     print(f"Error processing individual job: {e}")
#                     continue

#             next_button = page.locator("a[data-link='next_page']")
#             if await next_button.count() > 0 and await next_button.is_enabled():
#                 await next_button.click()
#                 await asyncio.sleep(2)
#                 print(f"Moving to next page. Current job count: {len(jobs)}")
#             else:
#                 print("No more pages to scrape")
#                 break

#         print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")

#     except Exception as e:
#         print(f"Scraping error: {e}")
#         print(f"Current URL: {page.url}")
#         print(f"Page title: {await page.title()}")

#     finally:
#         await browser.close()
#         await playwright.stop()

#     return jobs

# async def guru_scrapper(search_query):
#     playwright, browser, page = await setup_browser()
#     jobs = []

#     try:
#         url = f"https://www.guru.com/d/jobs/skill/{search_query}/"
#         await page.goto(url, wait_until='load')
#         if "guru.com" not in page.url:
#             raise Exception("Failed to load Guru.com jobs page")

#         await asyncio.sleep(5)

#         job_cards = await page.locator("div.record__details").all()
#         if not job_cards:
#             print("No job listings found on the page")

#         for card in job_cards:
#             try:
#                 title_elem = card.locator("//h2[@class='jobRecord__title jobRecord__title--changeVisited']")
#                 title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

#                 link_elm = card.locator("//h2[@class='jobRecord__title jobRecord__title--changeVisited']//a")
#                 link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
#                 if link and not link.startswith("http") and not link.startswith("https"):
#                     link = f"https://www.guru.com{link}"

#                 desc_elem = card.locator('//p[@class="jobRecord__desc"]')
#                 truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
#                 truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

#                 posted_time_elem = card.locator("//div[@class='jobRecord__meta']//strong[1]")
#                 posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

#                 post_ends_elem = card.locator('//p[@class="copy small grey rhythmMargin1"]')
#                 post_ends = await post_ends_elem.inner_text() if await post_ends_elem.count() > 0 else "N/A"

#                 budget_elem = card.locator("//div[@class='jobRecord__budget']")
#                 budget = await budget_elem.inner_text() if await budget_elem.count() > 0 else "N/A"

#                 proposals_elem = card.locator("div.jobRecord__meta > strong:nth-child(2)")
#                 proposals = await proposals_elem.inner_text() if await proposals_elem.count() > 0 else "N/A"

#                 skill_elem = card.locator(".skillsList")
#                 skills = await skill_elem.inner_text() if await skill_elem.count() > 0 else "N/A"

#                 job_data = {
#                     "title": title,
#                     "link": link,
#                     "description": truncated_description,
#                     "full_description": truncated_description,
#                     "budget": budget,
#                     "posted_time": posted_time,
#                     'proposals': proposals,
#                     "status": "Open",
#                     "post_ends": post_ends,
#                     "skills": skills,
#                     "keyword": search_query,
#                     "platform": "guru.com"
#                 }
#                 jobs.append(job_data)

#             except Exception as e:
#                 print(f"Error processing individual job: {e}")
#                 continue

#         print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")

#     except Exception as e:
#         print(f"Scraping error: {e}")
#         print(f"Current URL: {page.url}")
#         print(f"Page title: {await page.title()}")

#     finally:
#         await browser.close()
#         await playwright.stop()

#     return jobs

# async def upwork_scrapper(search_query):
#     playwright, browser, page = await setup_browser()
#     jobs = []

#     try:
#         url = f"https://www.upwork.com/nx/search/jobs/?per_page=50&q={search_query}"
#         await page.goto(url, wait_until='load')
#         if "upwork.com" not in page.url:
#             raise Exception("Failed to load Upwork.com jobs page")

#         job_cards = await page.locator('(//article[@data-ev-label="search_results_impression"])').all()
#         if not job_cards:
#             print("No job listings found on the page")

#         await asyncio.sleep(5)

#         for card in job_cards:
#             try:
#                 title_elem = card.locator('//h2[@class="h5 mb-0 mr-2 job-tile-title"]')
#                 title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

#                 link_elm = card.locator('//h2[@class="h5 mb-0 mr-2 job-tile-title"]//a')
#                 link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
#                 if link and not link.startswith("http") and not link.startswith("https"):
#                     link = f"https://www.upwork.com{link}"

#                 desc_elem = card.locator('.mb-0.text-body-sm')
#                 truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
#                 truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

#                 posted_time_elem = card.locator("//small[@data-test='job-pubilshed-date']")
#                 posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

#                 skill_elem = card.locator('div.air3-token-container')
#                 skills = await skill_elem.inner_text() if await skill_elem.count() > 0 else "N/A"

#                 meta_data_elem = card.locator('//ul[@data-test="JobInfo"]')
#                 meta_data = await meta_data_elem.inner_text() if await meta_data_elem.count() > 0 else "N/A"
#                 meta_data = meta_data.replace('\n', " ")

#                 job_data = {
#                     "title": title,
#                     "link": link,
#                     "description": truncated_description,
#                     "full_description": truncated_description,
#                     "budget": "N/A",
#                     "posted_time": posted_time,
#                     "meta_data": meta_data,
#                     "status": "Open",
#                     "skills": skills,
#                     "keyword": search_query,
#                     "platform": "upwork.com"
#                 }
#                 jobs.append(job_data)

#             except Exception as e:
#                 print(f"Error processing individual job: {e}")
#                 continue

#         print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")

#     except Exception as e:
#         print(f"Scraping error: {e}")
#         print(f"Current URL: {page.url}")
#         print(f"Page title: {await page.title()}")

#     finally:
#         await browser.close()
#         await playwright.stop()

#     return jobs

# async def peopleperhour_scrapper(search_query):
#     playwright, browser, page = await setup_browser()
#     jobs = []

#     try:
#         url = f"https://www.peopleperhour.com/freelance-{search_query}-jobs"
#         await page.goto(url, wait_until='load')
#         if "peopleperhour.com" not in page.url:
#             raise Exception("Failed to load PeoplePerHour.com jobs page")

#         await asyncio.sleep(5)

#         job_cards = await page.locator("//li[@class='list__item⤍List⤚2ytmm']").all()
#         if not job_cards:
#             print("No job listings found on the page")

#         for card in job_cards:
#             try:
#                 title_elem = card.locator("//a[@class='item__url⤍ListItem⤚20ULx']")
#                 title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

#                 link_elm = card.locator("//a[@class='item__url⤍ListItem⤚20ULx']")
#                 link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
#                 if link and not link.startswith("http") and not link.startswith("https"):
#                     link = f"https://www.peopleperhour.com{link}"

#                 desc_elem = card.locator('//p[@class="item__desc⤍ListItem⤚3f4JV"]')
#                 truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
#                 truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

#                 posted_time_elem = card.locator('//div[@class="card__footer⤍ListItem⤚1KHhv"]//span[2]/preceding-sibling::span[1]')
#                 posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

#                 proposals = card.locator('//div[@class="nano card__footer-left⤍ListItem⤚16Odv"]//span[2]')
#                 proposals = await proposals.inner_text() if await proposals.count() > 0 else "N/A"

#                 budget_elem = card.locator("//div[contains(@class, 'card__price')]/span[@class='title-nano']/div/span")
#                 budget = await budget_elem.inner_text() if await budget_elem.count() > 0 else "N/A"

#                 job_data = {
#                     "title": title,
#                     "link": link,
#                     "description": truncated_description,
#                     "full_description": truncated_description,
#                     "budget": budget,
#                     "posted_time": posted_time,
#                     "proposals": proposals,
#                     "post_ends": "N/A",
#                     "status": "Open",
#                     "skills": "N/A",
#                     "keyword": search_query,
#                     "platform": "peopleperhour.com"
#                 }
#                 jobs.append(job_data)

#             except Exception as e:
#                 print(f"Error processing individual job: {e}")
#                 continue

#         print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")

#     except Exception as e:
#         print(f"Scraping error: {e}")
#         print(f"Current URL: {page.url}")
#         print(f"Page title: {await page.title()}")

#     finally:
#         await browser.close()
#         await playwright.stop()

#     return jobs




# async def scrapper(search_query_list):
#     jobs = []
#     for search_query in search_query_list:
#         tasks = [
#             freelancer_scrapper(search_query),
#             guru_scrapper(search_query),
#             upwork_scrapper(search_query),
#             peopleperhour_scrapper(search_query)
#         ]
#         results = await asyncio.gather(*tasks, return_exceptions=True)
        
#         for result in results:
#             if isinstance(result, list):
#                 jobs.extend(result)
#             else:
#                 print(f"Error in concurrent scraping: {result}")
        
#         print(f"Total jobs scraped for query '{search_query}': {len(jobs)}")

#         #with open("bun.json", "w") as f:
#          #   json.dump(jobs, f, indent=2)
    
#     return jobs

# def scrapper(search_query_list):
#     jobs = []
#     for search_query in search_query_list:
#         try:
#             freelancer_job = asyncio.run(freelancer_scrapper(search_query))
#             jobs.extend(freelancer_job)
#         except Exception as e:
#             print(f"Error scraping Freelancer for '{search_query}': {e}")

#         try:
#             guru_job = asyncio.run(guru_scrapper(search_query))
#             jobs.extend(guru_job)
#         except Exception as e:
#             print(f"Error scraping Guru for '{search_query}': {e}")

#         try:
#             upwork_job =asyncio.run(upwork_scrapper(search_query))
#             jobs.extend(upwork_job)
#         except Exception as e:
#             print(f"Error scraping Upwork for '{search_query}': {e}")

#         try:
#             peopleperhour_job = asyncio.run(peopleperhour_scrapper(search_query))
#             jobs.extend(peopleperhour_job)
#         except Exception as e:
#             print(f"Error scraping PeoplePerHour for '{search_query}': {e}")

#         print(f"Total jobs scraped for query '{search_query}': {len(jobs)}")

#        # with open("bun.json", "w") as f:
#         #    json.dump(jobs, f, indent=2)

#     return jobs



# Global USER_AGENTS
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
]

proxy_list = [{'server': 'dc.oxylabs.io:8000', 'username': 'naveen_kY5lG', 'password': 'ea8vWq+NiqjurjkQ'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Gudbadugly_0cqzZ', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'ihubsns_zQVFs', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Testing_LoNLf', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'HarisankarJ_a5Nj2', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'akash_tNgmM', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Akilihub_Gy3Z1', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'mugilan_eGYDD', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'RNS_Sanjay_MZyzH', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'lathees_6pZh3', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'shruthi_1vvlk', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'kavin_bakyaraj_5cQ3F', 'password': 'SNS+ihub=123'}, {'server': 'dc.oxylabs.io:8000', 'username': 'Ajay_Chakravarthi_B5RJy', 'password': 'SNS+ihub=123'}]


# Semaphore for rate limiting (max 4 concurrent requests)
semaphore = asyncio.Semaphore(50)

async def get_random_proxy(proxy_list):
    tried_usernames = set()
    async with aiohttp.ClientSession() as session:
        while len(tried_usernames) < len(proxy_list):
            proxy_now = random.choice([p for p in proxy_list if p["username"] not in tried_usernames])
            tried_usernames.add(proxy_now["username"])
            try:
                username = proxy_now["username"]
                password = proxy_now["password"]
                proxy = proxy_now["server"]
                proxy_url = f"http://{username}:{password}@{proxy}"
                async with session.get("https://ip.oxylabs.io/location", proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                    if response.status == 200:
                        return proxy_now
            except Exception as e:
                print(f"Proxy {proxy_now['username']} failed: {e}")
                continue
        raise Exception("No working proxies found")

async def setup_browser(playwright, proxy):
    browser = await playwright.chromium.launch(
        headless=True,
        proxy={"server": proxy["server"], "username": proxy["username"], "password": proxy["password"]}
    )
    context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
    page = await context.new_page()
    return browser, context, page

async def freelancer_scrapper(search_query, browser, context):
    async with semaphore:  # Rate limiting
        page = await context.new_page()
        jobs = []
        try:
            await page.goto(f"https://www.freelancer.com/jobs/?keyword={search_query}&results=20", timeout=60000)
            if "freelancer.com" not in page.url:
                raise Exception("Failed to load Freelancer.com jobs page")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)

            await page.wait_for_selector("//input[@id='keyword-input']", timeout=60000)
            search_box = page.locator("//input[@id='keyword-input']")
            if not await search_box.is_visible() or not await search_box.is_enabled():
                raise Exception("Search input field not interactable")

            await search_box.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)

            while True:
                job_cards = await page.locator(".JobSearchCard-item").all()
                if not job_cards:
                    print("No job listings found on the page")
                    break

                for card in job_cards:
                    try:
                        title_elem = card.locator(".JobSearchCard-primary-heading-link")
                        title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"
                        link = await title_elem.get_attribute("href") if await title_elem.count() > 0 else None
                        if link and not link.startswith("http"):
                            link = f"https://www.freelancer.com{link}"

                        desc_elem = card.locator(".JobSearchCard-primary-description")
                        description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
                        truncated_description = description.replace("\n", " ")

                        budget_elem = card.locator(".JobSearchCard-primary-price")
                        budget = (await budget_elem.inner_text()).strip().replace(" ", "") if await budget_elem.count() > 0 else "N/A"

                        proposal_elem = card.locator(".JobSearchCard-secondary-entry")
                        proposals = (await proposal_elem.inner_text()).strip().replace(" ", "") if await budget_elem.count() > 0 else "N/A"

                        skills_elem = card.locator(".JobSearchCard-primary-tags")
                        skills = await skills_elem.inner_text() if await skills_elem.count() > 0 else "N/A"

                        post_ends = card.locator(".JobSearchCard-primary-heading-days")
                        post_ends = await post_ends.inner_text() if await post_ends.count() > 0 else "N/A"

                        job_data = {
                            "title": title,
                            "link": link,
                            "description": truncated_description,
                            "full_description": truncated_description,
                            "budget": budget,
                            "posted_time": "N/A",
                            'proposals': proposals,
                            "status": "Open",
                            "post_ends": post_ends,
                            "skills": skills,
                            "keyword": search_query,
                            "platform": "freelancer.com"
                        }
                        if title != "N/A":
                            jobs.append(job_data)
                    except Exception as e:
                        print(f"Error processing individual job: {e}")
                        continue

                next_button = page.locator("a[data-link='next_page']")
                if await next_button.count() > 0 and await next_button.is_enabled():
                    await next_button.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=60000)
                    print(f"Moving to next page. Current job count: {len(jobs)}")
                else:
                    print("No more pages to scrape")
                    break

            print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")
        except Exception as e:
            print(f"Scraping error: {e}")
            print(f"Current URL: {page.url}")
            print(f"Page title: {await page.title()}")
        finally:
            await page.close()
        return jobs

async def guru_scrapper(search_query, browser, context):
    async with semaphore:  # Rate limiting
        page = await context.new_page()
        jobs = []
        try:
            url = f"https://www.guru.com/d/jobs/skill/{search_query}/"
            await page.goto(url, wait_until='load')
            if "guru.com" not in page.url:
                raise Exception("Failed to load Guru.com jobs page")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)

            job_cards = await page.locator("div.record__details").all()
            if not job_cards:
                print("No job listings found on the page")

            for card in job_cards:
                try:
                    title_elem = card.locator("//h2[@class='jobRecord__title jobRecord__title--changeVisited']")
                    title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

                    link_elm = card.locator("//h2[@class='jobRecord__title jobRecord__title--changeVisited']//a")
                    link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
                    if link and not link.startswith("http") and not link.startswith("https"):
                        link = f"https://www.guru.com{link}"

                    desc_elem = card.locator('//p[@class="jobRecord__desc"]')
                    truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
                    truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

                    posted_time_elem = card.locator("//div[@class='jobRecord__meta']//strong[1]")
                    posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

                    post_ends_elem = card.locator('//p[@class="copy small grey rhythmMargin1"]')
                    post_ends = await post_ends_elem.inner_text() if await post_ends_elem.count() > 0 else "N/A"

                    budget_elem = card.locator("//div[@class='jobRecord__budget']")
                    budget = await budget_elem.inner_text() if await budget_elem.count() > 0 else "N/A"

                    proposals_elem = card.locator("div.jobRecord__meta > strong:nth-child(2)")
                    proposals = await proposals_elem.inner_text() if await proposals_elem.count() > 0 else "N/A"

                    skill_elem = card.locator(".skillsList")
                    skills = await skill_elem.inner_text() if await skill_elem.count() > 0 else "N/A"

                    job_data = {
                        "title": title,
                        "link": link,
                        "description": truncated_description,
                        "full_description": truncated_description,
                        "budget": budget,
                        "posted_time": posted_time,
                        'proposals': proposals,
                        "status": "Open",
                        "post_ends": post_ends,
                        "skills": skills,
                        "keyword": search_query,
                        "platform": "guru.com"
                    }
                    jobs.append(job_data)
                except Exception as e:
                    print(f"Error processing individual job: {e}")
                    continue

            print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")
        except Exception as e:
            print(f"Scraping error: {e}")
            print(f"Current URL: {page.url}")
            print(f"Page title: {await page.title()}")
        finally:
            await page.close()
        return jobs

async def upwork_scrapper(search_query, browser, context):
    async with semaphore:  # Rate limiting
        page = await context.new_page()
        jobs = []
        try:
            url = f"https://www.upwork.com/nx/search/jobs/?per_page=50&q={search_query}"
            await page.goto(url, wait_until='load')
            if "upwork.com" not in page.url:
                raise Exception("Failed to load Upwork.com jobs page")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)

            job_cards = await page.locator('(//article[@data-ev-label="search_results_impression"])').all()
            if not job_cards:
                print("No job listings found on the page")

            for card in job_cards:
                try:
                    title_elem = card.locator('//h2[@class="h5 mb-0 mr-2 job-tile-title"]')
                    title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

                    link_elm = card.locator('//h2[@class="h5 mb-0 mr-2 job-tile-title"]//a')
                    link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
                    if link and not link.startswith("http") and not link.startswith("https"):
                        link = f"https://www.upwork.com{link}"

                    desc_elem = card.locator('.mb-0.text-body-sm')
                    truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
                    truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

                    posted_time_elem = card.locator("//small[@data-test='job-pubilshed-date']")
                    posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

                    skill_elem = card.locator('div.air3-token-container')
                    skills = await skill_elem.inner_text() if await skill_elem.count() > 0 else "N/A"

                    meta_data_elem = card.locator('//ul[@data-test="JobInfo"]')
                    meta_data = await meta_data_elem.inner_text() if await meta_data_elem.count() > 0 else "N/A"
                    meta_data = meta_data.replace('\n', " ")

                    job_data = {
                        "title": title,
                        "link": link,
                        "description": truncated_description,
                        "full_description": truncated_description,
                        "budget": "N/A",
                        "posted_time": posted_time,
                        "meta_data": meta_data,
                        "status": "Open",
                        "skills": skills,
                        "keyword": search_query,
                        "platform": "upwork.com"
                    }
                    jobs.append(job_data)
                except Exception as e:
                    print(f"Error processing individual job: {e}")
                    continue

            print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")
        except Exception as e:
            print(f"Scraping error: {e}")
            print(f"Current URL: {page.url}")
            print(f"Page title: {await page.title()}")
        finally:
            await page.close()
        return jobs

async def peopleperhour_scrapper(search_query, browser, context):
    async with semaphore:  # Rate limiting
        page = await context.new_page()
        jobs = []
        try:
            url = f"https://www.peopleperhour.com/freelance-{search_query}-jobs"
            await page.goto(url, wait_until='load')
            if "peopleperhour.com" not in page.url:
                raise Exception("Failed to load PeoplePerHour.com jobs page")
            await page.wait_for_load_state("domcontentloaded", timeout=60000)

            job_cards = await page.locator("//li[@class='list__item⤍List⤚2ytmm']").all()
            if not job_cards:
                print("No job listings found on the page")

            for card in job_cards:
                try:
                    title_elem = card.locator("//a[@class='item__url⤍ListItem⤚20ULx']")
                    title = await title_elem.inner_text() if await title_elem.count() > 0 else "N/A"

                    link_elm = card.locator("//a[@class='item__url⤍ListItem⤚20ULx']")
                    link = await link_elm.get_attribute("href") if await link_elm.count() > 0 else None
                    if link and not link.startswith("http") and not link.startswith("https"):
                        link = f"https://www.peopleperhour.com{link}"

                    desc_elem = card.locator('//p[@class="item__desc⤍ListItem⤚3f4JV"]')
                    truncated_description = await desc_elem.inner_text() if await desc_elem.count() > 0 else "N/A"
                    truncated_description = " ".join(word for word in truncated_description.split() if not word.startswith(r"\u"))

                    posted_time_elem = card.locator('//div[@class="card__footer⤍ListItem⤚1KHhv"]//span[2]/preceding-sibling::span[1]')
                    posted_time = await posted_time_elem.inner_text() if await posted_time_elem.count() > 0 else "N/A"

                    proposals = card.locator('//div[@class="nano card__footer-left⤍ListItem⤚16Odv"]//span[2]')
                    proposals = await proposals.inner_text() if await proposals.count() > 0 else "N/A"

                    budget_elem = card.locator("//div[contains(@class, 'card__price')]/span[@class='title-nano']/div/span")
                    budget = await budget_elem.inner_text() if await budget_elem.count() > 0 else "N/A"

                    job_data = {
                        "title": title,
                        "link": link,
                        "description": truncated_description,
                        "full_description": truncated_description,
                        "budget": budget,
                        "posted_time": posted_time,
                        "proposals": proposals,
                        "post_ends": "N/A",
                        "status": "Open",
                        "skills": "N/A",
                        "keyword": search_query,
                        "platform": "peopleperhour.com"
                    }
                    jobs.append(job_data)
                except Exception as e:
                    print(f"Error processing individual job: {e}")
                    continue

            print(f"{page.url} Successfully scraped {len(jobs)} jobs for query: '{search_query}'")
        except Exception as e:
            print(f"Scraping error: {e}")
            print(f"Current URL: {page.url}")
            print(f"Page title: {await page.title()}")
        finally:
            await page.close()
        return jobs

async def scrapper(search_query_list):
    jobs = []
    async with async_playwright() as playwright:
        proxy = await get_random_proxy(proxy_list)
        browser, context, _ = await setup_browser(playwright, proxy)  # Single browser instance
        
        for search_query in search_query_list:
            tasks = [
                freelancer_scrapper(search_query, browser, context),
                guru_scrapper(search_query, browser, context),
                upwork_scrapper(search_query, browser, context),
                peopleperhour_scrapper(search_query, browser, context)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, list):
                    jobs.extend(result)
                else:
                    print(f"Error in concurrent scraping: {result}")
            
            print(f"Total jobs scraped for query '{search_query}': {len(jobs)}")
            # with open("bun.json", "w") as f:
            #     json.dump(jobs, f, indent=2)
        
        await browser.close()  # Close browser once all scraping is done
    
    return jobs


def perform_scraping(search_query, platform, username):
    try:
        # if platform == "freelancer.com":
        #     jobs = freelancer_scrapper(search_query)
        # elif platform == "upwork.com":
        #     jobs = upwork_scrapper(search_query)
        # elif platform == "guru.com":
        #     jobs = guru_scrapper(search_query)
        # elif platform == "peopleperhour.com":
        #     jobs = peopleperhour_scrapper(search_query)

        # else:
        #     logger.error(f"Invalid platform: {platform}")
        #     return [], False, []
        
        if isinstance(search_query, str):
            search_query = [search_query]
        
        jobs = asyncio.run(scrapper(search_query))
        # jobs = scrapper(search_query)

        if not isinstance(jobs, list):
            logger.error(f"Scraper returned invalid data for {platform}: {jobs}")
            return [], False, []

        if not jobs:
            logger.info(f"No jobs found for {platform} with query '{search_query}'")
            return [], False, []

        # Add the username to each job before removing duplicates
        for job in jobs:
            job["username"] = username

        jobs = remove_duplicates(jobs)
        new_jobs_found = False
        new_jobs_list = []

        # Fetch user settings for keywords and emails
        user_settings_doc = settings_collection.find_one({"username": username})
        user_settings = user_settings_doc.get("settings", {}) if user_settings_doc else {}
        notification_keywords = user_settings.get("notificationKeywords", [])
        
        for job in jobs:
            existing_job = jobs_collection.find_one({
                # "platform": platform,
                # "keyword": search_query,
                "title": job["title"],
                "link": job["link"],
                "username": username
            })

            if not existing_job:
                job["inserted_at"] = datetime.utcnow()
                job["status"] = "Open"
                jobs_collection.insert_one(job.copy())
                new_jobs_found = True
                new_jobs_list.append(job)

                # Check for keyword matches
                if notification_keywords:
                    description = job.get("full_description", "").lower()
                    title = job.get("title", "").lower()
                    matched_keywords = [kw for kw in notification_keywords if kw.lower() in description or kw.lower() in title]
                    if matched_keywords:
                        job["matched_keywords"] = matched_keywords
                       
        return jobs, new_jobs_found, new_jobs_list

    except Exception as e:
        logger.error(f"Scraping error for {platform}: {e}")
        return [], False, []

@api_view(["GET"])
def scrape_jobs(request):
    search_query = request.GET.get("search_query", "").strip()
    # platform = request.GET.get("platform", "").strip()
    
    platform = "All"
    
    username = request.GET.get("username", "").strip()

    logger.info(f"Manual scrape request: '{search_query}' on '{platform}' for user: '{username}'")

    if not search_query or not platform:
        return Response(
            {"error": "Search query and platform are required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    all_jobs_list, new_jobs_found, new_jobs_list = perform_scraping(search_query, platform, username)
    response_data = {
        "jobs": all_jobs_list,
        "new_job_found": new_jobs_found,
        "new_jobs": new_jobs_list
    }
    return Response(response_data, status=status.HTTP_200_OK)

def schedule_automatic_scraping(username):
    try:
        user_settings = settings_collection.find_one({"username": username})
        if not user_settings or "settings" not in user_settings:
            logger.info(f"No settings found for user: {username}, skipping scheduling")
            return

        settings = user_settings["settings"]
        scraping_mode = settings.get("scrapingMode", "manual")

        # Remove existing job if it exists
        if scheduler.get_job(f"scrape_{username}"):
            scheduler.remove_job(f"scrape_{username}")

        if scraping_mode != "automatic":
            logger.info(f"Scraping mode for {username} is '{scraping_mode}', no automatic scheduling.")
            return

        interval_settings = settings.get("automaticScrapeInterval", {"hours": 0, "minutes": 1})
        hours = interval_settings.get("hours", 0)
        minutes = interval_settings.get("minutes", 1)
        total_minutes = (hours * 60) + minutes

        if total_minutes <= 0:
            logger.warning(f"Invalid interval for {username}: {total_minutes} minutes")
            return

        def scrape_task():
            logger.info(f"Running automatic scrape for {username}")
            platforms = settings.get("selectedPlatforms", {"freelancer": True})
            keyword_list = ["Generative","GenAi","Generative AI","GenerativeAI","Gen AI","RAG","LLM","AiAgent","Agentic Ai","Ai Agent","AI Copywriting","chatbots",
                            "Ai Pipeline","Finetuning","AIModel","AI Model","Ai Text-Generation","Ai Workflows","Langchain","CrewAi","Ai Automation"]
            search_query = settings.get("defaultSearchQuery", keyword_list)

            if not any(platforms.values()):
                platforms = {"freelancer": True}
                logger.info(f"No platforms enabled for {username}, defaulting to freelancer.com")

            platform_map = {
                "freelancer": "freelancer.com",
                "upwork": "upwork.com",
                "peopleperhour": "peopleperhour.com",
                "guru": "guru.com"    
            }

            for platform, enabled in platforms.items():
                if enabled:
                    platform_name = platform_map.get(platform, "freelancer.com")
                    all_jobs_list, new_jobs_found, new_jobs_list = perform_scraping(
                        search_query, platform_name, username
                    )
                    logger.info(f"Scraped {platform_name} for {username}: {len(all_jobs_list)} jobs, {len(new_jobs_list)} new")
                    
        scheduler.start()

        scheduler.add_job(
            scrape_task,
            trigger=IntervalTrigger(minutes=total_minutes),
            id=f"scrape_{username}",
            name=f"Automatic scraping for {username}",
            replace_existing=True
        )
        
        
        logger.info(f"Scheduled automatic scraping for {username} every {total_minutes} minutes")

    except Exception as e:
        logger.error(f"Error scheduling scraping for {username}: {e}")

# def schedule_automatic_scraping(username):
#     try:
#         user_settings = settings_collection.find_one({"username": username})
#         if not user_settings or "settings" not in user_settings:
#             logger.info(f"No settings found for user: {username}, skipping scheduling")
#             return

#         settings = user_settings["settings"]
#         scraping_mode = settings.get("scrapingMode", "manual")

#         # Remove existing job if it exists
#         if scheduler.get_job(f"scrape_{username}"):
#             scheduler.remove_job(f"scrape_{username}")

#         if scraping_mode != "automatic":
#             logger.info(f"Scraping mode for {username} is '{scraping_mode}', no automatic scheduling.")
#             return

#         interval_settings = settings.get("automaticScrapeInterval", {"hours": 0, "minutes": 1})
#         hours = interval_settings.get("hours", 0)
#         minutes = interval_settings.get("minutes", 1)
#         total_minutes = (hours * 60) + minutes

#         if total_minutes <= 0:
#             logger.warning(f"Invalid interval for {username}: {total_minutes} minutes")
#             return

#         def scrape_task():
#             logger.info(f"Running automatic scrape for {username}")
#             platforms = settings.get("selectedPlatforms", {"freelancer": True})
#             keyword_list = ["Copilot", "github"]
#             search_query = settings.get("defaultSearchQuery", keyword_list)

#             if not any(platforms.values()):
#                 platforms = {"freelancer": True}
#                 logger.info(f"No platforms enabled for {username}, defaulting to freelancer.com")

#             platform_map = {
#                 "freelancer": "freelancer.com",
#                 "upwork": "upwork.com",
#                 "peopleperhour": "peopleperhour.com",
#                 "guru": "guru.com"    
#             }

#             for platform, enabled in platforms.items():
#                 if enabled:
#                     platform_name = platform_map.get(platform, "freelancer.com")
#                     all_jobs_list, new_jobs_found, new_jobs_list = perform_scraping(
#                         search_query, platform_name, username
#                     )
#                     logger.info(f"Scraped {platform_name} for {username}: {len(all_jobs_list)} jobs, {len(new_jobs_list)} new")

#         # Schedule the recurring task
#         scheduler.add_job(
#             scrape_task,
#             trigger=IntervalTrigger(minutes=total_minutes),
#             id=f"scrape_{username}",
#             name=f"Automatic scraping for {username}",
#             replace_existing=True
#         )
#         logger.info(f"Scheduled automatic scraping for {username} every {total_minutes} minutes")

#         # Run the scrape_task immediately after scheduling
#         scrape_task()

#     except Exception as e:
#         logger.error(f"Error scheduling scraping for {username}: {e}")

def initialize_schedules():
    """Check all users' settings on startup and schedule automatic tasks."""
    try:
        all_users = settings_collection.find()
        for user in all_users:
            username = user.get("username")
            if username:
                logger.info(f"Checking settings for {username} on startup")
                schedule_automatic_scraping(username)
    except Exception as e:
        logger.error(f"Error initializing schedules: {e}")

# Configure Gemini API key (store it securely in settings.py or environment variables)
GEMINI_API_KEY = "AIzaSyBJEq7saincPvLp9uzMWHsp2tPptl0NCmY"  # Replace with your actual API key
genai.configure(api_key=GEMINI_API_KEY)

@api_view(["POST"])
def generate_proposal(request):
    try:
        # Extract job details from the request
        job_data = request.data.get("job", {})
        if not job_data:
            return Response(
                {"error": "Job data is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Prepare the prompt with job details
        prompt = f"""
        "Generate a highly professional and persuasive freelance proposal for the following job posting:

        Title: {job_data.get('title', 'N/A')}
        Description: {job_data.get('full_description', 'N/A')}
        Skills Required: {job_data.get('skills', 'N/A')}
        Platform: {job_data.get('platform', 'N/A')}
        Proposal Requirements:
        The proposal should be well-structured, engaging, and tailored to the client’s project needs. It should include:
        
        A Personalized Greeting

        Address the client professionally (e.g., "Hi [Client’s Name]")
        Acknowledge their project in a warm and engaging manner.
        A Strong Introduction

        Briefly introduce yourself or your team.
        Highlight your expertise in the required skills.
        A Tailored Approach to the Project

        Explain how you plan to complete the project step by step.
        Mention any tools, frameworks, or strategies you will use.
        If applicable, highlight how you will handle challenges.
        Why You Are the Best Fit

        Showcase relevant experience or past projects.
        Highlight any unique value you bring (e.g., faster delivery, better quality, innovative approach).
        Include portfolio links, testimonials, or case studies if available.
        Budget and Timeline (If Applicable)

        Confirm whether you can work within the provided budget.
        Provide a realistic timeframe for project completion.
        A Closing Statement with a Strong Call to Action

        Express enthusiasm for the opportunity.
        Encourage the client to discuss further, ask questions, or schedule a call.
        End with a professional and warm closing.
        Tone and Style:
        Professional, yet friendly and engaging
        Concise, avoiding unnecessary fluff
        Client-focused, demonstrating understanding of their needs
        The proposal should be polished, error-free, and compelling to maximize the chances of winning the project.
        """

        # Configure generation settings
        generation_config = {
            "temperature": 0.7,
            "top_p": 1,
            "top_k": 1,
            "max_output_tokens": 1000,
        }

        # Initialize the Gemini model
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash-8b",
            generation_config=generation_config,
        )

        response = model.generate_content(prompt)
        proposal_text = response.text.replace("*", "")  # Remove asterisks

        return Response({"proposal": proposal_text}, status=status.HTTP_200_OK)


    except Exception as e:
        print(f"Error generating proposal: {e}")
        return Response(
            {"error": f"Failed to generate proposal: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )        

@csrf_exempt
def login_user(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_identifier = data.get("email")  # Accept username or email
            password = data.get("password")

            if not user_identifier or not password:
                return JsonResponse({"error": "Both fields are required"}, status=400)

            user = meetings_collection.find_one({
                "$or": [{"username": user_identifier}, {"email": user_identifier}]
            })
            
            if not user:
                return JsonResponse({"error": ""}, status=401)

            stored_password = user.get("password")
            if check_password(password, stored_password):  # Use Django's check_password
                user.pop("password", None)
                user["_id"] = str(user["_id"])
                # Generate JWT token
                user_id = user.get("_id")  # Corrected line
                print(f"User ID: {user_id}")
                username = user.get("username")  # Corrected line
                tokens = generate_tokens(user_id, username)
                return JsonResponse({"message": "Login successful", "user": user, "token": tokens}, status=200)
            else:
                return JsonResponse({"error": "Invalid username or password"}, status=401)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method"}, status=405)

@csrf_exempt
def send_otp(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            email = data.get("email", "").strip()

            if not email:
                return JsonResponse({"error": "Email is required."}, status=400)

            otp = generate_otp()
            otp_storage[email] = {"otp": otp, "timestamp": time.time()}

            subject = "Your OTP for Password Reset"
            message = f"Your OTP is {otp}. It is valid for 10 minutes."
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[email],
                fail_silently=False,
            )

            return JsonResponse({"message": "OTP sent successfully!"}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@csrf_exempt
def verify_otp(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            email = data.get("email", "").strip()
            otp = data.get("otp", "").strip()

            if not email or not otp:
                return JsonResponse({"error": "Email and OTP are required."}, status=400)

            stored_otp_data = otp_storage.get(email)
            if not stored_otp_data:
                return JsonResponse({"error": "OTP not found."}, status=400)

            if time.time() - stored_otp_data["timestamp"] > 600:  # OTP valid for 10 minutes
                del otp_storage[email]
                return JsonResponse({"error": "OTP has expired."}, status=400)

            if stored_otp_data["otp"] == otp:
                del otp_storage[email]
                return JsonResponse({"message": "OTP verified successfully!"}, status=200)
            else:
                return JsonResponse({"error": "Invalid OTP."}, status=400)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@csrf_exempt
def reset_password(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            email = data.get("email", "").strip()
            new_password = data.get("newPassword", "").strip()

            if not email or not new_password:
                return JsonResponse({"error": "Email and new password are required."}, status=400)

            user = meetings_collection.find_one({"email": email})
            if not user:
                return JsonResponse({"error": "User not found."}, status=404)

            # Hash the new password using Django's make_password
            hashed_password = make_password(new_password)
            meetings_collection.update_one({"email": email}, {"$set": {"password": hashed_password}})

            return JsonResponse({"message": "Password reset successfully!"}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@csrf_exempt
def createaccount(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            print(data)
            username = data.get("username", "").strip()
            email = data.get("email", "").strip()
            phone = data.get("phone", "").strip()
            password = data.get("password", "").strip()

            if not all([username, email, phone, password]):
                return JsonResponse({"error": "All fields are required."}, status=400)

            existing_user = meetings_collection.find_one(
                {"$or": [{"email": email}, {"username": username}]}
            )
            if existing_user:
                return JsonResponse({"error": "Username or Email already exists."}, status=400)

            hashed_password = make_password(password)  # Use Django's make_password
            user_data = {
                "username": username,
                "email": email,
                "phone": phone,
                "password": hashed_password,
                "settings": {
                "scrapingMode": "automatic",
                "automaticScrapeInterval": {
                  "hours": 0,
                  "minutes": 1
                },
                "notificationsEnabled": True,
                "notificationEmail": email,
                "notificationKeywords": [],
                "selectedPlatforms": {
                  "freelancer": True,
                  "upwork": True,
                  "fiverr": False
                }
            }
            }

            print(user_data)
            meetings_collection.insert_one(user_data)

            admin_data = {
                "username": username,
                "email": email,
                "phone": phone,
            }

            return JsonResponse(admin_data, status=201)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            print(f"Error: {e}")
            return JsonResponse({"error": "Internal server error"}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)




@csrf_exempt
def fetch_admins(request):
    if request.method == "GET":
        try:
            # Fetch all admin details from the collection
            admins = list(
                meetings_collection.find(
                    {},
                    {"_id": 1, "username": 1, "email": 1, "phone": 1, "createdAt": 1},
                )
            )

            # Convert ObjectId to string for JSON serialization
            for admin in admins:
                admin["id"] = str(admin["_id"])
                admin.pop("_id")

            # Return the admin details as a JSON response
            return JsonResponse(admins, safe=False, status=200)

        except Exception as e:
            print(f"Error fetching admins: {e}")
            return JsonResponse({"error": "Internal server error"}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)


@csrf_exempt
def delete_admin(request, admin_id):
    if request.method == "DELETE":
        try:
            result = meetings_collection.delete_one({"_id": ObjectId(admin_id)})
            if result.deleted_count == 1:
                return JsonResponse(
                    {"message": "Admin deleted successfully!"}, status=200
                )
            else:
                return JsonResponse({"error": "Admin not found."}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"error": "Invalid request method."}, status=405)


@csrf_exempt
def edit_admin(request, admin_id):
    if request.method == "PUT":
        try:
            data = json.loads(request.body.decode("utf-8"))
            username = data.get("username", "").strip()
            email = data.get("email", "").strip()
            phone = data.get("phone", "").strip()
            password = data.get("password", "").strip()

            if not all([username, email, phone]):
                return JsonResponse({"error": "All fields are required."}, status=400)

            # Check if the email already exists for another user
            existing_user = meetings_collection.find_one(
                {"_id": {"$ne": ObjectId(admin_id)}, "email": email}
            )
            if existing_user:
                return JsonResponse({"error": "Email already exists."}, status=400)

            update_data = {
                "username": username,
                "email": email,
                "phone": phone,
            }

            if password:
                update_data["password"] = make_password(password)

            result = meetings_collection.update_one(
                {"_id": ObjectId(admin_id)}, {"$set": update_data}
            )

            if result.modified_count == 1:
                return JsonResponse(
                    {"message": "Admin updated successfully!"}, status=200
                )
            else:
                return JsonResponse(
                    {"error": "Admin not found or no changes made."}, status=404
                )
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"error": "Invalid request method."}, status=405)


@csrf_exempt
def get_stats(request):
    if request.method == "GET":
        try:
            total_admins = meetings_collection.count_documents({})
            total_jobs = jobs_collection.count_documents({})
            return JsonResponse(
                {"total_admins": total_admins, "total_jobs": total_jobs}, status=200
            )
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"error": "Invalid request method."}, status=405)


@csrf_exempt
def forgot_send_otp(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            email = data.get("email", "").strip()

            if not email:
                return JsonResponse({"error": "Email is required."}, status=400)

            # Check if the email exists in the database
            user = meetings_collection.find_one({"email": email})
            if not user:
                return JsonResponse({"error": "Invalid email."}, status=400)

            otp = generate_otp()
            otp_storage[email] = {"otp": otp, "timestamp": time.time()}

            subject = "Your OTP for Password Reset"
            message = f"Your OTP is {otp}. It is valid for 10 minutes."
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[email],
                fail_silently=False,
            )

            return JsonResponse({"message": "OTP sent successfully!"}, status=200)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON format"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Invalid request method."}, status=405)

@api_view(["POST"])
def save_user_settings(request):
    try:
        data = request.data
        username = data.get("username", "").strip()
        settings = data.get("settings", {})

        if not username or not settings:
            return Response(
                {"error": "Username and settings are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update or insert settings in the dedicated collection
        settings_collection.update_one(
            {"username": username},
            {"$set": {"settings": settings, "updated_at": datetime.utcnow()}},
            upsert=True
        )

        schedule_automatic_scraping(username)
        
        print(f"Saved settings for user {username}: {settings}")
        return Response(
            {"message": "Settings saved successfully!"},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        print(f"Error saving settings: {e}")
        return Response(
            {"error": f"Failed to save settings: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_user_settings(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return JsonResponse(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user_settings = settings_collection.find_one({"username": username})
        if not user_settings:
            return JsonResponse(
                {"settings": {}},  # Return empty settings if none exist
                status=status.HTTP_200_OK
            )

        settings = user_settings.get("settings", {})
        return JsonResponse({"settings": settings}, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching settings: {e}")
        return JsonResponse(
            {"error": f"Failed to fetch settings: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

initialize_schedules()

# Ensure scheduler shuts down gracefully
import atexit
atexit.register(lambda: scheduler.shutdown())

@api_view(["PUT"])
def restore_job(request, job_id):
    try:
        # Convert the job_id to ObjectId
        job_id = ObjectId(job_id)

        # Update the job status to "Open"
        result = jobs_collection.update_one(
            {"_id": job_id},
            {"$set": {"status": "Open"}}
        )

        if result.modified_count == 0:
            return Response(
                {"error": "Job not found or status already Open."},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {"message": "Job status updated to Open."},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        print(f"Error updating job status: {e}")
        return Response(
            {"error": f"Failed to update job status: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["DELETE"])
def delete_job(request, job_id):
    try:
        # Convert the job_id to ObjectId for jobs_collection
        job_id_obj = ObjectId(job_id)
        # Convert to string for saved_jobs_collection and notes_collection
        job_id_str = str(job_id_obj)

        # Step 1: Verify the job exists in jobs_collection
        job = jobs_collection.find_one({"_id": job_id_obj})
        if not job:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Step 2: Delete the job from jobs_collection
        job_result = jobs_collection.delete_one({"_id": job_id_obj})
        if job_result.deleted_count == 0:
            return Response(
                {"error": "Job not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Step 3: Delete all associated saved jobs from saved_jobs_collection
        saved_result = saved_jobs_collection.delete_many({"job_id": job_id_str})
        saved_count = saved_result.deleted_count

        # Step 4: Delete all associated notes from notes_collection
        notes_result = notes_collection.delete_many({"job_id": job_id_str})
        notes_count = notes_result.deleted_count

        # Log the deletions for debugging
        logger.info(f"Deleted job {job_id_str}: {job_result.deleted_count} job, {saved_count} saved jobs, {notes_count} notes")

        # Prepare response with details of what was deleted
        response_message = {
            "message": "Job and associated data deleted successfully.",
            "details": {
                "job_deleted": True,
                "saved_jobs_deleted": saved_count,
                "notes_deleted": notes_count
            }
        }

        return Response(response_message, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error deleting job and associated data: {e}")
        return Response(
            {"error": f"Failed to delete job and associated data: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
   
import pytz

@api_view(["POST"])
def add_job_note(request):
    try:
        data = request.data
        job_id = data.get("job_id", "").strip()
        username = data.get("username", "").strip()
        note = data.get("note", "").strip()

        # Check required fields (timestamp is no longer required from request)
        if not all([job_id, username, note]):
            return Response(
                {"error": "Job ID, username, and note are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

# Get current UTC time and convert it to IST manually
        utc_now = datetime.utcnow()
        ist_now = utc_now + timedelta(hours=5, minutes=30)  # IST is UTC + 5:30

        note_data = {
            "job_id": job_id,
            "username": username,
            "note": note,
            "timestamp": ist_now  # Now this will reflect your local time
        }

        result = notes_collection.insert_one(note_data)

        return Response(
            {"message": "Note added successfully!", "note_id": str(result.inserted_id)},
            status=status.HTTP_201_CREATED
        )

    except Exception as e:
        print(f"Error adding note: {e}")
        return Response(
            {"error": f"Failed to add note: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_job_notes(request):
    try:
        job_id = request.GET.get("job_id", "").strip()
        if not job_id:
            return Response(
                {"error": "Job ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        notes = list(notes_collection.find({"job_id": job_id}))
        for note in notes:
            note["_id"] = str(note["_id"])
            note["timestamp"] = note["timestamp"].isoformat()

        return Response({"notes": notes}, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching notes: {e}")
        return Response(
            {"error": f"Failed to fetch notes: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_user_noted_jobs(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch all notes by the user
        user_notes = list(notes_collection.find({"username": username}))
        if not user_notes:
            return Response({"noted_jobs": []}, status=status.HTTP_200_OK)

        # Get unique job IDs from the notes
        job_ids = list(set(note["job_id"] for note in user_notes))

        # Fetch only existing jobs from jobs_collection
        noted_jobs = list(jobs_collection.find({"_id": {"$in": [ObjectId(job_id) for job_id in job_ids]}}))
        if not noted_jobs:
            return Response({"noted_jobs": []}, status=status.HTTP_200_OK)

        # Format the jobs for response
        for job in noted_jobs:
            job["_id"] = str(job["_id"])
            job["inserted_at"] = (
                job.get("inserted_at", "N/A").isoformat()
                if isinstance(job.get("inserted_at"), datetime)
                else job.get("inserted_at", "N/A")
            )

        logger.info(f"Fetched {len(noted_jobs)} noted jobs for user {username}")
        return Response({"noted_jobs": noted_jobs}, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error fetching noted jobs: {e}")
        return Response(
            {"error": f"Failed to fetch noted jobs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
@api_view(["DELETE"])
def delete_job_note(request, note_id):
    try:
        # Convert the note_id to ObjectId
        note_id = ObjectId(note_id)

        # Delete the note from the notes_collection
        result = notes_collection.delete_one({"_id": note_id})

        if result.deleted_count == 0:
            return Response(
                {"error": "Note not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {"message": "Note deleted successfully."},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        print(f"Error deleting note: {e}")
        return Response(
            {"error": f"Failed to delete note: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )    

@api_view(["DELETE"])
def delete_job_notes(request, job_id):
    try:
        # Convert job_id to string if needed (since frontend sends it as a string)
        job_id_str = str(job_id)

        # Delete all notes associated with the job_id
        result = notes_collection.delete_many({"job_id": job_id_str})

        if result.deleted_count == 0:
            return Response(
                {"message": "No notes found for this job."},
                status=status.HTTP_200_OK  # Still OK since it's not an error, just no notes
            )

        logger.info(f"Deleted {result.deleted_count} notes for job {job_id_str}")
        return Response(
            {"message": f"Deleted {result.deleted_count} note(s) successfully."},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        logger.error(f"Error deleting notes for job {job_id}: {e}")
        return Response(
            {"error": f"Failed to delete notes: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["POST"])
def save_job(request):
    try:
        data = request.data
        username = data.get("username", "").strip()
        job_id = data.get("job_id", "").strip()

        if not username or not job_id:
            return Response(
                {"error": "Username and job_id are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if job is already saved
        existing_save = saved_jobs_collection.find_one({"username": username, "job_id": job_id})
        if existing_save:
            return Response(
                {"message": "Job already saved."},
                status=status.HTTP_200_OK
            )

        # Save the job
        save_data = {
            "username": username,
            "job_id": job_id,
            "saved_at": datetime.utcnow(),
        }
        result = saved_jobs_collection.insert_one(save_data)

        return Response(
            {"message": "Job saved successfully!", "save_id": str(result.inserted_id)},
            status=status.HTTP_201_CREATED
        )

    except Exception as e:
        print(f"Error saving job: {e}")
        return Response(
            {"error": f"Failed to save job: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["GET"])
def get_saved_jobs(request):
    try:
        username = request.GET.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Fetch all saved job IDs for the user
        saved_jobs = list(saved_jobs_collection.find({"username": username}))
        if not saved_jobs:
            return Response({"saved_jobs": []}, status=status.HTTP_200_OK)

        # Get unique job IDs
        job_ids = [job["job_id"] for job in saved_jobs]

        # Fetch job details
        jobs = list(jobs_collection.find({"_id": {"$in": [ObjectId(job_id) for job_id in job_ids]}}))
        for job in jobs:
            job["_id"] = str(job["_id"])
            job["inserted_at"] = job.get("inserted_at", "N/A").isoformat() if isinstance(job.get("inserted_at"), datetime) else job.get("inserted_at", "N/A")

        return Response({"saved_jobs": jobs}, status=status.HTTP_200_OK)

    except Exception as e:
        print(f"Error fetching saved jobs: {e}")
        return Response(
            {"error": f"Failed to fetch saved jobs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["POST"])
def remove_saved_job(request):
    try:
        data = request.data
        username = data.get("username", "").strip()
        job_id = data.get("job_id", "").strip()

        if not username or not job_id:
            return Response(
                {"error": "Username and job_id are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Remove the saved job
        result = saved_jobs_collection.delete_one({"username": username, "job_id": job_id})
        if result.deleted_count == 0:
            return Response(
                {"error": "Saved job not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(
            {"message": "Job removed from saved list successfully!"},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        print(f"Error removing saved job: {e}")
        return Response(
            {"error": f"Failed to remove saved job: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(["PUT"])
def update_Savedjob_status_to_pending(request, job_id):
    try:
        # Convert the job_id to ObjectId
        job_id = ObjectId(job_id)

        # Get username from request data (assuming it's sent in the body)
        username = request.data.get("username", "").strip()
        if not username:
            return Response(
                {"error": "Username is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Step 1: Update the job status to "Pending" in jobs_collection
        result = jobs_collection.update_one(
            {"_id": job_id},
            {"$set": {"status": "pending"}}
        )

        if result.modified_count == 0:
            return Response(
                {"error": "Job not found or status already Pending."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Step 2: Remove the job from saved_jobs_collection
        remove_result = saved_jobs_collection.delete_one({"username": username, "job_id": str(job_id)})
        if remove_result.deleted_count == 0:
            logger.warning(f"No saved job found for username: {username}, job_id: {job_id} to remove.")
            # Not returning an error here since the status update succeeded; removal is secondary

        return Response(
            {"message": "Job status updated to Pending and removed from saved jobs."},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        print(f"Error updating job status to Pending: {e}")
        return Response(
            {"error": f"Failed to update job status or remove from saved jobs: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def send_notification_email(username, new_jobs, recipient_emails, matched_keywords):
    """Send email notification to multiple recipients with matched jobs."""
    try:
        if not recipient_emails or not new_jobs:
            logger.info(f"No recipients or jobs to notify for {username}")
            return

        subject = "New Job Opportunities Matching Your Keywords!"
        message = f"Hi {username},\n\nNew job opportunities matching your keywords have been found:\n\n"
        for job in new_jobs:
            message += f"- {job['title']} ({', '.join(job['matched_keywords'])}): {job['link']}\n"
        message += "\nVisit our site to view the jobs!"

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=recipient_emails,
            fail_silently=False,
        )
        logger.info(f"Notification email sent to {recipient_emails} for {username}")
    except Exception as email_error:
        logger.error(f"Error sending notification email to {recipient_emails}: {email_error}")     

