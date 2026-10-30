# -*- coding: utf-8 -*-
"""طبقة توافق: نفس كود البرنامج (المكتوب لـ SQLite) يشتغل على PostgreSQL.

البرنامج فيه ~1500 استعلام مكتوبين بلهجة SQLite. بدل ما نعيد كتابتهم، الاتصال بـ PostgreSQL
بيلبس شكل `sqlite3.Connection`: نفس `execute` و`Row` و`rowcount` و`lastrowid` ونفس أنواع الأخطاء
(`sqlite3.IntegrityError`/`OperationalError`) — والـ SQL بيتترجم وهو معدّي:

- `?` ← قيم بتتكتب جوه الاستعلام كنصوص من غير نوع (unknown) — PostgreSQL بيحوّلها لنوع العمود،
  فـ `WHERE employee_number = 365` و`WHERE id = '5'` بيشتغلوا زي SQLite.
- `INSERT OR IGNORE` ← `ON CONFLICT DO NOTHING`، و`INSERT OR REPLACE` ← `ON CONFLICT (المفتاح) DO UPDATE`.
- `LIKE` ← `ILIKE` (SQLite بيتجاهل حالة الحروف الإنجليزي)، و`==` ← `=`، و`IS ?` ← `IS NOT DISTINCT FROM`.
- تعريف الجداول: `INTEGER PRIMARY KEY` ← identity، والتواريخ بتفضل نصوص (زي SQLite بالظبط —
  المقارنات والتقارير بتعتمد على ده)، والـ FOREIGN KEY بتتشال (SQLite مابيطبّقهاش).
- دوال SQLite (`strftime`/`date`/`datetime`/`julianday`/`group_concat`/`max(a,b)`/`ifnull`/…)
  متعرّفة في schema اسمها `sqlite_compat`، ومعاها عرض `sqlite_master`، و`PRAGMA table_info` متقلّد.
- كل استعلام جوه SAVEPOINT: في PostgreSQL أي خطأ بيبوّظ المعاملة كلها، والبرنامج فيه أماكن كتير
  بتجرّب حاجة وتتجاهل خطأها (`ALTER TABLE … ADD COLUMN` في try) — في SQLite ده عادي.

الوضع ده بيتفعّل بـ `HR_DB_URL=postgresql://…` بس؛ من غيره البرنامج SQLite زي ما هو.
"""
import datetime as _dt
import decimal
import re
import sqlite3
import threading

try:
    import psycopg
    from psycopg import errors as pgerr
    from psycopg.adapt import Loader
except Exception:          # psycopg مش متسطّب: وضع SQLite بس
    psycopg = None

COMPAT_SCHEMA = 'sqlite_compat'
_FMT = 'YYYY-MM-DD HH24:MI:SS'


# ============================================================ تقسيم الـ SQL

_TOKEN = re.compile(r"""
    (?P<str>'(?:[^']|'')*')
  | (?P<ident>"(?:[^"]|"")*")
  | (?P<comment>--[^\n]*|/\*.*?\*/)
  | (?P<code>[^'"\-/]+|-(?!-)|/(?!\*))
""", re.X | re.S)


def _segments(sql):
    """[(نوع، نص)] — النوع code/str/ident/comment. الترجمة على الكود بس، مش جوه النصوص."""
    out, pos = [], 0
    for m in _TOKEN.finditer(sql):
        if m.start() != pos:                      # حاجة ماتطابقتش (علامة تنصيص مقفولش) — كود
            out.append(('code', sql[pos:m.start()]))
        kind = m.lastgroup
        if out and kind == 'code' and out[-1][0] == 'code':
            out[-1] = ('code', out[-1][1] + m.group())
        else:
            out.append((kind, m.group()))
        pos = m.end()
    if pos < len(sql):
        out.append(('code', sql[pos:]))
    return out


def _join(segs):
    return ''.join(t for _k, t in segs)


def _map_code(segs, fn):
    return [(k, fn(t) if k == 'code' else t) for k, t in segs]


_IDENT_OK = re.compile(r'^[a-z_][a-z0-9_]*$')


def _dq_strings(segs):
    """\"SENT\" في SQLite نص لو مفيش عمود بالاسم ده؛ في PostgreSQL دايمًا اسم. أسماء الجداول والأعمدة عندنا
    حروف صغيرة و_ — فأي حاجة تانية بين \"…\" (كبيرة، عربي، مسافات) بتتحوّل لنص."""
    out = []
    for k, t in segs:
        if k == 'ident':
            inner = t[1:-1].replace('""', '"')
            if not _IDENT_OK.match(inner):
                out.append(('str', "'" + inner.replace("'", "''") + "'"))
                continue
        out.append((k, t))
    return out


def split_statements(sql):
    """يقسم سكريبت على `;` اللي برّه النصوص."""
    stmts, cur = [], []
    for k, t in _segments(sql):
        if k != 'code' or ';' not in t:
            cur.append(t)
            continue
        parts = t.split(';')
        for p in parts[:-1]:
            cur.append(p)
            s = ''.join(cur).strip()
            if s:
                stmts.append(s)
            cur = []
        cur.append(parts[-1])
    s = ''.join(cur).strip()
    if s:
        stmts.append(s)
    return stmts


# ============================================================ القيم

def render(v):
    """قيمة بايثون ← نص SQL. الأرقام والنصوص بتتكتب '…' من غير نوع، فـ PostgreSQL يحوّلها لنوع العمود
    (زي ما SQLite بيقبل '5' في عمود رقم و5 في عمود نص)."""
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return "'1'" if v else "'0'"
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "'\\x%s'::bytea" % bytes(v).hex()
    if isinstance(v, _dt.datetime):
        v = v.isoformat(sep='T')            # زي adapter البرنامج في SQLite
    elif isinstance(v, (_dt.date, _dt.time)):
        v = v.isoformat()
    elif isinstance(v, float):
        v = repr(v)
    s = str(v)
    if '\x00' in s:
        s = s.replace('\x00', '')
    return "'" + s.replace("'", "''") + "'"


def _bind(segs, params):
    """`?` (و`?NNN` و`:name`) ← القيم، في الكود بس."""
    if params is None:
        params = ()
    named = isinstance(params, dict)
    seq = list(params) if not named else None
    idx = [0]

    def sub(m):
        tok = m.group(0)
        if tok.startswith(':'):
            if not named:
                return tok
            return render(params[tok[1:]])
        if named:
            raise sqlite3.ProgrammingError('positional ? with named params')
        if len(tok) > 1:                     # ?NNN
            return render(seq[int(tok[1:]) - 1])
        if idx[0] >= len(seq):
            raise sqlite3.ProgrammingError(
                'Incorrect number of bindings supplied. The current statement uses %d, and there are %d supplied.'
                % (idx[0] + 1, len(seq)))
        v = seq[idx[0]]
        idx[0] += 1
        return render(v)

    pat = re.compile(r'\?\d*|(?<![:\w]):[A-Za-z_]\w*' if named else r'\?\d*')
    out = _map_code(segs, lambda t: pat.sub(sub, t))
    if not named and idx[0] != len(seq):
        raise sqlite3.ProgrammingError(
            'Incorrect number of bindings supplied. The current statement uses %d, and there are %d supplied.'
            % (idx[0], len(seq)))
    return out


