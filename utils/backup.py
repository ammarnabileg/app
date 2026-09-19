# -*- coding: utf-8 -*-
"""نسخ قاعدة النظام واستعادتها من داخل النظام نفسه.

## لماذا داخل البرنامج لا خارجه

العميل المستضيف ذاتيًّا يُقال له «النسخ الاحتياطي مسؤوليتك» — وهو محقّ في
أن يسأل: من أين؟ نسخ ملف قاعدة SQLite وهو يعمل ليس آمنًا: قد يُنسخ
النصف الأول قبل معاملة والنصف الثاني بعدها، فيخرج ملف لا يفتح. والحلّ
ليس إيقاف النظام، بل واجهة النسخ الخاصة بـSQLite، وهي تأخذ لقطة متّسقة
والنظام يعمل.

## ثلاث صيغ، لكلٍّ موضعها

  * `.db` كامل  — نسخة كل شيء بلقطة متّسقة. هذه ما يُحتفظ به.
  * `.sql` كامل — نصّ يمكن قراءته وتعديله ونقله إلى نسخة أخرى.
  * `.sql` انتقائي — جداول بعينها، لنقل الموظفين وحدهم مثلًا.

## الاستعادة تُفحَص قبل أن تُنفَّذ

استعادة ملفٍ عمره شهر تمحو شهرًا من الحضور. فالملف يُقرأ أولًا ويُعرض ما
فيه جدولًا جدولًا بعدد صفوفه، ثم يختار المستخدم. وقبل كل استعادة تُؤخذ
نسخة تلقائية كاملة — فالتراجع ممكن دائمًا حتى لو اختار المستخدم خطأ.
"""
import os
import re
import shutil
import sqlite3
from datetime import datetime

from utils.db import DB_PATH, get_db_connection


def get_db_path():
    """مسار ملف القاعدة. يُقرأ من utils.db لئلا يصير للمسار مصدران."""
    return DB_PATH

# جداول يعرف المستخدم ما فيها قبل أن يشاركها: رواتب وسلف وحسابات دخول.
SENSITIVE = {'users', 'salaries', 'employee_loans', 'payroll_runs', 'license_settings'}

# لا تُستعاد ولا تُصدَّر مع «كل شيء»: ترخيص هذا التركيب ومعرّفه، وهما
# مربوطان بهذا الجهاز. استعادتها من نسخة جهاز آخر تُبطل الترخيص القائم.
BOUND_TO_MACHINE = {'license_settings'}


def _data_dir():
    return os.path.dirname(get_db_path())


def human_size(n):
    n = int(n or 0)
    if n <= 0:
        return '0 ب'
    units = ['ب', 'ك.ب', 'م.ب', 'ج.ب']
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f'{round(n, 1 if i > 1 else 0)} {units[i]}'


# --------------------------------------------------------------- استعراض

