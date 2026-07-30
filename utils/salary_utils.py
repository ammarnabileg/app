from datetime import datetime, date, timedelta
import calendar
from utils.settings_utils import get_currency_settings, get_salary_settings_v2, get_default_salary_data
from utils.leave_utils import calculate_leave_balance
from utils.leave_policy_utils import calculate_tiered_leave_pay, is_official_holiday
from utils.common import time_to_minutes

def calculate_daily_attendance_v3(employee_id, date_str, shift, holidays, leaves, punches, settings=None):
    """
    Core Logic V3: Strict Policy A
    - Late > 08:10 (Deduct from 08:00)
    - OT > 16:30 (Only if Net >= 8h)
    - Early < 16:00
    - Missing Punch = Invalid
    """
    if settings is None:
        settings = {}
        
    res = {
        'date': date_str,
        'status': 'Present', # Default, changes below
        'late_mins': 0,
        'early_mins': 0,
        'ot_mins': 0,
        'work_hours': 0,
        'is_absent': False,
        'is_invalid': False,
        'is_leave': False,
        'is_holiday': False,
        'leave_type': None,
        'deduction_amount': 0, # Calculated later
        'ot_amount': 0
    }

    # 1. Check Leaves (Priority 1)
    # leaves list should contain dicts with start_date, end_date, leave_type_name
    curr_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    for leave in leaves:
        s_date = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
        e_date = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
        if s_date <= curr_date <= e_date:
            res['status'] = 'Leave'
            res['is_leave'] = True
            res['leave_type'] = leave['leave_type_name']
            return res # Leave overrides everything (even if they punched in? Maybe warn, but for pay it's leave)

    # 2. Check Holidays (Priority 2)
    # holidays list string dates
    if date_str in holidays:
        res['status'] = 'Holiday'
        res['is_holiday'] = True
        # Continue to check punches for Holiday Work (OT)
    
    # 2.5 Check Excuses / Manual Override (Priority 1.5)
    # excuses list should contain dates. If excused, treat as Present (Mission)
    if 'excuses' in settings and date_str in settings['excuses']:
        res['status'] = 'Mission'
        res['work_hours'] = shift.get('hours_per_day', 8)
        # Assuming paid mission = full attendance
        return res

    
    # 3. Check Punches
    if not punches:
        if res['is_holiday']:
            return res # Just holiday, no work
        
        # Check if Weekend? (Passed in shift? Or calculated?)
        # For now assume weekday if not told otherwise. Caller handles weekends.
        # If no punches and not holiday/leave -> Absent
        res['status'] = 'Absent'
        res['is_absent'] = True
        return res

    if len(punches) < 2:
        # Single punch logic
        missing_policy = settings.get('missing_punch_policy', 'invalid')
        if missing_policy == 'invalid':
            res['status'] = 'Invalid'
            res['is_invalid'] = True
            return res
        # Else handle as present? No, strictly invalid usually.
        res['status'] = 'Invalid'
        res['is_invalid'] = True
        return res

    # 4. Process Punches (First In, Last Out)
    punches.sort(key=lambda x: x['check_time'])
    first_punch = punches[0]['check_time'] # str 'YYYY-MM-DD HH:MM:SS'
    last_punch = punches[-1]['check_time']
    
    # Store Display Times
    res['check_in'] = first_punch[11:16] # HH:MM
    res['check_out'] = last_punch[11:16] # HH:MM
    
    fmt = '%Y-%m-%d %H:%M:%S'
    t_in = datetime.strptime(first_punch, fmt)
    t_out = datetime.strptime(last_punch, fmt)
    
    # Net Duration
    work_duration = (t_out - t_in).total_seconds() / 3600.0 # Hours
    res['work_hours'] = work_duration
    
    # Grace Periods & Rules
    shift_start_str = shift.get('start_time', '08:00')
    shift_end_str = shift.get('end_time', '16:00')
    
    # Convert to timestamps on the same day for comparison
    # Use 2000-01-01 dummy date
    base_date = '2000-01-01'
    dt_shift_start = datetime.strptime(f"{base_date} {shift_start_str}:00", fmt)
    dt_shift_end = datetime.strptime(f"{base_date} {shift_end_str}:00", fmt)
    
    # Extract times from punches
    dt_in = datetime.strptime(f"{base_date} {t_in.strftime('%H:%M:%S')}", fmt)
    dt_out = datetime.strptime(f"{base_date} {t_out.strftime('%H:%M:%S')}", fmt)
    
    # Late Logic: Strict > 08:10 (Grace 10 mins)
    grace_late_mins = int(settings.get('grace_late_minutes', 10))
    limit_in = dt_shift_start + timedelta(minutes=grace_late_mins)
    
    if dt_in > limit_in:
        # Deduct from Shift START (08:00) not from 08:10
        actual_late_secs = (dt_in - dt_shift_start).total_seconds()
        
        policy = settings.get('late_arrival_policy', 'actual_time')
        shift_hours = shift.get('hours_per_day', 8)
        shift_mins = shift_hours * 60
        
        if policy == 'warning':
             res['late_mins'] = 0
             res['status'] = 'Late (Warn)'
        elif policy == 'quarter_day':
             res['late_mins'] = int(shift_mins * 0.25)
        elif policy == 'half_day':
             res['late_mins'] = int(shift_mins * 0.50)
        elif policy == 'full_day':
             res['late_mins'] = int(shift_mins)
        else:
             res['late_mins'] = int(actual_late_secs / 60)
    
    # Early Departure Logic
    early_grace = int(settings.get('early_departure_grace_minutes', 0))
    limit_early = dt_shift_end - timedelta(minutes=early_grace)

    if dt_out < limit_early:
        # Calculate actual early minutes (for reference/reporting if possible)
        actual_early_secs = (dt_shift_end - dt_out).total_seconds()
        # We can add a new key if needed, but 'early_mins' drives the deduction.
        
        policy = settings.get('early_departure_policy', 'actual_time')
        shift_hours = shift.get('hours_per_day', 8)
        shift_mins = shift_hours * 60
        
        if policy == 'warning':
            res['early_mins'] = 0
            res['status'] = 'Early (Warn)' # Or keep present but flag?
            # User wants "Warning only (No Deduction)"
        elif policy == 'quarter_day':
            res['early_mins'] = int(shift_mins * 0.25)
            # Maybe append status?
            # res['status'] += ' (Early 1/4)'
        elif policy == 'half_day':
            res['early_mins'] = int(shift_mins * 0.50)
        elif policy == 'full_day':
            res['early_mins'] = int(shift_mins)
        else: # actual_time
            res['early_mins'] = int(actual_early_secs / 60)

    # Effective Work Hours Logic
    # NetDuration = work_duration
    # Effective for OT = NetDuration (must be >= 8)
    
    # OT Logic
    # Starts after 16:30 (Buffer 30 mins)
    ot_buffer_mins = int(settings.get('ot_start_buffer', 30))
    dt_ot_start = dt_shift_end + timedelta(minutes=ot_buffer_mins)
    
    if dt_out > dt_ot_start and work_duration >= 8:
        # Calc raw OT
        ot_secs = (dt_out - dt_ot_start).total_seconds()
        res['ot_mins'] = int(ot_secs / 60)
        
    # Validation: Holiday Work
    if res['is_holiday']:
        # If worked on holiday:
        # Pay = Basic + (TotalHours * 2.0)
        # We store 'ot_mins' as the extra payable time? 
        # Actually user said: Pay Day Salary + (TotalWork * 2.0)
        # So we should mark all hours as OT?
        # Let's keep work_hours and handle multiplier in monthly calc
        res['status'] = 'Holiday Work'
        res['ot_mins'] = int(work_duration * 60) # All hours are special OT
        # Reset normal lateness/early for holidays? Usually yes.
        res['late_mins'] = 0
        res['early_mins'] = 0

    return res


