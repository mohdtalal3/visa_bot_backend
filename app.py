import os
import time
import threading
import json
from datetime import datetime, timedelta, timezone
from seleniumbase import Driver
from selenium.webdriver.support.ui import Select
from flask import Flask, request, jsonify
from supabase import create_client, Client
import logging
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from utils import solve_captcha
from constants import *
from email_sender import send_email
from dotenv import load_dotenv

# Configure logging to both file and console
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

# Create a formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # Rotating file handler - logs to file with rotation (10MB max, keep 5 files)
        RotatingFileHandler(
            log_dir / 'visa_bot.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        ),
        # Console handler - logs to console
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
PROXY_URL = os.getenv("PROXY_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Initialize Flask app and Supabase client
app = Flask(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Global configuration from environment variables
MAX_CAPTCHA_ATTEMPTS = int(os.getenv("MAX_CAPTCHA_ATTEMPTS", "5"))
AUTO_SUBMIT = os.getenv("AUTO_SUBMIT", "false").lower() == "true"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # seconds check interval
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", "30"))  # seconds retry interval
MAX_CONCURRENT_INSTANCES = int(os.getenv("MAX_CONCURRENT_INSTANCES", "50"))
ENABLE_SCREENSHOTS = os.getenv("ENABLE_SCREENSHOTS", "true").lower() == "true"
SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "screenshots")

# Thread pool for managing multiple Chrome instances
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_INSTANCES)
active_tasks = {}  # Track active tasks by user_id


class VisaBot:
    def __init__(self, user_data):
        self.user_data = user_data
        self.user_id = user_data['id']
        self.username = user_data['username']
        self.password = user_data['password']
        self.security_answers = {
            "food": user_data['favorite_food'],
            "pet": user_data['pet_name'],
            "siblings": user_data['sibling']
        }
        self.email = user_data['email']
        self.check_days = user_data.get('check_days', 1000)  # Default 1000 days if not specified
        self.screenshot_counter = 0
        
        # Create screenshots directory for this user
        if ENABLE_SCREENSHOTS:
            self.user_screenshot_dir = Path(SCREENSHOTS_DIR) / f"user_{self.user_id}"
            
            # Remove existing screenshots for this user if they exist
            if self.user_screenshot_dir.exists():
                import shutil
                shutil.rmtree(self.user_screenshot_dir)
            
            self.user_screenshot_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Screenshots will be saved to: {self.user_screenshot_dir}")
    
    def take_screenshot(self, driver, step_name):
        """Take a screenshot with step name (no timestamp)"""
        if not ENABLE_SCREENSHOTS:
            return
            
        try:
            self.screenshot_counter += 1
            filename = f"{self.screenshot_counter:02d}_{step_name}.png"
            screenshot_path = self.user_screenshot_dir / filename
            
            # Take screenshot using seleniumbase
            driver.save_screenshot(str(screenshot_path))
            logger.info(f"Screenshot saved: {filename}")
            
        except Exception as e:
            logger.error(f"Failed to take screenshot for step '{step_name}': {e}")
        
    def update_status(self, status):
        """Update user status in database"""
        try:
            supabase.table('users').update({'status': status}).eq('id', self.user_id).execute()
            logger.info(f"Updated status to {status} for user {self.user_id}")
        except Exception as e:
            logger.error(f"Failed to update status for user {self.user_id}: {e}")
    
    def update_last_checked(self):
        """Update last checked timestamp in database"""
        try:
            supabase.table('users').update({
                'last_checked': datetime.now(timezone.utc).isoformat()
            }).eq('id', self.user_id).execute()
            logger.info(f"Updated last_checked for user {self.user_id}")
        except Exception as e:
            logger.error(f"Failed to update last_checked for user {self.user_id}: {e}")

    def fill_security_questions(self, driver):
        """Fill in security questions with predefined answers."""
        try:
            self.take_screenshot(driver, "before_security_questions")
            
            driver.wait_for_element_visible(SECURITY_QUESTIONS_DATA, timeout=60)
            driver.wait_for_element_visible(SECURITY_QUESTIONS_SELECTOR, timeout=60)
            
            self.take_screenshot(driver, "security_questions_loaded")
            
            password_inputs = driver.find_elements(SECURITY_QUESTIONS_DATA)
            security_questions_selectors = driver.find_elements(SECURITY_QUESTIONS_SELECTOR)

            for i, (password_input, question) in enumerate(zip(password_inputs, security_questions_selectors)):
                question_text = question.text.lower()  
                logger.info(f"Processing security question: {question_text}")
                
                if "food" in question_text:
                    password_input.send_keys(self.security_answers.get("food"))  
                elif "pet" in question_text:
                    password_input.send_keys(self.security_answers.get("pet")) 
                elif "sibling" in question_text:
                    password_input.send_keys(self.security_answers.get("siblings"))
                
                self.take_screenshot(driver, f"security_question_{i+1}_filled")
            
            self.take_screenshot(driver, "before_security_submit")
            
            driver.wait_for_element_visible(SECURITY_QUESTIONS_SUBMIT_BUTTON, timeout=60)
            driver.click(SECURITY_QUESTIONS_SUBMIT_BUTTON)
            
            self.take_screenshot(driver, "after_security_submit")
            return True
        except Exception as e:
            logger.error(f"Failed to fill security questions for user {self.user_id}: {e}")
            self.take_screenshot(driver, "security_questions_error")
            return False

    def book_appointment(self, driver):
        """Select location and check for available appointments."""
        try:
            self.take_screenshot(driver, "appointment_booking_page_loaded")
            
            driver.wait_for_element_visible('select.select.form-control', timeout=60)
            time.sleep(4)
            self.inject_appointment_booking_script(driver)
            
            self.take_screenshot(driver, "before_consular_post_selection")
            
            # Select consular post from user data or default to Abu Dhabi
            consular_post = self.user_data.get('consular_post', '')
            dropdown = driver.find_element('select.select.form-control')
            select = Select(dropdown)
            driver.wait_for_text(consular_post, "select.select.form-control", timeout=60)
            select.select_by_visible_text(consular_post)

            self.take_screenshot(driver, "consular_post_selected")

            # Wait for submit button to be enabled
            try:
                submit_record=driver.wait_for_element_visible("#submitbtn:not([disabled])", timeout=15)
            except:
                print("Submit button not found or not clickable.")
            #submit_record = driver.find_element("#submitbtn:not([disabled])")
            if submit_record:
                logger.info(f"Submit button is ready for user {self.user_id}")
                
                self.take_screenshot(driver, "appointment_available_submit_ready")
                
                # Send email notification
                submit_record.click()
                print(self.email)
                time.sleep(10)
                
                self.take_screenshot(driver, "after_appointment_submission")
                
                send_email(
                    self.email, 
                    "Visa Appointment Available!", 
                    f"An appointment slot is Booked for {self.username}."
                )
                # Update status to complete (1)
                self.update_status(1)
                return True
            else:
                logger.info(f"No appointments available for user {self.user_id}")
                self.take_screenshot(driver, "no_appointments_available")
                return False
                
        except Exception as e:
            logger.error(f"Failed to book appointment for user {self.user_id}: {e}")
            self.take_screenshot(driver, "appointment_booking_error")
            return False

    def solve_captcha_with_retry(self, driver, max_attempts=MAX_CAPTCHA_ATTEMPTS):
        """Solve CAPTCHA with retry mechanism."""
        attempt = 0
        success = False
        time.sleep(4)
        
        self.take_screenshot(driver, "captcha_page_loaded")
        
        while attempt < max_attempts:
            attempt += 1
            logger.info(f"CAPTCHA attempt {attempt}/{max_attempts} for user {self.user_id}...")

            try:
                # Wait for captcha image
                driver.wait_for_element_visible(CAPTCHA_IMAGE, timeout=60)
                driver.is_element_present(CAPTCHA_IMAGE)

                self.take_screenshot(driver, f"captcha_attempt_{attempt}_image_visible")

                # Extract base64 from image using JavaScript
                get_base64_script = """
                const img = document.querySelector('#captchaImage');
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return canvas.toDataURL();
                """
                image_data_url = driver.execute_script(get_base64_script)
                base64_str = image_data_url.split(",")[1]

                # Solve captcha with error handling
                try:
                    captcha_text = solve_captcha(base64_str)
                    logger.info(f"Solved CAPTCHA: {captcha_text} for user {self.user_id}")
                except Exception as e:
                    error_msg = str(e)
                    if "less than 100 bytes" in error_msg:
                        logger.warning(f"CAPTCHA image too small for user {self.user_id}, refreshing and retrying...")
                    else:
                        logger.error(f"CAPTCHA solving error for user {self.user_id}: {error_msg}")
                    
                    self.take_screenshot(driver, f"captcha_attempt_{attempt}_solve_error")
                    driver.wait_for_element_visible(REFRESH_CAPTCHA_BUTTON, timeout=60)
                    driver.click(REFRESH_CAPTCHA_BUTTON)
                    driver.sleep(4)
                    continue

                # Fill in captcha
                driver.wait_for_element_visible(CAPTCHA_INPUT, timeout=60)
                driver.is_element_present(CAPTCHA_INPUT)
                driver.type(CAPTCHA_INPUT, captcha_text)

                self.take_screenshot(driver, f"captcha_attempt_{attempt}_filled")

                # Submit
                driver.wait_for_element_visible(SUBMIT_BUTTON, timeout=60)
                driver.click(SUBMIT_BUTTON)
                driver.sleep(2)

                self.take_screenshot(driver, f"captcha_attempt_{attempt}_submitted")

                # Check if captcha failed
                if driver.is_element_visible(ERROR_CAPTCHA):
                    logger.warning(f"CAPTCHA failed for user {self.user_id}. Refreshing and retrying...")
                    self.take_screenshot(driver, f"captcha_attempt_{attempt}_failed")
                    driver.wait_for_element_visible(REFRESH_CAPTCHA_BUTTON, timeout=60)
                    driver.click(REFRESH_CAPTCHA_BUTTON)
                    driver.sleep(4)
                    continue
                else:
                    logger.info(f"CAPTCHA accepted for user {self.user_id}")
                    self.take_screenshot(driver, f"captcha_attempt_{attempt}_success")
                    success = True
                    break
                    
            except Exception as e:
                logger.error(f"CAPTCHA attempt failed for user {self.user_id}: {e}")
                self.take_screenshot(driver, f"captcha_attempt_{attempt}_exception")
                if attempt < max_attempts:
                    continue
                break

        return success

    def inject_appointment_booking_script(self, driver):
        """Inject JavaScript for automated appointment booking."""
        script = f"""
    (function () {{
      var daysLimit = {self.check_days};
      var autoSubmit = {str(AUTO_SUBMIT).lower()};

      if (window.__patched_schedule_limit_v2__) return;
      window.__patched_schedule_limit_v2__ = true;

      function fmtDate(d, df) {{
        if (window.moment) return moment(d).format(df);
        try {{
          var dd = new Date(d);
          return (dd.getMonth()+1).toString().padStart(2,'0') + '/' +
                 dd.getDate().toString().padStart(2,'0') + '/' +
                 dd.getFullYear();
        }} catch(e) {{ return String(d); }}
      }}

      var origPopulate = window.populateCalendar;
      if (typeof origPopulate !== "function") return;

      window.populateCalendar = function(data) {{
        var today = new Date();
        var limitDate = new Date();
        limitDate.setDate(limitDate.getDate() + daysLimit);

        // Filter available days within the limit
        var filtered = (Array.isArray(data) ? data : []).filter(function(item) {{
          var dt = new Date(item.Date);
          return dt >= today && dt <= limitDate;
        }});

        console.log("Filtered to", filtered.length, "open dates within", daysLimit, "days.");

        // Store filtered dates globally
        window.__availableDays = filtered;

        // Call original to still update UI
        origPopulate.call(this, filtered);

        // Auto-select earliest date & first time
        if (filtered.length > 0) {{
          var earliest = filtered.sort(function(a,b) {{
            return new Date(a.Date) - new Date(b.Date);
          }})[0];

          jsdata.scheduleDayId = earliest.ID;
          jsdata.Token = window.sd;
          jsdata.Date = earliest.Date;

          getScheduleEntries(function (entries) {{
            var df = (typeof getDateFormat === 'function') ? getDateFormat() : 'MM/DD/YYYY';
            var formattedDate = fmtDate(earliest.Date, df);

            var tableHtml = '<thead><tr><th>Date (' + df + ')</th><th>Time</th><th>Availability</th></tr></thead><tbody>';
            for (var j = 0; j < entries.length; j++) {{
              var id = entries[j].Num;
              var time = entries[j].Time;
              var slots = entries[j].EntriesAvailable;
              tableHtml += '<tr>'
                + '<td><div class="radio"><label>'
                + '<input type="radio" id="' + id + '" name="schedule-entries" value="' + id + '" data-slots="' + slots + '" onclick="onSelectScheduleEntry(this)">' + formattedDate
                + '</label></div></td>'
                + '<td><div>' + time + '</div></td>'
                + '<td><div>' + slots + '</div></td>'
                + '</tr>';
            }}
            tableHtml += '</tbody>';
            $("#time_select").html(tableHtml);
            $("#datepicker-message").text("");
            $("#datepicker-input").val(formattedDate);

            // Auto-select first time slot
            var firstRadio = document.querySelector("#time_select input[type='radio']");
            if (firstRadio) {{
              firstRadio.checked = true;
              firstRadio.click();
              console.log("Auto-selected:", formattedDate, firstRadio.value);

              // Auto-submit if enabled
              if (autoSubmit) {{
                console.log("Auto-submitting...");
                var submitBtn = document.getElementById("submitbtn");
                if (submitBtn) submitBtn.click();
              }}
            }}
          }});
        }} else {{
          console.warn("No open dates within limit.");
        }}
      }};
    }})();
    """
        driver.execute_script(script)

    def login(self, driver):
        """Handle the login process including CAPTCHA solving."""
        try:
            logger.info(f"Starting login process for user {self.user_id}...")
            
            self.take_screenshot(driver, "login_page_loaded")
            
            # Fill username and password
            driver.wait_for_element_visible(SIGN_IN, timeout=60)
            driver.is_element_present(SIGN_IN)
            driver.send_keys(SIGN_IN, self.username)

            self.take_screenshot(driver, "username_filled")

            driver.wait_for_element_visible(PASSWORD_SIGN_IN, timeout=60)
            driver.is_element_present(PASSWORD_SIGN_IN)
            driver.send_keys(PASSWORD_SIGN_IN, self.password)

            self.take_screenshot(driver, "password_filled")

            # Solve CAPTCHA with retry
            if not self.solve_captcha_with_retry(driver):
                self.take_screenshot(driver, "login_captcha_failed")
                raise Exception("CAPTCHA failed after multiple attempts")
            
            self.take_screenshot(driver, "login_successful")
            logger.info(f"Login successful for user {self.user_id}!")
            return True
        except Exception as e:
            logger.error(f"Login failed for user {self.user_id}: {e}")
            self.take_screenshot(driver, "login_error")
            return False

    def run_automation(self):
        """Main automation process for a single user."""
        driver = None
        try:
            logger.info(f"Starting automation for user {self.user_id}")
            self.update_status(0)  # Set status to running
            

            extension_path = "2captcha"
            driver = Driver(uc=True,extension_dir=extension_path,headless2=False,proxy=PROXY_URL)
            
            # Navigate to website
            driver.get("https://www.usvisascheduling.com/signin")
            self.take_screenshot(driver, "website_loaded")
            input()
            # Login
            if not self.login(driver):
                raise Exception("Login failed")

            # Fill security questions
            if not self.fill_security_questions(driver):
                raise Exception("Failed to fill security questions")
            
            time.sleep(3)
            self.take_screenshot(driver, "after_security_questions_delay")
            
            # Navigate to appointment booking page
            #driver.get("https://www.usvisascheduling.com/en-US/schedule/?reschedule=true")


            try:
                driver.wait_for_element_present("#reschedule_appointment",timeout=30)
            except:
                driver.refresh()
                driver.wait_for_element_present("#reschedule_appointment",timeout=60)
            driver.click("#reschedule_appointment")
            self.take_screenshot(driver, "appointment_schedule_page_loaded")
            
            # Check for appointments
            appointment_found = self.book_appointment(driver)
            
            if not appointment_found:
                logger.info(f"No appointments found for user {self.user_id}, will retry later")
                # Don't change status if no appointments found, keep it as running (0)
            
            # Update last checked time
            self.update_last_checked()
            
        except Exception as e:
            logger.error(f"Automation failed for user {self.user_id}: {e}")
            # Keep status as running (0) so it will be retried
            self.update_last_checked()
            if driver:
                self.take_screenshot(driver, "automation_error")
            
        finally:
            if driver:
                try:
                    self.take_screenshot(driver, "automation_completed")
                    driver.quit()
                except:
                    pass
            
            # Remove from active tasks
            if self.user_id in active_tasks:
                del active_tasks[self.user_id]
            
            logger.info(f"Automation completed for user {self.user_id}")


def process_user(user_data):
    """Process a single user in a separate thread."""
    user_id = user_data['id']
    
    if user_id in active_tasks:
        logger.info(f"User {user_id} is already being processed, skipping")
        return
    
    # Mark as active
    active_tasks[user_id] = datetime.now(timezone.utc)
    
    try:
        bot = VisaBot(user_data)
        print(user_data)
        bot.run_automation()
    except Exception as e:
        logger.error(f"Failed to process user {user_id}: {e}")
        if user_id in active_tasks:
            del active_tasks[user_id]


def scan_and_process_users():
    """Scan database for users that need processing and launch Chrome instances."""
    try:
        # Get users with status 0 (running) that haven't been checked recently
        current_time = datetime.now(timezone.utc)
        cutoff_time = current_time - timedelta(seconds=RETRY_INTERVAL)
        
        response = supabase.table('users').select('*').eq('status', 0).execute()
        
        for user in response.data:
            user_id = user['id']
            last_checked = user.get('last_checked')
            
            # Skip if already being processed
            if user_id in active_tasks:
                continue
            
            # Check if enough time has passed since last check
            if last_checked:
                try:
                    # Handle different timestamp formats from database
                    if last_checked.endswith('Z'):
                        # ISO format with Z
                        last_checked_dt = datetime.fromisoformat(last_checked.replace('Z', '+00:00'))
                    elif last_checked.endswith('+00'):
                        # Database format: 2025-08-13 15:04:11.463593+00
                        last_checked_dt = datetime.fromisoformat(last_checked + ':00')
                    elif '+00:00' in last_checked:
                        # Standard ISO format
                        last_checked_dt = datetime.fromisoformat(last_checked)
                    else:
                        # Assume UTC if no timezone info
                        last_checked_dt = datetime.fromisoformat(last_checked).replace(tzinfo=timezone.utc)
                    
                    # Ensure timezone-aware for comparison
                    if last_checked_dt.tzinfo is None:
                        last_checked_dt = last_checked_dt.replace(tzinfo=timezone.utc)
                    
                    if current_time - last_checked_dt < timedelta(seconds=RETRY_INTERVAL):
                        continue
                        
                except ValueError as e:
                    logger.warning(f"Failed to parse last_checked timestamp '{last_checked}' for user {user_id}: {e}")
                    # Continue processing if timestamp parsing fails
            
            logger.info(f"Submitting user {user_id} for processing")
            executor.submit(process_user, user)
            
    except Exception as e:
        logger.error(f"Error in scan_and_process_users: {e}")


def database_scanner():
    """Background thread that continuously scans the database."""
    while True:
        try:
            scan_and_process_users()
            time.sleep(CHECK_INTERVAL)  # Wait before next scan
        except Exception as e:
            logger.error(f"Error in database scanner: {e}")
            time.sleep(CHECK_INTERVAL)


# Flask routes
@app.route('/receive-data', methods=['POST'])
def receive_data():
    """Webhook endpoint to receive user data and save to database"""
    try:
        data = request.get_json()
        
        logger.info(f"Received data: {data}")
        
        # Prepare data for database
        user_data = {
            'username': data.get('username'),
            'password': data.get('password'),
            'pet_name': data.get('pet_name'),
            'favorite_food': data.get('favorite_food'),
            'sibling': data.get('sibling'),
            'consular_post': data.get('consular_post', 'ABU DHABI'),  # Default to ABU DHABI if not provided
            'check_days': data.get('check_days', 1000),  # Default 1000 days if not specified
            'email': data.get('email'),
            'status': data.get('status', 0),  # Default to 0 (running)
            'created_at': data.get('created_at', datetime.now(timezone.utc).isoformat()),
            'last_checked': data.get('last_checked', datetime.now(timezone.utc).isoformat())
        }
        
        # Save to Supabase
        response = supabase.table('users').insert(user_data).execute()
        
        logger.info(f"User data saved successfully: {response.data}")
        
        return jsonify({
            'success': True,
            'message': 'Data received and saved successfully',
            'data': response.data
        }), 200
        
    except Exception as e:
        logger.error(f"Error in receive_data: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/update-status', methods=['POST'])
def update_status():
    """Endpoint to update user status"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        status = data.get('status')
        
        # Update status in database
        response = supabase.table('users').update({'status': status}).eq('id', user_id).execute()
        
        return jsonify({
            'success': True,
            'message': 'Status updated successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/delete-user', methods=['DELETE'])
def delete_user():
    """Endpoint to delete a user from the database"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'User ID is required'
            }), 400
        
        # Delete user from database
        response = supabase.table('users').delete().eq('id', user_id).execute()
        
        return jsonify({
            'success': True,
            'message': 'User deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/active-tasks', methods=['GET'])
def get_active_tasks():
    """Get currently active tasks for monitoring"""
    return jsonify({
        'active_tasks': len(active_tasks),
        'tasks': {str(k): v.isoformat() for k, v in active_tasks.items()}
    })


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'active_tasks': len(active_tasks),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


if __name__ == '__main__':
    # Start the database scanner in a separate thread
    scanner_thread = threading.Thread(target=database_scanner, daemon=True)
    scanner_thread.start()
    
    logger.info("Starting Flask server and database scanner...")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info(f"Retry interval: {RETRY_INTERVAL} seconds")
    logger.info(f"Max concurrent instances: {MAX_CONCURRENT_INSTANCES}")
    logger.info(f"Screenshots enabled: {ENABLE_SCREENSHOTS}")
    if ENABLE_SCREENSHOTS:
        logger.info(f"Screenshots directory: {SCREENSHOTS_DIR}")
        # Ensure screenshots directory exists
        Path(SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Get Flask configuration from environment
    flask_host = os.getenv("FLASK_HOST", "0.0.0.0")
    flask_port = int(os.getenv("FLASK_PORT", "5001"))
    flask_debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    
    # Start Flask app
    app.run(debug=flask_debug, host=flask_host, port=flask_port, threaded=True)
