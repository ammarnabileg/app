# -*- coding: utf-8 -*-
"""مقارنة كشف الرواتب بالسياسات الجديدة وبالقديم (من غير أي سطر سياسات) على شهر حقيقي.

    python tools/policy_compare.py --db /path/hr_system.db --month 9 --year 2026 [--csv out.csv]

بيشتغل على **نسخة** من الملف في فولدر مؤقت (الأصل مايتلمسش). في النسخة: مرة بالسياسات زي ما هي،
ومرة بعد مسح سطور السياسات وتاريخ أيام الراحة (= الحساب القديم). بيطبع كل موظف اتغيّر صافيه أو
خصوماته أو إضافيه، والفرق، وبنود الفرق.
"""
import argparse
import csv
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(path, month, year, strip):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if strip:
        for t in ('payroll_policies', 'employee_weekly_off_history'):
            try:
                con.execute(f'DELETE FROM {t}')
            except sqlite3.OperationalError:
                pass
        con.commit()
    from utils import payroll_engine as pe
    out = {r['employee_id']: r for r in pe.compute_monthly_payroll(con, month, year)['rows']}
    con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--month', type=int, required=True)
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--csv')
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='polcmp_')
    try:
        new_p, old_p = os.path.join(tmp, 'new.db'), os.path.join(tmp, 'old.db')
        shutil.copy2(a.db, new_p)
        shutil.copy2(a.db, old_p)
        os.environ['HR_DATA_DIR'] = tmp
        new, old = _run(new_p, a.month, a.year, False), _run(old_p, a.month, a.year, True)
        rows = []
        for eid in sorted(set(new) | set(old)):
            n, o = new.get(eid), old.get(eid)
            if not n or not o:
                continue
            items = {}
            for side, r in (('new', n), ('old', o)):
                for x in r['allowances'] + [dict(d, amount=-d['amount']) for d in r['deductions']]:
                    items.setdefault(x['code'], {'new': 0.0, 'old': 0.0})[side] += x['amount']
            diff_items = {k: round(v['new'] - v['old'], 3) for k, v in items.items() if abs(v['new'] - v['old']) > 0.0005}
            if abs(n['net'] - o['net']) > 0.0005 or diff_items:
                rows.append({'employee_number': n['employee_number'], 'name': n['arabic_name'] or n['name'],
                             'net_old': o['net'], 'net_new': n['net'], 'diff': round(n['net'] - o['net'], 3),
                             'items': '; '.join(f'{k}: {v:+.3f}' for k, v in diff_items.items())})
        print(f'{len(new)} موظف — {len(rows)} اتغيّر')
        for r in rows:
            print(f"{r['employee_number']:>8} {r['name'][:28]:28} {r['net_old']:>11.3f} → {r['net_new']:>11.3f} "
                  f"({r['diff']:+.3f})  {r['items']}")
        if a.csv:
            with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.DictWriter(f, fieldnames=['employee_number', 'name', 'net_old', 'net_new', 'diff', 'items'])
                w.writeheader()
                w.writerows(rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
