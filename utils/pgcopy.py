# -*- coding: utf-8 -*-
"""نقل البيانات بين ملف SQLite و schema في PostgreSQL — في الاتجاهين.

- **نقل عميل لـ PostgreSQL** (tools/migrate_to_pg.py): ملف `hr_system.db` ← schema الشركة.
- **النسخ الاحتياطي في وضع PostgreSQL** (utils/backup): الـ schema ← ملف SQLite بنفس شكل النسخ الحالية،
  فصفحة النسخ والاسترجاع وفحص الملف المرفوع بيشتغلوا زي ما هم، ونسخة اتاخدت من سيرفر PostgreSQL
  تترجع على تركيب SQLite والعكس.

SQLite بيقبل أي قيمة في أي عمود ('T1' في عمود رقم). PostgreSQL لأ — فالقيمة اللي مش من نوع العمود
بتتسجّل في التقرير (`coerced`) وبتتحط NULL، مش بتوقّف النقل ولا بتضيع في صمت.
"""
import re
import sqlite3

BATCH = 5000


def _pg_tables(pg):
    """جداول الـ schema الحالية في PostgreSQL ← {اسم: [(عمود، نوع)]}."""
    rows = pg._pg.execute("""SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
                             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                             JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                             WHERE n.nspname = %s AND c.relkind = 'r'
                             ORDER BY c.relname, a.attnum""", (pg.schema,)).fetchall()
    out = {}
    for t, col, typ, nn in rows:
        out.setdefault(t, []).append((col, typ + (' notnull' if nn else '')))
    return out


def _coerce(v, typ, report, table, col):
    notnull = typ.endswith(' notnull')
    typ = typ.replace(' notnull', '')
    if v is None:
        return None
    if typ in ('bigint', 'integer', 'smallint'):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        s = str(v).strip()
        if re.fullmatch(r'[+-]?\d+', s):
            return int(s)
        if re.fullmatch(r'[+-]?\d+\.0*', s):
            return int(float(s))
    elif typ.startswith('double') or typ == 'numeric':
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        s = str(v).strip()
        try:
            return float(s)
        except ValueError:
            pass
    elif typ == 'bytea':
        return bytes(v) if isinstance(v, (bytes, bytearray, memoryview)) else str(v).encode()
    else:                                   # text
        if isinstance(v, (bytes, bytearray, memoryview)):
            try:
                return bytes(v).decode()
            except UnicodeDecodeError:
                return bytes(v).hex()
        return str(v) if not isinstance(v, str) else v.replace('\x00', '')
    fallback = 0 if (notnull and typ != 'bytea') else None    # عمود إجباري: 0 بدل فاضي
    if s == '':
        return fallback
    key = f'{table}.{col}'
    report['coerced'][key] = report['coerced'].get(key, 0) + 1
    if len(report['samples'].setdefault(key, [])) < 3:
        report['samples'][key].append(str(v)[:40])
    return fallback


