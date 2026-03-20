from flask import Flask, render_template, redirect, url_for, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
from flask_bcrypt import Bcrypt
from flask import flash
from flask_migrate import Migrate
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

app = Flask(__name__)
app.config['MAIL_SERVER']   = 'smtp.gmail.com'
app.config['MAIL_PORT']     = 587
app.config['MAIL_USE_TLS']  = True
app.config['MAIL_USE_SSL']  = False
app.config['MAIL_USERNAME'] = 'pradeeppardeep65@gmail.com'
app.config['MAIL_PASSWORD'] = 'scacfyggqpplbfte'
mail = Mail(app)

app.config['SECRET_KEY']              = 'glowpark@123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///glowpark.db'
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


class User(db.Model, UserMixin):
    """Staff / Admin accounts."""
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role     = db.Column(db.String(20), nullable=False, default=ROLE_STAFF)

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLES


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
    Phone is the unique identifier.
    """
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    phone      = db.Column(db.String(20), unique=True, nullable=False)   # UNIQUE identity
    email      = db.Column(db.String(120))
    age        = db.Column(db.Integer)
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
        return self.total_visits > 1


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


def visits_for_user():
    """Staff see only their own visits. Admin sees all."""
    if current_user.is_admin:
        return Visit.query
    return Visit.query.filter_by(staff_id=current_user.id)


def get_dashboard_stats():
    today       = date.today()
    month_start = datetime(today.year, today.month, 1)
    base        = visits_for_user()

    today_visits = base.filter(db.func.date(Visit.visit_date) == today).all()
    month_visits = base.filter(Visit.visit_date >= month_start).all()
    all_visits   = base.all()

    total_customers = Customer.query.count() if current_user.is_admin \
                      else db.session.query(db.func.count(db.distinct(Visit.customer_id)))\
                               .filter(Visit.staff_id == current_user.id).scalar()

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
    # treatments   = Treatment.query.all()
    treatments = Treatment.query.filter_by(is_active=True).all()
    prefill_id   = request.args.get('customer_id', type=int)
    prefill_cust = Customer.query.get(prefill_id) if prefill_id else None

    if request.method == 'POST':
        phone          = normalize_phone(request.form['phone'])
        name           = request.form['name'].strip()
        email          = request.form.get('email', '').strip()
        age            = request.form.get('age') or None
        payment_method = request.form['payment_method']
        notes          = request.form.get('notes', '')
        treatment_ids  = request.form.getlist('treatments')
        vd_str     = request.form.get('visit_date', '').strip()
        if vd_str:
            # Form sends IST time as 'YYYY-MM-DDTHH:MM' — store as-is (treat all datetimes as IST)
            visit_date = datetime.strptime(vd_str, '%Y-%m-%dT%H:%M')
        else:
            # Fallback: current IST time (UTC + 5h30m)
            from datetime import timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            visit_date = datetime.now(IST).replace(tzinfo=None)

        # ── Phone validation ──
        if len(phone) != 10 or not phone.isdigit():
            return render_template('new_visit.html',
                error=f"Invalid phone number '{request.form['phone']}' — must be 10 digits.",
                treatments=Treatment.query.all(), prefill=None)

        # ── Step 1: Get or create Customer identity ──
        customer = Customer.query.filter_by(phone=phone).first()
        if not customer:
            # New customer — create identity record
            customer = Customer(name=name, phone=phone, email=email, age=age)
            db.session.add(customer)
            db.session.flush()   # get customer.id before visit insert
        else:
            # Returning customer — update profile if staff changed anything
            if name:  customer.name  = name
            if email: customer.email = email
            if age:   customer.age   = age

        # ── Step 2: Always create new Visit record ──
        selected   = Treatment.query.filter(Treatment.id.in_(treatment_ids)).all()
        total      = sum(float(t.price) for t in selected)

        visit = Visit(
            customer_id    = customer.id,
            staff_id       = current_user.id,
            visit_date     = visit_date,
            total_amount   = total,
            payment_method = payment_method,
            notes          = notes,
            treatments     = selected,
        )
        db.session.add(visit)
        db.session.commit()

        return redirect(url_for('generate_bill', visit_id=visit.id))

    return render_template('new_visit.html',
                           treatments=treatments,
                           prefill=prefill_cust)


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
    visits          = visits_for_user().order_by(Visit.visit_date.desc()).all()
    treatment_stats = get_treatment_stats()
    return render_template('campaigns.html', visits=visits, treatment_stats=treatment_stats)


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

@app.route('/treatments/add', methods=['POST'])
@login_required
@admin_required
def add_treatment():
    name = request.form['name'].strip()
    price = float(request.form['price'])

    if not name:
        flash('Name required', 'error')
        return redirect(url_for('manage_treatments'))

    db.session.add(Treatment(name=name, price=price))
    db.session.commit()

    flash('Treatment added!', 'success')
    return redirect(url_for('treatments'))

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