# ============================================================ الترجمة

_TYPE_MAP = [
    (re.compile(r'^(INTEGER|INT|BIGINT|SMALLINT|TINYINT|MEDIUMINT|INT2|INT8|UNSIGNED BIG INT)\b', re.I), 'BIGINT'),
    (re.compile(r'^(BOOLEAN|BOOL)\b', re.I), 'BIGINT'),
    (re.compile(r'^(REAL|FLOAT|DOUBLE PRECISION|DOUBLE)\b', re.I), 'DOUBLE PRECISION'),
    (re.compile(r'^(DECIMAL|NUMERIC)(\s*\([^)]*\))?', re.I), 'DOUBLE PRECISION'),
    (re.compile(r'^(DATETIME|TIMESTAMP|DATE|TIME)\b', re.I), 'TEXT'),
    (re.compile(r'^(VARCHAR|CHARACTER VARYING|NVARCHAR|NCHAR|CHAR|CHARACTER|CLOB|STRING|JSON)(\s*\([^)]*\))?', re.I), 'TEXT'),
    (re.compile(r'^BLOB\b', re.I), 'BYTEA'),
]
_CONSTRAINT_START = re.compile(r'^\s*(PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|CONSTRAINT)\b', re.I)
_NOW_DEFAULT = "to_char((now() AT TIME ZONE 'UTC'), '%s')" % _FMT


def _split_top(body, sep=','):
    """تقسيم على الفاصلة اللي برّه الأقواس والنصوص."""
    parts, depth, cur = [], 0, []
    for k, t in _segments(body):
        if k != 'code':
            cur.append(t)
            continue
        start = 0
        for i, ch in enumerate(t):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == sep and depth == 0:
                cur.append(t[start:i])
                parts.append(''.join(cur))
                cur, start = [], i + 1
        cur.append(t[start:])
    parts.append(''.join(cur))
    return parts


def _strip_references(s):
    """REFERENCES t(c) [ON DELETE …] [ON UPDATE …] [DEFERRABLE …] ← ولا حاجة (SQLite مابيطبّقهاش)."""
    return re.sub(r'\bREFERENCES\s+[\w"]+\s*(\([^)]*\))?(\s+ON\s+(DELETE|UPDATE)\s+'
                  r'(SET\s+NULL|SET\s+DEFAULT|CASCADE|RESTRICT|NO\s+ACTION))*'
                  r'(\s+(NOT\s+)?DEFERRABLE(\s+INITIALLY\s+(DEFERRED|IMMEDIATE))?)?', '', s, flags=re.I)


def _column_def(d):
    """تعريف عمود SQLite ← PostgreSQL."""
    m = re.match(r'\s*("(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]|\w+)\s*(.*)$', d, re.S)
    if not m:
        return d
    name, rest = m.group(1), m.group(2)
    if name.startswith(('`', '[')):
        name = '"' + name[1:-1] + '"'
    rest = _strip_references(rest)
    rest = re.sub(r'\bCOLLATE\s+(NOCASE|BINARY|RTRIM)\b', '', rest, flags=re.I)
    rest = re.sub(r'\bON\s+CONFLICT\s+(REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\b', '', rest, flags=re.I)
    if re.match(r'^INTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?\b', rest, re.I):
        rest = re.sub(r'^INTEGER\s+PRIMARY\s+KEY(\s+AUTOINCREMENT)?', 'BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY',
                      rest, flags=re.I)
    else:
        rest = re.sub(r'\bAUTOINCREMENT\b', '', rest, flags=re.I)
        typed = False
        for pat, repl in _TYPE_MAP:
            if pat.match(rest):
                rest = pat.sub(repl, rest, count=1)
                typed = True
                break
        if not typed and (not rest.strip() or re.match(r'^(PRIMARY|NOT|NULL|DEFAULT|UNIQUE|CHECK)\b', rest, re.I)):
            rest = 'TEXT ' + rest                 # عمود من غير نوع: SQLite بيقبل أي حاجة
    rest = re.sub(r'\bDEFAULT\s+CURRENT_TIMESTAMP\b', 'DEFAULT ' + _NOW_DEFAULT, rest, flags=re.I)
    rest = re.sub(r'\bDEFAULT\s+CURRENT_DATE\b', "DEFAULT to_char(current_date, 'YYYY-MM-DD')", rest, flags=re.I)
    rest = re.sub(r'\bDEFAULT\s+CURRENT_TIME\b', "DEFAULT to_char((now() AT TIME ZONE 'UTC'), 'HH24:MI:SS')",
                  rest, flags=re.I)
    # DEFAULT "x": في SQLite نص (لو مفيش عمود بالاسم ده)، في PostgreSQL اسم عمود.
    rest = re.sub(r'\bDEFAULT\s+"((?:[^"]|"")*)"', lambda m: "DEFAULT '" + m.group(1).replace("'", "''") + "'",
                  rest, flags=re.I)
    rest = re.sub(r'\bDEFAULT\s+(TRUE)\b', "DEFAULT 1", rest, flags=re.I)
    rest = re.sub(r'\bDEFAULT\s+(FALSE)\b', "DEFAULT 0", rest, flags=re.I)
    # DEFAULT 'x' على عمود رقم: SQLite بيقبل، PostgreSQL لأ لو مش رقم — نسيبها ونشوف
    return f'{name} {rest}'.rstrip()


def _strip_comments(sql):
    return ''.join(t if k != 'comment' else ' ' for k, t in _segments(sql))


def translate_create_table(sql):
    sql = _strip_comments(sql)
    m = re.match(r'(\s*CREATE\s+(?:TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)([\w".]+)\s*\((.*)\)\s*'
                 r'(WITHOUT\s+ROWID|STRICT)?\s*;?\s*$', sql, re.I | re.S)
    if not m:
        return sql                               # CREATE TABLE … AS SELECT
    head, name, body = m.group(1), m.group(2), m.group(3)
    defs = []
    for d in _split_top(body):
        if not d.strip():
            continue
        if _CONSTRAINT_START.match(d):
            if re.match(r'^\s*(CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\b', d, re.I):
                continue
            d = re.sub(r'\bON\s+CONFLICT\s+(REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\b', '', d, flags=re.I)
            d = re.sub(r'\bCOLLATE\s+NOCASE\b', '', d, flags=re.I)
            defs.append(d.strip())
        else:
            defs.append(_column_def(d).strip())
    return f'{head}{name} (\n    ' + ',\n    '.join(defs) + '\n)'