def calculate_weekly_leave_days_in_month(year, month, weekly_leave_start, weekly_leave_days):
    """حساب أيام الإجازة الأسبوعية في الشهر بدقة"""
    try:
        # تحويل القيم إلى أرقام
        weekly_leave_start = int(weekly_leave_start) if weekly_leave_start is not None else 5
        weekly_leave_days = int(weekly_leave_days) if weekly_leave_days is not None else 2
        
        if weekly_leave_days <= 0:
            return 0
        
        # تحويل من نظام الأيام العربي إلى نظام Python
        # النظام العربي: 0=الأحد, 1=الاثنين, 2=الثلاثاء, 3=الأربعاء, 4=الخميس, 5=الجمعة, 6=السبت
        # نظام Python: 0=الاثنين, 1=الثلاثاء, 2=الأربعاء, 3=الخميس, 4=الجمعة, 5=السبت, 6=الأحد
        
        # تحويل أيام الإجازة الأسبوعية إلى نظام Python
        python_weekdays = []
        
        # تحويل الأيام المختارة إلى نظام Python
        if weekly_leave_start == 0:  # الأحد
            python_weekdays.append(6)  # الأحد في Python
        elif weekly_leave_start == 1:  # الاثنين
            python_weekdays.append(0)  # الاثنين في Python
        elif weekly_leave_start == 2:  # الثلاثاء
            python_weekdays.append(1)  # الثلاثاء في Python
        elif weekly_leave_start == 3:  # الأربعاء
            python_weekdays.append(2)  # الأربعاء في Python
        elif weekly_leave_start == 4:  # الخميس
            python_weekdays.append(3)  # الخميس في Python
        elif weekly_leave_start == 5:  # الجمعة
            python_weekdays.append(4)  # الجمعة في Python
        elif weekly_leave_start == 6:  # السبت
            python_weekdays.append(5)  # السبت في Python
        
        # إضافة الأيام الإضافية بناءً على عدد الأيام
        if weekly_leave_days >= 2:
            if weekly_leave_start == 6:  # إذا بدأت من السبت
                python_weekdays.append(6)  # إضافة الأحد
            else:
                next_day = (python_weekdays[0] + 1) % 7
                python_weekdays.append(next_day)
        
        if weekly_leave_days >= 3:
            if weekly_leave_start == 5:  # إذا بدأت من الجمعة
                python_weekdays.extend([5, 6])  # إضافة السبت والأحد
            elif weekly_leave_start == 6:  # إذا بدأت من السبت
                python_weekdays.append(0)  # إضافة الاثنين
            else:
                next_day = (python_weekdays[1] + 1) % 7
                python_weekdays.append(next_day)
        
        days_in_month = calendar.monthrange(year, month)[1]
        weekly_leave_days_in_month = 0
        
        for day in range(1, days_in_month + 1):
            current_date = date(year, month, day)
            current_weekday = current_date.weekday()
            
            # التحقق من أن اليوم الحالي هو يوم إجازة أسبوعية
            if current_weekday in python_weekdays:
                weekly_leave_days_in_month += 1
        
        return weekly_leave_days_in_month
        
    except Exception as e:
        print(f"خطأ في حساب الإجازة الأسبوعية: {e}")
        return 0

