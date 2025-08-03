from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
from datetime import datetime
import sqlite3
import csv
import io
import os
import secrets
import hashlib
import requests  # Add this import for reCAPTCHA verification

app = Flask(__name__)
app.secret_key = 'hAcgqVoa+evvQeX3g/RiciTJk0m1fzlAuZysqpwbmU+7mmD8gc9JLtQP2hLBqwCZ'  # Change this to a secure random key
DATABASE = 'bookings.db'

# reCAPTCHA v3 configuration
RECAPTCHA_SITE_KEY = '6LfrKpQrAAAAAMmi51xrVRwb0LBFDBw-eoPPDrLc'
RECAPTCHA_SECRET_KEY = '6LfrKpQrAAAAAGS59CTJ0wiiBIQGJyTlflsvctzI'  # You need to get this from Google reCAPTCHA console
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
            booking_time TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_booking(booking_data):
    """Save booking data to SQLite database."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO bookings (name, email, phone, device, repair, issue, booking_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        booking_data['name'],
        booking_data['email'],
        booking_data['phone'],
        booking_data['device'],
        booking_data['repair'],
        booking_data['issue'],
        booking_data['booking_time']
    ))
    conn.commit()
    conn.close()

def get_all_bookings():
    """Retrieve all bookings from the database."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('SELECT name, email, phone, device, repair, issue, booking_time FROM bookings')
    bookings = cursor.fetchall()
    conn.close()
    return bookings

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
        booking_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Check if terms and conditions are accepted
        terms_accepted = request.form.get('terms_accepted')
        if not terms_accepted:
            flash('You must accept the Terms and Conditions to proceed.', 'error')
            return render_template('booking_form.html')

        booking_data = {
            'name': name,
            'email': email,
            'phone': phone,
            'device': device,
            'repair': repair,
            'issue': issue,
            'booking_time': booking_time
        }
        # Save booking details to the database
        save_booking(booking_data)
        return redirect(url_for('booking_success'))

    # Render the booking form
    return render_template('booking_form.html')

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
    
    return render_template('admin_dashboard.html')

@app.route('/admin/download_csv')
def download_csv():
    # Check if admin is logged in with valid session
    if not validate_admin_session():
        flash('Please log in first', 'error')
        return redirect(url_for('admin_view'))
    
    # Get all bookings from database
    bookings = get_all_bookings()
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Name', 'Email', 'Phone', 'Device', 'Repair', 'Issue', 'Booking Time'])
    
    # Write data
    for booking in bookings:
        writer.writerow(booking)
    
    # Create response
    csv_content = output.getvalue()
    output.close()
    
    response = make_response(csv_content)
    response.headers['Content-Disposition'] = 'attachment; filename=bookings.csv'
    response.headers['Content-Type'] = 'text/csv'
    
    return response

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

# Initialize database when module is imported (for production)
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False) 