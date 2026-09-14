#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تقارير بصمة ذكية بناءً على الوقت
=============================
تقارير يومية وشهرية للحضور بناءً على:
- الوقت فقط (مش check_type)
- الشفتات من صفحة الشفتات
- checkin = أول بصمة في اليوم
- checkout = آخر بصمة في اليوم
"""

import sqlite3
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
import calendar

from utils.db import DB_PATH

class SmartFingerprintReport:
    def __init__(self, db_path=None):
        self.db_path = db_path if db_path else DB_PATH
    
    def get_connection(self):
        """الحصول على اتصال قاعدة البيانات"""
        return sqlite3.connect(self.db_path)
    
    def get_grace_minutes(self) -> Dict:
        """جلب فترة سماح موحدة من الإعدادات.
        يفضّل attendance_grace_minutes إن وُجد؛ وإلا يستخدم أحد المفاتيح
        المتوفرة: grace_late_minutes أو grace_early_minutes (بدائل)،
        ثم يطبّق القيمة نفسها على التأخير والانصراف المتأخر.
        """
        setting_names = (
            'attendance_grace_minutes',      # الإعداد الموحد المقترح
            'grace_late_minutes',            # بدائل حالية
            'grace_early_minutes',
            'grace_late_arrival_minutes',    # للتوافق الرجعي
            'grace_late_departure_minutes'
        )
        settings: Dict[str, str] = {}
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT setting_name, setting_value
                FROM salary_settings_v2
                WHERE setting_name IN ({','.join(['?']*len(setting_names))})
                """,
                setting_names
            )
            for name, value in cur.fetchall():
                settings[name] = value
        except Exception:
            # جدول غير موجود أو أي خطأ آخر: سنستخدم القيم الافتراضية
            pass
        finally:
            try:
                pass # conn.close() removed to prevent leak in Flask g
            except Exception:
                pass

        def _to_int_minutes(val, default):
            try:
                return int(float(val))
            except Exception:
                return default

        # اختيار قيمة موحدة
        grace_val = _to_int_minutes(
            settings.get('attendance_grace_minutes', settings.get('grace_late_minutes', settings.get('grace_early_minutes', settings.get('grace_late_arrival_minutes', settings.get('grace_late_departure_minutes', 10))))),
            10
        )

        return {
            'late_arrival': grace_val,
            'late_departure': grace_val,
            'grace': grace_val,
        }
    
    def get_employee_shift_from_employee_data(self, emp_id: int) -> Dict:
        """الحصول على شفت الموظف من بياناته الأساسية مع دعم السحب من أنواع الشفت عند غياب القيم.
        يعيد أيضاً مؤشر عبور منتصف الليل عندما يكون وقت النهاية <= البداية.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # الحصول على بيانات الشفت من جدول الموظفين
        cursor.execute("""
            SELECT e.default_start_time, e.default_end_time, e.shift_type,
                   st.start_time, st.end_time, st.hours_per_day, st.description,
                   st.presence_start_time, st.presence_end_time
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        """, (emp_id,))
        
        row = cursor.fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        if row:
            # Check if Shift Type exists and has valid times
            # Prioritize Shift Type if available, otherwise use Employee Default
            shift_type_start = row[3]
            shift_type_end = row[4]
            emp_start = row[0]
            emp_end = row[1]
            presence_start = row[7]
            presence_end = row[8]
            
            if shift_type_start and shift_type_end:
                 start_time = shift_type_start
                 end_time = shift_type_end
                 source = 'shift_type'
                 hours = row[5] or 8.0
                 shift_name = row[2] or 'عادي'
                 notes = row[6] or ''
                 presence_start = row[7]
                 presence_end = row[8]
            elif emp_start and emp_end:
                 start_time = emp_start
                 end_time = emp_end
                 source = 'employee_data'
                 hours = row[5] or 8.0 
                 shift_name = row[2] or 'عادي'
                 notes = row[6] or ''
            else:
                 # Default fallback within row check
                 start_time = '09:00'
                 end_time = '17:00'
                 source = 'default'
                 hours = 8.0
                 shift_name = 'عادي'
                 notes = 'شفت افتراضي'

            # Check if it's a flexible shift (*) or (anytime/anything)
            shift_name_lower = (shift_name or '').lower().strip()
            notes_lower = (notes or '').lower().strip()
            
            is_flexible = (
                shift_name_lower in ['*', 'anytime', 'anything', 'any thing', 'حر', 'مرن'] or
                '*' in shift_name_lower or
                'flexible' in shift_name_lower or
                'anytime' in notes_lower or
                'anything' in notes_lower
            )

            cross = False
            try:
                st = datetime.strptime(start_time, "%H:%M")
                en = datetime.strptime(end_time, "%H:%M")
                cross = en <= st
            except Exception:
                pass
                
            return {
                'start': start_time,
                'end': end_time,
                'hours': hours,
                'shift_type': shift_name,
                'notes': notes,
                'source': source,
                'cross_midnight': cross,
                'is_flexible': is_flexible,
                'presence_start': presence_start,
                'presence_end': presence_end,
                'presence_start_time': presence_start,
                'presence_end_time': presence_end
            }
        # إذا لم توجد بيانات، نرجع شفت افتراضي
        return {
            'start': '09:00',
            'end': '17:00', 
            'hours': 8.0,
            'shift_type': 'عادي',
            'notes': 'شفت افتراضي',
            'source': 'default',
            'cross_midnight': False,
            'is_flexible': False,
            'presence_start': None,
            'presence_end': None,
            'presence_start_time': None,
            'presence_end_time': None
        }
    
    def get_daily_checkin_checkout_by_time(self, emp_id: int, target_date: str) -> Dict:
        """حساب الدخول والخروج بناءً على الوقت فقط"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # الحصول على جميع البصمات في هذا اليوم (مرتبة بالوقت)
        cursor.execute("""
            SELECT CASE 
                       WHEN check_time LIKE '%T%' THEN REPLACE(check_time, 'T', ' ')
                       ELSE check_time
                   END as check_time
            FROM attendance_records
            WHERE employee_id = ? AND DATE(check_time) = ?
            ORDER BY check_time ASC
        """, (emp_id, target_date))
        
        records = cursor.fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        if not records:
            return {
                'checkin': None,
                'checkout': None,
                'checkin_time': '00:00',
                'checkout_time': '00:00',
                'total_records': 0,
                'all_times': []
            }
        
        # أول بصمة = دخول
        first_record = records[0][0]
        checkin_time = datetime.fromisoformat(first_record).strftime("%H:%M")
        
        # آخر بصمة = خروج
        last_record = records[-1][0]
        checkout_time = datetime.fromisoformat(last_record).strftime("%H:%M")
        
        # إذا كان هناك بصمة واحدة فقط
        if len(records) == 1:
            checkout_time = '00:00'
        
        # جميع الأوقات
        all_times = [datetime.fromisoformat(r[0]).strftime("%H:%M") for r in records]
        
        return {
            'checkin': first_record,
            'checkout': last_record if len(records) > 1 else None,
            'checkin_time': checkin_time,
            'checkout_time': checkout_time,
            'total_records': len(records),
            'all_times': all_times
        }
    
    def calculate_work_hours(self, checkin_time: str, checkout_time: str) -> float:
        """حساب ساعات العمل"""
        if checkin_time == '00:00' or checkout_time == '00:00':
            return 0.0
        
        try:
            checkin = datetime.strptime(checkin_time, "%H:%M")
            checkout = datetime.strptime(checkout_time, "%H:%M")
            
            # إذا كان الخروج في اليوم التالي
            if checkout < checkin:
                checkout = checkout.replace(day=checkout.day + 1)
            
            duration = checkout - checkin
            return round(duration.seconds / 3600, 2)
        except:
            return 0.0
    
    def get_daily_report(self, target_date: str = None) -> List[Dict]:
        """تقرير يومي للحضور"""
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # الحصول على جميع الموظفين النشطين
        cursor.execute("""
            SELECT id, name, employee_number, department, position, weekly_leave_selected_days
            FROM employees
            WHERE is_active = 1 OR (end_of_service_date IS NOT NULL AND DATE(end_of_service_date) >= ?)
            ORDER BY CAST(employee_number AS INTEGER), name
        """, (target_date,))
        
        employees = cursor.fetchall()
        
        # Check for official holiday
        cursor.execute("SELECT name FROM official_holidays WHERE date = ?", (target_date,))
        holiday_row = cursor.fetchone()
        is_holiday = holiday_row is not None
        holiday_name = holiday_row[0] if is_holiday else ""
        
        # Fetch approved leaves for this day
        cursor.execute('''
            SELECT lr.employee_id, lt.name, lr.reason
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.status = 'approved' 
            AND ? BETWEEN lr.start_date AND lr.end_date
        ''', (target_date,))
        leaves_rows = cursor.fetchall()
        leaves_map = {row[0]: {'name': row[1], 'reason': row[2] or ''} for row in leaves_rows}
        
        pass # conn.close() removed to prevent leak in Flask g
        
        report = []
        grace_cfg = self.get_grace_minutes()
        
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        day_of_week = target_dt.isoweekday() % 7 # 0=Sunday, 1=Monday ... 6=Saturday
        
        for emp in employees:
            emp_id, name, emp_number, department, position, weekly_leave_days_str = emp
            
            # تحديد ما إذا كان اليوم إجازة أسبوعية للموظف
            is_weekly_off = False
            if weekly_leave_days_str:
                selected_days = [int(d.strip()) for d in weekly_leave_days_str.split(',') if d.strip().isdigit()]
                if day_of_week in selected_days:
                    is_weekly_off = True
            
            # الحصول على بيانات الحضور
            attendance = self.get_daily_checkin_checkout_by_time(emp_id, target_date)
            
            # الحصول على بيانات الشفت من بيانات الموظف
            shift = self.get_employee_shift_from_employee_data(emp_id)
            
            # حساب ساعات العمل
            work_hours = self.calculate_work_hours(
                attendance['checkin_time'], 
                attendance['checkout_time']
            )
            
            # تحديد الحالة
            if shift.get('is_flexible'):
                if attendance['total_records'] > 0:
                    status = 'حاضر'
                    work_hours = 0.0 # حسب طلب المستخدم "من غير ساعات"
                else:
                    if emp_id in leaves_map:
                        leave_info = leaves_map[emp_id]
                        status = f"إجازة: {leave_info['name']}"
                    elif is_holiday:
                        status = f"عطلة رسمية: {holiday_name}"
                    elif is_weekly_off:
                        status = 'إجازة أسبوعية'
                    else:
                        status = 'غائب'
            elif attendance['checkin_time'] != '00:00' and attendance['checkout_time'] != '00:00':
                status = 'حاضر'
            elif attendance['checkin_time'] != '00:00':
                status = 'دخول فقط'
            elif attendance['checkout_time'] != '00:00':
                status = 'خروج فقط'
            else:
                if emp_id in leaves_map:
                    leave_info = leaves_map[emp_id]
                    status = f"إجازة: {leave_info['name']}"
                elif is_holiday:
                    status = f"عطلة رسمية: {holiday_name}"
                elif is_weekly_off:
                    status = 'إجازة أسبوعية'
                else:
                    status = 'غائب'
            
            # حساب التأخير/الوصول المبكر + المغادرة المبكرة
            late_minutes = 0
            early_arrival_minutes = 0
            early_leave_minutes = 0
            
            if not shift.get('is_flexible'):
                if attendance['checkin_time'] != '00:00' and shift['start']:
                    try:
                        scheduled_start = datetime.strptime(shift['start'], "%H:%M")
                        actual_start = datetime.strptime(attendance['checkin_time'], "%H:%M")
                        # إذا الشفت يعبر منتصف الليل، ووقت الدخول قبل وقت البداية الاسمي بكثير، اعتبره من اليوم السابق وليس تأخيراً مبالغاً فيه
                        if actual_start > scheduled_start:
                            # تأخير
                            late_minutes = int((actual_start - scheduled_start).seconds / 60)
                        elif actual_start < scheduled_start:
                            # وصول مبكر
                            early_arrival_minutes = int((scheduled_start - actual_start).seconds / 60)
                    except:
                        pass
                
                if attendance['checkout_time'] != '00:00' and shift['end']:
                    try:
                        scheduled_end = datetime.strptime(shift['end'], "%H:%M")
                        actual_end = datetime.strptime(attendance['checkout_time'], "%H:%M")
                        if shift.get('cross_midnight') and actual_end > scheduled_end:
                            # في شفت يعبر منتصف الليل، الخروج بعد النهاية الاسمية قد يكون طبيعي، لا يعتبر انصراف مبكر
                            early_leave_minutes = 0
                        elif actual_end < scheduled_end:
                            early_leave_minutes = int((scheduled_end - actual_end).seconds / 60)
                    except:
                        pass
            
            # تطبيق فترة السماح على التأخير (بداية الشفت) بطرحه من القيمة
            raw_late_minutes = late_minutes
            if late_minutes > 0:
                late_minutes = max(0, late_minutes - grace_cfg['late_arrival'])
            
            report.append({
                'employee_id': emp_id,
                'name': name,
                'employee_number': emp_number,
                'department': department,
                'position': position,
                'date': target_date,
                'checkin_time': attendance['checkin_time'],
                'checkout_time': attendance['checkout_time'],
                'scheduled_start': shift['start'],
                'scheduled_end': shift['end'],
                'work_hours': work_hours,
                'scheduled_hours': shift['hours'],
                'status': status,
                'late_minutes': late_minutes,
                'raw_late_minutes': raw_late_minutes,
                'grace_minutes': grace_cfg['late_arrival'],
                'early_arrival_minutes': early_arrival_minutes,
                'early_leave_minutes': early_leave_minutes,
                'total_records': attendance['total_records'],
                'all_times': attendance['all_times'],
                'shift_notes': shift['notes'],
                'shift_source': shift['source'],
                'leave_reason': leaves_map.get(emp_id, {}).get('reason', '') if emp_id in leaves_map else '',
                'shift_type_name': shift['shift_type']
            })
        
        return report
    
    def get_monthly_report(self, year: int = None, month: int = None) -> Dict:
        """تقرير شهري للحضور"""
        if not year:
            year = date.today().year
        if not month:
            month = date.today().month
        
        # جلب الإجازات الرسمية للشهر مرة واحدة
        conn_holidays = self.get_connection()
        holidays_rows = conn_holidays.execute(
            "SELECT date, name FROM official_holidays WHERE strftime('%Y-%m', date) = ?", 
            (f"{year}-{month:02d}",)
        ).fetchall()
        conn_holidays.close()
        holidays_map = {row[0]: row[1] for row in holidays_rows}
        
        # الحصول على جميع أيام الشهر
        days_in_month = calendar.monthrange(year, month)[1]
        
        monthly_data = {}
        summary = {
            'year': year,
            'month': month,
            'month_name': calendar.month_name[month],
            'total_days': days_in_month,
            'total_employees': 0,
            'total_present_days': 0,
            'total_absent_days': 0,
            'total_partial_days': 0,
            'employees_summary': {}
        }
        
        for day in range(1, days_in_month + 1):
            target_date = f"{year}-{month:02d}-{day:02d}"
            daily_report = self.get_daily_report(target_date)
            monthly_data[target_date] = daily_report
            
            # حساب الإحصائيات
            for record in daily_report:
                emp_name = record['name']
                
                if emp_name not in summary['employees_summary']:
                    summary['employees_summary'][emp_name] = {
                        'present_days': 0,
                        'absent_days': 0,
                        'partial_days': 0,
                        'total_work_hours': 0,
                        'total_late_minutes': 0,
                        'total_early_leave_minutes': 0
                    }
                
                emp_summary = summary['employees_summary'][emp_name]
                
                if record['status'] == 'حاضر':
                    emp_summary['present_days'] += 1
                    summary['total_present_days'] += 1
                elif record['status'] == 'غائب':
                    emp_summary['absent_days'] += 1
                    summary['total_absent_days'] += 1
                elif 'عطلة رسمية' in record['status']:
                    # لا نحسبها غياب ولا حضور، أو يمكن إضافتها كحقل جديد
                    # حالياً لا نفعل شيئاً للإحصائيات أو نضيفها في partial إذا أردنا
                    pass
                else:
                    emp_summary['partial_days'] += 1
                    summary['total_partial_days'] += 1
                
                emp_summary['total_work_hours'] += record['work_hours']
                emp_summary['total_late_minutes'] += record['late_minutes']
                emp_summary['total_early_leave_minutes'] += record['early_leave_minutes']
        
        summary['total_employees'] = len(summary['employees_summary'])
        
        return {
            'summary': summary,
            'daily_data': monthly_data
        }

    def get_custom_date_range_report(self, start_date_str: str, end_date_str: str) -> Dict:
        """تقرير مخصص لنطاق تواريخ محسّن للأداء (بدون N+1 queries)"""
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 1. جلب جميع الموظفين
        cursor.execute("""
            SELECT id, name, employee_number, department, position, weekly_leave_selected_days, shift_type, default_start_time, default_end_time
            FROM employees
            WHERE is_active = 1 OR (end_of_service_date IS NOT NULL AND DATE(end_of_service_date) >= ?)
            ORDER BY CAST(employee_number AS INTEGER), name
        """, (start_date_str,))
        employees = cursor.fetchall()
        
        # 2. جلب جميع البصمات في الفترة المحددة
        cursor.execute("""
            SELECT employee_id, check_time, check_type 
            FROM attendance_records 
            WHERE DATE(check_time) BETWEEN ? AND ?
            ORDER BY employee_id, check_time ASC
        """, (start_date_str, end_date_str))
        all_records = cursor.fetchall()
        
        # تجميع البصمات
        attendance_map = {}
        for row in all_records:
            emp_id = row[0]
            check_time_full = row[1].replace('T', ' ')
            date_part = check_time_full.split(' ')[0]
            time_part = check_time_full.split(' ')[1][:5]
            
            key = (emp_id, date_part)
            if key not in attendance_map:
                attendance_map[key] = []
            attendance_map[key].append(time_part)
            
        # 3. جلب العطلات الرسمية
        cursor.execute("SELECT date, name FROM official_holidays WHERE date BETWEEN ? AND ?", (start_date_str, end_date_str))
        holidays_map = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 4. جلب الإجازات المعتمدة
        cursor.execute('''
            SELECT lr.employee_id, lr.start_date, lr.end_date, lt.name, lr.reason
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.status = 'approved' 
            AND lr.start_date <= ? AND lr.end_date >= ?
        ''', (end_date_str, start_date_str))
        leaves_rows = cursor.fetchall()
        
        leaves_map = {}
        for row in leaves_rows:
            emp_id, s_d, e_d, l_name, l_reason = row
            try:
                s_dt = max(datetime.strptime(s_d, '%Y-%m-%d').date(), start_date)
                e_dt = min(datetime.strptime(e_d, '%Y-%m-%d').date(), end_date)
            except Exception:
                continue
            curr = s_dt
            while curr <= e_dt:
                date_str = curr.strftime('%Y-%m-%d')
                if (emp_id, date_str) not in leaves_map:
                    leaves_map[(emp_id, date_str)] = {'name': l_name, 'reason': l_reason}
                curr += timedelta(days=1)
                
        # 5. جلب الشفتات
        cursor.execute('''
            SELECT name, start_time, end_time, hours_per_day, description
            FROM shift_types
        ''')
        shifts_rows = cursor.fetchall()
        shifts_map = {}
        for row in shifts_rows:
            shifts_map[row[0]] = {
                'start': row[1],
                'end': row[2],
                'hours': row[3],
                'shift_type': row[0],
                'notes': row[4]
            }
            
        default_shift = {
            'start': '09:00', 
            'end': '17:00', 
            'hours': 8.0,
            'shift_type': 'عادي',
            'is_flexible': False
        }
        
        pass # conn.close() removed to prevent leak in Flask g
        
        days_diff = (end_date - start_date).days + 1
        
        monthly_data = {}
        summary = {
            'start_date': start_date_str,
            'end_date': end_date_str,
            'total_days': days_diff,
            'total_employees': len(employees),
            'total_present_days': 0,
            'total_absent_days': 0,
            'total_partial_days': 0,
            'employees_summary': {}
        }
        
        for i in range(days_diff):
            current_date = start_date + timedelta(days=i)
            target_date = current_date.strftime('%Y-%m-%d')
            day_of_week = current_date.isoweekday() % 7
            
            is_holiday = target_date in holidays_map
            holiday_name = holidays_map.get(target_date, "")
            
            daily_report = []
            
            for emp in employees:
                emp_id = emp[0]
                name = emp[1]
                emp_number = emp[2]
                department = emp[3]
                position = emp[4]
                weekly_leave_days_str = emp[5]
                shift_type = emp[6]
                default_start = emp[7]
                default_end = emp[8]
                
                is_weekly_off = False
                if weekly_leave_days_str:
                    try:
                        selected_days = [int(d.strip()) for d in weekly_leave_days_str.split(',') if d.strip().isdigit()]
                        if day_of_week in selected_days:
                            is_weekly_off = True
                    except Exception:
                        pass
                        
                punches = attendance_map.get((emp_id, target_date), [])
                if not punches:
                    punches = attendance_map.get((str(emp_id), target_date), [])
                if not punches and emp_number is not None:
                    punches = attendance_map.get((emp_number, target_date), [])
                if not punches and emp_number is not None:
                    punches = attendance_map.get((str(emp_number), target_date), [])
                    
                if punches:
                    checkin_time = punches[0]
                    checkout_time = punches[-1] if len(punches) > 1 else '00:00'
                    total_records = len(punches)
                else:
                    checkin_time = '00:00'
                    checkout_time = '00:00'
                    total_records = 0
                    
                base_shift = shifts_map.get(shift_type)
                if base_shift and base_shift.get('start') and base_shift.get('end'):
                    shift = base_shift.copy()
                elif default_start and default_end:
                    shift = {
                        'start': default_start,
                        'end': default_end,
                        'hours': 8.0,
                        'shift_type': shift_type or 'عادي',
                        'is_flexible': False
                    }
                else:
                    shift = default_shift.copy()
                    
                shift_name_lower = (shift['shift_type'] or '').lower().strip()
                if shift_name_lower in ['*', 'anytime', 'anything', 'any thing', 'حر', 'مرن'] or '*' in shift_name_lower or 'flexible' in shift_name_lower:
                    shift['is_flexible'] = True
                    
                work_hours = self.calculate_work_hours(checkin_time, checkout_time)
                
                if shift.get('is_flexible'):
                    if total_records > 0:
                        status = 'حاضر'
                        work_hours = 0.0
                    else:
                        leave_info = leaves_map.get((emp_id, target_date))
                        if leave_info:
                            status = f"إجازة: {leave_info['name']}"
                        elif is_holiday:
                            status = f"عطلة رسمية: {holiday_name}"
                        elif is_weekly_off:
                            status = 'إجازة أسبوعية'
                        else:
                            status = 'غائب'
                elif checkin_time != '00:00' and checkout_time != '00:00':
                    status = 'حاضر'
                elif checkin_time != '00:00':
                    status = 'دخول فقط'
                elif checkout_time != '00:00':
                    status = 'خروج فقط'
                else:
                    leave_info = leaves_map.get((emp_id, target_date))
                    if leave_info:
                        status = f"إجازة: {leave_info['name']}"
                    elif is_holiday:
                        status = f"عطلة رسمية: {holiday_name}"
                    elif is_weekly_off:
                        status = 'إجازة أسبوعية'
                    else:
                        status = 'غائب'
                        
                late_minutes = 0
                early_leave_minutes = 0
                if not shift.get('is_flexible'):
                    if checkin_time != '00:00' and shift.get('start'):
                        try:
                            s_start = datetime.strptime(shift['start'], "%H:%M")
                            a_start = datetime.strptime(checkin_time, "%H:%M")
                            if a_start > s_start:
                                late_minutes = int((a_start - s_start).seconds / 60)
                        except: pass
                    if checkout_time != '00:00' and shift.get('end'):
                        try:
                            s_end = datetime.strptime(shift['end'], "%H:%M")
                            a_end = datetime.strptime(checkout_time, "%H:%M")
                            if a_end < s_end:
                                early_leave_minutes = int((s_end - a_end).seconds / 60)
                        except: pass
                        
                if name not in summary['employees_summary']:
                    summary['employees_summary'][name] = {
                        'present_days': 0, 'absent_days': 0, 'partial_days': 0,
                        'total_work_hours': 0, 'total_late_minutes': 0, 'total_early_leave_minutes': 0
                    }
                emp_sum = summary['employees_summary'][name]
                
                if status == 'حاضر':
                    emp_sum['present_days'] += 1
                    summary['total_present_days'] += 1
                elif status == 'غائب':
                    emp_sum['absent_days'] += 1
                    summary['total_absent_days'] += 1
                elif 'عطلة رسمية' in status or 'إجازة' in status or 'إجازة أسبوعية' in status:
                    pass
                else:
                    emp_sum['partial_days'] += 1
                    summary['total_partial_days'] += 1
                    
                emp_sum['total_work_hours'] += work_hours
                emp_sum['total_late_minutes'] += late_minutes
                emp_sum['total_early_leave_minutes'] += early_leave_minutes
                
                daily_report.append({
                    'employee_id': emp_id,
                    'name': name,
                    'employee_number': emp_number,
                    'department': department,
                    'position': position,
                    'checkin_time': checkin_time,
                    'checkout_time': checkout_time,
                    'work_hours': work_hours,
                    'late_minutes': late_minutes,
                    'early_leave_minutes': early_leave_minutes,
                    'status': status,
                    'shift': shift
                })
                
            monthly_data[target_date] = daily_report
            
        return {
            'summary': summary,
            'daily_data': monthly_data
        }


    def get_monthly_presence_integrated_report(self, year: int = None, month: int = None) -> Dict:
        """
        تقرير شهري مدمج يظهر الحضور والانصراف + حالة بصمة التواجد
        نفس هيكل التقرير الشهري العادي، لكن يضيف مؤشر (Presence OK / Missing) لكل يوم
        """
        # 1. Get the base monthly report data (we reuse the logic to avoid code duplication)
        base_report = self.get_monthly_report(year, month)
        
        daily_data = base_report['daily_data']
        
        # 2. Iterate over the data and enrich it with Presence info
        # We need to re-fetch shift info if not fully captured or just process existing 'all_times'
        
        conn = self.get_connection()
        
        for date_str, employees_list in daily_data.items():
            for emp_record in employees_list:
                emp_id = emp_record['employee_id']
                
                # Default presence info
                emp_record['presence_info'] = {
                    'required': False,
                    'status': 'none', # none, ok, missing
                    'time': None
                }
                
                # Check shift for presence window
                # We need to fetch it again or pass it through. 
                # get_daily_report -> get_employee_shift_from_employee_data already calls fetching shift.
                # However, the current get_daily_report implementation in this file DOES fetch shift data.
                # But looking at get_daily_report implementation (implied), it calls self.get_employee_shift_from_employee_data(emp_id)
                # and puts SOME fields in the report. Let's see if it puts presence start/end.
                # Looking at valid code: It does NOT put presence_start/end in the final list dict (lines 383+).
                # So we must fetch shift data again or modify convert get_daily_report to include it.
                # To be safe and less invasive, let's fetch shift data here for the specific employee/day logic.
                
                # Optimization: get_employee_shift_from_employee_data is cached or fast enough? SQL query.
                shift = self.get_employee_shift_from_employee_data(emp_id)
                
                p_start = shift.get('presence_start_time')
                p_end = shift.get('presence_end_time')
                
                if p_start and p_end:
                    emp_record['presence_info']['required'] = True
                    
                    # Check punches
                    all_times = emp_record.get('all_times', [])
                    
                    found_presence = False
                    presence_punch = None
                    
                    # Parse window
                    try:
                        ps_dt = datetime.strptime(p_start, "%H:%M").time()
                        pe_dt = datetime.strptime(p_end, "%H:%M").time()
                        
                        for t_str in all_times:
                            t = datetime.strptime(t_str, "%H:%M").time()
                            # Check if t is between start and end
                            if ps_dt <= t <= pe_dt:
                                found_presence = True
                                presence_punch = t_str
                                break
                    except:
                        pass
                        
                    if found_presence:
                        emp_record['presence_info']['status'] = 'ok'
                        emp_record['presence_info']['time'] = presence_punch
                    else:
                        # If the employee is ABSENT entirely, is presence missing?
                        # Usually yes, but depends on how we want to show it.
                        # If status is 'absent', presence is logically failure too.
                        # If status is 'present' but no punch in window -> RED FLAG.
                        if emp_record['status'] == 'حاضر' or emp_record['status'] == 'دخول فقط' or emp_record['status'] == 'خروج فقط':
                             emp_record['presence_info']['status'] = 'missing'
                        else:
                             # If absent, strictly speaking they missed it, but maybe we don't flag it as a separate "Presence Violation" unique icon
                             # just keep it as Absense.
                             emp_record['presence_info']['status'] = 'absent_missing'

        pass # conn.close() removed to prevent leak in Flask g
        return base_report
    
    def get_range_report(self, start_date: str, end_date: str, name_query: str = None, department: str = None) -> Dict:
        """تقرير ذكي لفترة زمنية (من تاريخ إلى تاريخ) لجميع الموظفين.
        - يعتمد على الوقت فقط: أول بصمة = دخول، آخر بصمة = خروج
        - يحسب التأخير عند بداية الشفت والانصراف المتأخر بعد نهاية الشفت
        - يجمع ملخصات لكل موظف عبر الفترة
        """
        # التحقق من التواريخ
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        except Exception:
            raise ValueError("تنسيق التاريخ غير صحيح. استخدم YYYY-MM-DD")
        if end_dt < start_dt:
            raise ValueError("تاريخ النهاية يجب أن يكون بعد أو يساوي تاريخ البداية")

        grace_cfg = self.get_grace_minutes()

        # جلب جميع الموظفين
        conn = self.get_connection()
        cursor = conn.cursor()
        # بناء استعلام الموظفين ديناميكياً (بحث بالاسم/الرقم + فلتر القسم)
        base_query = """
            SELECT id, name, employee_number, department, position
            FROM employees
            WHERE (hire_date IS NULL OR hire_date <= ?)
            AND (end_of_service_date IS NULL OR end_of_service_date >= ?)
        """
        params = [end_date, start_date]
        if name_query and name_query.strip():
            like_q = f"%{name_query.strip()}%"
            base_query += " AND (name LIKE ? OR employee_number LIKE ?)"
            params.extend([like_q, like_q])
        if department and department.strip():
            base_query += " AND department = ?"
            params.append(department.strip())
        base_query += " ORDER BY CAST(employee_number AS INTEGER), name"
        cursor.execute(base_query, params)
        employees = cursor.fetchall()
        pass # conn.close() removed to prevent leak in Flask g

        # تحضير النتائج
        rows: List[Dict] = []
        per_employee_summary: Dict[int, Dict] = {}

        # جلب الإجازات الرسمية للفترة
        # Re-open connection for holidays
        conn_h = self.get_connection()
        hol_cursor = conn_h.cursor()
        hol_cursor.execute("SELECT date, name FROM official_holidays WHERE date BETWEEN ? AND ?", (start_date, end_date))
        holidays_map = {row[0]: row[1] for row in hol_cursor.fetchall()}
        
        # جلب الإجازات المعتمدة للفترة للفترة
        # لتحسين الأداء سنجلب كل الإجازات في النطاق ونبني map
        # Dictionary structure: date_str -> {employee_id: leave_type_name}
        # Use conn_h cursor for leaves as well
        cursor = conn_h.cursor()
        cursor.execute('''
            SELECT lr.employee_id, lt.name, lr.start_date, lr.end_date
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.status = 'approved' 
            AND (lr.start_date <= ? AND lr.end_date >= ?)
        ''', (end_date, start_date))
        range_leaves_rows = cursor.fetchall()
        
        # Build optimized lookup: date -> emp_id -> type
        # Since range can be large, we iterate days or intervals. 
        # Given function iterates days, let's allow O(1) lookup per day/emp
        from collections import defaultdict
        leaves_calendar = defaultdict(dict)
        
        for r in range_leaves_rows:
            eid, l_name, s_d, e_d = r
            # Expand this leave into the dictionary for relevant days
            # Intersect [s_d, e_d] with [start_date, end_date]
            l_start = max(datetime.strptime(s_d, "%Y-%m-%d").date(), start_dt)
            l_end = min(datetime.strptime(e_d, "%Y-%m-%d").date(), end_dt)
            
            curr = l_start
            while curr <= l_end:
                 leaves_calendar[curr.strftime("%Y-%m-%d")][eid] = l_name
                 curr += timedelta(days=1)

        conn_h.close()

        # اجتياز الأيام في النطاق
        current = start_dt
        while current <= end_dt:
            current_str = current.strftime("%Y-%m-%d")
            for emp in employees:
                emp_id, name, emp_number, department, position = emp

                # بصمات اليوم
                attendance = self.get_daily_checkin_checkout_by_time(emp_id, current_str)

                # بيانات الشفت
                shift = self.get_employee_shift_from_employee_data(emp_id)

                # ساعات العمل
                work_hours = self.calculate_work_hours(
                    attendance['checkin_time'], attendance['checkout_time']
                )

                # الحالة
                if shift.get('is_flexible'):
                    if attendance['total_records'] > 0:
                        status = 'حاضر'
                        work_hours = 0.0 # حسب طلب المستخدم "من غير ساعات"
                    else:
                        day_leaves = leaves_calendar.get(current_str, {})
                        if emp_id in day_leaves:
                            status = f"إجازة: {day_leaves[emp_id]}"
                        elif current_str in holidays_map:
                            status = f"عطلة رسمية: {holidays_map[current_str]}"
                        else:
                            status = 'غائب'
                elif attendance['checkin_time'] != '00:00' and attendance['checkout_time'] != '00:00':
                    status = 'حاضر'
                elif attendance['checkin_time'] != '00:00':
                    status = 'دخول فقط'
                elif attendance['checkout_time'] != '00:00':
                    status = 'خروج فقط'
                else:
                    # Check leave first
                    day_leaves = leaves_calendar.get(current_str, {})
                    if emp_id in day_leaves:
                        status = f"إجازة: {day_leaves[emp_id]}"
                    elif current_str in holidays_map:
                        status = f"عطلة رسمية: {holidays_map[current_str]}"
                    else:
                        status = 'غائب'

                # التأخير عند البداية + وصول مبكر
                late_minutes = 0
                early_arrival_minutes = 0
                if not shift.get('is_flexible'):
                    try:
                        if attendance['checkin_time'] != '00:00' and shift['start']:
                            scheduled_start = datetime.strptime(shift['start'], "%H:%M")
                            actual_start = datetime.strptime(attendance['checkin_time'], "%H:%M")
                            if actual_start > scheduled_start:
                                late_minutes = int((actual_start - scheduled_start).seconds / 60)
                            elif actual_start < scheduled_start:
                                early_arrival_minutes = int((scheduled_start - actual_start).seconds / 60)
                    except Exception:
                        late_minutes = 0
                        early_arrival_minutes = 0

                # الانصراف المتأخر بعد نهاية الشفت (كم دقيقة بعد نهاية الشفت) + انصراف مبكر
                late_departure_minutes = 0
                early_leave_minutes = 0
                try:
                    if attendance['checkout_time'] != '00:00' and shift['end']:
                        scheduled_end = datetime.strptime(shift['end'], "%H:%M")
                        actual_end = datetime.strptime(attendance['checkout_time'], "%H:%M")
                        # في الشفتات العابرة لمنتصف الليل، إذا كان الخروج في اليوم التالي فزمن actual_end < scheduled_end
                        # نضيف 24 ساعة لحساب الفرق بشكل صحيح
                        if shift.get('cross_midnight') and actual_end < scheduled_end:
                            actual_end = actual_end.replace(day=actual_end.day + 1)
                        if actual_end > scheduled_end:
                            late_departure_minutes = int((actual_end - scheduled_end).seconds / 60)
                        elif actual_end < scheduled_end:
                            # انصراف مبكر
                            # إذا كان الشفت يعبر منتصف الليل وكان actual_end (الساعة الحقيقية) أكبر من scheduled_end قبل التعديل أعلاه
                            # فهذا يعني أنه ليس انصراف مبكر في سياق الشفت الليلي
                            if not (shift.get('cross_midnight') and actual_end > scheduled_end):
                                early_leave_minutes = int((scheduled_end - actual_end).seconds / 60)
                except Exception:
                    late_departure_minutes = 0
                    early_leave_minutes = 0

                # تطبيق فترات السماح بطرح القيمة
                raw_late_minutes = late_minutes
                if late_minutes > 0:
                    late_minutes = max(0, late_minutes - grace_cfg['late_arrival'])
                if late_departure_minutes > 0:
                    late_departure_minutes = max(0, late_departure_minutes - grace_cfg['late_departure'])

                rows.append({
                    'employee_id': emp_id,
                    'name': name,
                    'employee_number': emp_number,
                    'department': department,
                    'position': position,
                    'date': current_str,
                    'checkin_time': attendance['checkin_time'],
                    'checkout_time': attendance['checkout_time'],
                    'scheduled_start': shift['start'],
                    'scheduled_end': shift['end'],
                    'work_hours': work_hours,
                    'status': status,
                    'late_minutes': late_minutes,
                    'raw_late_minutes': raw_late_minutes,
                    'grace_minutes': grace_cfg['late_arrival'],
                    'early_arrival_minutes': early_arrival_minutes,
                    'late_departure_minutes': late_departure_minutes,
                    'early_leave_minutes': early_leave_minutes,
                    'total_records': attendance['total_records'],
                    'all_times': attendance['all_times'],
                    'shift_type_name': shift['shift_type']
                })

                # تجميع الملخص لكل موظف
                if emp_id not in per_employee_summary:
                    per_employee_summary[emp_id] = {
                        'employee_id': emp_id,
                        'name': name,
                        'employee_number': emp_number,
                        'department': department,
                        'present_days': 0,
                        'absent_days': 0,
                        'partial_days': 0,
                        'total_work_hours': 0.0,
                        'total_late_minutes': 0,
                        'total_late_departure_minutes': 0,
                    }

                s = per_employee_summary[emp_id]
                if status == 'حاضر':
                    s['present_days'] += 1
                elif status == 'غائب':
                    s['absent_days'] += 1
                elif 'عطلة رسمية' in status:
                    pass # Don't count as absent
                else:
                    s['partial_days'] += 1
                s['total_work_hours'] += work_hours
                s['total_late_minutes'] += late_minutes
                s['total_late_departure_minutes'] += late_departure_minutes

            current += timedelta(days=1)

        # ملخص عام
        overall = {
            'employees_count': len(per_employee_summary),
            'days_count': (end_dt - start_dt).days + 1,
            'present_days': sum(v['present_days'] for v in per_employee_summary.values()),
            'absent_days': sum(v['absent_days'] for v in per_employee_summary.values()),
            'partial_days': sum(v['partial_days'] for v in per_employee_summary.values()),
            'total_work_hours': round(sum(v['total_work_hours'] for v in per_employee_summary.values()), 1),
            'total_late_minutes': sum(v['total_late_minutes'] for v in per_employee_summary.values()),
            'total_late_departure_minutes': sum(v['total_late_departure_minutes'] for v in per_employee_summary.values()),
        }
        
        # إنشاء ملخص أسبوعي (1-7, 8-14, 15-21, 22-آخر يوم)
        # تحديد آخر يوم في الشهر لنطاق التقارير (اعتماداً على start_date)
        month_last_day = calendar.monthrange(start_dt.year, start_dt.month)[1]
        week_labels = [
            '1-7',
            '8-14',
            '15-21',
            f"22-{month_last_day}"
        ]

        # تجهيز هيكل للموظفين
        from collections import defaultdict
        weekly_marks = defaultdict(lambda: {0: [], 1: [], 2: [], 3: []})
        weekly_details = defaultdict(lambda: {0: [], 1: [], 2: [], 3: []})

        for r in rows:
            try:
                day = int(r['date'].split('-')[-1])
            except Exception:
                continue
            # تحديد الأسبوع 0..3
            week_idx = min((day - 1) // 7, 3)
            # علامة الحضور: ✅ للحاضر/جزئي، ❌ للغائب
            mark = '✅' if r['status'] != 'غائب' else '❌'
            weekly_marks[r['employee_id']][week_idx].append(mark)
            # بناء نص تفصيلي لليوم بصيغة مجموعتين:
            # 1) تأخير/وصول مبكر (د)
            # 2) انصراف مبكر/متأخر (د)
            late_val = r.get('late_minutes', 0) or 0
            early_arrival_val = r.get('early_arrival_minutes', 0) or 0
            if late_val > 0:
                first_group = f"تأخير {late_val}د"
            elif early_arrival_val > 0:
                first_group = f"وصول مبكر {early_arrival_val}د"
            else:
                first_group = "تأخير 0د"

            early_leave_val = r.get('early_leave_minutes', 0) or 0
            late_depart_val = r.get('late_departure_minutes', 0) or 0
            second_parts = []
            if early_leave_val > 0:
                second_parts.append(f"انصراف مبكر {early_leave_val}د")
            if late_depart_val > 0:
                second_parts.append(f"انصراف متأخر {late_depart_val}د")
            second_group = ' / '.join(second_parts) if second_parts else 'انصراف 0د'

            weekly_details[r['employee_id']][week_idx].append(
                f"اليوم {day}: {first_group}، {second_group}"
            )

        # تحويل إلى قائمة مرتبة حسب الاسم
        def _sort_emp(x):
            try:
                return int(x.get('employee_number', 0))
            except (ValueError, TypeError):
                return 999999
        employees_summary_list = sorted(per_employee_summary.values(), key=_sort_emp)
        weekly_summaries = []
        for emp in employees_summary_list:
            emp_id = emp['employee_id']
            marks_joined = [
                ''.join(weekly_marks[emp_id][0]) or '-',
                ''.join(weekly_marks[emp_id][1]) or '-',
                ''.join(weekly_marks[emp_id][2]) or '-',
                ''.join(weekly_marks[emp_id][3]) or '-',
            ]
            weekly_summaries.append({
                'employee_id': emp_id,
                'name': emp['name'],
                'employee_number': emp['employee_number'],
                'department': emp['department'],
                'weeks': marks_joined,
                'details': [
                    weekly_details[emp_id][0],
                ]
            })
        
        return {
            'rows': rows,
            'summary': overall,
            'employees_summary': employees_summary_list,
            'weekly_summaries': weekly_summaries,
            'week_labels': week_labels,
            'start_date': start_date,
            'end_date': end_date
        }

    def get_presence_daily_report(self, target_date: str = None) -> List[Dict]:
        """تقرير بصمة التواجد اليومي"""
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")
            
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Get employees
        cursor.execute("SELECT id, name, employee_number, department, position FROM employees WHERE (hire_date IS NULL OR hire_date <= ?) AND (end_of_service_date IS NULL OR end_of_service_date >= ?) ORDER BY CAST(employee_number AS INTEGER), name", (target_date, target_date))
        employees = cursor.fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        report = []
        
        for emp in employees:
            emp_id, name, emp_number, department, position = emp
            
            # Get Shift Info including Presence Window
            shift = self.get_employee_shift_from_employee_data(emp_id)
            
            presence_start = shift.get('presence_start')
            presence_end = shift.get('presence_end')
            
            # Get punches
            attendance = self.get_daily_checkin_checkout_by_time(emp_id, target_date)
            all_times = attendance['all_times']
            
            status = "غير مطلوب"
            presence_time = "-"
            
            if presence_start and presence_end:
                status = "غائب" # Default if required
                
                # Check punches
                # Need to handle strings vs time objects comparison
                p_start = presence_start
                p_end = presence_end
                
                # Simple string comparison works for HH:MM if format is consistent
                # But better to be safe
                for punch in all_times:
                    # punch is HH:MM string from previous function
                    if p_start <= punch <= p_end:
                        status = "تواجد"
                        presence_time = punch
                        break
            
            report.append({
                'id': emp_id,
                'name': name,
                'employee_number': emp_number,
                'department': department,
                'shift_name': shift.get('shift_type'),
                'presence_start': presence_start or '-',
                'presence_end': presence_end or '-',
                'presence_time': presence_time,
                'status': status
            })
            
        return report
    
    def print_daily_report(self, target_date: str = None):
        """طباعة التقرير اليومي"""
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")
        
        report = self.get_daily_report(target_date)
        
        print(f"\n📋 تقرير الحضور اليومي - {target_date}")
        print("=" * 100)
        print(f"{'#':<3} {'الموظف':<20} {'القسم':<15} {'دخول':<8} {'خروج':<8} {'ساعات':<8} {'تأخير':<8} {'الحالة':<12}")
        print("-" * 100)
        
        for i, record in enumerate(report, 1):
            late_str = f"{record['late_minutes']}د" if record['late_minutes'] > 0 else "-"
            print(f"{i:<3} {record['name'][:19]:<20} {record['department'][:14]:<15} {record['checkin_time']:<8} {record['checkout_time']:<8} {record['work_hours']:<8} {late_str:<8} {record['status']:<12}")
        
        # إحصائيات عامة
        total_employees = len(report)
        present = len([r for r in report if r['status'] == 'حاضر'])
        absent = len([r for r in report if r['status'] == 'غائب'])
        partial = total_employees - present - absent
        
        print("\n📊 ملخص:")
        print(f"   👥 إجمالي الموظفين: {total_employees}")
        print(f"   ✅ حاضر: {present} ({present/total_employees*100:.1f}%)")
        print(f"   ❌ غائب: {absent} ({absent/total_employees*100:.1f}%)")
        print(f"   ⚠️ حضور جزئي: {partial} ({partial/total_employees*100:.1f}%)")
    
    def print_monthly_summary(self, year: int = None, month: int = None):
        """طباعة ملخص التقرير الشهري"""
        monthly_report = self.get_monthly_report(year, month)
        summary = monthly_report['summary']
        
        print(f"\n📅 تقرير شهر {summary['month_name']} {summary['year']}")
        print("=" * 80)
        print(f"📆 عدد أيام الشهر: {summary['total_days']}")
        print(f"👥 عدد الموظفين: {summary['total_employees']}")
        print(f"✅ إجمالي أيام الحضور: {summary['total_present_days']}")
        print(f"❌ إجمالي أيام الغياب: {summary['total_absent_days']}")
        print(f"⚠️ إجمالي أيام الحضور الجزئي: {summary['total_partial_days']}")
        
        print(f"\n👥 تفاصيل الموظفين (أفضل 10):")
        print(f"{'الموظف':<20} {'حاضر':<8} {'غائب':<8} {'جزئي':<8} {'ساعات':<10} {'تأخير':<10}")
        print("-" * 75)
        
        # ترتيب الموظفين حسب أيام الحضور
        sorted_employees = sorted(
            summary['employees_summary'].items(),
            key=lambda x: x[1]['present_days'],
            reverse=True
        )[:10]
        
        for name, data in sorted_employees:
            late_hours = round(data['total_late_minutes'] / 60, 1)
            work_hours = round(data['total_work_hours'], 1)
            print(f"{name[:19]:<20} {data['present_days']:<8} {data['absent_days']:<8} {data['partial_days']:<8} {work_hours:<10} {late_hours:<10}")

# اختبار النظام
def main():
    print("🚀 تقارير بصمة ذكية (بناءً على الوقت)")
    print("=" * 50)
    
    reporter = SmartFingerprintReport()
    
    # تقرير يومي
    today = date.today().strftime("%Y-%m-%d")
    reporter.print_daily_report(today)
    
    # تقرير شهري (ملخص)
    reporter.print_monthly_summary()

if __name__ == "__main__":
    main()