def translate_add_column(sql):
    sql = _strip_comments(sql)
    m = re.match(r'(\s*ALTER\s+TABLE\s+[\w".]+\s+ADD\s+(?:COLUMN\s+)?)(.*?)\s*;?\s*$', sql, re.I | re.S)
    if not m:
        return sql
    return m.group(1) + _column_def(m.group(2))


_WORD_LIKE = re.compile(r'\bLIKE\b', re.I)
_EQEQ = re.compile(r'==')
_IS_VALUE = re.compile(r'\bIS\s+(NOT\s+)?(?!NOT\b|NULL\b|TRUE\b|FALSE\b|DISTINCT\b|UNKNOWN\b)(?=[\w?:\'(])', re.I)
_LIMIT_NEG = re.compile(r'\bLIMIT\s+-1\b', re.I)
_CAST_REAL = re.compile(r'\bAS\s+REAL\b', re.I)
_CAST_INT = re.compile(r'\bCAST\s*\(', re.I)


_CURRENT = [
    (re.compile(r'\bCURRENT_TIMESTAMP\b', re.I), "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"),
    (re.compile(r'\bCURRENT_DATE\b', re.I), "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD')"),
    (re.compile(r'\bCURRENT_TIME\b', re.I), "to_char((now() AT TIME ZONE 'UTC'), 'HH24:MI:SS')"),
]


# DATE(x)/TIME(x) بقيمة واحدة: PostgreSQL بيعتبرها تحويل لنوع date/time، مش دالة SQLite — فبالاسم الكامل.
_QUALIFY = re.compile(r'(?<![\w.])(date|time|datetime|strftime|julianday)\s*\(', re.I)


def _code_rules(t):
    t = _QUALIFY.sub(lambda m: f'{COMPAT_SCHEMA}.{m.group(1).lower()}(', t)
    # CURRENT_TIMESTAMP في SQLite نص 'YYYY-MM-DD HH:MM:SS' بتوقيت UTC — مش timestamptz
    for pat, repl in _CURRENT:
        t = pat.sub(repl, t)
    t = _WORD_LIKE.sub('ILIKE', t)
    t = _EQEQ.sub('=', t)
    t = _IS_VALUE.sub(lambda m: 'IS DISTINCT FROM ' if m.group(1) else 'IS NOT DISTINCT FROM ', t)
    t = _LIMIT_NEG.sub('LIMIT ALL', t)
    t = _CAST_REAL.sub('AS DOUBLE PRECISION', t)
    return t


def statement_kind(sql):
    m = re.match(r'\s*(?:--[^\n]*\n\s*)*(\w+)(?:\s+(\w+))?(?:\s+(\w+))?', sql)
    if not m:
        return ('', '', '')
    return tuple((g or '').upper() for g in m.groups())


# ============================================================ الصفوف

class Row(tuple):
    """زي sqlite3.Row: بالرقم وبالاسم (من غير حساسية حالة الحروف)، و`keys()`، و`dict(row)`."""
    __slots__ = ()
    _keys = ()
    _index = {}

    def __new__(cls, values, keys, index):
        r = tuple.__new__(cls, values)
        return r

    def keys(self):
        return list(self._cols)

    def __getitem__(self, k):
        if isinstance(k, str):
            try:
                return tuple.__getitem__(self, self._idx[k.lower()])
            except KeyError:
                raise IndexError('No item with that key')
        return tuple.__getitem__(self, k)


def _row_class(names):
    idx = {}
    for i, n in enumerate(names):
        idx.setdefault(n.lower(), i)
    return type('Row', (Row,), {'_cols': tuple(names), '_idx': idx, '__slots__': ()})


# ============================================================ الأخطاء

def _map_error(e):
    msg = str(getattr(e, 'diag', None) and e.diag.message_primary or e)
    if psycopg is None:
        return sqlite3.DatabaseError(msg)
    if isinstance(e, (pgerr.LockNotAvailable, pgerr.DeadlockDetected)):
        return sqlite3.OperationalError('database is locked: ' + msg)
    if isinstance(e, pgerr.UniqueViolation):
        return sqlite3.IntegrityError('UNIQUE constraint failed: ' + msg)
    if isinstance(e, pgerr.NotNullViolation):
        return sqlite3.IntegrityError('NOT NULL constraint failed: ' + msg)
    if isinstance(e, pgerr.CheckViolation):
        return sqlite3.IntegrityError('CHECK constraint failed: ' + msg)
    if isinstance(e, pgerr.IntegrityError):
        return sqlite3.IntegrityError(msg)
    if isinstance(e, pgerr.UndefinedTable):
        return sqlite3.OperationalError('no such table: ' + msg)
    if isinstance(e, pgerr.UndefinedColumn):
        return sqlite3.OperationalError('no such column: ' + msg)
    if isinstance(e, pgerr.DuplicateColumn):
        return sqlite3.OperationalError('duplicate column name: ' + msg)
    if isinstance(e, (pgerr.DuplicateTable, pgerr.DuplicateObject)):
        return sqlite3.OperationalError('already exists: ' + msg)
    if isinstance(e, psycopg.InterfaceError):
        return sqlite3.ProgrammingError(msg)
    if isinstance(e, psycopg.OperationalError):
        return sqlite3.OperationalError(msg)
    return sqlite3.OperationalError(msg)


# ============================================================ الاتصال

class _NumericLoader(Loader if psycopg else object):
    """numeric ← int لو صحيح، وإلا float (SQLite مابيرجّعش Decimal)."""
    def load(self, data):
        d = decimal.Decimal(bytes(data).decode())
        return int(d) if d == d.to_integral_value() and abs(d) < 2 ** 63 else float(d)


class _BoolLoader(Loader if psycopg else object):
    def load(self, data):
        return 1 if bytes(data) in (b't', b'true') else 0


class _Catalog:
    """أعمدة ومفاتيح الجداول — لـ INSERT OR REPLACE و lastrowid و PRAGMA."""
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}

    def clear(self):
        with self.lock:
            self.cache.clear()


_catalogs = {}

import weakref as _weakref
_open = _weakref.WeakSet()          # الاتصالات المفتوحة في العملية — للقفل الجماعي (اختبارات/إيقاف)


def close_all():
    for c in list(_open):
        try:
            c.close()
        except Exception:
            pass


class PgCursor:
    def __init__(self, conn):
        self.connection = conn
        self._rows = []
        self._pos = 0
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self.arraysize = 1

    # --- sqlite3 API
    def execute(self, sql, params=()):
        self.connection._run(self, sql, params)
        return self

    def executemany(self, sql, seq):
        total = 0
        for p in seq:
            self.connection._run(self, sql, p)
            total += max(self.rowcount, 0)
        self.rowcount = total
        return self

    def executescript(self, script):
        for s in split_statements(script):
            self.connection._run(self, s, ())
        return self

    def fetchone(self):
        if self._pos < len(self._rows):
            r = self._rows[self._pos]
            self._pos += 1
            return r
        return None

    def fetchmany(self, size=None):
        n = size or self.arraysize
        out = self._rows[self._pos:self._pos + n]
        self._pos += len(out)
        return out

    def fetchall(self):
        out = self._rows[self._pos:]
        self._pos = len(self._rows)
        return out

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                return
            yield r

    def close(self):
        self._rows = []

    def setinputsizes(self, *a):
        pass

    def setoutputsize(self, *a):
        pass