def calculate_attendance_allowance_v2(conn, employee_id, month, year, hourly_salary, salary_settings):
    """حساب بدلات الحضور - الإصدار المحسن"""
    try:
        # جلب إعدادات البدلات
        grace_early_minutes = int(salary_settings.get('grace_early_minutes', 5))
        grace_late_minutes = int(salary_settings.get('grace_late_minutes', 10))
        overtime_round_to_minutes = int(salary_settings.get('overtime_round_to_minutes', 15))
        overtime_cap_monthly_hours = float(salary_settings.get('overtime_cap_monthly_hours', 60))
        
        # مضاعفات الأوفر تايم
        weekday_ot_multiplier = float(salary_settings.get('weekday_ot_multiplier', 1.25))
        weekend_ot_multiplier = float(salary_settings.get('weekend_ot_multiplier', 1.50))
        holiday_ot_multiplier = float(salary_settings.get('holiday_ot_multiplier', 2.00))
        
        # جلب بيانات الشفت
        employee_shift = conn.execute('''
            SELECT st.start_time, st.end_time, st.hours_per_day
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        ''', (employee_id,)).fetchone()
        
        shift_start_time = employee_shift['start_time'] if employee_shift and employee_shift['start_time'] else '08:00'
        shift_end_time = employee_shift['end_time'] if employee_shift and employee_shift['end_time'] else '17:00'
        shift_hours = employee_shift['hours_per_day'] if employee_shift and employee_shift['hours_per_day'] else 8
        
        # جلب بيانات الحضور للشهر
        month_str = f"{year:04d}-{month:02d}"
        all_records = conn.execute('''
            SELECT DATE(check_time) as date, check_time, check_type
            FROM attendance_records 
            WHERE employee_id = ? AND strftime('%Y-%m', check_time) = ?
            ORDER BY check_time
        ''', (employee_id, month_str)).fetchall()
        
        # تجميع السجلات حسب اليوم
        daily_records = {}
        for record in all_records:
            date_str = record['date']
            if date_str not in daily_records:
                daily_records[date_str] = []
            daily_records[date_str].append(record)
        
        early_arrival_days = 0
        late_departure_days = 0
        holiday_work_dates = []
        overtime_hours = {'weekday': 0, 'weekend': 0, 'holiday': 0}
        
        for date_str, records in daily_records.items():
            if not records:
                continue
                
            # ترتيب السجلات حسب الوقت
            records.sort(key=lambda x: x['check_time'])
            
            first_record = records[0]['check_time']
            last_record = records[-1]['check_time']
            
            # حساب ساعات العمل
            try:
                first_time = datetime.strptime(first_record, '%Y-%m-%d %H:%M:%S')
                last_time = datetime.strptime(last_record, '%Y-%m-%d %H:%M:%S')
                work_hours = (last_time - first_time).total_seconds() / 3600
            except:
                work_hours = 0
            
            # حساب بدل الحضور المبكر
            checkin_time = first_record[11:16]  # HH:MM
            if checkin_time < shift_start_time:
                try:
                    shift_start = datetime.strptime(f"2000-01-01 {shift_start_time}", "%Y-%m-%d %H:%M")
                    checkin = datetime.strptime(f"2000-01-01 {checkin_time}", "%Y-%m-%d %H:%M")
                    diff_minutes = (shift_start - checkin).total_seconds() / 60
                except:
                    diff_minutes = 0
                
                if diff_minutes >= grace_early_minutes:
                    early_arrival_days += 1
            
            # حساب بدل الانصراف المتأخر
            last_time_str = last_record[11:16]  # HH:MM
            if last_time_str > shift_end_time:
                # حساب بدل التأخير بعد الدوام
                shift_end = datetime.strptime(f"2000-01-01 {shift_end_time}:00", "%Y-%m-%d %H:%M:%S")
                last_time_str = last_record[11:16]
                last_checkout = datetime.strptime(f"2000-01-01 {last_time_str}:00", "%Y-%m-%d %H:%M:%S")
                
                diff_minutes = (last_checkout - shift_end).total_seconds() / 60
                
                if diff_minutes >= grace_late_minutes:
                    late_departure_days += 1
            
            # التحقق من العطلات الرسمية
            is_holiday = conn.execute('''
                SELECT 1 FROM official_holidays WHERE date = ?
            ''', (date_str,)).fetchone() is not None

            # تسجيل يوم تعويضي إذا عمل في عطلة
            if is_holiday and work_hours > 0:
                holiday_work_dates.append(date_str)

            # حساب الساعات الإضافية
            if work_hours > shift_hours:
                overtime_hours_diff = work_hours - shift_hours
                
                # تحديد نوع اليوم
                current_date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                current_weekday = current_date_obj.weekday()
                
                if is_holiday:
                    overtime_hours['holiday'] += overtime_hours_diff
                elif current_weekday >= 5:  # السبت والأحد
                    overtime_hours['weekend'] += overtime_hours_diff
                else:
                    overtime_hours['weekday'] += overtime_hours_diff
        
        # حساب المبالغ
        early_arrival_bonus = float(salary_settings.get('early_arrival_bonus', 2.5))
        late_departure_bonus = float(salary_settings.get('late_departure_bonus', 30.0))
        
        early_arrival_amount = early_arrival_days * early_arrival_bonus
        late_departure_amount = late_departure_days * late_departure_bonus
        
        # حساب الأوفر تايم مع المضاعفات
        overtime_amount = (
            overtime_hours['weekday'] * weekday_ot_multiplier +
            overtime_hours['weekend'] * weekend_ot_multiplier +
            overtime_hours['holiday'] * holiday_ot_multiplier
        ) * hourly_salary
        
        # تطبيق سقف الأوفر تايم الشهري
        total_overtime_hours = sum(overtime_hours.values())
        if total_overtime_hours > overtime_cap_monthly_hours:
            # تقليل المبلغ بنسبة السقف
            reduction_factor = overtime_cap_monthly_hours / total_overtime_hours
            overtime_amount *= reduction_factor
        
        total_allowance = early_arrival_amount + late_departure_amount + overtime_amount
        
        return {
            'early_arrival_days': early_arrival_days,
            'early_arrival_amount': early_arrival_amount,
            'late_departure_days': late_departure_days,
            'overtime_amount': overtime_amount,
            'total': total_allowance,
            'holiday_work_dates': holiday_work_dates
        }
        
    except Exception as e:
        print(f"خطأ في حساب بدلات الحضور v2: {e}")
        return {
            'early_arrival_days': 0,
            'early_arrival_amount': 0,
            'late_departure_days': 0,
            'late_departure_amount': 0,
            'overtime_hours': {'weekday': 0, 'weekend': 0, 'holiday': 0},
            'overtime_amount': 0,
            'total': 0
        }

