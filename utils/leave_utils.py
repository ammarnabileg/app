from datetime import datetime, date, timedelta
import calendar
from utils.db import get_db_connection

def calculate_leave_balance(conn, employee_id, month, year):
    """حساب رصيد الإجازات السنوية (تراكمي)"""
    try:
        # 1. Employee Data
        employee = conn.execute('SELECT hire_date, previous_leave_balance FROM employees WHERE id = ?', (employee_id,)).fetchone()
        if not employee:
            return None
        
        hire_date = datetime.strptime(employee['hire_date'], '%Y-%m-%d').date()
        previous_balance = employee['previous_leave_balance'] or 0

        # Accrual rate now comes from settings (wired 2026-07-27); fallback 2.5
        try:
            from utils.settings_utils import get_salary_settings_v2
            accrual_per_month = float(get_salary_settings_v2(conn).get('leave_earned_per_month', 2.5))
        except Exception:
            accrual_per_month = 2.5
        
        # 2. Total Earned Days since Hire until End of Requested Month
        last_day_of_month = calendar.monthrange(year, month)[1]
        target_date = date(year, month, last_day_of_month)
        
        # Calculate total months worked from Hire Date to Target Date
        # Logic: (YearDiff * 12) + MonthDiff + 1 (to include start month)
        # Then subtract 1 if not yet reached the hire day in the target month.
        months_diff = (target_date.year - hire_date.year) * 12 + target_date.month - hire_date.month
        months_worked = months_diff + 1
        
        if target_date.day < hire_date.day:
            months_worked -= 1
        
        months_worked = max(0, months_worked)
        
        # Accrual Rate: 2.5 per month
        total_earned = months_worked * accrual_per_month
        
        # 3. Total Used Days (Annual + Emergency) from Hire Date until Target Date
        used_query = '''
            SELECT COALESCE(SUM(lr.days_count), 0) as total_used
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id = ? 
            AND lr.status = 'approved'
            AND (lt.name LIKE '%سنوية%' OR lt.name LIKE '%طارئة%')
            AND lr.end_date <= ?
        '''
        used_res = conn.execute(used_query, (employee_id, target_date.strftime('%Y-%m-%d'))).fetchone()
        total_used = used_res['total_used']
        
        # 4. Current Balance
        # Total Available = (Initial Balance + Total Earned) - Total Used
        current_balance = (previous_balance + total_earned) - total_used
        
        # For the specific month report (Opening/Earned/Used/Closing):
        # We need "Used This Month".
        used_this_month_query = '''
            SELECT COALESCE(SUM(lr.days_count), 0) as used_this_month
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id = ? 
            AND lr.status = 'approved'
            AND (lt.name LIKE '%سنوية%' OR lt.name LIKE '%طارئة%')
            AND strftime('%Y-%m', lr.start_date) = ?
        '''
        month_str = f'{year:04d}-{month:02d}'
        used_this_month = conn.execute(used_this_month_query, (employee_id, month_str)).fetchone()['used_this_month']
        
        # Earned This Month is strictly 2.5
        earned_this_month = accrual_per_month
        
        # Opening Balance (Start of Month)
        opening_balance = current_balance - earned_this_month + used_this_month
        
        is_negative = current_balance < 0
        
        return {
            'opening_balance': opening_balance,
            'earned_days': earned_this_month, # Display for this month
            'used_days': used_this_month,     # Display for this month
            'closing_balance': current_balance, # THE ACTUAL RUNNING BALANCE
            'is_negative': is_negative,
            'excess_days': abs(current_balance) if is_negative else 0
        }
        
    except Exception as e:
        print(f"خطأ في حساب رصيد الإجازات: {e}")
        return None

