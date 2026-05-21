import os
from app import app, db, Customer, Visit

def run_merge():
    with app.app_context():
        phones = db.session.query(Customer.phone, db.func.count(Customer.phone)).group_by(Customer.phone).having(db.func.count(Customer.phone) > 1).all()
        for phone, count in phones:
            print(f"Merging {count} customers for phone {phone}")
            customers = Customer.query.filter_by(phone=phone).order_by(Customer.id).all()
            primary = customers[0]
            for duplicate in customers[1:]:
                # Move visits using bulk update
                Visit.query.filter_by(customer_id=duplicate.id).update({"customer_id": primary.id})
                db.session.commit()
                # delete duplicate
                db.session.delete(duplicate)
                db.session.commit()
        print("Duplicates merged.")

if __name__ == '__main__':
    run_merge()
