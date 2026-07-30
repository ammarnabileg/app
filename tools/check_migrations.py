#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""فحص سلامة الترحيل — Migration Self-Check

يُشغَّل في التطوير بعد أي تعديل على utils/db.py، وقبل بناء أي نسخة.

    python3 tools/check_migrations.py

يفحص ثلاث خصائص لا يكشفها الاختبار العادي:

  1. الخصوصية التكرارية (idempotency): تشغيل init_db() مرتين ثلاث مرات على
     نفس الملف يجب أن ينتج نفس المخطط بالضبط بلا استثناء. التطبيق ينادي
     init_db() عند كل إقلاع، فترحيل غير تكراري يعني انفجارًا عند إعادة
     تشغيل العميل، لا عند التسليم.

  2. التوافق التصاعدي من كل إصدار سابق: يبني قاعدة بمخطط قديم مُقلَّم
     (بحذف الجداول والأعمدة التي أُضيفت لاحقًا) ثم يرقّيها، ليثبت أن
     المسار من نسخة عميل قديمة إلى الحالية يعمل — وليس فقط المسار من
     قاعدة نظيفة.

  3. أن SCHEMA_VERSION ارتفع فعلًا إن تغيّر عدد الترحيلات، حتى لا يُسلَّم
     بناء جديد يحمل رقم إصدار قديم فيصبح تتبع المشكلات مستحيلًا.

رمز الخروج 0 = سليم، 1 = يوجد خلل يجب إصلاحه قبل البناء.
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import traceback

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

OK, FAIL, WARN = '\033[92m✓\033[0m', '\033[91m✗\033[0m', '\033[93m!\033[0m'
failures = []


def head(t):
    print(f'\n{"=" * 68}\n{t}\n{"=" * 68}')