class PgConnection:
    """اتصال PostgreSQL بشكل sqlite3.Connection."""

    def __init__(self, dsn, schema):
        if psycopg is None:
            raise RuntimeError('psycopg مش متسطّب — HR_DB_URL محتاج: pip install "psycopg[binary]"')
        self.dsn, self.schema = dsn, schema
        self._pg = psycopg.connect(dsn, autocommit=False)
        self._pg.adapters.register_loader('numeric', _NumericLoader)
        self._pg.adapters.register_loader('bool', _BoolLoader)
        self.row_factory = None            # البرنامج بيحطه sqlite3.Row — الصفوف دايمًا بالاسم والرقم
        self.isolation_level = ''
        self.text_factory = str
        self._sp = 0
        self.total_changes = 0
        self._last_changes = 0
        self._last_rowid = None
        self._trace = None
        ensure_schema(self._pg, schema)
        self._cat = _catalogs.setdefault((dsn, schema), _Catalog())
        _open.add(self)
        with self._pg.cursor() as c:
            c.execute(f'SET search_path TO {_q(schema)}, {COMPAT_SCHEMA}, pg_catalog')
            c.execute(_local_tz_sql())
            # زي timeout بتاع sqlite3.connect: مايستناش قفل للأبد — بعد 30 ثانية «database is locked».
            c.execute("SET lock_timeout = '30s'")
        self._pg.commit()

    # --- sqlite3 API
    def cursor(self, *a):
        return PgCursor(self)

    def execute(self, sql, params=()):
        return PgCursor(self).execute(sql, params)

    def executemany(self, sql, seq):
        return PgCursor(self).executemany(sql, seq)

    def executescript(self, script):
        c = PgCursor(self).executescript(script)
        self.commit()
        return c

    def commit(self):
        try:
            self._pg.commit()
        except Exception as e:
            raise _map_error(e)

    def rollback(self):
        try:
            self._pg.rollback()
        except Exception as e:
            raise _map_error(e)

    def close(self):
        try:
            self._pg.close()
        except Exception:
            pass

    @property
    def closed(self):
        return self._pg.closed

    @property
    def in_transaction(self):
        return self._pg.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def create_function(self, *a, **k):
        pass                              # الدوال متعرّفة في sqlite_compat

    def set_trace_callback(self, fn):
        self._trace = fn

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            self.commit()
        else:
            self.rollback()
        return False

    # --- التنفيذ
    def _run(self, cur, sql, params):
        if self._pg.closed:
            raise sqlite3.ProgrammingError('Cannot operate on a closed database.')
        kind = statement_kind(sql)
        if kind[0] == 'PRAGMA':
            return self._pragma(cur, sql)
        if kind[0] in ('BEGIN', 'END') or (kind[0] == 'COMMIT') or kind[0] == 'VACUUM':
            if kind[0] in ('COMMIT', 'END'):
                self.commit()
            cur._rows, cur.description, cur.rowcount = [], None, -1
            return
        if kind[0] == 'ROLLBACK' and kind[1] != 'TO':
            self.rollback()
            cur._rows, cur.description, cur.rowcount = [], None, -1
            return
        if kind[0] == 'ATTACH' or kind[0] == 'DETACH':
            raise sqlite3.OperationalError('ATTACH مش مدعوم على PostgreSQL')
        segs = _map_code(_dq_strings(_segments(sql)), _code_rules)
        segs = _bind(segs, params)
        text = _join(segs)
        if kind[0] == 'CREATE' and (kind[1] == 'TABLE' or (kind[1].startswith('TEMP') and kind[2] == 'TABLE')):
            text = translate_create_table(text)
            self._cat.clear()
        elif kind[0] == 'CREATE' and kind[1] in ('TRIGGER', 'TEMP', 'TEMPORARY') and 'TRIGGER' in kind:
            return self._create_trigger(cur, text)
        elif kind[0] == 'DROP' and kind[1] == 'TRIGGER':
            return self._drop_trigger(cur, text)
        elif kind[0] == 'ALTER':
            if re.search(r'\bADD\s+(COLUMN\s+)?', text, re.I) and not re.search(r'\bADD\s+CONSTRAINT\b', text, re.I):
                text = translate_add_column(text)
            self._cat.clear()
        elif kind[0] == 'DROP':
            self._cat.clear()
        elif kind[0] == 'CREATE' and kind[1] in ('INDEX', 'UNIQUE'):
            text = re.sub(r'\bCOLLATE\s+NOCASE\b', '', text, flags=re.I)
        # changes() / last_insert_rowid() / total_changes(): من آخر استعلام على الاتصال ده (زي SQLite)
        if 'changes()' in text.lower() or 'last_insert_rowid()' in text.lower():
            text = re.sub(r'\btotal_changes\(\)', str(self.total_changes), text, flags=re.I)
            text = re.sub(r'\bchanges\(\)', str(self._last_changes), text, flags=re.I)
            text = re.sub(r'\blast_insert_rowid\(\)', str(self._last_rowid or 0), text, flags=re.I)
        returning_id = False
        if kind[0] in ('INSERT', 'REPLACE'):
            text, returning_id = self._translate_insert(text)
        self._exec(cur, text, returning_id)
        if kind[0] == 'CREATE' and 'TABLE' in kind:
            self._after_create_table(text)

    def _exec(self, cur, text, returning_id=False):
        if self._trace is not None:
            try:
                self._trace(text)
            except Exception:
                pass
        self._sp += 1
        sp = f'sp{self._sp}'
        pc = self._pg.cursor()
        try:
            pc.execute(f'SAVEPOINT {sp}')
            try:
                pc.execute(text)
            except Exception as e:
                pc.execute(f'ROLLBACK TO SAVEPOINT {sp}')
                pc.execute(f'RELEASE SAVEPOINT {sp}')
                raise _map_error(e) from None
            cur.rowcount = pc.rowcount
            if pc.description is not None:
                names = [d.name for d in pc.description]
                rows = pc.fetchall()
                if returning_id:
                    cur._rows, cur.description = [], None
                    cur.lastrowid = rows[-1][0] if rows else None
                else:
                    rc = _row_class(names)
                    cur._rows = [tuple.__new__(rc, r) for r in rows]
                    cur.description = tuple((n, None, None, None, None, None, None) for n in names)
            else:
                cur._rows, cur.description = [], None
            cur._pos = 0
            pc.execute(f'RELEASE SAVEPOINT {sp}')
            if pc.description is None or returning_id:
                self._last_changes = max(cur.rowcount, 0)
            if returning_id and cur.lastrowid is not None:
                self._last_rowid = cur.lastrowid
            if cur.rowcount and cur.rowcount > 0 and (pc.description is None or returning_id):
                self.total_changes += cur.rowcount
        except sqlite3.Error:
            raise
        except Exception as e:
            raise _map_error(e) from None
        finally:
            pc.close()

    # --- INSERT
    def _table_info(self, table):
        """[(name, type, notnull, default, pk)] + مفاتيح فريدة — من الكتالوج، مخزّنة."""
        key = table.lower()
        with self._cat.lock:
            if key in self._cat.cache:
                return self._cat.cache[key]
        pc = self._pg.cursor()
        try:
            pc.execute("""SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                                 pg_get_expr(d.adbin, d.adrelid), a.attidentity
                          FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                          WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                          ORDER BY a.attnum""", (self.schema, key))
            cols = pc.fetchall()
            # كل مفتاح فريد بأعمدته — أو تعابيره (COALESCE(…)) زي ما هي، لأن ON CONFLICT محتاجها بالظبط
            pc.execute("""SELECT i.indisprimary,
                                 array_agg(pg_get_indexdef(i.indexrelid, k.ord::int, true) ORDER BY k.ord)
                          FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                          CROSS JOIN LATERAL generate_series(1, i.indnkeyatts) k(ord)
                          WHERE n.nspname = %s AND c.relname = %s AND i.indisunique AND i.indpred IS NULL
                          GROUP BY i.indexrelid, i.indisprimary""", (self.schema, key))
            uniques = [(bool(p), list(c)) for p, c in pc.fetchall()]
        finally:
            pc.close()
        info = {'cols': cols, 'uniques': uniques,
                'identity': next((c[0] for c in cols if c[4] in ('a', 'd')), None)}
        with self._cat.lock:
            self._cat.cache[key] = info
        return info

    def _translate_insert(self, text):
        m = re.match(r'\s*(INSERT\s+OR\s+(IGNORE|REPLACE|ABORT|FAIL|ROLLBACK)\s+INTO|REPLACE\s+INTO|INSERT\s+INTO)\s+'
                     r'([\w".]+)\s*(\(([^)]*)\))?', text, re.I | re.S)
        if not m:
            return text, False
        verb = m.group(1).upper()
        table = m.group(3).strip('"')
        cols = [c.strip().strip('"').lower() for c in (m.group(5) or '').split(',')] if m.group(4) else None
        mode = 'REPLACE' if 'REPLACE' in verb else ('IGNORE' if 'IGNORE' in verb else None)
        body = text[m.end():]
        head = 'INSERT INTO ' + m.group(3) + (' ' + m.group(4) if m.group(4) else '')
        has_conflict = re.search(r'\bON\s+CONFLICT\b', body, re.I)
        has_returning = re.search(r'\bRETURNING\b', body, re.I)
        try:
            info = self._table_info(table)
        except Exception:
            info = {'cols': [], 'uniques': [], 'identity': None}
        tail = ''
        if mode and not has_conflict:
            if mode == 'IGNORE':
                tail = ' ON CONFLICT DO NOTHING'
            else:
                use = cols or [c[0] for c in info['cols']]
                target = None
                def refs(k):           # الأعمدة اللي المفتاح (عمود أو تعبير) بيعتمد عليها
                    return {w.strip('"').lower() for w in re.findall(r'"[^"]+"|[A-Za-z_]\w*', k)} & set(use)
                for _pk, ukeys in sorted(info['uniques'], key=lambda u: (not u[0], len(u[1]))):
                    simple = [k for k in ukeys if re.fullmatch(r'"?\w+"?', k)]
                    if all(k.strip('"').lower() in use for k in simple) and all(refs(k) for k in ukeys):
                        target = ukeys
                        break
                if target:
                    used = set().union(*(refs(k) for k in target))
                    upd = [c for c in use if c not in used]
                    sets = ', '.join(f'{_q(c)} = EXCLUDED.{_q(c)}' for c in upd)
                    tgt = ', '.join(k if not re.fullmatch(r'\w+', k) else _q(k) for k in target)
                    tail = f' ON CONFLICT ({tgt}) ' + (f'DO UPDATE SET {sets}' if sets else 'DO NOTHING')
        returning_id = False
        if info.get('identity') and not has_returning:
            tail += f' RETURNING {_q(info["identity"])}'
            returning_id = True
        body = body.rstrip().rstrip(';')
        return head + body + tail, returning_id

    def _after_create_table(self, text):
        m = re.match(r'\s*CREATE\s+(?:TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w".]+)', text, re.I)
        if not m:
            return
        table = m.group(1).strip('"').lower()
        info = self._table_info(table)
        if not info.get('identity'):
            return
        trig = f'{table}__seqsync'[:63]
        pc = self._pg.cursor()
        try:
            pc.execute('SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass(%s) AND tgname = %s',
                       (f'{_q(self.schema)}.{_q(table)}', trig))
            if not pc.fetchone():
                pc.execute(f'CREATE TRIGGER {_q(trig)} AFTER INSERT ON {_q(self.schema)}.{_q(table)} '
                           f'FOR EACH STATEMENT EXECUTE FUNCTION {COMPAT_SCHEMA}.seq_sync({_lit(info["identity"])})')
        finally:
            pc.close()

    def _create_trigger(self, cur, text):
        """تريجر SQLite ← دالة plpgsql + تريجر PostgreSQL (FOR EACH ROW)، وجسمه بيتترجم زي أي استعلام."""
        text = _strip_comments(text)
        m = re.match(r'\s*CREATE\s+(?:TEMP\w*\s+)?TRIGGER\s+(IF\s+NOT\s+EXISTS\s+)?("(?:[^"]|"")*"|\w+)\s+'
                     r'(BEFORE|AFTER|INSTEAD\s+OF)?\s*(INSERT|DELETE|UPDATE(?:\s+OF\s+[\w",\s]+?)?)\s+ON\s+'
                     r'("(?:[^"]|"")*"|\w+)\s*(FOR\s+EACH\s+ROW\s*)?(?:WHEN\s+(.*?))?\s*\bBEGIN\b(.*)\bEND\s*;?\s*$',
                     text, re.I | re.S)
        if not m:
            raise sqlite3.OperationalError('trigger مش مفهوم: ' + text[:120])
        if_not, name, timing, event, table, _fer, when, body = m.groups()
        name = name.strip('"').replace('""', '"')
        table = table.strip('"').replace('""', '"')
        timing = (timing or 'AFTER').upper()
        event = re.sub(r'\s+', ' ', event.strip())
        pc = self._pg.cursor()
        try:
            # بالجدول (فهرس tgrelid + tgname) — مش بالاسم في كل القاعدة
            pc.execute('SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass(%s) AND tgname = %s',
                       (f'{_q(self.schema)}.{_q(table.lower() if table == table.lower() else table)}', name))
            exists = pc.fetchone() is not None
        finally:
            pc.close()
        cur._rows, cur.description, cur.rowcount = [], None, -1
        if exists:
            if if_not:
                return
            raise sqlite3.OperationalError(f'trigger {name} already exists')
        stmts = []
        for st in split_statements(body):
            st = _join(_map_code(_segments(st), _code_rules))
            k = statement_kind(st)
            if k[0] in ('INSERT', 'REPLACE'):
                st, _r = self._translate_insert(st)
                st = re.sub(r'\s+RETURNING\s+"[^"]+"\s*$', '', st)
            stmts.append(st.rstrip().rstrip(';') + ';')
        cond = _join(_map_code(_segments(when), _code_rules)) if when else 'TRUE'
        ret = 'NULL' if timing == 'AFTER' else ('OLD' if event.upper().startswith('DELETE') else 'NEW')
        fn = _q(self.schema) + '.' + _q(('fn_' + name)[:63])
        tag = '$trg$'
        src = (f'CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS {tag}\n'
               f'BEGIN\n  IF {cond} THEN\n    ' + '\n    '.join(stmts) + f'\n  END IF;\n  RETURN {ret};\nEND {tag}')
        self._exec(cur, src)
        self._exec(cur, f'CREATE TRIGGER {_q(name)} {timing} {event} ON {_q(table)} FOR EACH ROW EXECUTE FUNCTION {fn}()')

    def _drop_trigger(self, cur, text):
        m = re.match(r'\s*DROP\s+TRIGGER\s+(IF\s+EXISTS\s+)?([\w"]+)', text, re.I)
        name = m.group(2).strip('"') if m else ''
        pc = self._pg.cursor()
        try:
            pc.execute("""SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                          WHERE n.nspname = %s AND t.tgname = %s""", (self.schema, name.lower()))
            r = pc.fetchone()
        finally:
            pc.close()
        cur._rows, cur.description, cur.rowcount = [], None, -1
        if r:
            self._exec(cur, f'DROP TRIGGER IF EXISTS {_q(name.lower())} ON {_q(r[0])}')
        elif not (m and m.group(1)):
            raise sqlite3.OperationalError(f'no such trigger: {name}')

    # --- PRAGMA
    def _pragma(self, cur, sql):
        m = re.match(r'\s*PRAGMA\s+(?:(\w+)\.)?(\w+)\s*(?:\(\s*[\'"]?([\w]+)[\'"]?\s*\)|=\s*(\S+))?', sql, re.I)
        cur._rows, cur.description, cur.rowcount, cur._pos = [], None, -1, 0
        if not m:
            return
        name, arg = m.group(2).lower(), m.group(3)
        rows, names = [], []
        if name == 'table_info' and arg:
            info = self._table_info(arg)
            pkcols = next((u[1] for u in info['uniques'] if u[0]), [])
            names = ['cid', 'name', 'type', 'notnull', 'dflt_value', 'pk']
            for i, (n, t, nn, d, _ident) in enumerate(info['cols']):
                rows.append((i, n, _sqlite_type(t), 1 if nn else 0, d,
                             (pkcols.index(n) + 1) if n in pkcols else 0))
        elif name == 'index_list' and arg:
            names = ['seq', 'name', 'unique', 'origin', 'partial']
            pc = self._pg.cursor()
            try:
                pc.execute("""SELECT ic.relname, i.indisunique, i.indisprimary FROM pg_index i
                              JOIN pg_class c ON c.oid = i.indrelid JOIN pg_class ic ON ic.oid = i.indexrelid
                              JOIN pg_namespace n ON n.oid = c.relnamespace
                              WHERE n.nspname = %s AND c.relname = %s""", (self.schema, arg.lower()))
                rows = [(i, r[0], 1 if r[1] else 0, 'pk' if r[2] else 'c', 0) for i, r in enumerate(pc.fetchall())]
            finally:
                pc.close()
        elif name in ('integrity_check', 'quick_check'):
            names, rows = [name], [('ok',)]
        elif name == 'journal_mode':
            names, rows = ['journal_mode'], [('wal',)]
        elif name == 'foreign_keys':
            names, rows = ['foreign_keys'], [(0,)]
        elif name == 'database_list':
            # الكود بيستخدم «الملف» مفتاحًا للقاعدة (كاش لكل قاعدة) — فمفتاح فريد لكل schema
            names, rows = ['seq', 'name', 'file'], [(0, 'main', f'pg:{self.schema}')]
        elif name in ('user_version', 'schema_version'):
            names, rows = [name], [(0,)]
        if names:
            rc = _row_class(names)
            cur._rows = [tuple.__new__(rc, r) for r in rows]
            cur.description = tuple((n, None, None, None, None, None, None) for n in names)


