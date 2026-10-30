# -*- coding: utf-8 -*-
"""نقل قاعدة عميل من SQLite لـ PostgreSQL — مع مقارنة قبل وبعد.

    python tools/migrate_to_pg.py --sqlite /app/data/hr_system.db \\
        --url postgresql://user:pass@host:5432/hr --schema nour [--months 6] [--force]

1. ينقل كل الجداول لـ schema الشركة (utils/pgcopy)، ويطبع أي قيمة ماتنقلتش زي ما هي (نص في عمود رقم).
2. يشغّل البرنامج على الاتنين (عمليتين منفصلتين: من غير HR_DB_URL ومعاه) ويقارن:
   عدد صفوف كل جدول، وساعات ودقايق وصافي راتب كل موظف في آخر N شهور.
3. أي فرق بيتطبع، والأمر بيرجع 1 — ماتنقلش العميل لحد ما يرجع 0.

الملف الأصلي مابيتلمسش (بيتفتح قراءة بس). الـ schema لو فيها بيانات: لازم --force (بتتمسح وتتملي من جديد).
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROBE = r'''
import json, os, sys
sys.path.insert(0, %(root)r)
months = %(months)d
import utils.db as db
from utils import payroll_engine as pe
from datetime import date
conn = db.get_db_connection() if False else None
from flask import Flask
app = Flask('probe')
out = {'counts': {}, 'months': {}}
with app.app_context():
    conn = db.get_db_connection()
    for (t,) in [tuple(r) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%%' ORDER BY name")]:
        try:
            out['counts'][t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception as e:
            out['counts'][t] = 'ERR ' + str(e)[:80]
    today = date.today()
    y, m = today.year, today.month
    for _ in range(months):
        key = f'{y}-{m:02d}'
        try:
            mets = pe.compute_month_metrics(conn, m, y)
            pay = {r['employee_id']: r for r in pe.compute_monthly_payroll(conn, m, y)['rows']}
            rows = {}
            for eid, mm in mets.items():
                p = pay.get(eid, {})
                rows[str(eid)] = [round(float(mm.get('actual_hours') or 0), 4), int(mm.get('late_mins') or 0),
                                  int(mm.get('ot_weekday_mins') or 0), int(mm.get('absent_days') or 0),
                                  round(float(p.get('net') or 0), 3)]
            out['months'][key] = rows
        except Exception as e:
            out['months'][key] = 'ERR ' + str(e)[:200]
        m -= 1
        if m == 0:
            y, m = y - 1, 12
print('@@' + json.dumps(out))
'''


def probe(env, months):
    code = _PROBE % {'root': ROOT, 'months': months}
    r = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=3600)
    for line in r.stdout.splitlines():
        if line.startswith('@@'):
            return json.loads(line[2:])
    raise RuntimeError('probe failed:\n' + r.stdout[-2000:] + r.stderr[-3000:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sqlite', required=True)
    ap.add_argument('--url', required=True)
    ap.add_argument('--schema', required=True)
    ap.add_argument('--months', type=int, default=6)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--verify-only', action='store_true')
    a = ap.parse_args()

    src = os.path.abspath(a.sqlite)
    if not os.path.exists(src):
        sys.exit(f'مفيش ملف: {src}')
    data_dir = os.path.dirname(src)
    base = dict(os.environ, HR_DATA_DIR=data_dir)
    base.pop('HR_DB_URL', None)
    base.pop('HR_DB_SCHEMA', None)
    pg_env = dict(base, HR_DB_URL=a.url, HR_DB_SCHEMA=a.schema)
    sys.path.insert(0, ROOT)
    os.environ.update(HR_DB_URL=a.url, HR_DB_SCHEMA=a.schema, HR_DATA_DIR=data_dir)

    if not a.verify_only:
        from utils import dbcompat, pgcopy
        pg = dbcompat.PgConnection(a.url, dbcompat.schema_for(src))
        n = pg.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if n and not a.force:
            sys.exit(f'الـ schema «{pg.schema}» فيها {n} جدول — استخدم --force عشان تتمسح وتتملي من الملف')
        if n:
            pg.close()
            import psycopg
            with psycopg.connect(a.url, autocommit=True) as c:
                c.execute(f'DROP SCHEMA "{a.schema}" CASCADE')
            dbcompat._ready.clear()
            pg = dbcompat.PgConnection(a.url, dbcompat.schema_for(src))
        # الجداول بتتعمل بالمخطّط الحالي الأول (init_db)، والبيانات بتدخلها
        print('• بنعمل الجداول (init_db) …')
        subprocess.run([sys.executable, '-c', f'import sys; sys.path.insert(0, {ROOT!r}); '
                        'import utils.db as d; d.init_db()'], env=pg_env, check=True,
                       stdout=subprocess.DEVNULL)
        print('• بننقل البيانات …')
        rep = pgcopy.sqlite_to_pg(src, pg, progress=lambda t, n: print(f'   {t}: {n:,}'))
        print(f'  اتنقل {len(rep["tables"])} جدول، {sum(rep["tables"].values()):,} صف')
        for k, n in rep['coerced'].items():
            print(f'  ⚠ {k}: {n} قيمة مش من نوع العمود (أمثلة: {rep["samples"].get(k)}) ← فاضية/0')
        for e in rep['errors']:
            print('  ✗', e)
        pg.close()

    print('• بنقارن (SQLite قصاد PostgreSQL) …')
    left, right = probe(base, a.months), probe(pg_env, a.months)
    diffs = []
    for t, n in left['counts'].items():
        if right['counts'].get(t) != n:
            diffs.append(f'جدول {t}: SQLite={n} PostgreSQL={right["counts"].get(t)}')
    for mon, rows in left['months'].items():
        other = right['months'].get(mon)
        if rows != other:
            if isinstance(rows, str) or isinstance(other, str):
                diffs.append(f'{mon}: {rows if isinstance(rows, str) else "ok"} / {other if isinstance(other, str) else "ok"}')
                continue
            for eid, v in rows.items():
                if other.get(eid) != v:
                    diffs.append(f'{mon} موظف {eid}: SQLite={v} PostgreSQL={other.get(eid)} '
                                 '[ساعات، تأخير، إضافي، غياب، صافي]')
    if diffs:
        print(f'✗ {len(diffs)} فرق:')
        for d in diffs[:200]:
            print('  ', d)
        return 1
    print(f'✓ مفيش فروق: {len(left["counts"])} جدول، و{a.months} شهور رواتب وساعات بنفس الأرقام بالظبط.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
