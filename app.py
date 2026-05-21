from flask import Flask, render_template, redirect, url_for, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
from flask_bcrypt import Bcrypt
from flask import flash
from flask import make_response
import csv
from io import TextIOWrapper
from flask_migrate import Migrate
import os
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from functools import wraps
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from flask_mail import Mail, Message
import io
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()
app = Flask(__name__)
app.config['MAIL_SERVER']   = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']     = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']  = os.getenv('MAIL_USE_TLS', 'True')
app.config['MAIL_USE_SSL']  = os.getenv('MAIL_USE_SSL', 'False')
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

app.config['SECRET_KEY']              = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
db     = SQLAlchemy(app)
bcrypt = Bcrypt(app)
lm     = LoginManager()
lm.init_app(app)
lm.login_view = 'login'


ROLE_ADMIN   = 'admin'
ROLE_MANAGER = 'manager'
ROLE_STAFF   = 'staff'
ADMIN_ROLES  = {ROLE_ADMIN, ROLE_MANAGER}

migrate = Migrate(app, db)
# ═══════════════════════════════════════════════════════════
#  DECORATORS
# ═══════════════════════════════════════════════════════════
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.role not in ADMIN_ROLES:
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════
@lm.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


from itsdangerous import URLSafeTimedSerializer

s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

class User(db.Model, UserMixin):
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role     = db.Column(db.String(20), nullable=False)

    def get_reset_token(self):
        return s.dumps(self.email)

    @staticmethod
    def verify_reset_token(token, expires_sec=1800):
        try:
            email = s.loads(token, max_age=expires_sec)
        except:
            return None
        return User.query.filter_by(email=email).first()

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLES
    
    def __repr__(self):
        return f"<User {self.name} ({self.role})> password: {self.password}"


class Treatment(db.Model):
    """Master list of services offered."""
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)


# ── Junction table: Visit ↔ Treatment ───────────────────
visit_treatments = db.Table('visit_treatments',
    db.Column('visit_id',     db.Integer, db.ForeignKey('visit.id')),
    db.Column('treatment_id', db.Integer, db.ForeignKey('treatment.id'))
)


class Customer(db.Model):
    """
    WHO — Identity record. One per person.
    Phone + Name is the unique identifier to handle cases where same phone comes with different name.
    """
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20),nullable=False,index=True)  
    email      = db.Column(db.String(120))
    age        = db.Column(db.Integer)
    dob        = db.Column(db.Date)
    gender     = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # All visits by this customer
    visits = db.relationship('Visit', backref='customer', lazy='dynamic',
                             order_by='Visit.visit_date.desc()')

    @property
    def total_visits(self):
        return self.visits.count()

    @property
    def total_spent(self):
        return sum(float(v.total_amount) for v in self.visits)

    @property
    def last_visit(self):
        return self.visits.first()

    @property
    def is_returning(self):
        # Check if this phone number has been used before (across all customer records with same phone)
        # This treats all visits from the same phone as a returning customer
        all_visits_with_phone = db.session.query(Visit).join(Customer).filter(
            Customer.phone == self.phone
        ).count()
        return all_visits_with_phone > 1


class Visit(db.Model):
    """
    WHAT — Transaction record. One per salon visit.
    Multiple visits per customer are expected and correct.
    """
    id             = db.Column(db.Integer, primary_key=True)
    customer_id    = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    staff_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    visit_date     = db.Column(db.DateTime, default=lambda: __import__('datetime').datetime.now(
                          __import__('datetime').timezone(
                          __import__('datetime').timedelta(hours=5,minutes=30))).replace(tzinfo=None))
    total_amount   = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(20))
    notes          = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    # Staff who handled
    staff      = db.relationship('User', backref='visits')
    # Treatments taken this visit
    treatments = db.relationship('Treatment', secondary=visit_treatments, backref='visits')


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════
def fmt_inr(val):
    try:
        return f"{float(val):,.0f}"
    except Exception:
        return "0"