def _sqlite_type(t):
    t = (t or '').lower()
    if t in ('bigint', 'integer', 'smallint'):
        return 'INTEGER'
    if t.startswith('double') or t == 'real':
        return 'REAL'
    if t == 'bytea':
        return 'BLOB'
    return 'TEXT'


def _q(ident):
    return '"' + str(ident).replace('"', '""') + '"'


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def _local_tz_sql():
    """منطقة الجلسة = منطقة الجهاز (datetime('now','localtime') زي SQLite)."""
    off = _dt.datetime.now().astimezone().utcoffset() or _dt.timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = '+' if mins >= 0 else '-'
    mins = abs(mins)
    # بصيغة POSIX الإشارة معكوسة؛ INTERVAL أوضح
    return f"SET TIME ZONE INTERVAL '{sign}{mins // 60:02d}:{mins % 60:02d}' HOUR TO MINUTE"


# ============================================================ دوال SQLite في PostgreSQL

_COMPAT_SQL = r"""
CREATE SCHEMA IF NOT EXISTS sqlite_compat;

-- النص ← timestamp (زي SQLite: 'YYYY-MM-DD[ T]HH:MM[:SS[.fff]]'، أو 'now'، أو رقم يوليان)
CREATE OR REPLACE FUNCTION sqlite_compat._ts(v text) RETURNS timestamp LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE s text := btrim(coalesce(v, ''));
BEGIN
  IF s = '' THEN RETURN NULL; END IF;
  IF lower(s) = 'now' THEN RETURN (now() AT TIME ZONE 'UTC'); END IF;
  IF s ~ '^\d{4}-\d{2}-\d{2}$' THEN RETURN s::timestamp; END IF;
  IF s ~ '^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$' THEN
    RETURN replace(regexp_replace(s, '(Z|[+-]\d{2}:?\d{2})$', ''), 'T', ' ')::timestamp;
  END IF;
  IF s ~ '^\d{2}:\d{2}(:\d{2}(\.\d+)?)?$' THEN RETURN ('2000-01-01 ' || s)::timestamp; END IF;
  IF s ~ '^-?\d+(\.\d+)?$' THEN       -- يوم يولياني
    RETURN timestamp '1970-01-01' + ((s::double precision - 2440587.5) * interval '1 day');
  END IF;
  RETURN NULL;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat._mods(t timestamp, mods text[]) RETURNS timestamp LANGUAGE plpgsql STABLE AS $$
DECLARE m text; n double precision; u text; unixepoch boolean := false;
BEGIN
  IF t IS NULL THEN RETURN NULL; END IF;
  IF mods IS NULL THEN RETURN t; END IF;
  FOREACH m IN ARRAY mods LOOP
    m := lower(btrim(coalesce(m, '')));
    IF m = '' THEN CONTINUE;
    ELSIF m = 'localtime' THEN t := (t AT TIME ZONE 'UTC') AT TIME ZONE current_setting('TimeZone');
    ELSIF m = 'utc' THEN t := (t AT TIME ZONE current_setting('TimeZone')) AT TIME ZONE 'UTC';
    ELSIF m = 'start of month' THEN t := date_trunc('month', t);
    ELSIF m = 'start of year' THEN t := date_trunc('year', t);
    ELSIF m = 'start of day' THEN t := date_trunc('day', t);
    ELSIF m ~ '^weekday \d$' THEN
      t := date_trunc('day', t) + (((substr(m, 9)::int - extract(dow FROM t)::int) + 7) % 7) * interval '1 day';
    ELSIF m ~ '^[+-]?\d+(\.\d+)?\s+\w+$' THEN
      n := split_part(regexp_replace(m, '\s+', ' '), ' ', 1)::double precision;
      u := rtrim(split_part(regexp_replace(m, '\s+', ' '), ' ', 2), 's');
      IF u = 'day' THEN t := t + n * interval '1 day';
      ELSIF u = 'hour' THEN t := t + n * interval '1 hour';
      ELSIF u = 'minute' THEN t := t + n * interval '1 minute';
      ELSIF u = 'second' THEN t := t + n * interval '1 second';
      ELSIF u = 'month' THEN t := t + (n::int) * interval '1 month';
      ELSIF u = 'year' THEN t := t + (n::int) * interval '1 year';
      ELSE RETURN NULL; END IF;
    ELSE RETURN NULL;
    END IF;
  END LOOP;
  RETURN t;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat.datetime(v text, VARIADIC mods text[] DEFAULT '{}') RETURNS text
  LANGUAGE sql STABLE AS $$ SELECT to_char(sqlite_compat._mods(sqlite_compat._ts(v), mods), 'YYYY-MM-DD HH24:MI:SS') $$;
CREATE OR REPLACE FUNCTION sqlite_compat.date(v text, VARIADIC mods text[] DEFAULT '{}') RETURNS text
  LANGUAGE sql STABLE AS $$ SELECT to_char(sqlite_compat._mods(sqlite_compat._ts(v), mods), 'YYYY-MM-DD') $$;
CREATE OR REPLACE FUNCTION sqlite_compat.time(v text, VARIADIC mods text[] DEFAULT '{}') RETURNS text
  LANGUAGE sql STABLE AS $$ SELECT to_char(sqlite_compat._mods(sqlite_compat._ts(v), mods), 'HH24:MI:SS') $$;
CREATE OR REPLACE FUNCTION sqlite_compat.julianday(v text, VARIADIC mods text[] DEFAULT '{}') RETURNS double precision
  LANGUAGE sql STABLE AS $$
  SELECT extract(epoch FROM sqlite_compat._mods(sqlite_compat._ts(v), mods))::double precision / 86400.0 + 2440587.5 $$;

CREATE OR REPLACE FUNCTION sqlite_compat.strftime(fmt text, v text, VARIADIC mods text[] DEFAULT '{}') RETURNS text
  LANGUAGE plpgsql STABLE AS $$
DECLARE t timestamp := sqlite_compat._mods(sqlite_compat._ts(v), mods); out text := ''; i int := 1; c text;
BEGIN
  IF t IS NULL OR fmt IS NULL THEN RETURN NULL; END IF;
  WHILE i <= length(fmt) LOOP
    c := substr(fmt, i, 1);
    IF c = '%' AND i < length(fmt) THEN
      i := i + 1; c := substr(fmt, i, 1);
      out := out || CASE c
        WHEN 'Y' THEN to_char(t, 'YYYY') WHEN 'm' THEN to_char(t, 'MM') WHEN 'd' THEN to_char(t, 'DD')
        WHEN 'H' THEN to_char(t, 'HH24') WHEN 'M' THEN to_char(t, 'MI') WHEN 'S' THEN to_char(t, 'SS')
        WHEN 'f' THEN to_char(t, 'SS.MS') WHEN 'w' THEN extract(dow FROM t)::int::text
        WHEN 'u' THEN extract(isodow FROM t)::int::text
        WHEN 'j' THEN to_char(t, 'DDD') WHEN 'W' THEN lpad(((extract(doy FROM t)::int + 6 - ((extract(dow FROM t)::int + 6) % 7)) / 7)::text, 2, '0')
        WHEN 's' THEN floor(extract(epoch FROM t))::bigint::text
        WHEN 'J' THEN (extract(epoch FROM t)::double precision / 86400.0 + 2440587.5)::text
        WHEN '%' THEN '%' ELSE '%' || c END;
    ELSE
      out := out || c;
    END IF;
    i := i + 1;
  END LOOP;
  RETURN out;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat.ifnull(a anycompatible, b anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT coalesce(a, b) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.iif(c boolean, a anycompatible, b anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN c THEN a ELSE b END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.instr(a text, b text) RETURNS int
  LANGUAGE sql IMMUTABLE AS $$ SELECT strpos(a, b) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.max(a anycompatible, b anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN a IS NULL OR b IS NULL THEN NULL ELSE greatest(a, b) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.max(a anycompatible, b anycompatible, c anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN a IS NULL OR b IS NULL OR c IS NULL THEN NULL ELSE greatest(a, b, c) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.min(a anycompatible, b anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN a IS NULL OR b IS NULL THEN NULL ELSE least(a, b) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.min(a anycompatible, b anycompatible, c anycompatible) RETURNS anycompatible
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN a IS NULL OR b IS NULL OR c IS NULL THEN NULL ELSE least(a, b, c) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.round(a double precision, n integer) RETURNS double precision
  LANGUAGE sql IMMUTABLE AS $$ SELECT round(a::numeric, n)::double precision $$;
CREATE OR REPLACE FUNCTION sqlite_compat.round(a double precision, n bigint) RETURNS double precision
  LANGUAGE sql IMMUTABLE AS $$ SELECT round(a::numeric, n::int)::double precision $$;
CREATE OR REPLACE FUNCTION sqlite_compat.substr(a bigint, s bigint) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT substr(a::text, s::int) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.substr(a bigint, s bigint, l bigint) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT substr(a::text, s::int, l::int) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.length(a bigint) RETURNS int
  LANGUAGE sql IMMUTABLE AS $$ SELECT length(a::text) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.length(a double precision) RETURNS int
  LANGUAGE sql IMMUTABLE AS $$ SELECT length(a::text) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.typeof(a anyelement) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN a IS NULL THEN 'null'
     WHEN pg_typeof(a)::text IN ('bigint','integer','smallint') THEN 'integer'
     WHEN pg_typeof(a)::text IN ('double precision','real','numeric') THEN 'real'
     WHEN pg_typeof(a)::text = 'bytea' THEN 'blob' ELSE 'text' END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.total(a double precision) RETURNS double precision
  LANGUAGE sql IMMUTABLE AS $$ SELECT coalesce(a, 0) $$;

CREATE OR REPLACE FUNCTION sqlite_compat._gc(acc text, v text, sep text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN v IS NULL THEN acc WHEN acc IS NULL THEN v ELSE acc || sep || v END $$;
CREATE OR REPLACE FUNCTION sqlite_compat._gc1(acc text, v text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT sqlite_compat._gc(acc, v, ',') $$;
CREATE OR REPLACE FUNCTION sqlite_compat._gc1a(acc text, v anyelement) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$ SELECT sqlite_compat._gc(acc, v::text, ',') $$;
DO $$ BEGIN
  CREATE AGGREGATE sqlite_compat.group_concat(text) (SFUNC = sqlite_compat._gc1, STYPE = text);
EXCEPTION WHEN duplicate_function THEN NULL; END $$;
DO $$ BEGIN
  CREATE AGGREGATE sqlite_compat.group_concat(text, text) (SFUNC = sqlite_compat._gc, STYPE = text);
EXCEPTION WHEN duplicate_function THEN NULL; END $$;
DO $$ BEGIN
  CREATE AGGREGATE sqlite_compat.group_concat(anyelement) (SFUNC = sqlite_compat._gc1a, STYPE = text);
EXCEPTION WHEN duplicate_function THEN NULL; END $$;

-- id صريح في INSERT (زي SQLite): العدّاد يتقدّم بعده، عشان الـ INSERT اللي بعده مايتصادمش.
CREATE OR REPLACE FUNCTION sqlite_compat.seq_sync() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE seq text; mx bigint; lv bigint; col text := TG_ARGV[0];
BEGIN
  seq := pg_get_serial_sequence(quote_ident(TG_TABLE_SCHEMA) || '.' || quote_ident(TG_TABLE_NAME), col);
  IF seq IS NULL THEN RETURN NULL; END IF;
  EXECUTE format('SELECT max(%I) FROM %I.%I', col, TG_TABLE_SCHEMA, TG_TABLE_NAME) INTO mx;
  EXECUTE format('SELECT last_value FROM %s', seq) INTO lv;
  IF mx IS NOT NULL AND mx > lv THEN PERFORM setval(seq, mx); END IF;
  RETURN NULL;
END $$;

-- sqlite_master للـ schema الحالية. الاسم بنوعه (name) عشان الفلترة بالفهرس، وتعريف الجدول (sql)
-- دالة بتتحسب للصفوف المطلوبة بس — كانت بتتحسب لكل الجداول في كل سؤال «الجدول ده موجود؟».
CREATE OR REPLACE FUNCTION sqlite_compat._table_sql(rel oid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT 'CREATE TABLE ' || c.relname || ' (' || coalesce((SELECT string_agg(a.attname || ' ' ||
           CASE format_type(a.atttypid, a.atttypmod) WHEN 'bigint' THEN 'INTEGER' WHEN 'text' THEN 'TEXT'
                WHEN 'double precision' THEN 'REAL' WHEN 'bytea' THEN 'BLOB'
                ELSE upper(format_type(a.atttypid, a.atttypmod)) END, ', ' ORDER BY a.attnum)
         FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped), '') || ')'
  FROM pg_class c WHERE c.oid = rel $$;
DROP VIEW IF EXISTS sqlite_compat.sqlite_master;
CREATE VIEW sqlite_compat.sqlite_master AS
  SELECT 'table'::text AS type, c.relname AS name, c.relname AS tbl_name, 0 AS rootpage,
         sqlite_compat._table_sql(c.oid) AS sql
  FROM pg_class c WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'view', c.relname, c.relname, 0, ('CREATE VIEW ' || c.relname || ' AS ' || pg_get_viewdef(c.oid))
  FROM pg_class c WHERE c.relnamespace = current_schema()::regnamespace AND c.relkind = 'v'
  UNION ALL
  SELECT 'index', ic.relname, c.relname, 0, pg_get_indexdef(ic.oid)
  FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid JOIN pg_class c ON c.oid = i.indrelid
  WHERE ic.relnamespace = current_schema()::regnamespace
  UNION ALL
  SELECT 'trigger', t.tgname, c.relname, 0, pg_get_triggerdef(t.oid)
  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
  WHERE c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal AND t.tgname NOT LIKE '%\_\_seqsync';
"""

