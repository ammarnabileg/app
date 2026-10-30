# -*- coding: utf-8 -*-
"""قياس سرعة كشف الرواتب على شركة كبيرة (multi-tenant / PostgreSQL أو SQLite).

    python tools/payroll_bench.py --employees 500             # SQLite في فولدر مؤقت
    HR_DB_URL=postgresql://… python tools/payroll_bench.py     # PostgreSQL (schema مؤقتة بتتمسح)

بيعمل موظفين بشفت 8-4 وبصمتين كل يوم شغل، وسلفة وعمولة لكل عاشر موظف، وبيقيس:
حساب الشهر (compute_month_metrics)، والكشف كامل (compute_monthly_payroll)، وكشف الأيام لموظف.
"""
import argparse
import os
import random
import shutil
import sys
import tempfile
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--employees', type=int, default=500)
    ap.add_argument('--month', type=int, default=9)
    ap.add_argument('--year', type=int, default=2026)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='hrbench_')
    os.environ['HR_DATA_DIR'] = tmp
    pg = (os.environ.get('HR_DB_URL') or '').startswith(('postgres://', 'postgresql://'))
    if pg:
        os.environ['HR_DB_SCHEMA'] = 'bench_' + str(os.getpid())
    from utils import db
    t0 = time.time()
    db.init_db()
    conn = db.connect()
    if not pg:
        import sqlite3
        conn.row_factory = sqlite3.Row
    print(f"{'PostgreSQL' if pg else 'SQLite'} — init {time.time() - t0:.1f}s")
    import utils.payroll_engine as pe
    rnd = random.Random(7)
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active) "
                 "VALUES ('B', '08:00', '16:00', 8, 'none', 1)")
    ps, pe_ = pe.resolve_period(conn, a.month, a.year)
    comm = conn.execute("SELECT id FROM payroll_item_types WHERE code = 'commission'").fetchone()[0]
    t0 = time.time()
    for i in range(1, a.employees + 1):
        eid = conn.execute("INSERT INTO employees (employee_number, name, department, position, hire_date, salary, "
                           "is_active, shift_type) VALUES (?, ?, 'D', 'P', '2020-01-01', ?, 1, 'B')",
                           (str(1000 + i), f'E{i}', 300 + i % 200)).lastrowid
        d = ps
        while d <= pe_:
            if d.isoweekday() not in (5, 6) and rnd.random() > 0.05:
                cin = f'{d.isoformat()} 08:{rnd.randint(0, 20):02d}:00'
                cout = f'{d.isoformat()} {16 + rnd.randint(0, 2)}:{rnd.randint(0, 59):02d}:00'
                conn.executemany('INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) '
                                 'VALUES (?, 1, ?, ?)', [(eid, cin, 0), (eid, cout, 1)])
            d += timedelta(days=1)
        if i % 10 == 0:
            conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month, "
                         "start_year, status) VALUES (?, 500, 50, ?, ?, 'active')", (eid, a.month, a.year))
            conn.execute('INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount) '
                         'VALUES (?, ?, ?, ?, 25)', (eid, comm, a.month, a.year))
    conn.commit()
    n = conn.execute('SELECT COUNT(*) FROM attendance_records').fetchone()[0]
    print(f'بيانات: {a.employees} موظف، {n:,} بصمة — {time.time() - t0:.1f}s')
    for label, fn in (('حساب الشهر', lambda: pe.compute_month_metrics(conn, a.month, a.year)),
                      ('الكشف كامل', lambda: pe.compute_monthly_payroll(conn, a.month, a.year)),
                      ('كشف أيام موظف', lambda: pe.compute_employee_days(conn, 1, a.month, a.year))):
        t0 = time.time()
        fn()
        print(f'{label}: {time.time() - t0:.2f}s')
    conn.close()
    if pg:
        from utils import dbcompat
        import psycopg
        dbcompat.close_all()
        with psycopg.connect(os.environ['HR_DB_URL'], autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{os.environ["HR_DB_SCHEMA"]}" CASCADE')
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