def list_tables():
    """كل الجداول بأعداد صفوفها، مرتَّبة بالاسم."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    out = []
    for r in rows:
        name = r[0] if not hasattr(r, 'keys') else r['name']
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            count = -1
        out.append({
            'name': name,
            'rows': count,
            'sensitive': name in SENSITIVE,
            'bound': name in BOUND_TO_MACHINE,
        })
    return out


def database_size():
    try:
        return os.path.getsize(get_db_path())
    except OSError:
        return 0


# ---------------------------------------------------------------- النسخ

def snapshot_file(dest_path):
    """لقطة متّسقة من القاعدة كاملةً، والنظام يعمل.

    عبر واجهة النسخ في SQLite لا shutil.copy: النسخ المباشر لملفٍ تُكتب
    فيه معاملة الآن يعطي ملفًا نصفُه قبلها ونصفُه بعدها — ولا يُكتشف ذلك
    إلا يوم الحاجة إليه.
    """
    src = sqlite3.connect(get_db_path())
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest_path


def default_name(ext):
    return f"hr-backup-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.{ext}"


# -------------------------------------------------- الملفات خارج القاعدة

def _attachment_dirs():
    """مجلدات فيها ملفات لا تعيشُ في القاعدة.

    صور زيارات المناديب مثالها: القاعدة تحمل بصمة كل صورة ومكانها
    وزمنها، والصورة نفسها ملفٌّ على القرص. فنسخةٌ للقاعدة وحدها تحفظ
    الادّعاء وتترك الدليل — وهو أسوأ من الاثنين معًا، لأن من يفتحها
    يظنّ أنه نسخ كل شيء.
    """
    base = _data_dir()
    return [('field_photos', os.path.join(base, 'field_photos'))]


def attachments_size():
    total, count = 0, 0
    for _label, path in _attachment_dirs():
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    count += 1
                except OSError:
                    pass
    return total, count


def archive_zip(dest_path, with_attachments=True):
    """أرشيف كامل: لقطة القاعدة + الملفات المرتبطة بها.

    اللقطة لا الملف الحيّ — للسبب نفسه في snapshot_file.
    """
    import zipfile

    tmp_db = dest_path + '.db.part'
    snapshot_file(tmp_db)

    tmp_zip = dest_path + '.part'
    try:
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED,
                             allowZip64=True) as z:
            z.write(tmp_db, 'hr_system.db')
            if with_attachments:
                for label, path in _attachment_dirs():
                    for root, _dirs, files in os.walk(path):
                        for f in files:
                            full = os.path.join(root, f)
                            rel = os.path.join(label, os.path.relpath(full, path))
                            try:
                                z.write(full, rel.replace(os.sep, '/'))
                            except OSError:
                                continue      # ملف اختفى أثناء النسخ
        os.replace(tmp_zip, dest_path)        # لا أرشيف نصفه
    finally:
        for p in (tmp_db, tmp_zip):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return dest_path


def archive_fits(needed_bytes):
    """(يتّسع؟، المتاح). القرص الممتلئ يقطع الكتابة في منتصفها."""
    try:
        free = shutil.disk_usage(_data_dir()).free
    except (OSError, NameError):
        return True, 0
    return free > needed_bytes * 1.2 + (50 * 1024 * 1024), free


def dump_sql(tables=None, with_data=True):
    """يولّد نسخة SQL نصيّة، جدولًا جدولًا.

    مولِّد لا نصّ واحد: قاعدة فيها سنوات من البصمات تتجاوز الذاكرة
    المتاحة لعملية PHP/بايثون على استضافة عادية.
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        available = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

        chosen = [t for t in available if (tables is None or t in tables)]
        if not chosen:
            raise ValueError('لم يُختَر أي جدول موجود.')

        yield '-- نسخة نظام الموارد البشرية\n'
        yield f'-- التاريخ: {datetime.now():%Y-%m-%d %H:%M:%S}\n'
        yield f'-- الجداول: {len(chosen)}' + (' (بالبيانات)\n' if with_data else ' (البنية فقط)\n')
        yield '--\n-- تحذير: قد يحوي رواتب وبيانات دخول. احفظه في مكان تملكه.\n\n'
        yield 'PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n'

        for name in chosen:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (name,)).fetchone()
            yield f'\n-- ---------- {name} ----------\n'
            yield f'DROP TABLE IF EXISTS "{name}";\n'
            if ddl and ddl['sql']:
                yield ddl['sql'].rstrip().rstrip(';') + ';\n'

            if with_data:
                for line in _insert_lines(conn, name):
                    yield line

            # الفهارس بعد البيانات: بناؤها على جدول ممتلئ أسرع من
            # تحديثها صفًّا صفًّا أثناء الإدراج.
            for idx in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND sql IS NOT NULL", (name,)):
                yield idx['sql'].rstrip().rstrip(';') + ';\n'

        yield '\nCOMMIT;\nPRAGMA foreign_keys=ON;\n'
    finally:
        conn.close()


def _insert_lines(conn, table):
    cur = conn.execute(f'SELECT * FROM "{table}"')
    cols = None
    batch = []
    total = 0

    for row in cur:
        if cols is None:
            cols = '(' + ', '.join(f'"{k}"' for k in row.keys()) + ')'
        batch.append('(' + ', '.join(_literal(v) for v in tuple(row)) + ')')
        total += 1
        if len(batch) >= 200:
            yield f'INSERT INTO "{table}" {cols} VALUES\n' + ',\n'.join(batch) + ';\n'
            batch = []

    if batch:
        yield f'INSERT INTO "{table}" {cols} VALUES\n' + ',\n'.join(batch) + ';\n'
    if total == 0:
        yield '-- (فارغ)\n'


