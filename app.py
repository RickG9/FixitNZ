from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import csv
import os

app = Flask(__name__)
BOOKINGS_FILE = 'bookings.txt'

def save_booking(booking_data):
    # Check if the text file exists already.
    file_exists = os.path.isfile(BOOKINGS_FILE)
    # Open the file in append mode. Using csv.DictWriter ensures proper CSV formatting.
    with open(BOOKINGS_FILE, mode='a', newline='') as csvfile:
        fieldnames = ['name', 'email', 'phone', 'device', 'issue', 'booking_time']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        # Write header only if file does not exist.
        if not file_exists:
            writer.writeheader()
        writer.writerow(booking_data)

@app.route('/', methods=['GET', 'POST'])
def book_inspection():
    if request.method == 'POST':
        # Retrieve form data
        name = request.form.get('name')
        email = request.form.get('email')
        # Note: the key for phone may be adjusted to match your form field name.
        phone = request.form.get('phone (optional)', '')
        device = request.form.get('device')
        issue = request.form.get('issue', '')
        booking_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        booking_data = {
            'name': name,
            'email': email,
            'phone': phone,
            'device': device,
            'issue': issue,
            'booking_time': booking_time
        }

        # Save booking details to the text file.
        save_booking(booking_data)
        return redirect(url_for('booking_success'))

    # Render the booking form
    return render_template('booking_form.html')

@app.route('/success')
def booking_success():
    return render_template('booking_success.html')

@app.route('/admin')
def admin_view():
    return render_template('admin_view.html')

if __name__ == '__main__':
    app.run(debug=True) 