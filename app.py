from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from datetime import datetime
import sqlite3
import csv
import io
import os
import secrets
import hashlib
import requests  # Add this import for reCAPTCHA verification
from datetime import timedelta

app = Flask(__name__)
app.secret_key = 'hAcgqVoa+evvQeX3g/RiciTJk0m1fzlAuZysqpwbmU+7mmD8gc9JLtQP2hLBqwCZ'  # TODO: move to environment variable

# SQLite file path: On Vercel (serverless, read-only code FS) only /tmp is writable.
# This allows the function to start instead of crashing on init_db(), but data is NOT persistent between cold starts.
BASE_DB_DIR = '/tmp' if os.environ.get('VERCEL') else os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DB_DIR, 'bookings.db')

# reCAPTCHA v3 configuration (keys can be disabled via settings)
RECAPTCHA_SITE_KEY = '6LfrKpQrAAAAAMmi51xrVRwb0LBFDBw-eoPPDrLc'
RECAPTCHA_SECRET_KEY = '6LfrKpQrAAAAAGS59CTJ0wiiBIQGJyTlflsvctzI'
RECAPTCHA_VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
RECAPTCHA_THRESHOLD = 0.5  # Minimum score required (0.0 = bot, 1.0 = human)

# Admin credentials and session management
ADMIN_USERNAME = 'admin' # Please change this to a secure username
ADMIN_PASSWORD = 'admin' # Please change this to a secure password
ADMIN_SESSIONS = {}  # Store active admin sessions

def generate_session_token():
    """Generate a secure session token."""
    return secrets.token_urlsafe(32)

def validate_admin_session():
    """Validate admin session token."""
    token = session.get('admin_token')
    if not token or token not in ADMIN_SESSIONS:
        return False
    return True

def init_db():
    """Initialize the database with the bookings table."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            device TEXT NOT NULL,
            repair TEXT,
            issue TEXT,
            booking_time TEXT NOT NULL,
            created_at TEXT  -- added later for distinguishing submission vs scheduled time
        )
    ''')
    # Settings table for feature toggles (e.g., reCAPTCHA)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    # Insert default setting for reCAPTCHA (disabled by default so form still works if keys broken)
    cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) VALUES ('recaptcha_enabled', '0')
    ''')
    # Migration: ensure created_at column exists (older deployments may miss it)
    cursor.execute("PRAGMA table_info(bookings)")
    cols = [r[1] for r in cursor.fetchall()]
    if 'created_at' not in cols:
        try:
            cursor.execute('ALTER TABLE bookings ADD COLUMN created_at TEXT')
            cursor.execute('UPDATE bookings SET created_at = booking_time WHERE created_at IS NULL OR created_at = \"\"')
        except sqlite3.OperationalError:
            pass  # ignore if cannot alter (unlikely)

    # Default availability / scheduling settings (stored in general settings table)
    defaults = {
        'availability_days': '0,1,2,3,4',  # Monday-Friday
        'availability_start_hour': '8',    # 08:00
        'availability_end_hour': '20',     # 20:00 (8 PM)
        'cooldown_minutes': '120',         # 2 hour cooldown
        'max_bookings_per_day': '7'        # maximum 7 bookings per day
    }
    for k, v in defaults.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))

    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return default
    return row[0]

def set_setting(key, value):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    conn.commit()
    conn.close()

def recaptcha_is_enabled():
    return get_setting('recaptcha_enabled', '0') == '1'

# Make settings accessor available inside Jinja templates (needed for admin dashboard)
app.jinja_env.globals['get_setting'] = get_setting

def save_booking(booking_data):
    """Save booking data to SQLite database."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO bookings (name, email, phone, device, repair, issue, booking_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        booking_data['name'],
        booking_data['email'],
        booking_data['phone'],
        booking_data['device'],
        booking_data['repair'],
        booking_data['issue'],
        booking_data['booking_time'],  # scheduled time
        booking_data['created_at']     # time of submission
    ))
    conn.commit()
    conn.close()

