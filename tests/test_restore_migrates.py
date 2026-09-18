"""استعادة نسخةٍ قديمة لا تُرجع النظام إلى مخطَّطٍ قديم.

## ما وقع عند عميل

رفع نسخةً احتياطية مأخوذةً من إصدارٍ قديم، فصار النظام يردّ **500**
على صفحاتٍ كانت تعمل قبل دقيقة. ونسخةٌ حديثة تُستعاد ويعمل كل شيء —
فيبدو العطب عشوائيًّا لمن يجرّب.

## السبب

`_restore_from_db` تُسقط الجدول القائم وتعيد بناءه **بمخطَّط الملف
المرفوع**:

    DROP TABLE IF EXISTS main."employees"
    <DDL من الملف القديم>
    INSERT INTO main."employees" SELECT * FROM src."employees"

فالجدول يعود كما كان يوم أُخذت النسخة — بلا الأعمدة التي أضافتها
الترحيلات بعدها. ولم يكن شيء يُعيد تشغيل الترحيلات بعد ذلك.

والشيفرة الحالية تسأل عن تلك الأعمدة. فـ`employees.work_mode`
الغائب ليس نقصًا صامتًا: هو `no such column` عند أول استعلام، أي
500 في وجه المستخدم.

يُشغَّل:  python -m pytest tests/test_restore_migrates.py -v
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# أعمدةٌ أضافتها ترحيلاتٌ بعد v59، وتقرؤها الشيفرة الحالية.
ADDED_SINCE = [
    ('employees', 'work_mode'),
    ('shift_types', 'is_split'),
    ('fingerprint_devices', 'branch_id'),
    ('fingerprint_devices', 'branch_name'),
]


def _columns(path, table):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    finally:
        con.close()


@pytest.fixture
def system(tmp_path, monkeypatch):
    """نظامٌ حيّ بمخطَّطه الحالي."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.backup as bk
    importlib.reload(bk)
    return bk, db, str(tmp_path)


def _old_backup(tmp_path, tables):
    """ملفّ نسخةٍ بمخطَّطٍ قديم: الجداول نفسها بلا الأعمدة المضافة.

    يُبنى بالحذف من المخطَّط الحالي لا بكتابة DDL بيد: الغرض أن يكون
    المفقود هو بالضبط ما أضافته الترحيلات، لا جدولًا يخترعه الاختبار.
    """
    path = os.path.join(str(tmp_path), 'old_backup.db')
    con = sqlite3.connect(path)
    for table, cols in tables.items():
        cols_sql = ', '.join(f'"{c}"' for c in cols)
        con.execute(f'CREATE TABLE "{table}" ({cols_sql})')
    con.commit()
    con.close()
    return path


def test_restoring_an_old_backup_keeps_the_current_columns(system, tmp_path):
    """القلب: الأعمدة المضافة تعود بعد الاستعادة.

    بلا ذلك ترجع الجداول إلى مخطَّط يوم النسخة، وتصير كل صفحةٍ تسأل
    عن عمودٍ مضاف خطأ 500.
    """
    bk, db, data_dir = system
    live = os.path.join(data_dir, 'hr_system.db')

    # نسخةٌ قديمة: الجداول بأعمدتها الحالية ناقصةً ما أُضيف بعد v59.
    old_shape = {}
    for table, col in ADDED_SINCE:
        old_shape.setdefault(table, _columns(live, table) - {c for t, c in ADDED_SINCE if t == table})

    backup = _old_backup(tmp_path, old_shape)

    res = bk.restore(backup, sorted(old_shape.keys()), keep_license=True)

    assert res['ran'] > 0, f'لم يُستعد شيء: {res}'
    for table, col in ADDED_SINCE:
        assert col in _columns(live, table), (
            f'{table}.{col} ضاع بعد الاستعادة — كل استعلام يسأل عنه يصير 500')


def test_the_restore_reports_that_it_migrated(system, tmp_path):
    """النتيجة تقول إن الترحيل جرى، فلا يُظنّ أنه لم يجرِ."""
    bk, db, data_dir = system
    live = os.path.join(data_dir, 'hr_system.db')

    shape = {'shift_types': _columns(live, 'shift_types') - {'is_split'}}
    res = bk.restore(_old_backup(tmp_path, shape), ['shift_types'])

    assert res.get('migrated') is True


def test_a_query_on_a_restored_table_still_works(system, tmp_path):
    """الفحص كما يراه المستخدم: استعلامٌ يسأل عن العمود المضاف.

    فحصُ `PRAGMA` يُثبت وجود العمود، وهذا يُثبت أن الاستعلام يمرّ —
    وهو ما كان يسقط بـ500.
    """
    bk, db, data_dir = system
    live = os.path.join(data_dir, 'hr_system.db')

    shape = {'employees': _columns(live, 'employees') - {'work_mode'}}
    bk.restore(_old_backup(tmp_path, shape), ['employees'])

    con = sqlite3.connect(live)
    try:
        con.execute('SELECT work_mode FROM employees LIMIT 1').fetchall()
    except sqlite3.OperationalError as e:
        pytest.fail(f'الاستعلام الذي يسقط عند العميل ما زال يسقط: {e}')
    finally:
        con.close()


def test_a_current_backup_still_restores(system, tmp_path):
    """النسخة الحديثة كانت تعمل، ويجب أن تبقى تعمل.

    هذه هي الحال التي أخفت العطب: من يجرّب بنسخةٍ أخذها اليوم لا يراه.
    """
    bk, db, data_dir = system
    live = os.path.join(data_dir, 'hr_system.db')

    shape = {'shift_types': _columns(live, 'shift_types')}
    res = bk.restore(_old_backup(tmp_path, shape), ['shift_types'])

    assert res['ok'] is True, res['errors']
    assert 'is_split' in _columns(live, 'shift_types')


def test_the_safety_copy_is_taken_before_anything_is_written(system, tmp_path):
    """النسخة الأمانية هي ما يجعل الخطأ قابلًا للتراجع."""
    bk, db, data_dir = system
    live = os.path.join(data_dir, 'hr_system.db')

    shape = {'shift_types': _columns(live, 'shift_types') - {'is_split'}}
    res = bk.restore(_old_backup(tmp_path, shape), ['shift_types'])

    safety = os.path.join(data_dir, res['safety_copy'])
    assert os.path.isfile(safety)
    # وهي من قبل الاستعادة: مخطَّطها الحالي لا مخطَّط الملف المرفوع.
    assert 'is_split' in _columns(safety, 'shift_types')


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
