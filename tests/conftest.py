# -*- coding: utf-8 -*-
"""تشغيل الاختبارات على PostgreSQL كمان (utils/dbcompat).

من غير HR_DB_URL: ولا حاجة بتتغيّر — الاختبارات على SQLite زي ما هي.

مع HR_DB_URL=postgresql://…: أي `sqlite3.connect(<…>/hr_system.db)` (في الاختبارات أو البرنامج) بيروح
لـ PostgreSQL، وكل HR_DATA_DIR (يعني كل اختبار) ليه schema لوحده — والـ schemas بتتمسح في الآخر.
"""
import os
import sqlite3

import pytest

_PG = (os.environ.get('HR_DB_URL') or '').startswith(('postgres://', 'postgresql://'))

if _PG:
    _real_connect = sqlite3.connect

    _mine = set()

    def _connect(database, *a, **k):
        p = str(database)
        if p.endswith('hr_system.db') and not p.startswith('file:'):
            from utils import dbcompat
            _mine.add(dbcompat.schema_for(p))
            return dbcompat.connect(p)
        return _real_connect(database, *a, **k)

    sqlite3.connect = _connect


@pytest.fixture(scope='session', autouse=True)
def _pg_cleanup():
    yield
    if not _PG:
        return
    import psycopg
    from utils import dbcompat
    # كل HR_DATA_DIR اتفتح في العملية دي (البرنامج كمان بيفتح من غير sqlite3.connect)
    dbcompat.close_all()
    with psycopg.connect(os.environ['HR_DB_URL'], autocommit=True) as c:
        c.execute("SET lock_timeout = '10s'")
        for s in _mine | {k[1] for k in dbcompat._ready}:
            if s.startswith('t_'):
                try:
                    c.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
                except Exception as e:          # التنضيف مايوقعش الاختبارات
                    print(f'[pg cleanup] {s}: {e}')
