from flask import Flask, render_template, redirect, url_for, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_bcrypt import Bcrypt
from flask import flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin, login_manager
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import send_file
import io
from flask_mail import Mail, Message


app = Flask(__name__)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = 'pradeeppardeep65@gmail.com'
app.config['MAIL_PASSWORD'] = 'scacfyggqpplbfte'  # 16-char Gmail App Password
mail = Mail(app)

app.config['SECRET_KEY'] = 'glowpark@123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    # created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'User {self.name}\n Email: {self.email}\n Role: {self.role}\n password: {self.password}'

# Treatment master table (your salon's service menu)
class Treatment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)   # e.g. "Hair Spa"
    price = db.Column(db.Numeric(10, 2), nullable=False)  # e.g. 500.00

# Junction table — links customers to multiple treatments
customer_treatments = db.Table('customer_treatments',
    db.Column('customer_id', db.Integer, db.ForeignKey('customer.id')),
    db.Column('treatment_id', db.Integer, db.ForeignKey('treatment.id'))
)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # Basic Info
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120))
    age = db.Column(db.Integer)

    # Visit Info
    visit_date = db.Column(db.DateTime, default=datetime.utcnow)
    staff_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    # Billing
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(20))

    # Extra
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    staff = db.relationship('User', backref='customers')
    treatments = db.relationship('Treatment', secondary=customer_treatments, backref='customers')
    
@app.route('/seed-treatments')
def seed_treatments():
    treatments = [
        Treatment(name='Hair Spa', price=500),
        Treatment(name='Facial', price=800),
        Treatment(name='Manicure', price=300),
        Treatment(name='Pedicure', price=350),
        Treatment(name='Hair Cut', price=200),
        Treatment(name='Eyebrow Threading', price=50),
        Treatment(name='Waxing', price=400),
        Treatment(name='Bleach', price=450),
    ]
    db.session.bulk_save_objects(treatments)
    db.session.commit()
    return 'Treatments seeded!'

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/dashboard')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 5  # rows per page
    customers = Customer.query.order_by(Customer.visit_date.desc())\
                              .paginate(page=page, per_page=per_page, error_out=False)
    return render_template('dashboard.html', customers=customers)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['Name']
        email = request.form['Email']
        password = request.form['password']
        confirm_password = request.form['Confirm password']
        role = request.form['Role']

        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('register.html')

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered', 'error')
            return render_template('register.html')

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(name=name, email=email, password=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()

        flash('Registered successfully! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')
        


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['Email']
        password = request.form['password']

        # Step 1 — find user by email only
        user = User.query.filter_by(email=email).first()

        # Step 2 — then verify password against hash
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user) 
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
        
    return render_template('login.html')


@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    customers = Customer.query.filter(
        db.or_(
            Customer.name.ilike(f'%{query}%'),
            Customer.phone.ilike(f'%{query}%')
        )
    ).limit(10).all()
    
    results = [{
        'id': c.id,
        'name': c.name,
        'phone': c.phone,
        'treatment': c.treatments
    } for c in customers]
    
    return jsonify(results)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
import io