def calculate_leave_deductions_v2(conn, employee_id, month, year, daily_salary, total_leave_days):
    """حساب خصومات الإجازات - الإصدار المحسن مع نظام الشرائح"""
    try:
        # جلب الإجازات المعتمدة في الشهر
        month_str = f"{year:04d}-{month:02d}"
        leaves = conn.execute('''
            SELECT lr.*, lt.name as leave_type_name, lt.id as leave_type_id, lt.is_paid
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id = ? 
            AND lr.status = 'approved'
            AND strftime('%Y-%m', lr.start_date) = ?
        ''', (employee_id, month_str)).fetchall()
        
        total_deduction = 0
        deduction_details = []
        
        for leave in leaves:
            # 1. حساب مدة الإجازة داخل هذا الشهر فقط
            start_date = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
            end_date = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
            
            month_start = date(year, month, 1)
            # End of month calc
            if month == 12:
                next_month = date(year + 1, 1, 1)
            else:
                next_month = date(year, month + 1, 1)
            month_end = next_month - timedelta(days=1)
            
            actual_start = max(start_date, month_start)
            actual_end = min(end_date, month_end)
            
            if actual_start <= actual_end:
                current_month_days = (actual_end - actual_start).days + 1
                
                # 2. حساب الاستهلاك السابق لهذا النوع قبل هذا الطلب (في نفس السنة)
                # نحتاج حساب "كم يوم استهلكت *قبل* بداية فترة التقاطع هذه؟"
                # لكن الشرائح تطبق على "الطلب ككل" أم "السنة ككل تراكمياً"؟ القانون: تراكمي سنوي.
                # لذا يجب معرفة ترتيب هذه الأيام ضمن السنة.
                
                # أ) حساب جميع الأيام المستهلكة من نفس النوع في السنة *قبل بداية هذا الشهر* (للتبسيط)
                # أو الأفضل: قبل `actual_start`.
                
                leave_type_id = leave['leave_type_id']
                year_start = date(year, 1, 1).strftime('%Y-%m-%d')
                current_start_str = actual_start.strftime('%Y-%m-%d')
                
                # استعلام عن مجموع الأيام السابقة في السنة
                used_before = conn.execute('''
                    SELECT SUM(days_count) as total
                    FROM leave_requests
                    WHERE employee_id = ?
                    AND leave_type_id = ?
                    AND status = 'approved'
                    AND start_date >= ?
                    AND start_date < ? -- نعتبر التاريخ بداية الطلب. 
                    -- ملاحظة: هذا تقريب إذا كان هناك تداخل معقد، لكنه مقبول للطلبات المتتابعة.
                ''', (employee_id, leave_type_id, year_start, leave['start_date'])).fetchone()
                
                prior_used_days = used_before['total'] if used_before and used_before['total'] else 0
                
                # إذا كانت الإجازة نفسها تمتد عبر أشهر، يجب إضافة الأيام التي مرت في الإجازة نفسها قبل `actual_start`
                days_elapsed_in_request = (actual_start - start_date).days
                total_prior_days = prior_used_days + days_elapsed_in_request
                
                # 3. حساب نسبة الدفع بناءً على الشرائح
                # نمرر `total_prior_days` (ما تم استهلاكه قبل هذه الدفعة من الأيام)
                # و `current_month_days` (عدد الأيام التي نحسب لها الآن)
                
                pay_percentage = calculate_tiered_leave_pay(conn, leave_type_id, total_prior_days, current_month_days)
                
                # 4. حساب الخصم
                # الخصم = الأيام * الراتب اليومي * (1 - نسبة الدفع)
                deduction_factor = 1.0 - pay_percentage
                deduction_amount = current_month_days * daily_salary * deduction_factor
                
                total_deduction += deduction_amount
                
                deduction_details.append({
                    'leave_type': leave['leave_type_name'],
                    'leave_type_id': leave_type_id,
                    'days': current_month_days,
                    'daily_salary': daily_salary,
                    'pay_percentage': pay_percentage,
                    'deduction_amount': deduction_amount,
                    'is_paid': pay_percentage > 0
                })

        # Apply Safety Cap (Optional / Per User Request)
        # max_deduction = daily_salary * 5
        # if total_deduction > max_deduction: 
        #    total_deduction = max_deduction 
        # (Disabled by default as strictly Unpaid Leave should be fully deducted)

        return {
            'total': total_deduction,
            'details': deduction_details
        }
        
    except Exception as e:
        print(f"خطأ في حساب خصومات الإجازات v2: {e}")
        return {
            'total': 0,
            'details': []
        }

