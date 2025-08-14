import base64
import os
import requests
import time
import json
import shutil
import os
import tempfile
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("API_KEY")


def create_captcha_task_base64(base64_img):
    url = "https://2captcha.com/in.php"
    data = {
        "key": API_KEY,
        "method": "base64",
        "body": base64_img  # your base64 string here
    }
    response = requests.post(url, data=data)
    result = response.text
    if not result.startswith("OK|"):
        raise Exception(f"2Captcha error: {result}")
    return result.split("|")[1]

def get_captcha_result(request_id):
    url = "https://2captcha.com/res.php"
    params = {
        "key": API_KEY,
        "action": "get",
        "id": request_id,
        "json": 1
    }
    while True:
        response = requests.get(url, params=params)
        result = response.json()
        if result["status"] == 1:
            return result["request"]
        elif result["request"] == "CAPCHA_NOT_READY":
            time.sleep(2)
            continue
        else:
            raise Exception(f"2Captcha error: {result['request']}")

def solve_captcha(base64_img):
    request_id = create_captcha_task_base64(base64_img)
    solved_text = get_captcha_result(request_id)
    return solved_text