def generate_pdf_buffer(c):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=15*mm, leftMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)

    # ── Colors ──
    pink        = colors.HexColor('#E91E8C')
    light_pink  = colors.HexColor('#FCE4F3')
    dark_gray   = colors.HexColor('#2D2D2D')
    mid_gray    = colors.HexColor('#666666')
    soft_white  = colors.HexColor('#FFF8FC')

    # ── Styles ──
    styles = getSampleStyleSheet()

    salon_name_style = ParagraphStyle('SalonName',
        fontSize=28, textColor=pink,
        alignment=TA_CENTER, fontName='Helvetica-Bold',
        spaceAfter=2)

    tagline_style = ParagraphStyle('Tagline',
        fontSize=10, textColor=mid_gray,
        alignment=TA_CENTER, fontName='Helvetica-Oblique',
        spaceAfter=2)

    receipt_title_style = ParagraphStyle('ReceiptTitle',
        fontSize=13, textColor=soft_white,
        alignment=TA_CENTER, fontName='Helvetica-Bold')

    label_style = ParagraphStyle('Label',
        fontSize=10, textColor=mid_gray,
        fontName='Helvetica', leading=16)

    value_style = ParagraphStyle('Value',
        fontSize=10, textColor=dark_gray,
        fontName='Helvetica-Bold', leading=16)

    total_label_style = ParagraphStyle('TotalLabel',
        fontSize=13, textColor=soft_white,
        fontName='Helvetica-Bold', alignment=TA_LEFT)

    total_value_style = ParagraphStyle('TotalValue',
        fontSize=13, textColor=soft_white,
        fontName='Helvetica-Bold', alignment=TA_RIGHT)

    footer_style = ParagraphStyle('Footer',
        fontSize=9, textColor=mid_gray,
        alignment=TA_CENTER, fontName='Helvetica-Oblique')

    story = []

    # ── Header ──
    # ── Header ──
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("Glow Park Salon", salon_name_style))
    story.append(Spacer(1, 8*mm))  # ← add space after name
    story.append(Paragraph("Beauty &amp; Wellness | Chennai", tagline_style))
    story.append(Spacer(1, 3*mm))  # ← add space after tagline
    story.append(Paragraph("Phone: 9999999999 | glowpark@gmail.com", tagline_style))
    story.append(Spacer(1, 6*mm))  # ← bigger space before divider
    story.append(HRFlowable(width="100%", thickness=2, color=pink, spaceAfter=4*mm))

    # ── RECEIPT Badge ──
    receipt_table = Table([[Paragraph("RECEIPT", receipt_title_style)]], colWidths=[180*mm])
    receipt_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), pink),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(receipt_table)
    story.append(Spacer(1, 6*mm))

    # ── Bill Number + Date ──
    meta_data = [
        [Paragraph(f"Bill No: #GP{c.id:04d}", label_style),
         Paragraph(f"Date: {c.visit_date.strftime('%d %b %Y, %I:%M %p')}", 
                   ParagraphStyle('Right', fontSize=10, textColor=mid_gray,
                                  fontName='Helvetica', alignment=TA_RIGHT))]
    ]
    meta_table = Table(meta_data, colWidths=[90*mm, 90*mm])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), light_pink),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (0,-1), 8),
        ('RIGHTPADDING', (-1,0), (-1,-1), 8),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6*mm))

    # ── Customer Details ──
    details = [
        ["Customer Name",  c.name],
        ["Phone",          c.phone],
        ["Email",          c.email or "—"],
        ["Age",            str(c.age) if c.age else "—"],
        ["Treatment",      c.treatments],
        ["Staff Handled",  c.staff.name if c.staff else "—"],
        ["Payment Method", c.payment_method.upper() if c.payment_method else "—"],
        ["Notes",          c.notes or "—"],
    ]

    table_data = [
        [Paragraph(label, label_style), Paragraph(str(value), value_style)]
        for label, value in details
    ]

    detail_table = Table(table_data, colWidths=[60*mm, 120*mm])
    detail_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (0, -1), light_pink),
        ('BACKGROUND',    (1, 0), (1, -1), colors.white),
        ('ROWBACKGROUNDS',(0, 0), (-1,-1), [colors.white, colors.HexColor('#FDF0F8')]),
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.HexColor('#F0C8E8')),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 6*mm))

    # ── Total Amount Banner ──
    total_data = [[
        Paragraph("TOTAL AMOUNT", total_label_style),
        Paragraph(f"Rs. {c.total_amount}", total_value_style)
    ]]
    total_table = Table(total_data, colWidths=[120*mm, 60*mm])
    total_table.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), pink),
        ('TOPPADDING',    (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING',   (0,0), (0,-1), 12),
        ('RIGHTPADDING',  (-1,0), (-1,-1), 12),
    ]))
    story.append(total_table)
    story.append(Spacer(1, 8*mm))

    # ── Footer ──
    story.append(HRFlowable(width="100%", thickness=1, color=light_pink, spaceAfter=4*mm))
    story.append(Paragraph("Thank you for visiting Glow Park Salon!", footer_style))
    story.append(Paragraph("We look forward to seeing you again. Have a beautiful day! 💕", footer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ─── New Customer + Save to DB ───
@app.route('/new-customer', methods=['GET', 'POST'])
@login_required
def new_customer():
    treatments = Treatment.query.all() # load all treatments for the form
    staff_list = User.query.all()

    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        email = request.form.get('email', '')
        age = request.form.get('age')
        payment_method = request.form['payment_method']
        notes = request.form.get('notes', '')
        staff_id = current_user.id
        treatment_ids = request.form.getlist('treatments')  # list of selected IDs

        # Check duplicate phone
        existing = Customer.query.filter_by(phone=phone).first()
        if existing:
            return render_template('new_customer.html', error="Phone number already exists", treatments=treatments)

        # Calculate total from selected treatments
        selected_treatments = Treatment.query.filter(Treatment.id.in_(treatment_ids)).all()
        total_amount = sum(t.price for t in selected_treatments)

        customer = Customer(
            name=name,
            phone=phone,
            email=email,
            age=age,
            payment_method=payment_method,
            notes=notes,
            staff_id=staff_id,
            total_amount=total_amount,
            treatments=selected_treatments  # link treatments
        )
        db.session.add(customer)
        db.session.commit()

        return redirect(url_for('generate_bill', customer_id=customer.id))

    return render_template('new_customer.html', treatments=treatments, staff_list=staff_list)

# ─── Bill Preview Page ───
@app.route('/bill/<int:customer_id>')
@login_required
def generate_bill(customer_id):
    c = Customer.query.get_or_404(customer_id)
    return render_template('bill.html', customer=c)


# ─── Download Bill as PDF ───
@app.route('/bill/<int:customer_id>/download')
@login_required
def download_bill(customer_id):
    c = Customer.query.get_or_404(customer_id)
    buffer = generate_pdf_buffer(c)

    return send_file(buffer, as_attachment=True,
                     download_name=f'bill_{c.name}.pdf',
                     mimetype='application/pdf')


# ─── Send Bill via Email ───
@app.route('/send-bill/<int:customer_id>')
@login_required
def send_bill(customer_id):
    c = Customer.query.get_or_404(customer_id)

    if not c.email:
        return jsonify({'status': 'error', 'message': 'No email address on record'})

    buffer = generate_pdf_buffer(c)

    msg = Message('Your Salon Bill',
                  sender=app.config['MAIL_USERNAME'],
                  recipients=[c.email])
    msg.body = f"Hi {c.name}, please find your bill attached. Thank you for visiting!"
    msg.attach(f"bill_{c.name}.pdf", "application/pdf", buffer.read())

    mail.send(msg)
    return jsonify({'status': 'success', 'message': f'Bill sent to {c.email}'})






@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))




if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)