#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""محاكاة ترقية الإصدار — Upgrade Simulation Harness

الغرض: قبل تسليم أي نسخة لعميل، جرّب الترقية على نسخة من قاعدته الفعلية
وتأكد أن البيانات نجت وأن الحسابات لم تنحرف — بدل أن تُكتشف المشكلة عنده.

    python3 tools/simulate_upgrade.py /path/to/client_backup.db

يعمل على نسخة مؤقتة دائمًا؛ لا يلمس الملف الأصلي إطلاقًا.

ما يفحصه:
  1. أن init_db() يكمل بلا استثناء على المخطط القديم (الترحيل التلقائي)
  2. أن أعداد الصفوف قبل/بعد متطابقة — لا فقدان بيانات
  3. أن كل جدول وعمود تحتاجه النسخة الجديدة موجود بعد الترقية
  4. أن الصفحات الأساسية تُفتح فعليًا بعد الترقية (لا 500)
  5. أن حسابات الرواتب تُنتَج بلا استثناء
  6. أن سلسلة دفتر الأستاذ سليمة إن وُجدت قيود
  7. تشخيص جودة بيانات العميل (أرصدة شاذة، سجلات يتيمة، أرقام بكسور)

رمز الخروج 0 = آمن للتسليم، 1 = يوجد ما يجب حلّه أولًا.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

OK, WARN, FAIL = '\033[92m✓\033[0m', '\033[93m!\033[0m', '\033[91m✗\033[0m'
problems = []
warnings = []


def head(t):
    print(f'\n{"=" * 68}\n{t}\n{"=" * 68}')