def calculate_official_holidays_in_month(conn, year, month, weekly_leave_start, weekly_leave_days):
    """حساب أيام العطلات الرسمية في الشهر (التي لا تقع في عطلة أسبوعية)"""
    try:
        # 1. تحديد أيام العطلة الأسبوعية (لعدم احتساب العطلة الرسمية إذا وقعت فيها)
        weekly_leave_start = int(weekly_leave_start) if weekly_leave_start is not None else 5
        weekly_leave_days = int(weekly_leave_days) if weekly_leave_days is not None else 2
        
        python_weekdays = []
        if weekly_leave_start == 0: python_weekdays.append(6)  # الأحد
        elif weekly_leave_start == 1: python_weekdays.append(0)  # الاثنين
        elif weekly_leave_start == 2: python_weekdays.append(1)  # الثلاثاء
        elif weekly_leave_start == 3: python_weekdays.append(2)  # الأربعاء
        elif weekly_leave_start == 4: python_weekdays.append(3)  # الخميس
        elif weekly_leave_start == 5: python_weekdays.append(4)  # الجمعة
        elif weekly_leave_start == 6: python_weekdays.append(5)  # السبت
        
        if weekly_leave_days >= 2:
            if weekly_leave_start == 6: python_weekdays.append(6)
            else: python_weekdays.append((python_weekdays[0] + 1) % 7)
        if weekly_leave_days >= 3:
            if weekly_leave_start == 5: python_weekdays.extend([5, 6])
            elif weekly_leave_start == 6: python_weekdays.append(0)
            else: python_weekdays.append((python_weekdays[1] + 1) % 7)
            
        # 2. جلب العطلات الرسمية للشهر
        month_str = f"{year:04d}-{month:02d}"
        holidays = conn.execute("SELECT date FROM official_holidays WHERE strftime('%Y-%m', date) = ?", (month_str,)).fetchall()
        
        count = 0
        for h in holidays:
            try:
                h_date = datetime.strptime(h['date'], '%Y-%m-%d').date()
                if h_date.weekday() not in python_weekdays:
                    count += 1
            except:
                pass
                
        return count
    except Exception as e:
        print(f"خطأ في حساب العطلات الرسمية: {e}")
        return 0

