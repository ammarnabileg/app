# -*- coding: utf-8 -*-
"""لقطة مسير الرواتب الحاليّ — مقياسُ نجاح محرّك V4 (المرحلة ٠ في docs/PAYROLL_V4_PLAN.md).

تحسب مسيرَ شهرٍ أو أكثر بالمحرّك الحاليّ (`utils/payroll_engine.compute_monthly_payroll`) على **نسخةٍ**
من قاعدةٍ (يُفضَّل مجهّلة — tools/anonymize_db.py) وتكتب لكلّ موظّف: الأساسيّ، وكلَّ بندٍ بكوده ومبلغه،
والمجاميع، والساعات، وأيّامَ الشهر من `day_sink`. ثمّ V4 يُشغَّل على القاعدة نفسها، والمطلوب **فرق صفر**.

    python tools/payroll_snapshot.py anon.db 2026-08 2026-09 --save golden.json
    python tools/payroll_snapshot.py anon.db 2026-08 2026-09 --check golden.json

- لا تُعدِّل القاعدة: تعمل على نسخةٍ مؤقّتة.
- اللقطة بلا أسماء (رقم الموظّف فقط) — لكنّها رواتب حقيقيّة: مكانها `tests/fixtures/payroll_private/` (خارج git).
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY_KEYS = ('kind', 'first', 'last', 'punches', 'span_hours', 'delta')


def _items(items):
    """[{code, amount}] → {code: مجموع} (بنودٌ متكرّرة الكود — سلفتان مثلًا — تُجمع)."""
    out = {}
    for it in items or []:
        k = str(it.get('code') or it.get('name') or '?')
        out[k] = round(out.get(k, 0) + float(it.get('amount') or 0), 3)
    return dict(sorted(out.items()))


def snapshot_month(conn, month, year, with_days=True):
    from utils import payroll_engine as pe
    days = [] if with_days else None
    data = pe.compute_monthly_payroll(conn, month, year, day_sink=days)
    by_emp = {}
    for r in data['rows']:
        by_emp[str(r['employee_number'])] = {
            'basic': r['basic'], 'allowances': _items(r['allowances']), 'deductions': _items(r['deductions']),
            'total_allowances': r['total_allowances'], 'total_deductions': r['total_deductions'], 'net': r['net'],
            'employer_pifss': r.get('employer_pifss', 0),
            'required_hours': r['required_hours'], 'actual_hours': r['actual_hours'],
            'law_warnings': sorted(w.get('code', '') for w in r.get('law_warnings') or []),
        }
    if with_days:
        num = {r['employee_id']: str(r['employee_number']) for r in data['rows']}
        for dr in days:
            if dr['kind'] == 'outside' or dr['employee_id'] not in num:
                continue
            e = by_emp[num[dr['employee_id']]].setdefault('days', {})
            e[dr['date']] = {k: dr[k] for k in DAY_KEYS}
    return {'period': data['period'], 'totals': data['totals'], 'employees': dict(sorted(by_emp.items()))}


def snapshot(db_path, months, with_days=True):
    """months: ['YYYY-MM', …] → {month: لقطة}. تعمل على نسخة."""
    tmp = tempfile.mkdtemp(prefix='payroll_snap_')
    old = os.environ.get('HR_DATA_DIR')
    try:
        # backup API لا نسخ ملف: قاعدةٌ مفتوحة (WAL) جزءٌ من بياناتها لسه في ملف الـ-wal.
        import sqlite3
        src = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        dst = sqlite3.connect(os.path.join(tmp, 'hr_system.db'))
        src.backup(dst)
        src.close()
        dst.close()
        os.environ['HR_DATA_DIR'] = tmp
        sys.path.insert(0, ROOT)
        import importlib
        import utils.db as db
        importlib.reload(db)
        db.init_db()                     # ترحيلُ نسخةٍ قديمة للمخطّط الحاليّ — على النسخة وحدها
        import utils.payroll_engine as pe
        importlib.reload(pe)
        conn = db.get_db_connection()
        out = {}
        for m in months:
            y, mo = (int(x) for x in m.split('-'))
            out[m] = snapshot_month(conn, mo, y, with_days)
        conn.close()
        return json.loads(json.dumps(out, ensure_ascii=False, default=str))
    finally:
        if old is None:
            os.environ.pop('HR_DATA_DIR', None)
        else:
            os.environ['HR_DATA_DIR'] = old
        shutil.rmtree(tmp, ignore_errors=True)


def diff(want, got, limit=50):
    """فروقٌ مقروءة: ['2026-09 / 101 / net: 300.0 ≠ 290.0', …]."""
    out = []

    def walk(a, b, path):
        if len(out) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a:
                    out.append(f'{path}/{k}: زيادة في الجديد')
                elif k not in b:
                    out.append(f'{path}/{k}: ناقص في الجديد')
                else:
                    walk(a[k], b[k], f'{path}/{k}')
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > 0.0005:
                out.append(f'{path}: {a} ≠ {b}')
        elif a != b:
            out.append(f'{path}: {a!r} ≠ {b!r}')
    walk(want, got, '')
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='لقطة مسير الرواتب الحالي')
    ap.add_argument('db')
    ap.add_argument('months', nargs='+', help='YYYY-MM')
    ap.add_argument('--save')
    ap.add_argument('--check')
    ap.add_argument('--no-days', action='store_true', help='من غير تفاصيل الأيام')
    a = ap.parse_args(argv)
    snap = snapshot(a.db, a.months, with_days=not a.no_days)
    if a.save:
        with open(a.save, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
        n = sum(len(v['employees']) for v in snap.values())
        print(f'اتحفظت اللقطة: {a.save} ({len(snap)} شهر، {n} سطر موظف)')
        return 0
    if a.check:
        with open(a.check, encoding='utf-8') as f:
            want = json.load(f)
        d = diff(want, snap)
        if d:
            print('\n'.join('✗ ' + x for x in d))
            return 1
        print('✓ نفس النتيجة بالظبط')
        return 0
    print(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