def get_all_bookings():
    """Retrieve all bookings from the database."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    # Include id and created_at for transparency
    cursor.execute('SELECT id, name, email, phone, device, repair, issue, booking_time, created_at FROM bookings ORDER BY datetime(booking_time) ASC')
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def delete_booking(booking_id: int):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bookings WHERE id = ?', (booking_id,))
    conn.commit()
    conn.close()

def clear_all_bookings():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bookings')
    conn.commit()
    conn.close()

def recreate_bookings_table():
    """Drop and recreate bookings table (hard reset)."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('DROP TABLE IF EXISTS bookings')
    cursor.execute('''
        CREATE TABLE bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            device TEXT NOT NULL,
            repair TEXT,
            issue TEXT,
            booking_time TEXT NOT NULL,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

# ---------------- Availability & Scheduling Logic ---------------- #
def get_availability_settings():
    """Return availability settings with defaults enforced."""
    settings = {
        'availability_days': get_setting('availability_days', '0,1,2,3,4'),
        'availability_start_hour': int(get_setting('availability_start_hour', '8')),
        'availability_end_hour': int(get_setting('availability_end_hour', '20')),
        'cooldown_minutes': int(get_setting('cooldown_minutes', '120')),
        'max_bookings_per_day': int(get_setting('max_bookings_per_day', '7'))
    }
    # Parse days to int list safely
    try:
        settings['days_list'] = [int(d) for d in settings['availability_days'].split(',') if d.strip() != '']
    except ValueError:
        settings['days_list'] = [0,1,2,3,4]
    return settings

def fetch_existing_booking_times_for_date(target_date):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('SELECT booking_time FROM bookings WHERE date(booking_time) = ?', (target_date.strftime('%Y-%m-%d'),))
    rows = cursor.fetchall()
    conn.close()
    times = []
    for (bt,) in rows:
        try:
            times.append(datetime.strptime(bt, '%Y-%m-%d %H:%M:%S'))
        except ValueError:
            # fallback; ignore malformed
            continue
    return times

def is_slot_conflicting(candidate_dt, existing_times, cooldown_minutes):
    for existing in existing_times:
        delta = abs((candidate_dt - existing).total_seconds()) / 60.0
        if delta < cooldown_minutes:  # must have at least cooldown gap
            return True
    return False

def generate_available_slots_for_date(target_date):
    settings = get_availability_settings()
    # Date constraints
    today = datetime.now().date()
    if target_date <= today or target_date > today + timedelta(days=14):
        return []
    if target_date.weekday() not in settings['days_list']:
        return []
    existing = fetch_existing_booking_times_for_date(target_date)
    if len(existing) >= settings['max_bookings_per_day']:
        return []
    slots = []
    start_hour = settings['availability_start_hour']
    end_hour = settings['availability_end_hour']
    cooldown = settings['cooldown_minutes']
    # Generate hourly slots inclusive of start, exclusive of end
    for hour in range(start_hour, end_hour):
        candidate_dt = datetime(year=target_date.year, month=target_date.month, day=target_date.day, hour=hour, minute=0, second=0)
        if is_slot_conflicting(candidate_dt, existing, cooldown):
            continue
        # Would adding this exceed daily max? (Count existing + this hypothetical)
        if len(existing) + len(slots) >= settings['max_bookings_per_day']:
            break
        slots.append(candidate_dt.strftime('%H:%M'))
    return slots

def validate_selected_slot(scheduled_date_str, scheduled_time_str):
    settings = get_availability_settings()
    try:
        target_date = datetime.strptime(scheduled_date_str, '%Y-%m-%d').date()
        scheduled_dt = datetime.strptime(f"{scheduled_date_str} {scheduled_time_str}:00", '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False, 'Invalid date or time format.'
    today = datetime.now().date()
    if target_date <= today:
        return False, 'Selected date must be after today.'
    if target_date > today + timedelta(days=14):
        return False, 'Date must be within the next 2 weeks.'
    if target_date.weekday() not in settings['days_list']:
        return False, 'Selected day is not available.'
    if not (settings['availability_start_hour'] <= scheduled_dt.hour < settings['availability_end_hour']):
        return False, 'Selected time is outside available hours.'
    existing = fetch_existing_booking_times_for_date(target_date)
    if len(existing) >= settings['max_bookings_per_day']:
        return False, 'Daily booking limit reached for this date.'
    if is_slot_conflicting(scheduled_dt, existing, settings['cooldown_minutes']):
        return False, f'Selected time conflicts with existing bookings. Please choose a different slot.'
    return True, None

@app.route('/', methods=['GET', 'POST'])
def book_inspection():
    if request.method == 'POST':
        # Retrieve form data
        name = request.form.get('name')
        email = request.form.get('email')
        # Updated to match the actual form field name
        phone = request.form.get('phone', '')
        device = request.form.get('device')
        repair = request.form.get('repair', '')
        issue = request.form.get('issue', '')
        # New scheduling fields
        scheduled_date = request.form.get('scheduled_date')
        scheduled_time = request.form.get('scheduled_time')  # HH:MM

        # Validate scheduling selection
        if not scheduled_date or not scheduled_time:
            flash('Please select a date and time for your booking.', 'error')
            return render_template('booking_form.html', recaptcha_enabled=recaptcha_is_enabled(), site_key=RECAPTCHA_SITE_KEY if recaptcha_is_enabled() else None)
        valid_slot, error_msg = validate_selected_slot(scheduled_date, scheduled_time)
        if not valid_slot:
            flash(error_msg, 'error')
            return render_template('booking_form.html', recaptcha_enabled=recaptcha_is_enabled(), site_key=RECAPTCHA_SITE_KEY if recaptcha_is_enabled() else None)
        booking_time = f"{scheduled_date} {scheduled_time}:00"  # Scheduled time stored in booking_time
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Optional reCAPTCHA verification if enabled
        if recaptcha_is_enabled():
            token = request.form.get('g-recaptcha-response')
            if not token:
                flash('Security verification failed. Please retry.', 'error')
                return render_template('booking_form.html', recaptcha_enabled=True, site_key=RECAPTCHA_SITE_KEY)
            try:
                verify_resp = requests.post(
                    RECAPTCHA_VERIFY_URL,
                    data={
                        'secret': RECAPTCHA_SECRET_KEY,
                        'response': token,
                        'remoteip': request.remote_addr
                    },
                    timeout=5
                )
                verify_json = verify_resp.json()
                success = verify_json.get('success')
                score = verify_json.get('score', 0)
                action = verify_json.get('action')
                if not success or score < RECAPTCHA_THRESHOLD or action != 'booking_submit':
                    flash('Suspicious activity detected. Please try again.', 'error')
                    return render_template('booking_form.html', recaptcha_enabled=True, site_key=RECAPTCHA_SITE_KEY)
            except requests.RequestException:
                flash('Security service unavailable. Please try again later.', 'error')
                return render_template('booking_form.html', recaptcha_enabled=True, site_key=RECAPTCHA_SITE_KEY)
        
        # Check if terms and conditions are accepted
        terms_accepted = request.form.get('terms_accepted')
        if not terms_accepted:
            flash('You must accept the Terms and Conditions to proceed.', 'error')
            return render_template('booking_form.html', recaptcha_enabled=recaptcha_is_enabled(), site_key=RECAPTCHA_SITE_KEY if recaptcha_is_enabled() else None)

        booking_data = {
            'name': name,
            'email': email,
            'phone': phone,
            'device': device,
            'repair': repair,
            'issue': issue,
            'booking_time': booking_time,
            'created_at': created_at
        }
        # Save booking details to the database
        save_booking(booking_data)
        return redirect(url_for('booking_success'))

    # Render the booking form
    return render_template('booking_form.html', recaptcha_enabled=recaptcha_is_enabled(), site_key=RECAPTCHA_SITE_KEY if recaptcha_is_enabled() else None)

@app.route('/success')
def booking_success():
    return render_template('booking_success.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin_view():
    if request.method == 'POST':
        username = request.form.get('admin_username')
        password = request.form.get('admin_password')
        
        # Check if credentials are correct
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            # Generate a secure session token
            token = generate_session_token()
            session['admin_token'] = token
            session['admin_logged_in'] = True
            ADMIN_SESSIONS[token] = {
                'username': username,
                'login_time': datetime.now().isoformat(),
                'ip_address': request.remote_addr
            }
            flash('Login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('admin_form.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    # Check if admin is logged in with valid session
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    return render_template('admin_dashboard.html', recaptcha_enabled=recaptcha_is_enabled())

@app.route('/admin/recaptcha/toggle', methods=['POST'])
def toggle_recaptcha():
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    # Flip setting
    new_value = '0' if recaptcha_is_enabled() else '1'
    set_setting('recaptcha_enabled', new_value)
    flash(f"reCAPTCHA {'enabled' if new_value == '1' else 'disabled'} successfully", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/bookings/plain', methods=['POST'])
def admin_bookings_plain():
    """Display bookings in plain text after password confirmation."""
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    password = request.form.get('admin_password_confirm')
    if password != ADMIN_PASSWORD:
        flash('Password confirmation failed.', 'error')
        return redirect(url_for('admin_dashboard'))
    bookings = get_all_bookings()
    return render_template('admin_bookings.html', bookings=bookings, recaptcha_enabled=recaptcha_is_enabled())

@app.route('/admin/bookings/delete/<int:booking_id>', methods=['POST'])
def admin_delete_booking(booking_id):
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    password = request.form.get('admin_password_confirm')
    if password != ADMIN_PASSWORD:
        flash('Password confirmation failed.', 'error')
        return redirect(url_for('admin_dashboard'))
    delete_booking(booking_id)
    flash(f'Booking {booking_id} deleted.', 'success')
    # Re-fetch bookings for updated view
    bookings = get_all_bookings()
    return render_template('admin_bookings.html', bookings=bookings, recaptcha_enabled=recaptcha_is_enabled())

@app.route('/admin/bookings/clear', methods=['POST'])
def admin_clear_bookings():
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    password = request.form.get('admin_password_confirm')
    if password != ADMIN_PASSWORD:
        flash('Password confirmation failed.', 'error')
        return redirect(url_for('admin_dashboard'))
    clear_all_bookings()
    flash('All bookings cleared.', 'success')
    bookings = get_all_bookings()
    return render_template('admin_bookings.html', bookings=bookings, recaptcha_enabled=recaptcha_is_enabled())

@app.route('/admin/bookings/reset', methods=['POST'])
def admin_reset_bookings():
    """Drop and recreate the bookings table after strong confirmation."""
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    password = request.form.get('admin_password_confirm')
    confirm_text = request.form.get('confirm_text', '')
    if password != ADMIN_PASSWORD:
        flash('Password confirmation failed.', 'error')
        return redirect(url_for('admin_dashboard'))
    if confirm_text.strip().upper() != 'RESET':
        flash('Type RESET in the confirmation box to proceed.', 'error')
        return redirect(url_for('admin_dashboard'))
    recreate_bookings_table()
    flash('Bookings table dropped and recreated (all data permanently removed).', 'success')
    bookings = get_all_bookings()
    return render_template('admin_bookings.html', bookings=bookings, recaptcha_enabled=recaptcha_is_enabled())

@app.route('/admin/logout')
def admin_logout():
    # Remove session token from active sessions
    token = session.get('admin_token')
    if token and token in ADMIN_SESSIONS:
        del ADMIN_SESSIONS[token]
    
    session.pop('admin_logged_in', None)
    session.pop('admin_token', None)
    flash('Logged out successfully', 'success')
    return redirect(url_for('admin_view'))

@app.route('/terms')
def terms_and_conditions():
    return render_template('terms.html')

@app.route('/api/available_slots')
def api_available_slots():
    """Return JSON list of available slots for a given date (YYYY-MM-DD)."""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'error': 'Missing date parameter'}), 400
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    slots = generate_available_slots_for_date(target_date)
    return jsonify({'date': date_str, 'slots': slots})

@app.route('/admin/availability/update', methods=['POST'])
def update_availability():
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    # Extract and persist settings
    try:
        days = request.form.getlist('days')  # list of strings numbers
        if not days:
            raise ValueError('Select at least one day')
        availability_days = ','.join(sorted(set(days), key=lambda x: int(x)))
        start_hour = int(request.form.get('start_hour'))
        end_hour = int(request.form.get('end_hour'))
        cooldown_minutes = int(request.form.get('cooldown_minutes'))
        max_bookings_per_day = int(request.form.get('max_bookings_per_day'))
        if start_hour >= end_hour:
            raise ValueError('Start hour must be before end hour')
        if cooldown_minutes < 0:
            raise ValueError('Cooldown must be positive')
        if max_bookings_per_day <= 0:
            raise ValueError('Max bookings per day must be positive')
        set_setting('availability_days', availability_days)
        set_setting('availability_start_hour', str(start_hour))
        set_setting('availability_end_hour', str(end_hour))
        set_setting('cooldown_minutes', str(cooldown_minutes))
        set_setting('max_bookings_per_day', str(max_bookings_per_day))
        flash('Availability settings updated successfully', 'success')
    except Exception as e:
        flash(f'Error updating settings: {e}', 'error')
    return redirect(url_for('admin_dashboard'))

# Initialize database when module is imported (for production)
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False) 