def sqlite_to_pg(sqlite_path, pg, tables=None, replace=True, progress=None):
    """ينسخ جداول ملف SQLite لـ schema الاتصال `pg` (utils.dbcompat.PgConnection).

    الجدول اللي مش موجود في PostgreSQL بيتعمل من تعريفه في الملف (مترجَم). الموجود بيتملي بالأعمدة
    المشتركة بس. `replace`: الجدول بيتفضّى الأول. يرجّع تقرير: لكل جدول عدد الصفوف، والقيم اللي اتغيّرت.
    """
    src = sqlite3.connect(f'file:{sqlite_path}?mode=ro', uri=True)
    report = {'tables': {}, 'coerced': {}, 'samples': {}, 'errors': []}
    try:
        names = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                           "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if tables is not None:
            names = [n for n in names if n in set(tables)]
        have = _pg_tables(pg)
        for name in names:
            if name not in have:
                ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
                if not ddl or not ddl[0]:
                    continue
                try:
                    pg.execute(ddl[0])
                    pg.commit()
                except sqlite3.Error as e:
                    report['errors'].append(f'{name}: {e}')
                    pg.rollback()
                    continue
                have = _pg_tables(pg)
            pcols = dict(have.get(name, []))
            scols = [r[1] for r in src.execute(f'PRAGMA table_info("{name}")')]
            cols = [c for c in scols if c.lower() in pcols]
            if not cols:
                continue
            raw = pg._pg
            with raw.cursor() as cur:
                # الـ triggers (أوراكل، الرفع للسحابة) مايشتغلوش على بيانات بتتنقل — دي مش بصمات جديدة
                cur.execute(f'ALTER TABLE "{pg.schema}"."{name.lower()}" DISABLE TRIGGER USER')
                if replace:
                    cur.execute(f'DELETE FROM "{pg.schema}"."{name.lower()}"')
                n = 0
                qcols = ', '.join(f'"{c.lower()}"' for c in cols)
                types = [pcols[c.lower()] for c in cols]
                with cur.copy(f'COPY "{pg.schema}"."{name.lower()}" ({qcols}) FROM STDIN') as cp:
                    for row in src.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{name}"'):
                        cp.write_row([_coerce(v, t, report, name, c) for v, t, c in zip(row, types, cols)])
                        n += 1
                        if progress and n % 50000 == 0:
                            progress(name, n)
                # الـ id اللي جاي من الملف: العدّاد يتقدّم بعده
                cur.execute("""SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                               JOIN pg_namespace s ON s.oid = c.relnamespace
                               WHERE s.nspname = %s AND c.relname = %s AND a.attidentity IN ('a', 'd')""",
                            (pg.schema, name.lower()))
                ids = cur.fetchall()
                cur.execute(f'ALTER TABLE "{pg.schema}"."{name.lower()}" ENABLE TRIGGER USER')
                for (idcol,) in ids:
                    cur.execute(f"""SELECT setval(pg_get_serial_sequence('"{pg.schema}"."{name.lower()}"', %s),
                                    GREATEST(COALESCE((SELECT max("{idcol}") FROM "{pg.schema}"."{name.lower()}"), 0), 1),
                                    (SELECT max("{idcol}") FROM "{pg.schema}"."{name.lower()}") IS NOT NULL)""", (idcol,))
            pg.commit()
            report['tables'][name] = n
    finally:
        src.close()
    return report


def _sqlite_type(t):
    t = (t or '').lower().replace(' notnull', '')
    if t in ('bigint', 'integer', 'smallint'):
        return 'INTEGER'
    if t.startswith('double') or t in ('real', 'numeric'):
        return 'REAL'
    if t == 'bytea':
        return 'BLOB'
    return 'TEXT'


def pg_to_sqlite(pg, dest_path, tables=None):
    """الـ schema ← ملف SQLite جديد بنفس الجداول والبيانات (نسخة احتياطية بشكل النسخ الحالية)."""
    import os
    if os.path.exists(dest_path):
        os.remove(dest_path)
    dst = sqlite3.connect(dest_path)
    counts = {}
    try:
        have = _pg_tables(pg)
        pks = {}
        for t, kc in pg._pg.execute("""SELECT c.relname, array_agg(a.attname ORDER BY k.ord)
                                      FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
                                      JOIN pg_namespace n ON n.oid = c.relnamespace
                                      CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord)
                                      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                                      WHERE n.nspname = %s AND i.indisprimary GROUP BY c.relname""",
                                   (pg.schema,)).fetchall():
            pks[t] = list(kc)
        for name, cols in have.items():
            if tables is not None and name not in set(tables):
                continue
            pk = pks.get(name, [])
            defs = []
            for c, t in cols:
                d = f'"{c}" {_sqlite_type(t)}'
                if pk == [c] and _sqlite_type(t) == 'INTEGER':
                    d += ' PRIMARY KEY AUTOINCREMENT'
                defs.append(d)
            if len(pk) > 1:
                defs.append('PRIMARY KEY (' + ', '.join(f'"{c}"' for c in pk) + ')')
            dst.execute(f'CREATE TABLE "{name}" ({", ".join(defs)})')
            qcols = ', '.join(f'"{c}"' for c, _t in cols)
            n = 0
            with pg._pg.cursor(name=f'exp_{n}_{name}'[:60]) as cur:
                cur.itersize = BATCH
                cur.execute(f'SELECT {qcols} FROM "{pg.schema}"."{name}"')
                ph = ', '.join('?' * len(cols))
                while True:
                    rows = cur.fetchmany(BATCH)
                    if not rows:
                        break
                    dst.executemany(f'INSERT INTO "{name}" ({qcols}) VALUES ({ph})', rows)
                    n += len(rows)
            counts[name] = n
        dst.commit()
    finally:
        dst.close()
        try:
            pg._pg.rollback()
        except Exception:
            pass
    return counts
