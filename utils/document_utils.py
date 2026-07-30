import sqlite3
from datetime import date, timedelta
from utils.db import get_db_connection

def check_document_expiry():
    """فحص انتهاء صلاحية الوثائق وتحديث حالة انتهاء الصلاحية"""
    try:
        conn = get_db_connection()
        today = date.today()
        
        # تحديث حالة انتهاء الصلاحية
        # تمرير التواريخ كسلاسل ISO لتفادي تحذيرات Python 3.12 حول محولات التواريخ الافتراضية
        conn.execute('''
            UPDATE documents 
            SET is_expired = 1 
            WHERE expiry_date IS NOT NULL 
            AND expiry_date < ? 
            AND is_expired = 0
        ''', (today.isoformat(),))
        
        # جلب الوثائق التي انتهت صلاحيتها أو ستنتهي خلال 10 أيام
        expiring_documents = conn.execute('''
            SELECT d.*, e.name as employee_name, e.employee_number,
                   CASE 
                       WHEN d.expiry_date < ? THEN 'منتهية'
                       WHEN d.expiry_date <= ? THEN 'تنتهي قريباً'
                       ELSE 'صالحة'
                   END as status,
                   CASE 
                       WHEN d.expiry_date < ? THEN 0
                       ELSE JULIANDAY(d.expiry_date) - JULIANDAY(?)
                   END as days_remaining
            FROM documents d
            JOIN employees e ON d.employee_id = e.id
            WHERE d.expiry_date IS NOT NULL
            AND (d.expiry_date < ? OR d.expiry_date <= ?)
            ORDER BY d.expiry_date ASC
        ''', (
            today.isoformat(),
            (today + timedelta(days=10)).isoformat(),
            today.isoformat(),
            today.isoformat(),
            today.isoformat(),
            (today + timedelta(days=10)).isoformat()
        )).fetchall()
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        return expiring_documents
    except sqlite3.OperationalError as e:
        print(f"Error checking document expiry: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error in check_document_expiry: {e}")
        return []

def get_document_expiry_alerts():
    """جلب تنبيهات انتهاء صلاحية الوثائق"""
    expiring_documents = check_document_expiry()
    alerts = []
    
    for doc in expiring_documents:
        if doc['status'] == 'منتهية':
            alerts.append({
                'type': 'expired',
                'message': f"وثيقة {doc['document_type']} للموظف {doc['employee_name']} منتهية الصلاحية",
                'document': doc,
                'severity': 'danger'
            })
        elif doc['status'] == 'تنتهي قريباً':
            alerts.append({
                'type': 'expiring_soon',
                'message': f"وثيقة {doc['document_type']} للموظف {doc['employee_name']} تنتهي خلال {doc['days_remaining']} أيام",
                'document': doc,
                'severity': 'warning'
            })
    
    return alerts
