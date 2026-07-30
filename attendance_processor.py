#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
نظام معالجة الحضور المحسن
=======================
يحول البيانات الخام من البصمة إلى تقارير واضحة
مع التحكم الصحيح في check_type
"""

import sqlite3
from datetime import datetime, date
from typing import List, Dict, Optional

from utils.db import DB_PATH

# ================== إعداد قاعدة البيانات ==================
class AttendanceProcessor:
    def __init__(self, db_path=None):
        self.db_path = db_path if db_path else DB_PATH
        self.setup_new_tables()
    
    def get_connection(self):
        """الحصول على اتصال قاعدة البيانات"""
        return sqlite3.connect(self.db_path)
    
    def setup_new_tables(self):
        """إنشاء الجداول الجديدة للنظام المحسن"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # جدول الحضور الخام (مثل CHECKINOUT)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                USERID INTEGER NOT NULL,
                CHECKTIME TEXT NOT NULL,
                CHECKTYPE INTEGER NOT NULL,
                device_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(USERID, CHECKTIME, CHECKTYPE)
            )
        """)
        
        # جدول الشفتات (مثل pay_emp_shifts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pay_emp_shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                USERID INTEGER NOT NULL,
                EMP_NAME TEXT NOT NULL,
                DEPT_ID INTEGER,
                DEPT_NAME TEXT,
                JOB_ID INTEGER,
                JOB_NAME TEXT,
                SHIFT_NAME TEXT DEFAULT 'عادي',
                START1 TEXT DEFAULT '09:00',
                END1 TEXT DEFAULT '17:00',
                HOURS_DAY REAL DEFAULT 8.0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(USERID)
            )
        """)
        
        # جدول الحضور المعالج (مثل pay_emp_att)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pay_emp_att (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                DT TEXT NOT NULL,
                DES TEXT,
                USERID INTEGER NOT NULL,
                EMP_NAME TEXT,
                DEPT_ID INTEGER,
                DEPT_NAME TEXT,
                JOB_ID INTEGER,
                JOB_NAME TEXT,
                SHIFT_NAME TEXT,
                START1 TEXT,
                END1 TEXT,
                HOURS_DAY REAL,
                CHIN TEXT DEFAULT '00:00',
                A_H TEXT DEFAULT '00',
                A_M TEXT DEFAULT '00',
                CHOUT TEXT DEFAULT '00:00',
                L_H TEXT DEFAULT '00',
                L_M TEXT DEFAULT '00',
                WORK_HOURS REAL DEFAULT 0,
                STATUS TEXT DEFAULT 'غائب',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(DT, USERID)
            )
        """)
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        print("✅ تم إنشاء جداول النظام المحسن")
    
    def get_employee_shift(self, emp_id: int) -> Dict:
        """الحصول على معلومات شفت الموظف"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT START1, END1, SHIFT_NAME, HOURS_DAY
            FROM pay_emp_shifts
            WHERE USERID = ?
        """, (emp_id,))
        
        row = cursor.fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        if row:
            return {
                'start': row[0] or "09:00",
                'end': row[1] or "17:00", 
                'name': row[2] or "عادي",
                'hours': row[3] or 8.0
            }
        return {'start': "09:00", 'end': "17:00", 'name': "عادي", 'hours': 8.0}
    
    def calculate_shift_midpoint(self, start_time: str, end_time: str) -> str:
        """حساب منتصف الشفت لتحديد الدخول من الخروج"""
        try:
            start = datetime.strptime(start_time, "%H:%M")
            end = datetime.strptime(end_time, "%H:%M")
            
            # إذا كان الخروج في اليوم التالي
            if end < start:
                end = end.replace(day=end.day + 1)
            
            # حساب منتصف الوقت
            midpoint = start + (end - start) / 2
            return midpoint.strftime("%H:%M")
        except:
            return "12:00"  # افتراضي
    
    def smart_checkin_checkout(self, emp_id: int, dt: str) -> Dict:
        """نظام ذكي لتحديد الدخول والخروج بناءً على وقت الشفت"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # الحصول على معلومات الشفت
        shift = self.get_employee_shift(emp_id)
        midpoint = self.calculate_shift_midpoint(shift['start'], shift['end'])
        
        # الحصول على جميع البصمات لهذا اليوم
        cursor.execute("""
            SELECT CHECKTIME, CHECKTYPE
            FROM raw_attendance
            WHERE USERID = ? AND DATE(CHECKTIME) = ?
            ORDER BY CHECKTIME
        """, (emp_id, dt))
        
        records = cursor.fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        checkin = "00:00"
        checkout = "00:00"
        
        for record in records:
            check_time = record[0]
            check_type = record[1]
            
            try:
                time_obj = datetime.fromisoformat(check_time)
                time_str = time_obj.strftime("%H:%M")
                
                # تحديد الدخول والخروج بناءً على الوقت
                if time_str < midpoint:
                    # قبل منتصف الشفت = دخول
                    if checkin == "00:00" or time_str < checkin:
                        checkin = time_str
                else:
                    # بعد منتصف الشفت = خروج
                    if checkout == "00:00" or time_str > checkout:
                        checkout = time_str
                        
            except:
                continue
        
        return {
            'checkin': checkin,
            'checkout': checkout,
            'shift': shift,
            'midpoint': midpoint
        }
    
    def checkin(self, emp_id: int, dt: str) -> str:
        """الدخول الذكي بناءً على وقت الشفت"""
        result = self.smart_checkin_checkout(emp_id, dt)
        return result['checkin']
    
    def checkout(self, emp_id: int, dt: str) -> str:
        """الخروج الذكي بناءً على وقت الشفت"""
        result = self.smart_checkin_checkout(emp_id, dt)
        return result['checkout']
    
    def calculate_work_hours(self, checkin: str, checkout: str) -> float:
        """حساب ساعات العمل"""
        if checkin == "00:00" or checkout == "00:00":
            return 0.0
        
        try:
            checkin_time = datetime.strptime(checkin, "%H:%M")
            checkout_time = datetime.strptime(checkout, "%H:%M")
            
            # إذا كان الخروج في اليوم التالي
            if checkout_time < checkin_time:
                checkout_time = checkout_time.replace(day=checkout_time.day + 1)
            
            duration = checkout_time - checkin_time
            return round(duration.seconds / 3600, 2)
        except:
            return 0.0
    
    def migrate_existing_data(self):
        """نقل البيانات الموجودة إلى النظام الجديد"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        print("🔄 نقل البيانات من attendance_records إلى raw_attendance...")
        
        # نقل سجلات الحضور
        cursor.execute("""
            INSERT OR IGNORE INTO raw_attendance (USERID, CHECKTIME, CHECKTYPE, device_id)
            SELECT employee_id, check_time, check_type, device_id
            FROM attendance_records
        """)
        
        # إنشاء شفتات للموظفين الموجودين
        cursor.execute("""
            INSERT OR IGNORE INTO pay_emp_shifts (USERID, EMP_NAME, DEPT_ID, DEPT_NAME)
            SELECT id, name, 1, department
            FROM employees
        """)
        
        conn.commit()
        
        # عرض إحصائيات
        cursor.execute("SELECT COUNT(*) FROM raw_attendance")
        raw_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM pay_emp_shifts")
        shifts_count = cursor.fetchone()[0]
        
        pass # conn.close() removed to prevent leak in Flask g
        
        print(f"✅ تم نقل {raw_count} سجل حضور و {shifts_count} موظف")
    
    def setup_sample_shifts(self):
        """إعداد شفتات تجريبية للاختبار"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        print("⚙️ إعداد شفتات تجريبية...")
        
        # أمثلة على أنواع مختلفة من الشفتات
        sample_shifts = [
            # شفت صباحي (8 صباحاً - 4 مساءً)
            ("08:00", "16:00", "صباحي", 8.0),
            # شفت مسائي (4 مساءً - 12 منتصف الليل) 
            ("16:00", "00:00", "مسائي", 8.0),
            # شفت ليلي (12 منتصف الليل - 8 صباحاً)
            ("00:00", "08:00", "ليلي", 8.0),
            # شفت طويل (9 صباحاً - 6 مساءً)
            ("09:00", "18:00", "طويل", 9.0),
        ]
        
        # تحديث شفتات عشوائية للموظفين
        cursor.execute("SELECT USERID FROM pay_emp_shifts LIMIT 20")
        employees = cursor.fetchall()
        
        import random
        
        for i, (emp_id,) in enumerate(employees):
            shift = sample_shifts[i % len(sample_shifts)]
            start, end, name, hours = shift
            
            cursor.execute("""
                UPDATE pay_emp_shifts 
                SET START1 = ?, END1 = ?, SHIFT_NAME = ?, HOURS_DAY = ?
                WHERE USERID = ?
            """, (start, end, name, hours, emp_id))
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        print(f"✅ تم إعداد {len(employees)} شفت تجريبي")
    
    def fip_sync(self, target_date: str = None):
        """معالجة بيانات الحضور لليوم المحدد (مكافئ FIP_SYNC)"""
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # حذف البيانات القديمة لنفس اليوم
        cursor.execute("DELETE FROM pay_emp_att WHERE DT = ?", (target_date,))
        
        print(f"🔄 معالجة بيانات الحضور لتاريخ: {target_date}")
        
        # الحصول على جميع الموظفين
        cursor.execute("SELECT * FROM pay_emp_shifts")
        employees = cursor.fetchall()
        
        processed_count = 0
        
        for emp in employees:
            (id, user_id, emp_name, dept_id, dept_name, job_id, job_name,
             shift_name, start1, end1, hours_day, created_at) = emp
            
            # النظام الذكي للحضور
            smart_result = self.smart_checkin_checkout(user_id, target_date)
            chin = smart_result['checkin']
            chout = smart_result['checkout']
            shift_info = smart_result['shift']
            midpoint = smart_result['midpoint']
            
            # تقسيم الوقت إلى ساعات ودقائق
            a_h, a_m = chin.split(":") if chin != "00:00" else ("00", "00")
            l_h, l_m = chout.split(":") if chout != "00:00" else ("00", "00")
            
            # حساب ساعات العمل
            work_hours = self.calculate_work_hours(chin, chout)
            
            # تحديد الحالة بطريقة أكثر ذكاءً
            if chin != "00:00" and chout != "00:00":
                status = "حاضر"
            elif chin != "00:00":
                status = "دخول فقط"
            elif chout != "00:00":
                status = "خروج فقط"
            else:
                status = "غائب"
            
            # اسم اليوم
            day_name = datetime.strptime(target_date, "%Y-%m-%d").strftime("%A")
            
            # إدراج البيانات
            cursor.execute("""
                INSERT INTO pay_emp_att
                (DT, DES, USERID, EMP_NAME, DEPT_ID, DEPT_NAME, JOB_ID, JOB_NAME,
                 SHIFT_NAME, START1, END1, HOURS_DAY, CHIN, A_H, A_M, CHOUT, L_H, L_M,
                 WORK_HOURS, STATUS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                target_date, day_name, user_id, emp_name, dept_id, dept_name,
                job_id, job_name, shift_name, start1, end1, hours_day,
                chin, a_h, a_m, chout, l_h, l_m, work_hours, status
            ))
            
            processed_count += 1
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        print(f"✅ تم معالجة {processed_count} موظف لتاريخ {target_date}")
    
    def get_daily_report(self, target_date: str = None) -> List[Dict]:
        """الحصول على تقرير يومي"""
        if not target_date:
            target_date = date.today().strftime("%Y-%m-%d")
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM pay_emp_att
            WHERE DT = ?
            ORDER BY EMP_NAME
        """, (target_date,))
        
        results = cursor.fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        columns = [
            'id', 'DT', 'DES', 'USERID', 'EMP_NAME', 'DEPT_ID', 'DEPT_NAME',
            'JOB_ID', 'JOB_NAME', 'SHIFT_NAME', 'START1', 'END1', 'HOURS_DAY',
            'CHIN', 'A_H', 'A_M', 'CHOUT', 'L_H', 'L_M', 'WORK_HOURS', 'STATUS', 'created_at'
        ]
        
        return [dict(zip(columns, row)) for row in results]
    
    def show_checktype_statistics(self):
        """عرض إحصائيات check_type"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        print("\n📊 إحصائيات CHECKTYPE:")
        cursor.execute("""
            SELECT CHECKTYPE, COUNT(*) as count
            FROM raw_attendance
            GROUP BY CHECKTYPE
            ORDER BY CHECKTYPE
        """)
        
        results = cursor.fetchall()
        
        type_names = {
            0: "خروج (Check-Out)",
            1: "دخول (Check-In)", 
            2: "بداية راحة (Break-Out)",
            3: "نهاية راحة (Break-In)",
            4: "عمل إضافي (Overtime-In)",
            5: "انتهاء عمل إضافي (Overtime-Out)",
            255: "غير معرف (Unknown)"
        }
        
        for checktype, count in results:
            type_name = type_names.get(checktype, f"نوع غير معروف ({checktype})")
            print(f"   {checktype}: {type_name} - {count} سجل")
        
        pass # conn.close() removed to prevent leak in Flask g

# ================== اختبار النظام ==================
def main():
    print("🚀 نظام معالجة الحضور المحسن")
    print("=" * 40)
    
    processor = AttendanceProcessor()
    
    # نقل البيانات الموجودة
    processor.migrate_existing_data()
    
    # إعداد شفتات تجريبية
    processor.setup_sample_shifts()
    
    # عرض إحصائيات
    processor.show_checktype_statistics()
    
    # معالجة بيانات اليوم
    today = date.today().strftime("%Y-%m-%d")
    processor.fip_sync(today)
    
    # عرض التقرير
    print(f"\n📋 تقرير الحضور لتاريخ {today}:")
    report = processor.get_daily_report(today)
    
    if report:
        print(f"{'الموظف':<20} {'الشفت':<10} {'الدخول':<8} {'الخروج':<8} {'ساعات العمل':<12} {'الحالة':<12}")
        print("-" * 82)
        
        for record in report:
            print(f"{record['EMP_NAME']:<20} {record['SHIFT_NAME']:<10} {record['CHIN']:<8} {record['CHOUT']:<8} {record['WORK_HOURS']:<12} {record['STATUS']:<12}")
            
        # إحصائيات سريعة
        total_present = len([r for r in report if r['STATUS'] == 'حاضر'])
        total_absent = len([r for r in report if r['STATUS'] == 'غائب'])
        total_partial = len([r for r in report if 'فقط' in r['STATUS']])
        
        print("\n📊 ملخص:")
        print(f"   👥 إجمالي الموظفين: {len(report)}")
        print(f"   ✅ حاضر: {total_present}")
        print(f"   ❌ غائب: {total_absent}")
        print(f"   ⚠️ حضور جزئي: {total_partial}")
    else:
        print("لا توجد بيانات لهذا التاريخ")

if __name__ == "__main__":
    main()