def save_leave_balance(conn, employee_id, month, year):
    """حفظ رصيد الإجازات الشهري في قاعدة البيانات"""
    try:
        leave_balance = calculate_leave_balance(conn, employee_id, month, year)
        if not leave_balance:
            return False
        
        conn.execute('''
            INSERT OR REPLACE INTO leave_balance 
            (employee_id, month, year, opening_balance, earned_days, used_days, closing_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (employee_id, month, year, 
              leave_balance['opening_balance'], 
              leave_balance['earned_days'], 
              leave_balance['used_days'], 
              leave_balance['closing_balance']))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"خطأ في حفظ رصيد الإجازات: {e}")
        return False

def check_leave_cancellation_eligibility(conn, leave_request_id):
    """التحقق من إمكانية قطع الإجازة"""
    try:
        # جلب بيانات الإجازة
        leave_request = conn.execute('''
            SELECT lr.*, e.name as employee_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            WHERE lr.id = ?
        ''', (leave_request_id,)).fetchone()
        
        if not leave_request:
            return {'eligible': False, 'message': 'الإجازة غير موجودة'}
        
        if leave_request['status'] != 'approved':
            return {'eligible': False, 'message': 'يمكن قطع الإجازات المعتمدة فقط'}
        
        # التحقق من أن الإجازة لم تنته بعد
        today = datetime.now().date()
        end_date = datetime.strptime(leave_request['end_date'], '%Y-%m-%d').date()
        
        if today >= end_date:
            return {'eligible': False, 'message': 'لا يمكن قطع إجازة منتهية'}
        
        # التحقق من عدم وجود قطع سابق
        existing_cancellation = conn.execute('''
            SELECT 1 FROM leave_cancellations WHERE leave_request_id = ?
        ''', (leave_request_id,)).fetchone()
        
        if existing_cancellation:
            return {'eligible': False, 'message': 'تم قطع هذه الإجازة مسبقاً'}
        
        return {
            'eligible': True,
            'leave_request': leave_request,
            'remaining_days': (end_date - today).days
        }
        
    except Exception as e:
        return {'eligible': False, 'message': f'خطأ في التحقق: {str(e)}'}

def cancel_leave_request(conn, leave_request_id, cancellation_date, reason, cancelled_by):
    """قطع الإجازة (إلغاء مبكر)"""
    try:
        # جلب بيانات الإجازة
        leave_request = conn.execute('''
            SELECT lr.*, e.name as employee_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            WHERE lr.id = ? AND lr.status = 'approved'
        ''', (leave_request_id,)).fetchone()
        
        if not leave_request:
            return {'success': False, 'message': 'الإجازة غير موجودة أو غير معتمدة'}
        
        # التحقق من أن تاريخ القطع قبل تاريخ انتهاء الإجازة
        if cancellation_date >= leave_request['end_date']:
            return {'success': False, 'message': 'تاريخ القطع يجب أن يكون قبل تاريخ انتهاء الإجازة'}
        
        # حساب الأيام الملغاة
        end_date = datetime.strptime(leave_request['end_date'], '%Y-%m-%d').date()
        cancel_date = datetime.strptime(cancellation_date, '%Y-%m-%d').date()
        cancelled_days = (end_date - cancel_date).days
        
        if cancelled_days <= 0:
            return {'success': False, 'message': 'لا يمكن قطع الإجازة - التاريخ غير صحيح'}
        
        # تحديث الإجازة
        conn.execute('''
            UPDATE leave_requests 
            SET end_date = ?, days_count = days_count - ?
            WHERE id = ?
        ''', (cancellation_date, cancelled_days, leave_request_id))
        
        # تسجيل عملية القطع
        conn.execute('''
            INSERT INTO leave_cancellations (
                leave_request_id, employee_id, original_start_date, original_end_date,
                original_days_count, cancellation_date, cancelled_days, reason, cancelled_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            leave_request_id, leave_request['employee_id'], 
            leave_request['start_date'], leave_request['end_date'],
            leave_request['days_count'], cancellation_date, cancelled_days, reason, cancelled_by
        ))
        
        conn.commit()
        
        return {
            'success': True, 
            'message': f'تم قطع الإجازة بنجاح. تم إلغاء {cancelled_days} أيام وإرجاعها لرصيد الموظف',
            'cancelled_days': cancelled_days,
            'employee_name': leave_request['employee_name']
        }
        
    except Exception as e:
        conn.rollback()
        return {'success': False, 'message': f'خطأ في قطع الإجازة: {str(e)}'}

def get_leave_cancellations(conn, employee_id=None):
    """جلب عمليات قطع الإجازات"""
    try:
        if employee_id:
            cancellations = conn.execute('''
                SELECT lc.*, e.name as employee_name, lr.leave_type_id, lt.name as leave_type_name
                FROM leave_cancellations lc
                JOIN employees e ON lc.employee_id = e.id
                JOIN leave_requests lr ON lc.leave_request_id = lr.id
                JOIN leave_types lt ON lr.leave_type_id = lt.id
                WHERE lc.employee_id = ?
                ORDER BY lc.created_at DESC
            ''', (employee_id,)).fetchall()
        else:
            cancellations = conn.execute('''
                SELECT lc.*, e.name as employee_name, lr.leave_type_id, lt.name as leave_type_name
                FROM leave_cancellations lc
                JOIN employees e ON lc.employee_id = e.id
                JOIN leave_requests lr ON lc.leave_request_id = lr.id
                JOIN leave_types lt ON lr.leave_type_id = lt.id
                ORDER BY lc.created_at DESC
            ''').fetchall()
        
        return cancellations
        
    except Exception as e:
        print(f"خطأ في جلب عمليات قطع الإجازات: {e}")
        return []

def check_is_official_holiday(conn, check_date):
    """
    التحقق مما إذا كان التاريخ المحدد هو إجازة رسمية
     Returns:
         dict or None: بيانات الإجازة إذا وجدت، وإلا None
    """
    try:
        # تأكد من أن التاريخ نصي بالتنسيق الصحيح
        if isinstance(check_date, (datetime, date)):
            check_date_str = check_date.strftime('%Y-%m-%d')
        else:
            check_date_str = str(check_date)
            
        holiday = conn.execute('SELECT * FROM official_holidays WHERE date = ?', (check_date_str,)).fetchone()
        return dict(holiday) if holiday else None
        
    except Exception as e:
        print(f"خطأ في التحقق من الإجازة الرسمية: {e}")
        return None

def calculate_actual_leave_days(conn, start_date, end_date):
    """
    حساب عدد أيام الإجازة الفعلية باستثناء العطلات الأسبوعية (الجمعة والسبت) والرسمية
    """
    try:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
            
        current_date = start_date
        days_count = 0
        
        # جلب الإجازات الرسمية في الفترة
        official_holidays = conn.execute('''
            SELECT date FROM official_holidays 
            WHERE date BETWEEN ? AND ?
        ''', (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))).fetchall()
        
        holiday_dates = {row['date'] for row in official_holidays}
        
        while current_date <= end_date:
            # استبعاد الجمعة (4) والسبت (5)
            # Python: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
            if current_date.weekday() not in [4, 5]:
                # استبعاد الإجازات الرسمية
                if current_date.strftime('%Y-%m-%d') not in holiday_dates:
                    days_count += 1
            
            current_date += timedelta(days=1)
            
        return days_count
        
    except Exception as e:
        print(f"خطأ في حساب أيام الإجازة الفعلية: {e}")
        return (end_date - start_date).days + 1 # Fallback to calendar days