def normalize_phone(raw: str) -> str:
    """
    Strip everything except digits, then keep only the last 10 digits.
    Examples:
        '+91 98765 43210'  → '9876543210'
        '91-9876543210'    → '9876543210'
        '09876543210'      → '9876543210'
        '9876543210'       → '9876543210'
    """
    digits = ''.join(filter(str.isdigit, raw or ''))
    # Drop leading country code 91 if present and result would be 12 digits
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():

    if request.method == 'POST':

        email = request.form['email']

        user = User.query.filter_by(email=email).first()

        if user:

            token = user.get_reset_token()

            reset_link = url_for(
                'reset_password',
                token=token,
                _external=True
            )
            print("RESET LINK:", reset_link)

            msg = Message(
                'Password Reset - Glow Park',
                sender=app.config['MAIL_USERNAME'],
                recipients=[user.email]
            )

            msg.body = f"""
Reset your password:

{reset_link}

Link valid for 30 minutes.
"""

            mail.send(msg)

        flash('Reset link sent to email.', 'success')

        return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET','POST'])
def reset_password(token):

    user = User.verify_reset_token(token)

    if not user:
        flash('Invalid or expired token.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':

        password = request.form['password']
        confirm  = request.form['confirm']

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return redirect(request.url)

        hashed = bcrypt.generate_password_hash(password).decode('utf-8')

        user.password = hashed

        db.session.commit()

        flash('Password reset successful.', 'success')

        return redirect(url_for('login'))

    return render_template('reset_password.html')

@app.route('/change-password', methods=['GET','POST'])
@login_required
def change_password():

    if request.method == 'POST':

        current = request.form['current_password']
        new     = request.form['new_password']
        confirm = request.form['confirm_password']

        if not bcrypt.check_password_hash(
                current_user.password,
                current):

            flash('Current password incorrect.', 'error')
            return redirect(request.url)

        if new != confirm:
            flash('Passwords do not match.', 'error')
            return redirect(request.url)

        hashed = bcrypt.generate_password_hash(new).decode('utf-8')

        current_user.password = hashed

        db.session.commit()

        flash('Password changed successfully.', 'success')

        return redirect(url_for('dashboard'))

    return render_template('change_password.html')

def visits_for_user():
    """Staff see only their own visits. Admin sees all."""
    if current_user.is_admin:
        return Visit.query
    return Visit.query.filter_by(staff_id=current_user.id)


def get_dashboard_stats():
    today       = date.today()
    month_start = datetime(today.year, today.month, 1)
    # Analytics are global (billing level)
    base        = Visit.query

    today_visits = base.filter(db.func.date(Visit.visit_date) == today).all()
    month_visits = base.filter(Visit.visit_date >= month_start).all()
    all_visits   = base.all()

    total_customers = Customer.query.count()

    return {
        'today':           fmt_inr(sum(float(v.total_amount) for v in today_visits)),
        'today_count':     len(today_visits),
        'month':           fmt_inr(sum(float(v.total_amount) for v in month_visits)),
        'month_count':     len(month_visits),
        'total_customers': total_customers,
        'total_revenue':   fmt_inr(sum(float(v.total_amount) for v in all_visits)),
    }


def get_analytics_data():
    """
    Returns visits as flat dicts for JS analytics.
    Includes customer identity + visit transaction data.
    """
    visits = visits_for_user().order_by(Visit.visit_date.asc()).all()
    return [{
        'customer_id':  v.customer_id,
        'visit_id':     v.id,
        'name':         v.customer.name,
        'phone':        v.customer.phone,
        'is_returning': v.customer.is_returning,   # True if customer has >1 visit ever
        'visit_number': Visit.query.filter_by(customer_id=v.customer_id)
                             .filter(Visit.visit_date <= v.visit_date).count(),
        'amount':       float(v.total_amount),
        'payment':      (v.payment_method or 'cash').lower(),
        'staff':        v.staff.name if v.staff else 'Unknown',
        'date':         v.visit_date.isoformat(),
        'treatments':   [t.name for t in v.treatments],
        # Each treatment's actual price — used for per-treatment revenue breakdown
        'treatment_prices': {t.name: float(t.price) for t in v.treatments},
    } for v in visits]


TREATMENT_ICONS = {
    'hair spa':'💆','facial':'✨','manicure':'💅','pedicure':'🦶',
    'hair cut':'✂️','eyebrow threading':'🪮','waxing':'🌿','bleach':'🌸',
}

def get_treatment_stats():
    treatments = Treatment.query.all()
    base_visits = visits_for_user().all()
    total_rev   = sum(float(v.total_amount) for v in base_visits) or 1
    stats = []
    for t in treatments:
        visible = [v for v in t.visits if v in base_visits]
        rev = sum(float(v.total_amount) for v in visible)
        stats.append({
            'id': t.id, 'name': t.name, 'count': len(visible),
            'revenue': fmt_inr(rev), 'pct': round((rev/total_rev)*100),
            'icon': TREATMENT_ICONS.get(t.name.lower(), '💖'),
        })
    return sorted(stats, key=lambda x: x['count'], reverse=True)


# ═══════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════
@app.route('/')
def home():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['Email']).first()
        if user and bcrypt.check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'error')
    return render_template('login.html', no_users=User.query.count()==0)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    First-run: open to anyone when zero users exist.
    First user is automatically set as admin.
    After that: logged-in admin only.
    """
    first_run = User.query.count() == 0

    if not first_run:
        if not current_user.is_authenticated:
            flash('Please login first.', 'error')
            return redirect(url_for('login'))
        if current_user.role not in ADMIN_ROLES:
            flash('Access denied. Admin only.', 'error')
            return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name  = request.form['Name']
        email = request.form['Email']
        pw    = request.form['password']
        cpw   = request.form['Confirm password']
        role  = 'admin' if first_run else request.form['Role']

        if pw != cpw:
            flash('Passwords do not match', 'error')
            return render_template('register.html', first_run=first_run)
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return render_template('register.html', first_run=first_run)

        hashed = bcrypt.generate_password_hash(pw).decode('utf-8')
        db.session.add(User(name=name, email=email, password=hashed, role=role))
        db.session.commit()

        if first_run:
            flash('Admin account created! Please login.', 'success')
            return redirect(url_for('login'))

        flash(f'Account created for {name} ({role}).', 'success')
        return redirect(url_for('manage_staff'))

    return render_template('register.html', first_run=first_run)


# ═══════════════════════════════════════════════════════════
#  STAFF MANAGEMENT
# ═══════════════════════════════════════════════════════════
@app.route('/staff')
@login_required
@admin_required
def manage_staff():
    return render_template('manage_staff.html', staff_list=User.query.order_by(User.name).all())


@app.route('/staff/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_staff(user_id):
    if user_id == current_user.id:
        flash("You can't delete your own account.", 'error')
        return redirect(url_for('manage_staff'))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash(f'{user.name} removed.', 'success')
    return redirect(url_for('manage_staff'))


# ═══════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def dashboard():
    page   = request.args.get('page', 1, type=int)
    visits = visits_for_user()\
                .order_by(Visit.visit_date.desc())\
                .paginate(page=page, per_page=8, error_out=False)
    stats  = get_dashboard_stats()
    return render_template('dashboard.html', visits=visits, stats=stats)


# ═══════════════════════════════════════════════════════════
#  CUSTOMER LOOKUP API
# ═══════════════════════════════════════════════════════════
@app.route('/api/lookup-phone')
@login_required
def lookup_phone():
    """
    Returns customer identity by exact phone match.
    Used by the new-visit form to auto-fill returning customers.
    """
    phone    = normalize_phone(request.args.get('phone', ''))
    customer = Customer.query.filter_by(phone=phone).first()
    if not customer:
        return jsonify({'found': False})
    return jsonify({
        'found':        True,
        'customer_id':  customer.id,
        'name':         customer.name,
        'email':        customer.email or '',
        'age':          customer.age or '',
        'total_visits': customer.total_visits,
        'total_spent':  float(customer.total_spent),
        'last_visit':   customer.last_visit.visit_date.strftime('%d %b %Y') if customer.last_visit else '—',
        'is_returning': customer.is_returning,
    })


@app.route('/search')
@login_required
def search():
    q       = request.args.get('q', '')
    # Try exact normalized match first, then fall back to ilike
    norm_q  = normalize_phone(q)
    results = Customer.query.filter(
        db.or_(
            Customer.name.ilike(f'%{q}%'),
            Customer.phone.ilike(f'%{q}%')
        )
    ).limit(10).all()
    return jsonify([{
        'id':           c.id,
        'name':         c.name,
        'phone':        c.phone,
        'total_visits': c.total_visits,
        'total_spent':  float(c.total_spent),
    } for c in results])


# ═══════════════════════════════════════════════════════════
#  NEW VISIT (core flow)
# ═══════════════════════════════════════════════════════════
@app.route('/new-visit', methods=['GET', 'POST'])
@login_required
def new_visit():

    treatments = Treatment.query.filter_by(
        is_active=True
    ).all()

    prefill_id   = request.args.get('customer_id', type=int)
    prefill_cust = Customer.query.get(prefill_id) if prefill_id else None

    if request.method == 'POST':

        phone = normalize_phone(
            request.form['phone']
        )

        name   = request.form['name'].strip()
        email  = request.form.get('email', '').strip()
        age    = request.form.get('age')
        gender = request.form.get('gender')
        
        dob_str = request.form.get('dob')
        dob = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None

        payment_method = request.form['payment_method']
        notes          = request.form.get('notes', '')

        treatment_ids = request.form.getlist(
            'treatments'
        )

        vd_str = request.form.get('visit_date')

        if vd_str:
            visit_date = datetime.strptime(
                vd_str,
                '%Y-%m-%dT%H:%M'
            )
        else:
            IST = timedelta(hours=5, minutes=30)
            visit_date = datetime.utcnow() + IST

        # ───────── PHONE VALIDATION ─────────

        if len(phone) != 10:

            flash("Invalid phone number", "error")

            return render_template(
                'new_visit.html',
                treatments=treatments
            )

        # ───────── CUSTOMER ─────────
        # First, try to find exact match (same phone AND same name)
        customer = Customer.query.filter_by(phone=phone, name=name).first()

        # If not found, check if there's a customer with same phone but different name
        same_phone_diff_name = None
        if not customer:
            same_phone_diff_name = Customer.query.filter_by(phone=phone).first()

        if not customer:
            if same_phone_diff_name:
                # Same phone but different name - create a NEW customer record
                # This handles cases where different people use the same phone
                flash(
                    f"⚠️ Phone {phone} was previously used by '{same_phone_diff_name.name}'. Creating new customer record for '{name}'.",
                    'warning'
                )
                customer = Customer(
                    name=name,
                    phone=phone,
                    email=email,
                    age=age,
                    dob=dob,
                    gender=gender
                )
            else:
                # Completely new customer
                customer = Customer(
                    name=name,
                    phone=phone,
                    email=email,
                    age=age,
                    dob=dob,
                    gender=gender
                )
            db.session.add(customer)
            db.session.flush()
        else:
            # Exact match found (same phone AND same name)
            # Just update missing fields
            if not customer.email and email:
                customer.email = email
            if not customer.age and age:
                customer.age = age
            if not customer.dob and dob:
                customer.dob = dob
            if not customer.gender and gender:
                customer.gender = gender

        # ───────── TREATMENTS ─────────

        selected = Treatment.query.filter(
            Treatment.id.in_(treatment_ids)
        ).all()

        total = sum(
            float(t.price)
            for t in selected
        )

        # ───────── CREATE VISIT ─────────

        visit = Visit(

            customer_id=customer.id,
            staff_id=current_user.id,

            visit_date=visit_date,

            total_amount=total,

            payment_method=payment_method,

            notes=notes,

            treatments=selected
        )

        db.session.add(visit)
        db.session.commit()

        return redirect(
            url_for(
                'generate_bill',
                visit_id=visit.id
            )
        )

    return render_template(
        'new_visit.html',
        treatments=treatments,
        prefill=prefill_cust
    )


# ═══════════════════════════════════════════════════════════
#  BILL
# ═══════════════════════════════════════════════════════════
@app.route('/bill/<int:visit_id>')
@login_required
def generate_bill(visit_id):
    v = Visit.query.get_or_404(visit_id)
    if not current_user.is_admin and v.staff_id != current_user.id:
        flash("You don't have access to this bill.", 'error')
        return redirect(url_for('dashboard'))
    return render_template('bill.html', visit=v)


@app.route('/bill/<int:visit_id>/download')
@login_required
def download_bill(visit_id):
    v = Visit.query.get_or_404(visit_id)
    if not current_user.is_admin and v.staff_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    buf = generate_pdf_buffer(v)
    return send_file(buf, as_attachment=True,
                     download_name=f'GlowPark_Bill_{v.customer.name}_Visit{v.id}.pdf',
                     mimetype='application/pdf')


@app.route('/send-bill/<int:visit_id>')
@login_required
def send_bill(visit_id):
    v = Visit.query.get_or_404(visit_id)
    if not v.customer.email:
        return jsonify({'status': 'error', 'message': 'No email address on record'})
    buf = generate_pdf_buffer(v)
    msg = Message('Your Glow Park Salon Bill',
                  sender=app.config['MAIL_USERNAME'],
                  recipients=[v.customer.email])
    msg.body = f"Hi {v.customer.name},\n\nThank you for visiting Glow Park Salon!\n\nSee you again! 💕"
    msg.attach(f'GlowPark_Bill_{v.customer.name}.pdf', 'application/pdf', buf.read())
    mail.send(msg)
    return jsonify({'status': 'success', 'message': f'Bill sent to {v.customer.email}'})


# ═══════════════════════════════════════════════════════════
#  CUSTOMER PROFILE (history of all visits)
# ═══════════════════════════════════════════════════════════
@app.route('/customer/<int:customer_id>')
@login_required
def customer_profile(customer_id):
    c = Customer.query.get_or_404(customer_id)
    
    # Industry RBAC: Staff only see profiles of customers they have handled
    if not current_user.is_admin:
        handled_visits = Visit.query.filter_by(customer_id=c.id, staff_id=current_user.id).count()
        if handled_visits == 0:
            flash("Access denied. You have not handled this customer.", "error")
            return redirect(url_for('dashboard'))

    visits = c.visits.all()
    return render_template('customer_profile.html', customer=c, visits=visits)


@app.route('/customer/<int:customer_id>/edit', methods=['POST'])
@login_required
def edit_customer(customer_id):
    """Edit customer identity — does NOT touch past visits."""
    c = Customer.query.get_or_404(customer_id)
    c.name  = request.form.get('name', c.name).strip()
    c.email = request.form.get('email', c.email).strip()
    c.age   = request.form.get('age') or c.age
    db.session.commit()
    flash('Customer profile updated.', 'success')
    return redirect(url_for('customer_profile', customer_id=customer_id))

# ═══════════════════════════════════════════════════════════
#  BILLING Dump as CSV 
# ═══════════════════════════════════════════════════════════
@app.route('/import-billing-csv', methods=['POST'])
@login_required
@admin_required
def import_billing_csv():

    file = request.files.get('file')

    if not file:
        flash("No file uploaded", "error")
        return redirect(url_for('billing_view'))

    stream = io.StringIO(
        file.stream.read().decode("UTF8"),
        newline=None
    )

    reader = csv.DictReader(stream)

    created_visits = 0

    for row in reader:

        phone  = normalize_phone(row.get('Phone'))
        name   = row.get('Customer Name')
        age    = row.get('Age')
        gender = row.get('Gender')
        email  = row.get('Email')

        payment_method = row.get('Payment Method')

        treatments_raw = row.get('Treatments')

        if not phone or not treatments_raw:
            continue

        # ───────── CUSTOMER ─────────

        customer = Customer.query.filter_by(phone=phone).first()

        if not customer:
            customer = Customer(
                name=name,
                phone=phone,
                age=age,
                gender=gender,
                email=email
            )
            db.session.add(customer)
            db.session.flush()
        else:
            existing_name = customer.name
            if not existing_name and name:
                customer.name = name
            if not customer.email and email:
                customer.email = email
            if not customer.age and age:
                customer.age = age
            if not customer.gender and gender:
                customer.gender = gender
            if name and existing_name and name != existing_name:
                flash(
                    'Existing customer found for this phone. Keeping the original customer record.',
                    'info'
                )
            

        # ───────── MULTIPLE TREATMENTS ─────────

        treatment_names = [
            t.strip()
            for t in treatments_raw.split('|')
            if t.strip()
        ]

        treatments = []

        total_amount = 0

        for t_name in treatment_names:

            treatment = Treatment.query.filter(
                db.func.lower(Treatment.name)
                == t_name.lower()
            ).first()

            # Create if missing

            if not treatment:

                treatment = Treatment(
                    name=t_name,
                    price=0
                )

                db.session.add(treatment)
                db.session.flush()

            treatments.append(treatment)

            total_amount += float(treatment.price)

        # ───────── CREATE VISIT ─────────

        visit = Visit(
            customer_id=customer.id,
            staff_id=current_user.id,
            total_amount=total_amount,
            payment_method=payment_method,
            treatments=treatments
        )

        db.session.add(visit)

        created_visits += 1

    db.session.commit()

    flash(
        f"{created_visits} visits imported successfully!",
        "success"
    )

    return redirect(url_for('billing_view'))



@app.route('/import-treatments-csv', methods=['POST'])
@login_required
@admin_required
def import_treatments_csv():

    file = request.files.get('file')

    if not file:
        flash("No file selected", "error")
        return redirect(url_for('dashboard'))

    stream = TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)

    added = 0
    updated = 0

    for row in reader:

        name = row.get("Treatment Name")
        price = row.get("Price")

        if not name or not price:
            continue

        name = name.strip().title()
        price = float(price)

        # Check if treatment exists
        treatment = Treatment.query.filter_by(name=name).first()

        if treatment:
            # Update price only
            treatment.price = price
            updated += 1

        else:
            # Add new treatment
            new_treatment = Treatment(
                name=name,
                price=price
            )

            db.session.add(new_treatment)
            added += 1

    db.session.commit()

    flash(
        f"{added} added, {updated} updated successfully!",
        "success"
    )

    return redirect(url_for('dashboard'))
# ═══════════════════════════════════════════════════════════
#  BILLING (all transactions)
# ═══════════════════════════════════════════════════════════
@app.route('/billing')
@login_required
def billing_view():
    page = request.args.get('page', 1, type=int)

    visits = visits_for_user()\
        .order_by(Visit.visit_date.desc())\
        .paginate(page=page, per_page=8, error_out=False)

    return render_template('billing.html', visits=visits)


@app.route('/master-customers')
@login_required
def master_customers():
    """Master customer table - unique by phone number (Brand ID)"""
    page = request.args.get('page', 1, type=int)
    
    # Get all customers, deduplicate by phone, keep latest record per phone
    all_customers = Customer.query.all()
    seen_phones = {}
    unique_customers = []
    for c in sorted(all_customers, key=lambda x: x.created_at, reverse=True):
        if c.phone not in seen_phones:
            seen_phones[c.phone] = True
            unique_customers.append(c)
    
    # Simple pagination
    per_page = 10
    total = len(unique_customers)
    pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    paginated = unique_customers[start:end]
    
    class Pagination:
        pass
    
    result = Pagination()
    result.items = paginated
    result.total = total
    result.pages = pages
    result.page = page
    
    return render_template('master_customers.html', customers=result)


@app.route('/export-billing-csv')
@login_required
def export_billing_csv():

    visits = visits_for_user()\
        .order_by(Visit.visit_date.desc())\
        .all()

    output = io.StringIO()

    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Bill ID",
        "Customer Name",
        "Phone",
        "Age",
        "Gender",
        "Payment Method",
        "Treatments",
        "Total Amount",
        "Staff",
        "Visit Date"
    ])

    # Rows
    for v in visits:

        treatment_names = "|".join(
    [t.name for t in v.treatments]
)

        writer.writerow([
            v.id,
            v.customer.name,
            v.customer.phone,
            v.customer.age,
            v.customer.gender,
            v.payment_method,
            treatment_names,
            float(v.total_amount),
            v.staff.name if v.staff else "",
            v.visit_date.strftime('%Y-%m-%d %H:%M')
        ])

    response = make_response(output.getvalue())

    response.headers["Content-Disposition"] = \
        "attachment; filename=billing_export.csv"

    response.headers["Content-Type"] = \
        "text/csv"

    return response

# ═══════════════════════════════════════════════════════════
#  ANALYTICS
# ═══════════════════════════════════════════════════════════
@app.route('/analytics')
@login_required
@admin_required
def analytics_view():
    return render_template('analytics.html', analytics_data=get_analytics_data())


# ═══════════════════════════════════════════════════════════
#  CAMPAIGNS
# ═══════════════════════════════════════════════════════════
@app.route('/campaigns')
@login_required
@admin_required
def campaigns_view():
    # Group by customer and get aggregate data
    # We'll fetch all customers and their visits to calculate loyalty and spend
    customers = Customer.query.all()
    customer_data = []
    
    for c in customers:
        all_visits = c.visits.all()
        if not all_visits:
            continue
            
        latest_visit = all_visits[0] # visits are ordered by date desc
        
        # Aggregate treatments across all visits for filtering
        treatment_ids = set()
        treatment_names = []
        for v in all_visits:
            for t in v.treatments:
                treatment_ids.add(str(t.id))
                treatment_names.append(t.name)
        
        customer_data.append({
            'customer': c,
            'total_spent': sum(float(v.total_amount) for v in all_visits),
            'total_visits': len(all_visits),
            'latest_visit': latest_visit,
            'days_since_last_visit': (date.today() - latest_visit.visit_date.date()).days,
            'all_treatment_ids': ",".join(treatment_ids),
            'all_treatment_names': ", ".join(list(set(treatment_names)))
        })
    
    # Calculate dynamic filter ranges from actual data
    filter_meta = {
        'min_spend': 0,
        'max_spend': 0,
        'avg_spend': 0,
        'min_visits': 0,
        'max_visits': 0,
        'avg_visits': 0,
        'max_inactive_days': 0,
        'available_months': set(),
    }
    
    if customer_data:
        spends = [c['total_spent'] for c in customer_data]
        visits = [c['total_visits'] for c in customer_data]
        inactive = [c['days_since_last_visit'] for c in customer_data]
        
        filter_meta['min_spend'] = int(min(spends))
        filter_meta['max_spend'] = int(max(spends))
        filter_meta['avg_spend'] = int(sum(spends) / len(spends))
        
        filter_meta['min_visits'] = int(min(visits))
        filter_meta['max_visits'] = int(max(visits))
        filter_meta['avg_visits'] = int(sum(visits) / len(visits))
        
        filter_meta['max_inactive_days'] = int(max(inactive)) if inactive else 0
        
        # Collect available birthday months
        for c in customer_data:
            if c['customer'].dob:
                filter_meta['available_months'].add(c['customer'].dob.month)
        
        filter_meta['available_months'] = sorted(list(filter_meta['available_months']))
        
    treatment_stats = get_treatment_stats()
    return render_template('campaigns.html', customers=customer_data, treatment_stats=treatment_stats, filter_meta=filter_meta)


# ═══════════════════════════════════════════════════════════
#  SEED
# ═══════════════════════════════════════════════════════════
# @app.route('/seed-treatments')
# def seed_treatments():
#     if Treatment.query.count() > 0:
#         return 'Already seeded!'
#     db.session.bulk_save_objects([
#         Treatment(name='Hair Spa',          price=500),
#         Treatment(name='Facial',            price=800),
#         Treatment(name='Manicure',          price=300),
#         Treatment(name='Pedicure',          price=350),
#         Treatment(name='Hair Cut',          price=200),
#         Treatment(name='Eyebrow Threading', price=50),
#         Treatment(name='Waxing',            price=400),
#         Treatment(name='Bleach',            price=450),
#     ])
#     db.session.commit()
#     return 'Treatments seeded!'

@app.route('/treatments')
@login_required
@admin_required
def manage_treatments():
    treatments = Treatment.query.order_by(Treatment.id.desc()).all()
    return render_template('treatments.html', treatments=treatments)

from sqlalchemy import func

@app.route('/treatments/add', methods=['POST'])
@login_required
@admin_required
def add_treatment():
    name = request.form['name'].strip()
    price = float(request.form['price'])

    if not name:
        flash('Treatment name required!', 'error')
        return redirect(url_for('manage_treatments'))

    # ✅ Check duplicate (case-insensitive)
    existing = Treatment.query.filter(
        func.lower(Treatment.name) == name.lower()
    ).first()

    if existing:
        flash(f'Treatment "{name}" already exists!', 'warning')
        return redirect(url_for('manage_treatments'))

    # ✅ Add new treatment
    new_treatment = Treatment(
        name=name,
        price=price
    )

    db.session.add(new_treatment)
    db.session.commit()

    flash('Treatment added successfully!', 'success')

    return redirect(url_for('manage_treatments'))

@app.route('/treatments/<int:id>/edit', methods=['POST'])
@login_required
@admin_required
def edit_treatment(id):
    t = Treatment.query.get_or_404(id)

    t.name = request.form['name']
    t.price = float(request.form['price'])

    db.session.commit()
    flash('Updated!', 'success')
    return redirect(url_for('manage_treatments'))

@app.route('/treatments/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_treatment(id):
    t = Treatment.query.get_or_404(id)
    t.is_active = not t.is_active

    db.session.commit()
    flash('Status updated!', 'success')
    return redirect(url_for('manage_treatments'))
# ═══════════════════════════════════════════════════════════
#  PDF
# ═══════════════════════════════════════════════════════════
def generate_pdf_buffer(v):
    """Generate receipt PDF for a Visit object."""
    c      = v.customer
    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=A4,
                               rightMargin=15*mm, leftMargin=15*mm,
                               topMargin=15*mm, bottomMargin=15*mm)

    pink       = colors.HexColor('#E91E8C')
    light_pink = colors.HexColor('#FCE4F3')
    dark_gray  = colors.HexColor('#2D2D2D')
    mid_gray   = colors.HexColor('#666666')
    soft_white = colors.HexColor('#FFF8FC')

    def sty(name, **kw): return ParagraphStyle(name, **kw)
    salon_s = sty('SN', fontSize=28, textColor=pink, alignment=TA_CENTER, fontName='Helvetica-Bold')
    tag_s   = sty('TG', fontSize=10, textColor=mid_gray, alignment=TA_CENTER, fontName='Helvetica-Oblique')
    recv_s  = sty('RC', fontSize=13, textColor=soft_white, alignment=TA_CENTER, fontName='Helvetica-Bold')
    lbl_s   = sty('LB', fontSize=10, textColor=mid_gray, fontName='Helvetica', leading=16)
    val_s   = sty('VL', fontSize=10, textColor=dark_gray, fontName='Helvetica-Bold', leading=16)
    tot_l   = sty('TL', fontSize=13, textColor=soft_white, fontName='Helvetica-Bold', alignment=TA_LEFT)
    tot_v   = sty('TV', fontSize=13, textColor=soft_white, fontName='Helvetica-Bold', alignment=TA_RIGHT)
    foot_s  = sty('FT', fontSize=9, textColor=mid_gray, alignment=TA_CENTER, fontName='Helvetica-Oblique')
    right_s = sty('RS', fontSize=10, textColor=mid_gray, fontName='Helvetica', alignment=TA_RIGHT)

    story = []
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("Glow Park Salon", salon_s))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("Beauty &amp; Wellness | Chennai", tag_s))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("Phone: 9999999999 | glowpark@gmail.com", tag_s))
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="100%", thickness=2, color=pink, spaceAfter=4*mm))

    recv_tbl = Table([[Paragraph("RECEIPT", recv_s)]], colWidths=[180*mm])
    recv_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), pink),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(recv_tbl)
    story.append(Spacer(1, 5*mm))

    visit_num = Visit.query.filter_by(customer_id=c.id)\
                    .filter(Visit.visit_date <= v.visit_date).count()

    meta_tbl = Table([[
        Paragraph(f"Bill No: #GP{v.id:04d} &nbsp;|&nbsp; Visit #{visit_num}", lbl_s),
        Paragraph(f"Date: {v.visit_date.strftime('%d %b %Y, %I:%M %p')}", right_s)
    ]], colWidths=[90*mm, 90*mm])
    meta_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), light_pink),
        ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (0,-1), 8), ('RIGHTPADDING', (-1,0), (-1,-1), 8),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 5*mm))

    treatment_names = ', '.join(t.name for t in v.treatments) or '—'
    details = [
        ["Customer Name",  c.name],
        ["Phone",          c.phone],
        ["Email",          c.email or "—"],
        ["Age",            str(c.age) if c.age else "—"],
        ["Gender", c.gender],
        ["Total Visits",   f"#{visit_num} (lifetime)"],
        ["Treatments",     treatment_names],
        ["Staff Handled",  v.staff.name if v.staff else "—"],
        ["Payment Method", (v.payment_method or "cash").upper()],
        ["Notes",          v.notes or "—"],
    ]
    tdata = [[Paragraph(l, lbl_s), Paragraph(str(val), val_s)] for l, val in details]
    dtbl  = Table(tdata, colWidths=[60*mm, 120*mm])
    dtbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), light_pink),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#FDF0F8')]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#F0C8E8')),
        ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(dtbl)
    story.append(Spacer(1, 5*mm))

    total_tbl = Table([[Paragraph("TOTAL AMOUNT", tot_l), Paragraph(f"Rs. {v.total_amount}", tot_v)]],
                      colWidths=[120*mm, 60*mm])
    total_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), pink),
        ('TOPPADDING', (0,0), (-1,-1), 12), ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING', (0,0), (0,-1), 12), ('RIGHTPADDING', (-1,0), (-1,-1), 12),
    ]))
    story.append(total_tbl)
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=light_pink, spaceAfter=3*mm))
    story.append(Paragraph("Thank you for visiting Glow Park Salon!", foot_s))
    story.append(Paragraph("We look forward to seeing you again. Have a beautiful day! 💕", foot_s))

    doc.build(story)
    buffer.seek(0)
    return buffer

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
