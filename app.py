from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import sqlite3

app = Flask(__name__)
DATABASE = 'bookings.db'

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
            device TEXT NOT NULL,
            issue TEXT,
            booking_time TEXT NOT NULL
        )
        '''
    )
    conn.commit()
    conn.close()

@app.before_first_request
def setup():
    init_db()

@app.route('/', methods=['GET', 'POST'])
def book_inspection():
    if request.method == 'POST':
        # Retrieve form data
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone', '')
        device = request.form.get('device')
        issue = request.form.get('issue', '')
        booking_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Save booking to database
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute(
            '''
            INSERT INTO bookings
            (name, email, phone, device, issue, booking_time)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (name, email, phone, device, issue, booking_time)
        )
        conn.commit()
        conn.close()

        return redirect(url_for('booking_success'))

    # Render the booking form
    return render_template('booking_form.html')

@app.route('/success')
def booking_success():
    return render_template('booking_success.html')

if __name__ == '__main__':
    app.run(debug=True)