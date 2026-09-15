"""أداة الإزالة: النسخة التي تبقى للعميل بعد أن يذهب النظام.

تُشغَّل مرةً واحدة في عمر التركيب، وغالبًا على جهاز انكسر فيه شيء —
فلا فرصة لاكتشاف خطئها إلا يوم لا تنفع فيه المحاولة الثانية.

يُشغَّل:  python -m pytest tests/test_uninstall.py -v
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.uninstall import resolve_data_dir, snapshot, uninstall, verify


def _make_db(path, rows=3):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT)')
    for i in range(rows):
        con.execute('INSERT INTO employees (name) VALUES (?)', (f'موظف {i}',))
    con.commit()
    return con


def _count(path):
    con = sqlite3.connect(path)
    try:
        return con.execute('SELECT COUNT(*) FROM employees').fetchone()[0]
    finally:
        con.close()


# ------------------------------------------------- أين يبحث عن القاعدة

def test_data_dir_follows_the_installation(monkeypatch):
    """كان المجلد مكتوبًا في الملف، فمن ركّب على غيره لم تُنسخ قاعدته."""
    monkeypatch.setenv('HR_DATA_DIR', '/srv/hr-data')
    assert resolve_data_dir() == '/srv/hr-data'

    monkeypatch.setattr(os, 'name', 'nt')
    monkeypatch.delenv('HR_DATA_DIR', raising=False)
    assert resolve_data_dir() == r'C:\ProgramData\HRSystem'

    # والتمرير الصريح يسبق كل شيء
    monkeypatch.setenv('HR_DATA_DIR', '/srv/hr-data')
    assert resolve_data_dir('/mnt/usb/data') == os.path.abspath('/mnt/usb/data')


def test_missing_directory_says_where_it_looked(tmp_path, capsys):
    code = uninstall(str(tmp_path / 'nope'), assume_yes=True)
    out = capsys.readouterr().out
    assert code == 0
    assert '--data-dir' in out, 'لم يُرشد إلى المجلد البديل'


# --------------------------------------------- ما يفوت النسخ بإعادة التسمية

def test_snapshot_carries_what_is_still_in_the_wal(tmp_path):
    """بيت القصيد.

    القاعدة تعمل بـWAL: المعاملة تُكتب في hr_system.db-wal ولا تنتقل إلى
    الملف الأصلي إلا عند الدمج. فنقلُ الملف وحده — وهو ما كان يفعله
    os.rename — يعطي نسخةً ينقصها آخر ما أُدخل، والرسالة تقول «نُسخت
    بنجاح».
    """
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db), rows=3)

    # إدخال يبقى في WAL: الاتصال مفتوح فلا دمج
    con.execute("INSERT INTO employees (name) VALUES ('آخر موظف')")
    con.commit()
    assert os.path.exists(str(db) + '-wal'), 'البيئة لا تستعمل WAL — الاختبار بلا معنى'

    dest = tmp_path / 'snap.db'
    snapshot(str(db), str(dest))
    con.close()

    assert _count(str(dest)) == 4, 'ضاع ما كان في WAL من اللقطة'

    names = sqlite3.connect(str(dest)).execute(
        'SELECT name FROM employees ORDER BY id').fetchall()
    assert ('آخر موظف',) in names


def test_the_snapshot_is_one_self_contained_file(tmp_path):
    """من ينسخ الملف إلى ذاكرة يجب أن ينقل معه كل شيء.

    اللقطة ترث WAL عن الأصل، فلولا الدمج لبقي -wal إلى جانبها ومضى
    العميل بنصف قاعدته.
    """
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db), rows=4)
    con.execute("INSERT INTO employees (name) VALUES ('متأخر')")
    con.commit()

    dest = tmp_path / 'snap.db'
    snapshot(str(db), str(dest))
    con.close()

    assert not os.path.exists(str(dest) + '-wal'), 'بقي -wal بجانب اللقطة'
    assert not os.path.exists(str(dest) + '-shm'), 'بقي -shm بجانب اللقطة'
    assert _count(str(dest)) == 5


def test_the_old_rename_would_have_lost_it(tmp_path):
    """توثيق للخطأ نفسه: نسخ الملف وحده ينقص.

    لو سقط هذا الاختبار يومًا فمعناه أن WAL لم يعد يُستعمل، لا أن النسخ
    صار آمنًا.
    """
    import shutil

    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db), rows=3)
    con.execute("INSERT INTO employees (name) VALUES ('آخر موظف')")
    con.commit()

    plain = tmp_path / 'plain-copy.db'
    shutil.copy(str(db), str(plain))      # ما كانت تفعله os.rename فعليًا
    con.close()

    try:
        got = _count(str(plain))
    except sqlite3.OperationalError:
        got = None      # لم يُدمج شيء بعد: الملف بلا جداول أصلًا

    assert got != 4, 'نسخ الملف وحده لم ينقص — راجع الافتراض'

    # وهذا ما يمسكه الفحص الآن قبل حذف الأصل
    ok, _ = verify(str(plain))
    assert ok is False


# ------------------------------------------------------ لا تُحذف قبل التحقّق

def test_originals_are_removed_only_after_a_verified_snapshot(tmp_path, capsys):
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db), rows=5)
    con.close()

    code = uninstall(str(tmp_path), assume_yes=True)
    assert code == 0
    assert not db.exists()
    assert not os.path.exists(str(db) + '-wal'), 'بقي ملف WAL يتيمًا'

    backups = list(tmp_path.glob('hr_system_backup_*.db'))
    assert len(backups) == 1
    assert _count(str(backups[0])) == 5


def test_a_bad_snapshot_keeps_the_original(tmp_path, monkeypatch, capsys):
    """إن لم تُثبت سلامة النسخة فالأصل لا يُمسّ — والرسالة تقول ذلك."""
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db))
    con.close()

    import tools.uninstall as u
    monkeypatch.setattr(u, 'verify', lambda p: (False, 'فحص السلامة: تالف'))

    code = u.uninstall(str(tmp_path), assume_yes=True)
    out = capsys.readouterr().out

    assert code != 0
    assert db.exists(), 'حُذفت القاعدة رغم فشل التحقّق'
    assert 'لم تُحذف' in out


def test_failure_is_not_reported_as_success(tmp_path, monkeypatch, capsys):
    """كانت رسالة «تمت الإزالة» تُطبع حتى بعد فشل النسخ."""
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db))
    con.close()

    import tools.uninstall as u

    def boom(*a, **k):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(u, 'snapshot', boom)
    code = u.uninstall(str(tmp_path), assume_yes=True)
    out = capsys.readouterr().out

    assert code != 0
    assert 'تمت الإزالة' not in out
    assert db.exists()


def test_keep_database_takes_a_copy_without_removing(tmp_path):
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db), rows=2)
    con.close()

    code = uninstall(str(tmp_path), assume_yes=True, keep_database=True)
    assert code == 0
    assert db.exists()
    assert len(list(tmp_path.glob('hr_system_backup_*.db'))) == 1


# ---------------------------------------------------------- سؤال التأكيد

def test_nothing_moves_without_confirmation(tmp_path, monkeypatch, capsys):
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db))
    con.close()

    monkeypatch.setattr('builtins.input', lambda *a: '')
    code = uninstall(str(tmp_path), assume_yes=False)

    assert code != 0
    assert db.exists()
    assert not list(tmp_path.glob('hr_system_backup_*.db'))


def test_confirmation_needs_the_whole_word(tmp_path, monkeypatch):
    db = tmp_path / 'hr_system.db'
    con = _make_db(str(db))
    con.close()

    for typed in ('y', 'yes', 'n', 'rem', ' '):
        monkeypatch.setattr('builtins.input', lambda *a, t=typed: t)
        assert uninstall(str(tmp_path), assume_yes=False) != 0
        assert db.exists()

    monkeypatch.setattr('builtins.input', lambda *a: 'REMOVE')
    assert uninstall(str(tmp_path), assume_yes=False) == 0


def test_verify_rejects_an_empty_or_broken_file(tmp_path):
    empty = tmp_path / 'empty.db'
    empty.write_bytes(b'')
    ok, _ = verify(str(empty))
    assert ok is False, 'قُبل ملف فارغ نسخةً سليمة'

    junk = tmp_path / 'junk.db'
    junk.write_bytes(b'this is not a database' * 50)
    ok, _ = verify(str(junk))
    assert ok is False


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