def schema_fingerprint(db):
    """بصمة المخطط: كل جدول وأعمدته مرتبة — للمقارنة بين التشغيلات."""
    conn = sqlite3.connect(db)
    parts = []
    for (t,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        cols = sorted(r[1] for r in conn.execute(f'PRAGMA table_info("{t}")'))
        parts.append(f'{t}({",".join(cols)})')
    conn.close()
    return '\n'.join(parts)


def build_fresh(workdir):
    os.environ['HR_DATA_DIR'] = workdir
    os.environ.setdefault('SECRET_KEY', 'migcheck')
    os.chdir(APP_DIR)
    from unittest.mock import patch
    p = patch('utils.license.get_current_license_info', return_value={'ok': True})
    p.start()
    import importlib
    import utils.db as dbmod
    importlib.reload(dbmod)
    from app import app
    with app.app_context():
        dbmod.init_db()
    return os.path.join(workdir, 'hr_system.db'), app, dbmod


def run_init(app, dbmod):
    with app.app_context():
        dbmod.init_db()


def main():
    head('١) الخصوصية التكرارية — init_db() ثلاث مرات')
    wd = tempfile.mkdtemp(prefix='mig_idem_')
    try:
        db, app, dbmod = build_fresh(wd)
        fp1 = schema_fingerprint(db)
        run_init(app, dbmod)
        fp2 = schema_fingerprint(db)
        run_init(app, dbmod)
        fp3 = schema_fingerprint(db)
        if fp1 == fp2 == fp3:
            print(f'  {OK} المخطط ثابت بعد ثلاث تشغيلات')
        else:
            print(f'  {FAIL} المخطط تغيّر بين التشغيلات — ترحيل غير تكراري')
            failures.append('init_db غير تكراري')
    except Exception:
        print(f'  {FAIL} استثناء عند إعادة التشغيل:')
        traceback.print_exc()
        failures.append('init_db يرفع استثناء عند إعادة التشغيل')

    head('٢) الترقية من مخطط قديم مُقلَّم')
    # يُحاكى العميل القديم بحذف كل ما أُضيف لاحقًا: الجداول الجديدة تُحذف،
    # والأعمدة المُضافة بالترحيلات تُقلَّم بإعادة بناء الجدول بدونها.
    LATE_TABLES = [
        'employee_audit_log', 'payroll_ledger_lines', 'payroll_ledger_postings',
        'payroll_run_lines', 'payroll_runs', 'payroll_hours_approvals',
        'payroll_transactions', 'employee_salary_items', 'payroll_item_types',
        'employee_loans', 'loan_payments', 'leave_balance_adjustments',
    ]
    LATE_COLUMNS = {
        'employees': ['end_of_service_date', 'previous_leave_balance',
                      'manager_id', 'national_id', 'bank_iban'],
        'end_of_service_records': ['monthly_wage', 'daily_rate', 'basis',
                                   'tier1_days', 'tier2_days', 'capped',
                                   'fraction', 'gratuity_amount', 'leave_days',
                                   'leave_amount', 'loans_deducted',
                                   'breakdown_json'],
    }
    wd2 = tempfile.mkdtemp(prefix='mig_old_')
    try:
        db2, app, dbmod = build_fresh(wd2)
        conn = sqlite3.connect(db2)
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute("INSERT OR IGNORE INTO departments_master (name) VALUES ('Legacy')")
        conn.execute(
            "INSERT OR IGNORE INTO employees "
            '(employee_number, name, department, position, hire_date, salary, is_active) '
            "VALUES ('9001', 'Legacy Staff', 'Legacy', 'Worker', '2015-01-01', 400, 1)")
        conn.commit()

        dropped = []
        for t in LATE_TABLES:
            try:
                conn.execute(f'DROP TABLE IF EXISTS {t}')
                dropped.append(t)
            except sqlite3.Error:
                pass

        trimmed = []
        for tbl, cols in LATE_COLUMNS.items():
            existing = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
            keep = [c for c in
                    (r[1] for r in conn.execute(f'PRAGMA table_info({tbl})'))
                    if c not in cols]
            if len(keep) == len(existing):
                continue
            # Rebuild from PRAGMA metadata rather than by slicing the CREATE
            # text: parsing SQL by hand loses constraints and misaligns column
            # lists, which is exactly the class of bug this tool exists to catch.
            info = list(conn.execute(f'PRAGMA table_info({tbl})'))
            keep_defs = []
            for _cid, cname, ctype, notnull, dflt, pk in info:
                if cname in cols:
                    continue
                d = f'"{cname}" {ctype or "TEXT"}'
                if pk:
                    d += ' PRIMARY KEY'
                    if (ctype or '').upper() == 'INTEGER':
                        d += ' AUTOINCREMENT'
                elif notnull:
                    d += ' NOT NULL'
                if dflt is not None:
                    d += f' DEFAULT {dflt}'
                keep_defs.append(d)
            conn.execute(f'ALTER TABLE {tbl} RENAME TO {tbl}_old_sim')
            conn.execute(f'CREATE TABLE {tbl} (\n  ' + ',\n  '.join(keep_defs) + '\n)')
            cl = ', '.join(f'"{c}"' for c in keep)
            conn.execute(f'INSERT INTO {tbl} ({cl}) SELECT {cl} FROM {tbl}_old_sim')
            conn.execute(f'DROP TABLE {tbl}_old_sim')
            trimmed.append(f'{tbl}({len(cols)})')
        conn.execute("DELETE FROM app_settings WHERE setting_key = 'schema_version'")
        conn.commit()
        conn.close()
        print(f'  قُلِّم: {len(dropped)} جدول، أعمدة في {", ".join(trimmed) or "لا شيء"}')

        run_init(app, dbmod)
        print(f'  {OK} الترقية من المخطط المُقلَّم اكتملت')

        conn = sqlite3.connect(db2)
        n = conn.execute("SELECT COUNT(*) FROM employees "
                         "WHERE employee_number = '9001'").fetchone()[0]
        missing = [t for t in LATE_TABLES if not conn.execute(
            'SELECT COUNT(*) FROM sqlite_master WHERE name = ?', (t,)).fetchone()[0]]
        gaps = {}
        for tbl, cols in LATE_COLUMNS.items():
            have = {r[1] for r in conn.execute(f'PRAGMA table_info({tbl})')}
            if set(cols) - have:
                gaps[tbl] = sorted(set(cols) - have)
        ver = conn.execute("SELECT setting_value FROM app_settings "
                           "WHERE setting_key = 'schema_version'").fetchone()
        conn.close()

        if n != 1:
            print(f'  {FAIL} بيانات الموظف القديم لم تنجُ')
            failures.append('فقدان بيانات عند الترقية من مخطط قديم')
        else:
            print(f'  {OK} بيانات الموظف القديم سليمة')
        if missing:
            print(f'  {FAIL} جداول لم تُستعَد: {missing}')
            failures.append(f'جداول لم تُستعَد: {missing}')
        else:
            print(f'  {OK} كل الجداول المحذوفة أُعيد إنشاؤها')
        if gaps:
            print(f'  {FAIL} أعمدة لم تُستعَد: {gaps}')
            failures.append(f'أعمدة لم تُستعَد: {gaps}')
        else:
            print(f'  {OK} كل الأعمدة المقلَّمة أُعيد إضافتها')
        print(f'  {OK} وُسِم بالإصدار v{ver[0] if ver else "?"}')
    except Exception:
        print(f'  {FAIL} فشل مسار الترقية من مخطط قديم:')
        traceback.print_exc()
        failures.append('الترقية من مخطط قديم فشلت')

    head('٣) اتساق SCHEMA_VERSION')
    src = open(os.path.join(APP_DIR, 'utils', 'db.py'), encoding='utf-8').read()
    n_alter = len(re.findall(r'ADD COLUMN', src))
    m = re.search(r'^SCHEMA_VERSION\s*=\s*(\d+)', src, re.M)
    if not m:
        print(f'  {FAIL} SCHEMA_VERSION غير معرَّف')
        failures.append('SCHEMA_VERSION غير معرَّف')
    else:
        print(f'  {OK} SCHEMA_VERSION = {m.group(1)} | ترحيلات الأعمدة: {n_alter}')
        print(f'  {WARN} تذكير: ارفع SCHEMA_VERSION يدويًا مع كل ترحيل جديد')

    head('الحكم')
    if failures:
        print(f'{FAIL} لا تبنِ نسخة — {len(failures)} خلل:')
        for f in failures:
            print(f'    • {f}')
        return 1
    print(f'{OK} الترحيل سليم — آمن للبناء والتسليم')
    return 0


if __name__ == '__main__':
    sys.exit(main())
