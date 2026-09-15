"""النسخ والاستعادة داخل نظام الموارد البشرية.

ما يجعل هذه الأداة أداةً لا زرّ حذف بطيئًا:

  * اللقطة متّسقة والنظام يعمل — لا نسخ ملف يدويًّا وسط معاملة.
  * الملف يُفحَص قبل أن يُنفَّذ منه شيء، فيُعرف ما فيه.
  * الاستعادة انتقائية: ما لم يُختَر لا يُمسّ.
  * وتُسبَق بنسخة تلقائية، فالخطأ قابل للتراجع.

يُشغَّل:  python -m pytest tests/test_backup.py -v
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def db(tmp_path, monkeypatch):
    """قاعدة مستأجر حقيقية، بجدولَي اختبار مستقلَّين."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))

    import utils.db as d
    importlib.reload(d)
    d.init_db()

    import utils.backup as bk
    importlib.reload(bk)

    path = os.path.join(str(tmp_path), 'hr_system.db')
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE probe_a (id INTEGER PRIMARY KEY, v TEXT)')
    con.execute('CREATE TABLE probe_b (id INTEGER PRIMARY KEY, v TEXT)')
    con.execute("INSERT INTO probe_a VALUES (1, 'أصلي-أ')")
    con.execute("INSERT INTO probe_b VALUES (1, 'أصلي-ب')")
    con.commit()
    con.close()

    def value(table):
        c = sqlite3.connect(path)
        try:
            return c.execute(f'SELECT v FROM {table} WHERE id=1').fetchone()[0]
        finally:
            c.close()

    def setvalue(table, v):
        c = sqlite3.connect(path)
        try:
            c.execute(f'UPDATE {table} SET v=? WHERE id=1', (v,))
            c.commit()
        finally:
            c.close()

    return {'bk': bk, 'path': path, 'dir': str(tmp_path),
            'value': value, 'setvalue': setvalue}


# --------------------------------------------------------------- النسخ

def test_snapshot_is_a_real_openable_database(db, tmp_path):
    out = str(tmp_path / 'snap.db')
    db['bk'].snapshot_file(out)

    assert os.path.getsize(out) > 0
    con = sqlite3.connect(out)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        assert 'probe_a' in names and 'employees' in names
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        con.close()


def test_sql_dump_contains_only_what_was_asked(db):
    sql = ''.join(db['bk'].dump_sql(['probe_a'], True))

    assert 'CREATE TABLE' in sql and 'probe_a' in sql
    assert 'أصلي-أ' in sql
    assert 'probe_b' not in sql


def test_schema_only_has_no_rows(db):
    sql = ''.join(db['bk'].dump_sql(['probe_a'], False))

    assert 'CREATE TABLE' in sql
    assert 'INSERT INTO' not in sql


