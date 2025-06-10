from flask import Flask, render_template, request, redirect, url_for, flash
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this')

# Mail configuration (set these env vars in production)
app.config.update(
    MAIL_SERVER='smtp.example.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USERNAME=os.environ.get('MAIL_USERNAME'),
    MAIL_PASSWORD=os.environ.get('MAIL_PASSWORD'),
    MAIL_DEFAULT_SENDER=('Repair Service', os.environ.get('MAIL_USERNAME'))
)
mail = Mail(app)
serializer = URLSafeTimedSerializer(app.secret_key)

DATABASE = 'bookings.db'

# Initialize the SQLite database
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute(
        '''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            device_brand TEXT NOT NULL,
            device_model TEXT,
            device TEXT,
            issue TEXT,
            booking_time TEXT NOT NULL,
            confirmed INTEGER DEFAULT 0
        )
        '''
    )
    conn.commit()
    conn.close()

# Create tables at startup
with app.app_context():
    init_db()

# Anti-bot honeypot field name
HONEYPOT_FIELD = 'website'

@app.route('/', methods=['GET', 'POST'])
def book_inspection():
    if request.method == 'POST':
        # Bot prevention: honeypot must be empty
        if request.form.get(HONEYPOT_FIELD):
            flash('Bot detected, submission rejected.', 'danger')
            return redirect(url_for('book_inspection'))

        # Retrieve form data
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone', '')
        device_brand = request.form.get('device_brand')
        if device_brand == 'iPhone':
            device_model = request.form.get('iphone_model', '')
        elif device_brand == 'Android':
            device_model = request.form.get('android_brand', '')
        else:
            device_model = ''
        device = request.form.get('device')
        issue = request.form.get('issue', '')
        booking_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Save booking as unconfirmed
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute(
            '''
            INSERT INTO bookings
            (name, email, phone, device_brand, device_model, device, issue, booking_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (name, email, phone, device_brand, device_model, device, issue, booking_time)
        )
        booking_id = c.lastrowid
        conn.commit()
        conn.close()

        # Generate confirmation token valid for 1 hour
        token = serializer.dumps(booking_id, salt='email-confirm')
        confirm_url = url_for('confirm_email', token=token, _external=True)

        # Send confirmation email
        msg = Message('Confirm your repair booking', recipients=[email])
        msg.body = render_template('confirm_email.html', confirm_url=confirm_url)
        mail.send(msg)

        return render_template('booking_pending.html')

    return render_template('booking_form.html')

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        booking_id = serializer.loads(token, salt='email-confirm', max_age=3600)
    except SignatureExpired:
        flash('The confirmation link has expired.', 'warning')
        return redirect(url_for('book_inspection'))
    except BadSignature:
        flash('Invalid confirmation token.', 'danger')
        return redirect(url_for('book_inspection'))

    # Mark booking as confirmed
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('UPDATE bookings SET confirmed = 1 WHERE id = ?', (booking_id,))
    conn.commit()
    conn.close()

    return render_template('booking_success.html')

if __name__ == '__main__':
    app.run(debug=True)