def calculate_salary_for_employee_v3(conn, employee_id, month, year):
    """
    Calculate Salary using strict daily logic V3.
    """
    try:
        # 1. Fetch Settings & Employee
        salary_settings = get_salary_settings_v2(conn)
        employee = conn.execute('''
            SELECT e.*, st.name as shift_type_name, st.start_time, st.end_time, st.hours_per_day
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        ''', (employee_id,)).fetchone()
        
        if not employee:
            return None

        # 2. Prepare Shift & Salary Basics
        base_salary = employee['salary'] or 0
        hours_per_day = employee['hours_per_day'] or 8
        
        # Daily Salary Definition (per User): Monthly / 30 (or standard working days)
        # Using 30 as fixed standard for monthly salary division is common in ERPs.
        # Or using actual days in month? User said "Monthly Basic / Working Days"
        # Let's use 30 as a safe default for now or strict calendar days.
        days_in_month = calendar.monthrange(year, month)[1]
        daily_salary = base_salary / days_in_month if days_in_month > 0 else 0
        hourly_rate = daily_salary / hours_per_day if hours_per_day > 0 else 0
        
        shift = {
            'start_time': employee['start_time'] or '08:00',
            'end_time': employee['end_time'] or '16:00',
            'hours_per_day': hours_per_day
        }
        
        # 3. Fetch Data (Punches, Leaves, Holidays)
        month_str = f"{year:04d}-{month:02d}"
        
        # Punches
        all_punches = conn.execute('''
            SELECT check_time FROM attendance_records 
            WHERE employee_id = ? AND strftime('%Y-%m', check_time) = ?
        ''', (employee_id, month_str)).fetchall()
        
        punches_by_day = {}
        for p in all_punches:
            d = p['check_time'].split(' ')[0]
            if d not in punches_by_day: punches_by_day[d] = []
            punches_by_day[d].append(p)
            
        # Approved Leaves
        all_leaves = conn.execute('''
            SELECT start_date, end_date, lt.name as leave_type_name 
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE employee_id = ? AND status = 'approved'
            AND (strftime('%Y-%m', start_date) = ? OR strftime('%Y-%m', end_date) = ?)
        ''', (employee_id, month_str, month_str)).fetchall()
        
        leaves_list = [dict(l) for l in all_leaves]
        
        # Holidays
        holidays_res = conn.execute("SELECT date FROM official_holidays WHERE strftime('%Y-%m', date) = ?", (month_str,)).fetchall()
        holidays_list = [h['date'] for h in holidays_res]
        
        # Excuses / Manual Override
        excuses_res = conn.execute('''
            SELECT date FROM attendance_excuses 
            WHERE employee_id = ? AND strftime('%Y-%m', date) = ?
        ''', (employee_id, month_str)).fetchall()
        excuses_list = [e['date'] for e in excuses_res]
        
        # Inject excuses into settings for daily calculation
        salary_settings['excuses'] = excuses_list
        
        # 4. Iterate Days
        # 4. Iterate Days
        stats = {
            'days_worked': 0, 'present_days': 0, 'absent_days': 0, 
            'late_mins': 0, 'early_mins': 0, 'ot_mins': 0,
            'official_leaves': 0, 'sick_leaves': 0, 'holidays': 0,
            'invalid_days': 0
        }
        
        daily_details = []
        
        # Prepare Weekend Config
        # DB: 0=Sun, 1=Mon... 6=Sat (User Convention?)
        # Let's assume standard mapping:
        # Python: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        # We need to map DB `weekly_leave_start` to Python weekdays.
        # Often DB 0=Sun...
        
        w_start = employee['weekly_leave_start'] 
        if w_start is None: w_start = 5 # Default Friday
        w_days = employee['weekly_leave_days']
        if w_days is None: w_days = 1 # Default 1 day
        
        # Map DB(0=Sun) to Python(6=Sun)
        # If DB 0=Sun, 1=Mon...
        # Python: Mon=0, Tue=1, ... Sun=6
        # Map: Python = (DB_Day - 1) % 7? No.
        # DB 0 (Sun) -> Py 6
        # DB 1 (Mon) -> Py 0
        # DB 2 (Tue) -> Py 1
        # DB 3 (Wed) -> Py 2
        # DB 4 (Thu) -> Py 3
        # DB 5 (Fri) -> Py 4
        # DB 6 (Sat) -> Py 5
        
        def map_db_to_py(d):
            if d == 0: return 6
            return d - 1
            
        weekend_days_py = []
        # Add primary day
        py_start = map_db_to_py(w_start)
        weekend_days_py.append(py_start)
        
        # Add subsequent days
        for i in range(1, int(w_days)):
             weekend_days_py.append((py_start + i) % 7)
             
        for day in range(1, days_in_month + 1):
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
            curr_date = date(year, month, day)
            py_wd = curr_date.weekday()
            
            is_weekend = (py_wd in weekend_days_py)
            
            day_punches = punches_by_day.get(date_str, [])
            
            # Special Handling for Weekend with No Punches -> strictly 'Weekend' not 'Absent'
            if is_weekend and not day_punches and date_str not in holidays_list:
                # Check leaves?
                # If leave exists, loop below handles it (pass leaves_list to daily func)
                # But to avoid 'Absent', we can pre-check or modify daily func.
                # Let's rely on daily func but pass 'is_weekend' context?
                # Actually daily_v3 marks 'Absent' if no punches.
                # We should override it.
                
                # Update: Check leave first
                is_on_leave = False
                for l in leaves_list:
                    if l['start_date'] <= date_str <= l['end_date']:
                        is_on_leave = True
                        break
                
                if not is_on_leave:
                    daily_details.append({
                        'date': date_str, 'status': 'Weekend', 'code': 'W',
                        'work_hours': 0, 'is_absent': False, 'is_invalid': False,
                        'is_leave': False, 'is_holiday': False, 'late_mins': 0, 'early_mins': 0
                    })
                    continue

            day_res = calculate_daily_attendance_v3(
                employee_id, date_str, shift, holidays_list, leaves_list, day_punches, salary_settings
            )
            
            # Post-Process: If Absent but it was Weekend (and somehow logic missed it, mainly if punches exist? No, if punches exist it's work)
            # If logic returned Absent (no punches) and we didn't catch it above?
            # The block above catches "No Punch + Weekend".
            # So here we are safe.

            
            # Aggregate
            if day_res['is_invalid']:
                stats['invalid_days'] += 1
            elif day_res['is_absent']:
                # Only count absent if Work Day
                # Check weekend
                # ...
                stats['absent_days'] += 1
            elif day_res['is_leave']:
                stats['official_leaves'] += 1
            elif day_res['is_holiday'] and not day_res['ot_mins']:
                stats['holidays'] += 1
            else:
                # Present (or Holiday Work)
                stats['present_days'] += 1
                stats['days_worked'] += 1 # Is it?
                stats['late_mins'] += day_res['late_mins']
                stats['early_mins'] += day_res['early_mins']
                stats['ot_mins'] += day_res['ot_mins']
            
            daily_details.append(day_res)

        # 5. Financials
        # Rates
        ot_multiplier = float(salary_settings.get('weekday_ot_multiplier', 1.25)) # Simplify
        
        deductions = {
            'late': round((stats['late_mins'] / 60.0) * hourly_rate, 3),
            'early': round((stats['early_mins'] / 60.0) * hourly_rate, 3),
            'absent': round(stats['absent_days'] * daily_salary, 3)
        }
        
        ot_amount = round((stats['ot_mins'] / 60.0) * hourly_rate * ot_multiplier, 3)
        
        # Missing Punch Penalty Logic
        missing_punch_policy = salary_settings.get('missing_punch_policy', 'invalid')
        penalty_deduction_amount = 0
        
        if missing_punch_policy == 'penalty_tiered':
            # Count invalid days (already aggregated in stats['invalid_days'])
            # But we need per-occurrence logic if distinct penalties apply to 1st, 2nd...
            # The loop already ran. We can just iterate the count? 
            # Or better: Loop through details to find 'is_invalid' and apply specific penalty.
            
            p1 = float(salary_settings.get('missing_punch_penalty_1', 0))
            p2 = float(salary_settings.get('missing_punch_penalty_2', 0.25))
            p3 = float(salary_settings.get('missing_punch_penalty_3', 1.0))
            
            current_offense_count = 0
            for d in daily_details:
                if d['is_invalid']:
                   current_offense_count += 1
                   penalty_days = 0
                   penalty_label = ""
                   
                   if current_offense_count == 1:
                       penalty_days = p1
                       penalty_label = "(1st Offense)"
                   elif current_offense_count == 2:
                       penalty_days = p2
                       penalty_label = "(2nd Offense)"
                   else:
                       penalty_days = p3
                       penalty_label = f"({current_offense_count}th Offense)"
                   
                   if penalty_days > 0:
                       amount = round(penalty_days * daily_salary, 3)
                       penalty_deduction_amount += amount
                       
                       # Add to specific deduction details for report
                       deductions[f'penalty_{d["date"]}'] = amount # Hacky key?
                       # Better: Append to d['deduction_note'] if exists or create separate list?
                       # For now, let's add to 'total_deduction' and maybe a generic 'penalties' key
                       
        elif missing_punch_policy == 'zero_hours':
             # Already handled: Work Hours = 0.
             # If strictly "Deduct Day", then we should treat as Absent?
             # "Zero Hours" affects "Basic Salary" if paid by hour.
             # If paid by day (fixed), getting 0 hours doesn't deduct salary unless marked Absent.
             # If policy is 'zero_hours' -> Treat as Absent (mark absent_days += 1)
             # But we already have 'invalid_days'.
             # Let's add invalid days to absent deduction if this policy is on?
             # User said: "Count as zero hours (deduct day automatically)".
             # Implies treating it like Absence for deduction.
             penalty_deduction_amount = round(stats['invalid_days'] * daily_salary, 3)

        deductions['penalties'] = penalty_deduction_amount
        
        total_deduction = round(deductions['late'] + deductions['early'] + deductions['absent'] + deductions['penalties'], 3)
        net_salary = base_salary + ot_amount - total_deduction
        
        return {
            'base_salary': base_salary,
            'net_salary': round(net_salary, 2),
            'stats': stats,
            'deductions': deductions,
            'ot_amount': ot_amount,
            'details': daily_details
        }
        
    except Exception as e:
        print(f"Error V3: {e}")
        return None