_ready = set()
_ready_lock = threading.Lock()


def ensure_schema(pg, schema):
    """sqlite_compat (مرة لكل قاعدة) والـ schema بتاعة الشركة (مرة لكل schema)."""
    key = (pg.info.dsn, schema)
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with pg.cursor() as c:
            c.execute('SELECT pg_advisory_xact_lock(727274)')     # اتصالين مع بعض مايعملوش نفس الحاجة
            c.execute("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                      "WHERE n.nspname = 'sqlite_compat' AND p.proname = 'seq_sync'")
            if not c.fetchone() or _COMPAT_VERSION_CHANGED(c):
                c.execute(_COMPAT_SQL)
                c.execute("COMMENT ON SCHEMA sqlite_compat IS " + _lit(_COMPAT_VERSION))
            c.execute(f'CREATE SCHEMA IF NOT EXISTS {_q(schema)}')
        pg.commit()
        _ready.add(key)


_COMPAT_VERSION = 'compat-2'


def _COMPAT_VERSION_CHANGED(c):
    c.execute("SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = 'sqlite_compat'")
    r = c.fetchone()
    return not r or r[0] != _COMPAT_VERSION


# ============================================================ الإعداد

def pg_url():
    import os
    u = (os.environ.get('HR_DB_URL') or '').strip()
    return u if u.startswith(('postgres://', 'postgresql://')) else ''


def schema_for(path):
    """اسم الـ schema لقاعدة: من HR_DB_SCHEMA، أو من مسار ملف SQLite (كل HR_DATA_DIR = schema)."""
    import hashlib
    import os
    s = (os.environ.get('HR_DB_SCHEMA') or '').strip()
    if s:
        return re.sub(r'[^a-z0-9_]', '_', s.lower())[:60]
    return 't_' + hashlib.sha1(os.path.abspath(str(path)).encode()).hexdigest()[:16]


def connect(path, timeout=30):
    """sqlite3.connect(path) — أو PostgreSQL لو HR_DB_URL متظبط."""
    url = pg_url()
    if url:
        return PgConnection(url, schema_for(path))
    return sqlite3.connect(path, timeout=timeout)