def test_quotes_in_data_survive_the_round_trip(db, tmp_path):
    """قيمة فيها علامة اقتباس هي ما يكسر المولِّدات الساذجة."""
    db['setvalue']('probe_a', "شركة اسمها 'الأمل' ; ونقطة")

    out = str(tmp_path / 'q.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(['probe_a'], True):
            f.write(c)

    info = db['bk'].inspect(out)
    names = {t['name']: t for t in info['tables']}
    assert names['probe_a']['rows'] == 1          # لا صفران بسبب الفاصلة

    db['setvalue']('probe_a', 'شيء آخر')
    db['bk'].restore(out, ['probe_a'])
    assert db['value']('probe_a') == "شركة اسمها 'الأمل' ; ونقطة"


# --------------------------------------------------------------- الفحص

def test_inspect_recognises_a_database_file(db, tmp_path):
    out = str(tmp_path / 'snap.db')
    db['bk'].snapshot_file(out)

    info = db['bk'].inspect(out)
    assert info['kind'] == 'db'
    names = {t['name'] for t in info['tables']}
    assert 'probe_a' in names


def test_inspect_recognises_a_sql_file(db, tmp_path):
    out = str(tmp_path / 'x.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(['probe_a', 'probe_b'], True):
            f.write(c)

    info = db['bk'].inspect(out)
    assert info['kind'] == 'sql'
    names = {t['name']: t for t in info['tables']}
    assert names['probe_a']['rows'] == 1
    assert names['probe_b']['rows'] == 1


def test_inspect_executes_nothing(db, tmp_path):
    """الفحص قراءة محضة — وإلا لما كان فحصًا."""
    bad = str(tmp_path / 'bad.sql')
    with open(bad, 'w', encoding='utf-8') as f:
        f.write('DROP TABLE probe_a;\nDROP TABLE probe_b;\n')

    db['bk'].inspect(bad)

    assert db['value']('probe_a') == 'أصلي-أ'
    assert db['value']('probe_b') == 'أصلي-ب'


# ----------------------------------------------------------- الاستعادة

def test_restore_touches_only_the_chosen_tables(db, tmp_path):
    out = str(tmp_path / 'both.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(['probe_a', 'probe_b'], True):
            f.write(c)

    db['setvalue']('probe_a', 'مُعدَّل-أ')
    db['setvalue']('probe_b', 'مُعدَّل-ب')

    res = db['bk'].restore(out, ['probe_a'])

    assert res['ok'], res['errors']
    assert db['value']('probe_a') == 'أصلي-أ'      # استُعيد
    assert db['value']('probe_b') == 'مُعدَّل-ب'    # لم يُمسّ
    assert res['skipped'] > 0


def test_restore_from_a_database_file(db, tmp_path):
    snap = str(tmp_path / 'snap.db')
    db['bk'].snapshot_file(snap)

    db['setvalue']('probe_a', 'مُعدَّل-أ')
    db['setvalue']('probe_b', 'مُعدَّل-ب')

    res = db['bk'].restore(snap, ['probe_a'])

    assert res['ok'], res['errors']
    assert db['value']('probe_a') == 'أصلي-أ'
    assert db['value']('probe_b') == 'مُعدَّل-ب'


def test_a_safety_copy_is_taken_before_restoring(db, tmp_path):
    """الخطأ قابل للتراجع: نسخة ما قبل الاستعادة تُؤخذ دائمًا."""
    out = str(tmp_path / 'a.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(['probe_a'], True):
            f.write(c)

    db['setvalue']('probe_a', 'قيمة سأندم على فقدها')
    res = db['bk'].restore(out, ['probe_a'])

    safety = os.path.join(db['dir'], res['safety_copy'])
    assert os.path.isfile(safety)

    con = sqlite3.connect(safety)
    try:
        assert con.execute('SELECT v FROM probe_a WHERE id=1').fetchone()[0] \
            == 'قيمة سأندم على فقدها'
    finally:
        con.close()


def test_license_data_is_kept_by_default(db, tmp_path):
    """ترخيص النسخة مربوط بجهازها؛ استعادته هنا تُبطل الترخيص العامل."""
    out = str(tmp_path / 'full.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(None, True):
            f.write(c)

    res = db['bk'].restore(out, ['probe_a', 'license_settings'], keep_license=True)
    assert 'license_settings' not in res['tables']

    with pytest.raises(ValueError):
        db['bk'].restore(out, ['license_settings'], keep_license=True)


def test_a_statement_for_an_unchosen_table_never_runs(db, tmp_path):
    """العطل الذي وقع فعلًا: تعليق قبل الجملة منع مطابقة اسم الجدول،
    فعُوملت كجملة عامّة ونُفِّذت — فسقط جدول لم يختره المستخدم."""
    out = str(tmp_path / 'c.sql')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('-- ---------- probe_b ----------\n')
        f.write('DROP TABLE IF EXISTS "probe_b";\n')

    res = db['bk'].restore(out, ['probe_a'])

    assert res['skipped'] > 0
    assert db['value']('probe_b') == 'أصلي-ب', 'نُفِّذت جملة لجدول لم يُختَر'


def test_unrecognised_statements_are_skipped_not_run(db, tmp_path):
    """ملف مرفوع قد يحمل أي شيء. ما لا يُفهَم لا يُنفَّذ."""
    out = str(tmp_path / 'evil.sql')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('DELETE FROM probe_b;\n')
        f.write('UPDATE probe_b SET v = \'مخترَق\';\n')
        f.write('ATTACH DATABASE \'/tmp/x.db\' AS other;\n')

    res = db['bk'].restore(out, ['probe_a'])

    assert res['skipped'] >= 3
    assert db['value']('probe_b') == 'أصلي-ب'


def test_bad_table_names_are_refused(db, tmp_path):
    out = str(tmp_path / 'a.sql')
    with open(out, 'w', encoding='utf-8') as f:
        for c in db['bk'].dump_sql(['probe_a'], True):
            f.write(c)

    for bad in ['probe_a; DROP TABLE employees', '../../etc/passwd', 'a b']:
        with pytest.raises(ValueError):
            db['bk'].restore(out, [bad])

    with pytest.raises(ValueError):
        db['bk'].restore(out, [])


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))


# ------------------------------------------------------ النسخ التلقائي

def test_auto_backup_takes_a_valid_copy_and_prunes(db):
    bk = db['bk']
    bk.save_auto_settings(True, keep=2, hours=24)

    made = []
    for _ in range(4):
        ok, msg = bk.run_auto_backup(force=True)
        assert ok, msg
        made.append(msg)
        import time
        time.sleep(1.1)          # الأسماء بالثانية

    kept = bk.list_auto_backups()
    assert len(kept) == 2, 'لم يُحذف القديم — القرص يمتلئ يومًا'

    # وما بقي نسخٌ تُفتح فعلًا، لا ملفات بالاسم.
    p = os.path.join(bk.auto_dir(), kept[0]['name'])
    con = sqlite3.connect(p)
    try:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert con.execute('SELECT v FROM probe_a WHERE id=1').fetchone()[0] == 'أصلي-أ'
    finally:
        con.close()


def test_auto_backup_respects_the_schedule(db):
    bk = db['bk']
    bk.save_auto_settings(True, keep=5, hours=24)

    ok, _ = bk.run_auto_backup(force=True)
    assert ok
    assert bk.auto_backup_due() is False          # أُخذت للتوّ

    ok, msg = bk.run_auto_backup()                # بلا force
    assert ok is False and 'الموعد' in msg


def test_auto_backup_off_means_off(db):
    bk = db['bk']
    bk.save_auto_settings(False, keep=5, hours=1)

    assert bk.auto_backup_due() is False
    ok, msg = bk.run_auto_backup()
    assert ok is False and 'متوقّف' in msg


def test_settings_are_bounded(db):
    bk = db['bk']
    bk.save_auto_settings(True, keep=9999, hours=99999)
    cfg = bk.auto_settings()

    # صفر نسخ يعني حذف كل شيء، وألف نسخة تملأ القرص.
    assert 1 <= cfg['keep'] <= 60
    assert 1 <= cfg['hours'] <= 24 * 30


def test_a_corrupt_setting_stops_backups_rather_than_guessing(db):
    from utils.db import set_setting

    set_setting('backup_auto_enabled', 'نعم-ربما')
    assert db['bk'].auto_settings()['enabled'] is False


def test_auto_backup_filenames_cannot_escape_the_folder(db, tmp_path):
    """اسم ملف يصل من الطلب هو الطريق المعتاد لقراءة ما ليس لك."""
    import routes.backup_routes as r

    for bad in ['../../../etc/passwd', '..%2f..%2fx.db', 'auto-../x.db',
                'hr_system.db', '', None, 'auto-x.txt']:
        assert r._auto_path(bad) is None


# ================================================== الأرشيف الكامل
# القاعدة تحفظ صفّ زيارة المندوب — زمنها ومكانها وبصمة صورتها — والصورة
# ملفٌّ على القرص. فنسخة القاعدة وحدها تحفظ الادّعاء وتترك الدليل.

def _with_photos(tmp_path, n=3):
    """قاعدة اختبار ومعها صور زيارات على القرص."""
    import importlib
    import os as _os

    _os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    import utils.backup as b
    importlib.reload(b)
    db.init_db()

    photos = tmp_path / 'field_photos' / '2026-09-15'
    photos.mkdir(parents=True)
    for i in range(n):
        (photos / f'v{i}-in.jpg').write_bytes(b'\xff\xd8\xff' + bytes(500))
    return b


def test_the_archive_carries_the_photos_the_database_only_points_at(tmp_path):
    import zipfile

    b = _with_photos(tmp_path, n=3)
    dest = str(tmp_path / 'arc.zip')
    b.archive_zip(dest)

    names = zipfile.ZipFile(dest).namelist()
    assert 'hr_system.db' in names
    assert sum(1 for n in names if n.startswith('field_photos/')) == 3


def test_the_archived_database_is_a_valid_snapshot(tmp_path):
    import sqlite3 as s3
    import zipfile

    b = _with_photos(tmp_path, n=1)
    dest = str(tmp_path / 'arc.zip')
    b.archive_zip(dest)

    out = tmp_path / 'out'
    out.mkdir()
    zipfile.ZipFile(dest).extract('hr_system.db', str(out))
    con = s3.connect(str(out / 'hr_system.db'))
    try:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        con.close()


def test_no_half_written_archive_is_left_behind(tmp_path):
    import os as _os

    b = _with_photos(tmp_path, n=2)
    dest = str(tmp_path / 'arc.zip')
    b.archive_zip(dest)

    assert _os.path.exists(dest)
    assert not _os.path.exists(dest + '.part')
    assert not _os.path.exists(dest + '.db.part')


def test_attachments_are_measured_before_they_are_written(tmp_path):
    b = _with_photos(tmp_path, n=4)
    size, count = b.attachments_size()

    assert count == 4
    assert size > 0


def test_a_full_disk_is_caught_before_writing(tmp_path, monkeypatch):
    b = _with_photos(tmp_path, n=1)
    monkeypatch.setattr(b.shutil, 'disk_usage',
                        lambda p: type('U', (), {'free': 1024})())

    fits, free = b.archive_fits(10 * 1024 * 1024)
    assert fits is False and free == 1024


def test_an_unreadable_disk_does_not_block_the_archive(tmp_path, monkeypatch):
    b = _with_photos(tmp_path, n=1)

    def boom(_p):
        raise OSError('no such device')

    monkeypatch.setattr(b.shutil, 'disk_usage', boom)
    assert b.archive_fits(1000)[0] is True


def test_an_archive_without_photos_still_works(tmp_path):
    import zipfile

    import importlib
    import os as _os
    _os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    import utils.backup as b
    importlib.reload(b)
    db.init_db()

    dest = str(tmp_path / 'arc.zip')
    b.archive_zip(dest)
    assert zipfile.ZipFile(dest).namelist() == ['hr_system.db']
    assert b.attachments_size() == (0, 0)