def snapshot(db_path):
    """أعداد صفوف كل جدول — خط الأساس لمقارنة ما قبل/بعد."""
    conn = sqlite3.connect(db_path)
    out = {}
    for (t,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"):
        try:
            out[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except sqlite3.Error:
            out[t] = -1
    conn.close()
    return out


def diagnose(db_path):
    """جودة بيانات العميل — مشاكل تُورَّث للنسخة الجديدة إن لم تُعالج."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    found = []

    def tables():
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    t = tables()

    if 'employees' in t:
        # أرقام وظيفية بكسور عشرية — تكسر مطابقة البصمة وتُنشئ تكرارًا
        bad = [r['employee_number'] for r in conn.execute(
            "SELECT employee_number FROM employees "
            "WHERE employee_number LIKE '%.%'")]
        if bad:
            found.append(('FAIL', f'أرقام وظيفية بكسور عشرية: {bad}'))

        dup = [r[0] for r in conn.execute(
            'SELECT employee_number FROM employees '
            'GROUP BY employee_number HAVING COUNT(*) > 1')]
        if dup:
            found.append(('FAIL', f'أرقام وظيفية مكررة: {dup}'))

        no_hire = conn.execute(
            'SELECT COUNT(*) FROM employees '
            "WHERE hire_date IS NULL OR TRIM(hire_date) = ''").fetchone()[0]
        if no_hire:
            found.append(('FAIL', f'{no_hire} موظف بلا تاريخ تعيين — يكسر كل حساب'))

        no_sal = conn.execute(
            'SELECT COUNT(*) FROM employees '
            'WHERE COALESCE(salary, 0) <= 0 AND is_active = 1').fetchone()[0]
        if no_sal:
            found.append(('WARN', f'{no_sal} موظف نشط بلا راتب'))

        inactive_no_date = conn.execute(
            'SELECT COUNT(*) FROM employees '
            'WHERE is_active = 0 AND end_of_service_date IS NULL').fetchone()[0]
        if inactive_no_date:
            found.append(('WARN', f'{inactive_no_date} موظف معطَّل بلا تاريخ '
                                  'انتهاء خدمة — يظل ظاهرًا في الرواتب'))

        # موظفون قدامى بلا رصيد افتتاحي معتمد
        old_staff = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE is_active = 1 "
            "AND COALESCE(previous_leave_balance, 0) = 0 "
            "AND DATE(hire_date) < DATE('now', '-1 year')").fetchone()[0]
        if old_staff:
            found.append(('WARN', f'{old_staff} موظف قديم بلا رصيد إجازات '
                                  'افتتاحي — اضبط تاريخ القطع واعتمد أرصدتهم'))

    # سجلات مرتبطة بموظف محذوف
    for tbl, col in (('leave_requests', 'employee_id'),
                     ('attendance_records', 'employee_id'),
                     ('employee_salary_items', 'employee_id'),
                     ('employee_loans', 'employee_id')):
        if tbl in t and 'employees' in t:
            n = conn.execute(
                f'SELECT COUNT(*) FROM {tbl} WHERE {col} NOT IN '
                '(SELECT id FROM employees)').fetchone()[0]
            if n:
                found.append(('WARN', f'{tbl}: {n} سجل لموظف غير موجود'))

    # أرصدة إجازات مستحيلة (أكثر من سنة كاملة)
    if 'leave_balance' in t:
        n = conn.execute('SELECT COUNT(*) FROM leave_balance '
                         'WHERE opening_balance > 200').fetchone()[0]
        if n:
            found.append(('WARN', f'{n} سجل رصيد إجازات يتجاوز 200 يوم — '
                                  'أثر تراكم آلي قديم، لا تُرحَّل كما هي'))

    conn.close()
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = sys.argv[1]
    if not os.path.isfile(src):
        print(f'{FAIL} الملف غير موجود: {src}')
        return 1

    head(f'محاكاة ترقية: {os.path.basename(src)} → SCHEMA_VERSION الحالي')

    workdir = tempfile.mkdtemp(prefix='upgrade_sim_')
    target = os.path.join(workdir, 'hr_system.db')
    shutil.copy2(src, target)
    print(f'{OK} نسخة معزولة: {target}')
    print(f'{OK} الملف الأصلي لم يُلمس')

    # ---- 1) تشخيص جودة البيانات قبل أي شيء ----
    head('١) تشخيص بيانات العميل (قبل الترقية)')
    for level, msg in diagnose(target):
        print(f'  {FAIL if level == "FAIL" else WARN} {msg}')
        (problems if level == 'FAIL' else warnings).append(msg)
    if not diagnose(target):
        print(f'  {OK} لا مشاكل ظاهرة')

    before = snapshot(target)
    conn0 = sqlite3.connect(target)
    try:
        row = conn0.execute("SELECT setting_value FROM app_settings "
                            "WHERE setting_key = 'schema_version'").fetchone()
        ver_before = int(row[0]) if row and row[0] else 0
    except sqlite3.Error:
        ver_before = 0
    conn0.close()
    print(f'\n  إصدار المخطط قبل الترقية: v{ver_before or "غير مُوسَّم (قديم)"}')
    print(f'  جداول: {len(before)} | صفوف: {sum(v for v in before.values() if v > 0)}')

    # ---- 2) تشغيل الترحيل الفعلي ----
    head('٢) تشغيل init_db() — الترحيل التلقائي')
    os.environ['HR_DATA_DIR'] = workdir
    os.environ.setdefault('SECRET_KEY', 'simulation')
    os.chdir(APP_DIR)
    from unittest.mock import patch
    lic = {'ok': True}
    p = patch('utils.license.get_current_license_info', return_value=lic)
    p.start()
    try:
        from app import app
        from utils.db import init_db, get_db_connection, SCHEMA_VERSION
        with app.app_context():
            init_db()
        print(f'  {OK} اكتمل بلا استثناء → v{SCHEMA_VERSION}')
    except Exception:
        print(f'  {FAIL} فشل الترحيل:')
        traceback.print_exc()
        problems.append('init_db() فشل على مخطط العميل')
        return 1

    # ---- 3) لا فقدان بيانات ----
    head('٣) سلامة البيانات (قبل/بعد)')
    after = snapshot(target)
    lost = {t: (before[t], after.get(t, 0))
            for t in before if before[t] > 0 and after.get(t, 0) < before[t]}
    if lost:
        for t, (b, a) in lost.items():
            print(f'  {FAIL} {t}: {b} → {a}')
            problems.append(f'فقدان بيانات في {t}')
    else:
        print(f'  {OK} لا فقدان في أي جدول')
    new_tables = sorted(set(after) - set(before))
    print(f'  {OK} جداول أُضيفت: {len(new_tables)}')
    if new_tables:
        print(f'     {", ".join(new_tables)}')

    # ---- 4) المخطط مكتمل مقابل نسخة نظيفة ----
    head('٤) اكتمال المخطط مقابل قاعدة جديدة نظيفة')
    fresh_dir = tempfile.mkdtemp(prefix='fresh_')
    os.environ['HR_DATA_DIR'] = fresh_dir
    import importlib
    import utils.db as dbmod
    importlib.reload(dbmod)
    with app.app_context():
        dbmod.init_db()
    fresh = os.path.join(fresh_dir, 'hr_system.db')

    fc, tc_ = sqlite3.connect(fresh), sqlite3.connect(target)
    ft = {r[0] for r in fc.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    tt = {r[0] for r in tc_.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    missing_t = ft - tt
    if missing_t:
        print(f'  {FAIL} جداول ناقصة بعد الترقية: {sorted(missing_t)}')
        problems.append(f'جداول ناقصة: {sorted(missing_t)}')
    else:
        print(f'  {OK} كل الجداول ({len(ft)}) موجودة')

    col_gaps = {}
    for t in sorted(ft & tt):
        fcols = {r[1] for r in fc.execute(f'PRAGMA table_info("{t}")')}
        tcols = {r[1] for r in tc_.execute(f'PRAGMA table_info("{t}")')}
        if fcols - tcols:
            col_gaps[t] = sorted(fcols - tcols)
    if col_gaps:
        for t, cols in col_gaps.items():
            print(f'  {FAIL} {t}: أعمدة ناقصة {cols}')
            problems.append(f'أعمدة ناقصة في {t}: {cols}')
    else:
        print(f'  {OK} لا أعمدة ناقصة في أي جدول')
    fc.close()
    tc_.close()

    # ---- 5) الصفحات والحسابات تعمل فعليًا ----
    head('٥) تشغيل فعلي بعد الترقية')
    os.environ['HR_DATA_DIR'] = workdir
    importlib.reload(dbmod)
    import utils.auth as A
    with patch.object(A, 'get_current_license_info', return_value=lic), \
         patch('app.get_current_license_info', return_value=lic), \
         patch('utils.rbac.RBACService.has_permission', return_value=True):
        app.config['TESTING'] = True
        with app.test_client() as tc2:
            with tc2.session_transaction() as sess:
                sess['user_id'] = 1
                sess['lang'] = 'ar'
            pages = ['/', '/employees', '/employees/profile', '/payroll/monthly',
                     '/payroll/ledger', '/leaves', '/leaves/balance',
                     '/structure', '/org-chart', '/settings', '/eos/list',
                     '/payroll/loans', '/payroll/transactions']
            for url in pages:
                try:
                    code = tc2.get(url).status_code
                except Exception as e:
                    code = f'EXC {type(e).__name__}: {e}'
                if code == 200:
                    print(f'  {OK} {url}')
                else:
                    print(f'  {FAIL} {url} → {code}')
                    problems.append(f'{url} → {code}')

            from datetime import datetime
            now = datetime.now()
            try:
                r = tc2.get(f'/api/payroll/monthly?month={now.month}&year={now.year}')
                j = r.get_json()
                if j and j.get('success'):
                    rows = j['data']['rows']
                    bad = [x['employee_id'] for x in rows
                           if abs((x['basic'] or 0) + (x['total_allowances'] or 0)
                                  - (x['total_deductions'] or 0) - (x['net'] or 0)) > 1e-6]
                    if bad:
                        print(f'  {FAIL} معادلة الصافي غير متزنة للموظفين: {bad}')
                        problems.append('معادلة الصافي غير متزنة بعد الترقية')
                    else:
                        print(f'  {OK} احتساب الرواتب: {len(rows)} موظف، المعادلة متزنة')
                else:
                    print(f'  {FAIL} احتساب الرواتب فشل: '
                          f'{(j or {}).get("message")}')
                    problems.append('احتساب الرواتب فشل بعد الترقية')
            except Exception as e:
                print(f'  {FAIL} احتساب الرواتب رفع استثناء: {e}')
                problems.append(f'احتساب الرواتب: {e}')

            try:
                v = tc2.get('/api/payroll/ledger/verify').get_json()
                if v.get('ok'):
                    print(f'  {OK} سلسلة دفتر الأستاذ سليمة')
                else:
                    print(f'  {FAIL} سلسلة الدفتر مكسورة عند القيد '
                          f'{v.get("broken_posting_id")}')
                    problems.append('سلسلة دفتر الأستاذ مكسورة')
            except Exception as e:
                print(f'  {WARN} تحقق الدفتر غير متاح: {e}')

    # ---- الحكم ----
    head('الحكم')
    if problems:
        print(f'{FAIL} غير آمن للتسليم — {len(problems)} مشكلة:')
        for x in problems:
            print(f'    • {x}')
    else:
        print(f'{OK} الترقية آمنة — البيانات سليمة والنظام يعمل')
    if warnings:
        print(f'\n{WARN} تحتاج قرارًا من العميل ({len(warnings)}):')
        for x in warnings:
            print(f'    • {x}')
    print(f'\nمجلد المحاكاة (احذفه بعد المراجعة): {workdir}')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
