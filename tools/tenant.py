# -*- coding: utf-8 -*-
"""إدارة الشركات على السيرفر المشترك (multi-tenant، utils/tenancy).

    export HR_DB_URL=postgresql://…  HR_MULTI_TENANT=1  [HR_TENANT_BASE_DOMAIN=onz.one]

    python tools/tenant.py create nour --name "صالون النور" [--host hr.nour.com]
    python tools/tenant.py import nour /backup/nour/hr_system.db [--name …]   # عميل موجود: ملفه ← شركة
    python tools/tenant.py list
    python tools/tenant.py add-host nour hr.nour.com
    python tools/tenant.py route-device nour AJE1262000048          # جهاز ADMS ← الشركة
    python tools/tenant.py suspend nour | resume nour

`import` بيعمل الشركة، وبينقل ملفها (utils/pgcopy)، وبيطبع أي قيمة ماتنقلتش زي ما هي. وبعده شغّل
tools/migrate_to_pg.py --verify-only على نفس الملف والـ schema (c_<slug>) عشان تقارن الرواتب قبل وبعد.
والمرفقات: انسخ فولدر uploads بتاع العميل لـ <DATA_DIR>/tenants/<slug>/uploads.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('create'); c.add_argument('slug'); c.add_argument('--name', default=''); c.add_argument('--host', action='append', default=[])
    i = sub.add_parser('import'); i.add_argument('slug'); i.add_argument('sqlite'); i.add_argument('--name', default=''); i.add_argument('--host', action='append', default=[])
    sub.add_parser('list')
    h = sub.add_parser('add-host'); h.add_argument('slug'); h.add_argument('host')
    d = sub.add_parser('route-device'); d.add_argument('slug'); d.add_argument('sn')
    s1 = sub.add_parser('suspend'); s1.add_argument('slug')
    s2 = sub.add_parser('resume'); s2.add_argument('slug')
    a = ap.parse_args()

    from utils import tenancy
    if not tenancy.enabled():
        sys.exit('لازم HR_DB_URL=postgresql://… و HR_MULTI_TENANT=1')
    tenancy.ensure_platform()

    if a.cmd == 'create':
        t = tenancy.create(a.slug, a.name, a.host)
        print(f'✓ اتعملت «{t["name"]}» — schema {tenancy.schema_of(a.slug)}، دومينات: {t["hosts"] or "(من HR_TENANT_BASE_DOMAIN)"}')
    elif a.cmd == 'import':
        if not os.path.exists(a.sqlite):
            sys.exit(f'مفيش ملف: {a.sqlite}')
        tenancy.create(a.slug, a.name, a.host)
        from utils import pgcopy
        from utils.db import connect
        with tenancy.use(a.slug):
            pg = connect()
            rep = pgcopy.sqlite_to_pg(a.sqlite, pg, progress=lambda t, n: print(f'   {t}: {n:,}'))
            pg.close()
        print(f'✓ اتنقل {len(rep["tables"])} جدول، {sum(rep["tables"].values()):,} صف لـ {tenancy.schema_of(a.slug)}')
        for k, n in rep['coerced'].items():
            print(f'  ⚠ {k}: {n} قيمة مش من نوع العمود (أمثلة: {rep["samples"].get(k)}) ← فاضية/0')
        for e in rep['errors']:
            print('  ✗', e)
        print(f'  قارن: python tools/migrate_to_pg.py --sqlite {a.sqlite} --url $HR_DB_URL '
              f'--schema {tenancy.schema_of(a.slug)} --verify-only')
        print(f'  المرفقات: انسخ uploads لـ {tenancy.data_dir(a.slug)}/uploads')
    elif a.cmd == 'list':
        tenancy._load(force=True)
        for slug, t in sorted(tenancy._cache['tenants'].items()):
            print(f'{slug:20} {t["status"]:10} {", ".join(t["hosts"]) or "-":40} {t["name"]}')
    elif a.cmd == 'add-host':
        tenancy.add_host(a.slug, a.host)
        print('✓')
    elif a.cmd == 'route-device':
        tenancy.route_device(a.sn, a.slug)
        print(f'✓ الجهاز {a.sn} ← {a.slug}')
    elif a.cmd == 'suspend':
        tenancy.set_status(a.slug, 'suspended')
        print('✓ اتوقفت (الدومين بيرد 404، والبيانات زي ما هي)')
    elif a.cmd == 'resume':
        tenancy.set_status(a.slug, 'active')
        print('✓')


if __name__ == '__main__':
    main()