def _literal(v):
    if v is None:
        return 'NULL'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    return "'" + str(v).replace("'", "''") + "'"


# ------------------------------------------------------- فحص ملف مرفوع

def inspect(path):
    """يقول ما في الملف دون تنفيذ حرف منه.

    يقبل الصيغتين: ملف SQLite كامل، أو نصّ SQL.
    """
    if not os.path.isfile(path):
        raise ValueError('الملف غير موجود.')

    size = os.path.getsize(path)

    with open(path, 'rb') as fh:
        head = fh.read(16)

    if head.startswith(b'SQLite format 3'):
        return _inspect_db(path, size)
    return _inspect_sql(path, size)


def _inspect_db(path, size):
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        tables = []
        for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            try:
                rows = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                rows = -1
            tables.append({'name': name, 'rows': rows, 'create': True,
                           'sensitive': name in SENSITIVE, 'bound': name in BOUND_TO_MACHINE})
    finally:
        conn.close()

    warnings = []
    if not tables:
        warnings.append('ملف قاعدة لكنه بلا جداول.')

    return {'kind': 'db', 'bytes': size, 'tables': tables,
            'statements': 0, 'warnings': warnings}


_CREATE = re.compile(r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["\'`\[]?([A-Za-z0-9_]+)', re.I)
_DROP = re.compile(r'^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?["\'`\[]?([A-Za-z0-9_]+)', re.I)
_INSERT = re.compile(r'^(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+["\'`\[]?([A-Za-z0-9_]+)', re.I)
_INDEX = re.compile(r'^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                    r'["\'`\[]?[A-Za-z0-9_]+["\'`\]]?\s+ON\s+["\'`\[]?([A-Za-z0-9_]+)', re.I)
_DELETE = re.compile(r'^DELETE\s+FROM\s+["\'`\[]?([A-Za-z0-9_]+)', re.I)
_UPDATE = re.compile(r'^UPDATE\s+["\'`\[]?([A-Za-z0-9_]+)', re.I)

# جمل لا تخصّ جدولًا بعينه ويصحّ تنفيذها كما هي.
_SAFE_GLOBAL = re.compile(r'^(?:PRAGMA|BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|ANALYZE|VACUUM)\b', re.I)

# تعليق متصدّر أو مسافات. بدون نزعها كان `-- عنوان` قبل الجملة يمنع
# مطابقة اسم الجدول، فتُعامَل الجملة كجملة عامّة وتُنفَّذ مهما كان
# اختيار المستخدم — وهذا ما أسقط جدولًا لم يُختَر في أول تجربة.
_LEAD = re.compile(r'^(?:\s+|--[^\n]*\n?|/\*.*?\*/)+', re.S)


def _strip_lead(sql):
    return _LEAD.sub('', sql, count=1).lstrip()


def _table_of(sql):
    """الجدول الذي تخصّه الجملة، أو None لجملة عامّة.

    تُرجِع ('', ...) للجملة المجهولة كي يميّزها المتّصل عن الجملة
    العامّة: المجهول يُترك ولا يُنفَّذ.
    """
    probe = _strip_lead(sql)
    for rx in (_CREATE, _DROP, _INSERT, _INDEX, _DELETE, _UPDATE):
        m = rx.match(probe)
        if m:
            return m.group(1)
    if _SAFE_GLOBAL.match(probe) or probe == '':
        return None
    return ''      # مجهولة


def _inspect_sql(path, size):
    found = {}
    statements = 0

    for sql in _statements(path):
        statements += 1
        probe = _strip_lead(sql)
        for rx, kind in ((_CREATE, 'create'), (_DROP, 'drop'), (_INSERT, 'insert')):
            m = rx.match(probe)
            if not m:
                continue
            name = m.group(1)
            e = found.setdefault(name, {'name': name, 'rows': 0, 'create': False,
                                        'drop': False,
                                        'sensitive': name in SENSITIVE,
                                        'bound': name in BOUND_TO_MACHINE})
            if kind == 'create':
                e['create'] = True
            elif kind == 'drop':
                e['drop'] = True
            else:
                e['rows'] += _count_groups(probe)
            break

    warnings = []
    if not found:
        warnings.append('لم يُعثر على أي جدول. تأكّد أن الملف .sql أو .db وليس مضغوطًا.')

    return {'kind': 'sql', 'bytes': size, 'statements': statements,
            'tables': sorted(found.values(), key=lambda t: t['name']),
            'warnings': warnings}


def _statements(path):
    """يقسّم ملف SQL إلى جُمل، محترمًا النصوص والتهريب.

    فاصلة منقوطة داخل نصّ — وهي شائعة في العناوين والملاحظات — كانت
    تقسم الجملة نصفين لو قُسّم الملف بـsplit(';').
    """
    buf = []
    in_str = None
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            for ch in chunk:
                if in_str:
                    buf.append(ch)
                    if ch == in_str:
                        in_str = None
                    continue
                if ch in ("'", '"'):
                    in_str = ch
                    buf.append(ch)
                    continue
                if ch == ';':
                    s = ''.join(buf).strip()
                    buf = []
                    if s:
                        yield s
                    continue
                buf.append(ch)
    s = ''.join(buf).strip()
    if s:
        yield s


def _count_groups(sql):
    """عدد مجموعات (...) بعد VALUES، بعدّ الأقواس خارج النصوص."""
    i = sql.upper().find(' VALUES')
    if i < 0:
        return 0
    depth = groups = 0
    in_str = None
    for ch in sql[i:]:
        if in_str:
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch == '(':
            if depth == 0:
                groups += 1
            depth += 1
        elif ch == ')':
            depth -= 1
    return groups


# ----------------------------------------------------------- الاستعادة

def restore(path, tables, keep_license=True):
    """يستعيد الجداول المختارة، بعد أخذ نسخة تلقائية.

    keep_license: لا تُلمس بيانات ترخيص هذا التركيب. النسخة قد تكون من
    جهاز آخر، وترخيصها مربوط بذلك الجهاز — استعادتها تُبطل الترخيص
    العامل هنا وتُقفل النظام على صاحبه.

    يُرجِع dict فيه ما نُفِّذ وما تُرك ومسار النسخة الاحتياطية.
    """
    if not tables:
        raise ValueError('لم يُختَر أي جدول للاستعادة.')

    for t in tables:
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', t):
            raise ValueError(f'اسم جدول غير صالح: {t}')

    wanted = set(tables)
    if keep_license:
        wanted -= BOUND_TO_MACHINE

    if not wanted:
        raise ValueError('لم يبقَ جدول للاستعادة بعد استثناء بيانات الترخيص.')

    # نسخة قبل أي كتابة. هذه هي التي تجعل الخطأ قابلًا للتراجع.
    safety = os.path.join(_data_dir(),
                          f"before-restore-{datetime.now():%Y-%m-%d-%H%M%S}.db")
    snapshot_file(safety)

    conn = get_db_connection()
    ran = skipped = 0
    errors = []

    kind = inspect(path)['kind']

    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        if kind == 'db':
            ran, skipped, errors = _restore_from_db(conn, path, wanted)
        else:
            for sql in _statements(path):
                name = _table_of(sql)

                # الفشل مغلقًا. ثلاث حالات لا اثنتان:
                #   None  جملة عامّة معروفة (PRAGMA/BEGIN…) تُنفَّذ
                #   ''    جملة لم تُفهَم — تُترك. ملفٌ مرفوع قد يحمل
                #         DELETE FROM employees، ولا يصحّ أن يُنفَّذ لأننا
                #         عجزنا عن تصنيفه
                #   اسم   يُنفَّذ إن كان مختارًا، ويُترك إن لم يكن
                if name == '' or (name is not None and name not in wanted):
                    skipped += 1
                    continue
                try:
                    conn.execute(sql)
                    ran += 1
                except sqlite3.Error as e:
                    errors.append(str(e)[:200])
                    if len(errors) > 50:
                        errors.append('… وأخطاء أخرى، أُوقف التسجيل.')
                        break
        conn.commit()
    finally:
        conn.execute('PRAGMA foreign_keys=ON')

    # الترحيل بعد الاستعادة — وهذا ليس احتياطًا.
    #
    # استعادة ملف قاعدة تُسقط الجدول القائم وتعيد بناءه **بمخطَّط
    # الملف المرفوع** (`_restore_from_db` أدناه: DROP ثم DDL المصدر).
    # فنسخةٌ من إصدارٍ قديم تعيد الجداول كما كانت يومها — بلا الأعمدة
    # التي أضافتها الترحيلات بعدها.
    #
    # والنتيجة ليست نقصًا صامتًا: الشيفرة الحالية تسأل عن تلك الأعمدة،
    # فيردّ SQLite «no such column» وتصير كل صفحة تلمسها خطأ 500. وهذا
    # ما وقع فعلًا — استعادة نسخة من v59 تُسقط `employees.work_mode`
    # و`shift_types.is_split` و`fingerprint_devices.branch_id`،
    # و`work_mode` تُقرأ في مسارات كثيرة.
    #
    # ونسخةٌ حديثة لا تُظهر العطب لأن مخطَّطها هو المخطَّط الحالي —
    # فيبدو النظام سليمًا عند من يجرّب بنسخةٍ أخذها اليوم.
    #
    # `init_db` هي نفسها ما يُشغَّل عند كل إقلاع، فنداؤها هنا آمنٌ
    # بحكم أنه يقع في كل مرة أصلًا.
    migrated = True
    try:
        from utils.db import init_db
        init_db()
    except Exception as e:                      # pragma: no cover - حارس
        migrated = False
        errors.append(f'تعذّر ترحيل المخطَّط بعد الاستعادة: {str(e)[:180]}')

    # الاستعادة استبدلت محتوى الجداول، فما رُفع إلى السحابة يخصّ قاعدةً
    # أخرى. والصفوف المستعادة لم تمرّ بمُشغِّلٍ فلا أثر لها في الدفتر —
    # فمتابعةُ الرفع من حيث وقف تترك السحابةَ على بياناتٍ لم تعد قائمة.
    # إعادةُ المشي الأوّل تُصالحها من جديد.
    try:
        from utils.cloud_outbox import reset_baseline
        reset_baseline(get_db_connection())
    except Exception as e:                          # pragma: no cover - حارس
        errors.append(f'تعذّر تصفير مؤشّر الرفع السحابي: {str(e)[:120]}')

    return {'ok': not errors, 'ran': ran, 'skipped': skipped,
            'tables': sorted(wanted), 'errors': errors,
            'migrated': migrated,
            'safety_copy': os.path.basename(safety)}


def _restore_from_db(conn, path, wanted):
    """ينسخ الجداول المختارة من ملف قاعدة مرفوع."""
    ran = skipped = 0
    errors = []

    conn.execute("ATTACH DATABASE ? AS src", (path,))
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM src.sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]

        for name in names:
            if name not in wanted:
                skipped += 1
                continue
            try:
                ddl = conn.execute(
                    "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                    (name,)).fetchone()
                conn.execute(f'DROP TABLE IF EXISTS main."{name}"')
                if ddl and ddl[0]:
                    conn.execute(ddl[0])
                conn.execute(f'INSERT INTO main."{name}" SELECT * FROM src."{name}"')
                ran += 1
            except sqlite3.Error as e:
                errors.append(f'{name}: {str(e)[:180]}')
    finally:
        # الإيداع قبل الفصل: SQLite يرفض DETACH وفيه معاملة مفتوحة
        # برسالة "database src is locked" — فيبقى الملف مربوطًا.
        try:
            conn.commit()
        except sqlite3.Error:
            pass
        conn.execute("DETACH DATABASE src")

    return ran, skipped, errors


# =====================================================================
#  النسخ التلقائي
# =====================================================================
#
# أداةُ نسخٍ لا يضغطها أحد ليست أداة. العميل يعِد نفسه بأن ينسخ أسبوعيًّا،
# ثم يتذكّر ذلك يوم يفقد البيانات. فالنسخ يجري من تلقائه، والقديم يُحذف
# من تلقائه أيضًا — وإلا امتلأ القرص وتوقّف النظام لسببٍ هو «الحماية».

AUTO_DIR_NAME = 'backups'

_DEFAULTS = {
    'backup_auto_enabled': '1',
    'backup_auto_keep': '7',        # كم نسخة تُحفظ
    'backup_auto_hours': '24',      # كل كم ساعة
}


def auto_settings():
    """إعدادات النسخ التلقائي، بقيَم سليمة دائمًا."""
    from utils.db import get_setting

    out = {}
    for k, d in _DEFAULTS.items():
        try:
            out[k] = get_setting(k, d)
        except Exception:
            out[k] = d

    try:
        keep = int(out['backup_auto_keep'])
    except (TypeError, ValueError):
        keep = 7
    try:
        hours = int(out['backup_auto_hours'])
    except (TypeError, ValueError):
        hours = 24

    return {
        # أي قيمة غير '1' تعني إيقافًا: إعدادٌ تالف يوقف النسخ ولا
        # يُشغّله بلا حساب.
        'enabled': str(out['backup_auto_enabled']) == '1',
        # حدّان: صفر نسخ يعني حذف كل شيء، ومئة نسخة تملأ القرص.
        'keep': max(1, min(keep, 60)),
        'hours': max(1, min(hours, 24 * 30)),
    }


def save_auto_settings(enabled, keep, hours):
    from utils.db import set_setting

    set_setting('backup_auto_enabled', '1' if enabled else '0')
    set_setting('backup_auto_keep', str(max(1, min(int(keep), 60))))
    set_setting('backup_auto_hours', str(max(1, min(int(hours), 24 * 30))))


def auto_dir():
    d = os.path.join(_data_dir(), AUTO_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def list_auto_backups():
    """النسخ التلقائية الموجودة، الأحدث أولًا."""
    d = auto_dir()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out

    for n in names:
        if not n.endswith('.db'):
            continue
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            'name': n,
            'bytes': st.st_size,
            'size_h': human_size(st.st_size),
            'when': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'mtime': st.st_mtime,
        })

    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out


def _prune(keep):
    """يحذف ما زاد على العدد المطلوب، الأقدم أولًا."""
    removed = 0
    for item in list_auto_backups()[keep:]:
        try:
            os.remove(os.path.join(auto_dir(), item['name']))
            removed += 1
        except OSError:
            pass
    return removed


def auto_backup_due():
    """هل حان موعد النسخة التالية؟"""
    cfg = auto_settings()
    if not cfg['enabled']:
        return False

    latest = list_auto_backups()
    if not latest:
        return True

    age_hours = (datetime.now().timestamp() - latest[0]['mtime']) / 3600.0
    return age_hours >= cfg['hours']


def run_auto_backup(force=False):
    """يأخذ نسخة تلقائية إن حان موعدها، ويحذف الزائد.

    لا يرمي أبدًا: يُنادى من خيط خلفي، واستثناءٌ فيه يقتل الخيط بصمت
    فتتوقّف النسخ كلّها دون أن يلاحظ أحد.

    يُرجِع (تمّ؟, رسالة).
    """
    try:
        cfg = auto_settings()
        if not force and not cfg['enabled']:
            return False, 'النسخ التلقائي متوقّف'
        if not force and not auto_backup_due():
            return False, 'لم يحن الموعد بعد'

        name = f"auto-{datetime.now():%Y-%m-%d-%H%M%S}.db"
        path = os.path.join(auto_dir(), name)
        snapshot_file(path)

        # التحقّق بعد الكتابة: ملفٌ ناقص يبدو نسخةً ويخذل صاحبه يوم
        # الحاجة. أفضل أن يُحذف الآن ويُعاد.
        try:
            con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
            try:
                ok = con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            finally:
                con.close()
        except sqlite3.Error:
            ok = False

        if not ok:
            os.remove(path)
            return False, 'خرجت النسخة تالفة ولم تُحفظ'

        pruned = _prune(cfg['keep'])
        return True, f'{name} (حُذف {pruned} قديمة)' if pruned else name

    except Exception as e:                              # noqa: BLE001
        print(f'[backup] تعذّرت النسخة التلقائية: {e}')
        return False, str(e)