def calculate_salary_for_employee(conn, employee_id, month, year, days_worked, total_leave_days, total_work_days, total_work_hours):
    """حساب الرواتب للموظف - الإصدار المحسن"""
    try:
        # جلب إعدادات العملة والتحسينات
        currency_settings = get_currency_settings(conn)
        salary_settings = get_salary_settings_v2(conn)
        
        # جلب بيانات الموظف والراتب الأساسي
        employee = conn.execute('''
            SELECT e.*, st.name as shift_type_name, st.start_time, st.end_time, st.hours_per_day
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        ''', (employee_id,)).fetchone()
        
        if not employee:
            print(f"Debug - Employee {employee_id} not found")
            return get_default_salary_data(days_worked, total_work_days, total_work_hours)
        
        base_salary = employee['salary'] or 0

        
        # حساب الراتب اليومي (المنطق القياسي للموارد البشرية هو قسمة الراتب على 30 يوماً ثابتة)
        days_in_month = calendar.monthrange(year, month)[1]
        daily_salary = base_salary / 30.0 if base_salary > 0 else 0
        
        # حساب الأجر بالساعة
        standard_shift_hours = employee['hours_per_day'] or 8
        hourly_salary = daily_salary / standard_shift_hours if standard_shift_hours > 0 else 0
        
        # حساب أيام الإجازة الأسبوعية بدقة
        weekly_leave_start = employee['weekly_leave_start'] if 'weekly_leave_start' in employee.keys() and employee['weekly_leave_start'] is not None else 5
        weekly_leave_days = employee['weekly_leave_days'] if 'weekly_leave_days' in employee.keys() and employee['weekly_leave_days'] is not None else 2
        
        weekly_leave_days_in_month = calculate_weekly_leave_days_in_month(
            year, month, weekly_leave_start, weekly_leave_days
        )

        official_holidays_in_month = calculate_official_holidays_in_month(
            conn, year, month, weekly_leave_start, weekly_leave_days
        )
        
        # إجمالي أيام العمل المحتسبة = أيام الحضور + أيام العطلة الأسبوعية + العطلات الرسمية
        total_working_days = days_worked + weekly_leave_days_in_month + official_holidays_in_month
        
        # الراتب الأساسي الشهري يكون ثابتاً، ويخصم منه أيام الغياب فقط
        # ولا يتم زيادة الراتب الأساسي إذا كان الموظف قد عمل أياماً إضافية (تحتسب في البدلات)
        basic_salary = base_salary
        if total_working_days < days_in_month:
            absent_days = days_in_month - total_working_days
            basic_salary = base_salary - (absent_days * daily_salary)
            if basic_salary < 0:
                basic_salary = 0
        
        # حساب بدلات الحضور المحسنة
        attendance_allowance = calculate_attendance_allowance_v2(
            conn, employee_id, month, year, hourly_salary, salary_settings
        )
        
        # حساب خصومات الإجازات المحسنة
        leave_deductions = calculate_leave_deductions_v2(
            conn, employee_id, month, year, daily_salary, total_leave_days
        )
        
        # حساب رصيد الإجازات
        leave_balance = calculate_leave_balance(conn, employee_id, month, year)
        
        # حساب الراتب الصافي
        # net_salary = basic_salary + attendance_allowance['total'] - leave_deductions['total']
        # Fixed logic: Salary usually starts from base_salary, adding allowances and subtracting deductions
        # If the strategy is "pay by day", then basic_salary is correct.
        # But usually monthly salary is fixed. The code uses basic_salary calculated from days.
        # Let's keep existing logic.
        net_salary = basic_salary + attendance_allowance['total'] - leave_deductions['total']
        
        # تطبيق قواعد التقريب
        rounding_decimals = int(salary_settings.get('rounding_display_decimals', 2))
        net_salary = round(net_salary, rounding_decimals)
        
        return {
            'base_salary': base_salary,
            'daily_salary': daily_salary,
            'hourly_salary': hourly_salary,
            'basic_salary': basic_salary,
            'attendance_allowance': attendance_allowance,
            'leave_deductions': leave_deductions,
            'leave_balance': leave_balance,
            'net_salary': net_salary,
            'days_worked': days_worked,
            'weekly_leave_days': weekly_leave_days_in_month,
            'total_working_days': total_working_days,
            'total_work_days': total_work_days,
            'total_work_hours': total_work_hours,
            'currency': currency_settings,
            'salary_settings': salary_settings
        }
        
    except Exception as e:
        print(f"خطأ في حساب الرواتب v2: {e}")
        return get_default_salary_data(days_worked, total_work_days, total_work_hours)

def generate_salary_anomalies_report(conn, employee_id, month, year):
    """تقرير الشذوذات في بيانات الراتب"""
    anomalies = []
    
    # 1. أيام ناقصة Check-in/Check-out
    month_str = f"{year:04d}-{month:02d}"
    incomplete_days = conn.execute('''
        SELECT DATE(check_time) as date, COUNT(*) as punch_count
        FROM attendance_records 
        WHERE employee_id = ? AND strftime('%Y-%m', check_time) = ?
        GROUP BY DATE(check_time)
        HAVING punch_count < 2
    ''', (employee_id, month_str)).fetchall()
    
    for day in incomplete_days:
        anomalies.append({
            'type': 'incomplete_punches',
            'date': day['date'],
            'message': f"عدد البصمات: {day['punch_count']} (مطلوب 2 على الأقل)"
        })
    
    # 2. تضارب بين إجازة وحضور
    conflicting_days = conn.execute('''
        SELECT lr.start_date, lr.end_date, ar.date
        FROM leave_requests lr
        JOIN attendance_records ar ON lr.employee_id = ar.employee_id
        WHERE lr.employee_id = ? 
        AND lr.status = 'approved'
        AND strftime('%Y-%m', lr.start_date) = ?
        AND ar.date BETWEEN lr.start_date AND lr.end_date
    ''', (employee_id, month_str)).fetchall()
    
    for conflict in conflicting_days:
        anomalies.append({
            'type': 'leave_attendance_conflict',
            'date': conflict['date'],
            'message': f"تضارب: إجازة من {conflict['start_date']} إلى {conflict['end_date']} مع حضور في {conflict['date']}"
        })
    
    # 3. فرق ساعات غير منطقي
    extreme_hours = conn.execute('''
        SELECT DATE(check_time) as date, 
               MIN(check_time) as first_punch,
               MAX(check_time) as last_punch,
               (julianday(MAX(check_time)) - julianday(MIN(check_time))) * 24 as work_hours
        FROM attendance_records 
        WHERE employee_id = ? AND strftime('%Y-%m', check_time) = ?
        GROUP BY DATE(check_time)
        HAVING work_hours > 16 OR work_hours < 1
    ''', (employee_id, month_str)).fetchall()
    
    for extreme in extreme_hours:
        anomalies.append({
            'type': 'extreme_work_hours',
            'date': extreme['date'],
            'message': f"ساعات عمل غير منطقية: {extreme['work_hours']:.1f} ساعة"
        })
    
    return anomalies

def get_late_arrival_alerts(conn, target_date=None):
    """جلب تنبيهات التأخير لليوم المحدد (أو اليوم الحالي)"""
    today = target_date if target_date else date.today().strftime('%Y-%m-%d')
    
    # إعدادات الرواتب (للحصول على فترة السماح)
    settings = get_salary_settings_v2(conn)
    grace_minutes = int(settings.get('attendance_grace_minutes', 15))
    
    # جلب الموظفين مع دوامهم
    employees = conn.execute('''
        SELECT e.id, e.name, e.employee_number, e.department, e.position, st.start_time
        FROM employees e
        LEFT JOIN shift_types st ON e.shift_type = st.name
        WHERE e.is_active = 1 AND st.start_time IS NOT NULL
    ''').fetchall()
    
    alerts = []
    
    for emp in employees:
        # جلب أول بصمة دخول اليوم
        # Fix: check_type is often integer (0=In, 1=Out). standard check.
        check_in = conn.execute('''
            SELECT MIN(check_time) as check_in 
            FROM attendance_records 
            WHERE employee_id = ? 
            AND date(check_time) = ?
            AND (
                check_type IN (1, 4, 'check_in') 
                OR check_type IS NULL 
            ) 
        ''', (emp['id'], today)).fetchone()
        
        if check_in and check_in['check_in']:
            check_in_time_str = check_in['check_in'].split(' ')[1] # HH:MM:SS
            # تحويل للدقائق
            actual_minutes = time_to_minutes(check_in_time_str[:5])
            shift_start_minutes = time_to_minutes(emp['start_time'])
            
            # حساب التأخير
            if actual_minutes > (shift_start_minutes + grace_minutes):
                late_min = actual_minutes - shift_start_minutes
                alerts.append({
                    'name': emp['name'],
                    'employee_number': emp['employee_number'],
                    'department': emp['department'] if emp['department'] else '',
                    'position': emp['position'] if emp['position'] else '',
                    'late_minutes': late_min,
                    'check_in': check_in_time_str,
                    'shift_start': emp['start_time']
                })
                
    return alerts
