"""مُركِّب التحديث: إمّا النسخة القديمة كاملةً أو الجديدة كاملةً.

كان يفكّ الحزمة فوق مجلد التركيب مباشرةً، فالتعثّر في منتصف الفكّ يترك
نصف التطبيق جديدًا ونصفه قديمًا — ثم يُقال للعميل «أعد المحاولة».
والنسخة المنفصلة عن الشبكة لا تملك خادمًا تعيد التنزيل منه.

يُشغَّل:  python -m pytest tests/test_updater.py -v
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.updater import (check_package, enough_space, file_sha256, run,
                           stage, staged_files, swap_in)


def _install(tmp_path, files):
    d = tmp_path / 'install'
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return d


def _package(tmp_path, files, name='update.zip'):
    p = tmp_path / name
    with zipfile.ZipFile(p, 'w') as z:
        for n, body in files.items():
            z.writestr(n, body)
    return p


def _snapshot(d):
    out = {}
    for root, _dirs, fs in os.walk(d):
        for f in fs:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, d)
            out[rel.replace(os.sep, '/')] = open(full).read()
    return out


# محتوى صالح لغويًا: exe_name يُعاد تشغيله فعلًا في بعض الحالات.
OLD = {'app.py': '# OLD', 'utils/db.py': '# OLD', 'tools/updater.py': '# OLD'}
NEW = {'app.py': '# NEW', 'utils/db.py': '# NEW', 'tools/updater.py': '# NEW'}


# ------------------------------------------------------ التحقّق قبل الفكّ

def test_a_corrupt_package_is_refused_before_anything_is_touched(tmp_path):
    inst = _install(tmp_path, OLD)
    bad = tmp_path / 'update.zip'
    bad.write_bytes(b'this is not a zip file at all')

    code = run(str(bad), str(inst), 'app-not-here.py')

    assert code != 0
    assert _snapshot(inst) == OLD, 'مُسّ التركيب رغم رفض الحزمة'


def test_a_wrong_checksum_is_refused(tmp_path):
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    code = run(str(pkg), str(inst), 'app.py', expected_sha256='0' * 64)

    assert code != 0
    assert _snapshot(inst) == OLD


def test_the_right_checksum_passes(tmp_path):
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    code = run(str(pkg), str(inst), 'app.py',
               expected_sha256=file_sha256(str(pkg)))

    assert code == 0
    assert _snapshot(inst) == NEW


def test_an_empty_package_is_refused(tmp_path):
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, {})

    assert run(str(pkg), str(inst), 'app.py') != 0
    assert _snapshot(inst) == OLD


def test_a_full_disk_is_caught_before_the_swap(tmp_path, monkeypatch):
    """القرص الممتلئ كان يقطع الفكّ في منتصفه."""
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    import tools.updater as u
    monkeypatch.setattr(u, 'enough_space', lambda d, n: (False, 1024))

    assert u.run(str(pkg), str(inst), 'app.py') != 0
    assert _snapshot(inst) == OLD


# ------------------------------------- لا حالة بين القديم والجديد

def test_a_failure_midway_leaves_the_old_version_whole(tmp_path, monkeypatch):
    """بيت القصيد.

    جرّبتُ الفكّ المباشر على تركيب فيه عائق: خرج ملفان جديدين وثالث
    قديمًا. الآن يتعثّر الاستبدال في منتصفه فيعود كل شيء.
    """
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    import shutil as real_shutil
    import tools.updater as u

    calls = {'n': 0}
    real_move = real_shutil.move

    def flaky_move(src, dst):
        calls['n'] += 1
        if calls['n'] == 4:        # في منتصف الاستبدال تمامًا
            raise OSError('الملف مقفل')
        return real_move(src, dst)

    monkeypatch.setattr(u.shutil, 'move', flaky_move)
    code = u.run(str(pkg), str(inst), 'app.py', retries=1)

    assert code != 0
    assert _snapshot(inst) == OLD, 'بقي التركيب نصفه جديدًا ونصفه قديمًا'


def test_the_package_survives_a_failed_update(tmp_path, monkeypatch):
    """العميل المنفصل عن الشبكة لا يستطيع التنزيل ثانيةً."""
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    import tools.updater as u
    monkeypatch.setattr(u, 'swap_in', lambda *a: (False, 'انقطع الاستبدال'))

    assert u.run(str(pkg), str(inst), 'app.py', retries=1) != 0
    assert pkg.exists(), 'حُذفت الحزمة بعد إخفاق التحديث'


def test_swap_restores_every_moved_file(tmp_path):
    inst = _install(tmp_path, OLD)
    staging = tmp_path / 'staging'
    rollback = tmp_path / 'rollback'
    pkg = _package(tmp_path, dict(NEW, **{'new_file.py': 'BRAND NEW'}))

    stage(str(pkg), str(staging))
    rollback.mkdir()
    rels = staged_files(str(staging))

    import shutil as real_shutil
    real_move = real_shutil.move
    seen = {'n': 0}

    def flaky(src, dst):
        seen['n'] += 1
        if seen['n'] == 6:
            raise OSError('توقّف')
        return real_move(src, dst)

    import tools.updater as u
    orig = u.shutil.move
    u.shutil.move = flaky
    try:
        ok, _ = swap_in(str(staging), str(inst), str(rollback), rels)
    finally:
        u.shutil.move = orig

    assert ok is False
    assert _snapshot(inst) == OLD
    # والملف الذي لم يكن موجودًا قبل التحديث لا يبقى منه أثر
    assert not (inst / 'new_file.py').exists()


# --------------------------------------------------------- الحالة السعيدة

def test_a_clean_update_replaces_everything_and_cleans_up(tmp_path):
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, dict(NEW, **{'utils/new.py': 'ADDED'}))

    code = run(str(pkg), str(inst), 'nonexistent-app.py')

    assert code == 0
    snap = _snapshot(inst)
    assert snap['app.py'] == '# NEW'
    assert snap['utils/db.py'] == '# NEW'
    assert snap['utils/new.py'] == 'ADDED'
    assert not (inst / '.update-staging').exists()
    assert not (inst / '.update-rollback').exists()


def test_files_not_in_the_package_are_left_alone(tmp_path):
    """بيانات العميل وإعداداته إلى جانب التطبيق — لا تُمسّ."""
    inst = _install(tmp_path, dict(OLD, **{'config.local.json': 'MINE'}))
    pkg = _package(tmp_path, NEW)

    assert run(str(pkg), str(inst), 'app.py') == 0
    assert (inst / 'config.local.json').read_text() == 'MINE'


def test_the_log_goes_next_to_the_installation(tmp_path):
    """كان يُكتب في مجلد العمل أيًّا كان، فلا يُعثر عليه."""
    inst = _install(tmp_path, OLD)
    pkg = _package(tmp_path, NEW)

    from tools.updater import main
    main([str(pkg), str(inst), 'app.py', '--no-pause'])

    assert (inst / 'update.log').exists()
    assert 'Updater' in (inst / 'update.log').read_text()


# ------------------------------------------------- حزمة العميل لا تُحذف

def test_a_package_outside_the_install_folder_is_kept(tmp_path):
    """نسخة جاء بها العميل على ذاكرة: تبقى له."""
    usb = tmp_path / 'usb'
    usb.mkdir()
    inst = _install(tmp_path, OLD)
    pkg = _package(usb, NEW, name='update.zip')

    assert run(str(pkg), str(inst), 'app.py') == 0
    assert pkg.exists(), 'حُذفت نسخة العميل من ذاكرته'


def test_our_own_temporary_package_is_removed(tmp_path):
    inst = _install(tmp_path, OLD)
    pkg = inst / 'update_pkg.zip'      # حيث يضعها update_manager
    with zipfile.ZipFile(pkg, 'w') as z:
        for n, body in NEW.items():
            z.writestr(n, body)

    assert run(str(pkg), str(inst), 'app.py') == 0
    assert not pkg.exists()


# -------------------------------------------------------- فحوص صغيرة

def test_check_package_reports_size_and_names(tmp_path):
    pkg = _package(tmp_path, NEW)
    ok, names, size = check_package(str(pkg))

    assert ok is True
    assert sorted(names) == sorted(NEW)
    assert size == sum(len(v) for v in NEW.values())


def test_missing_package_is_not_a_crash(tmp_path):
    ok, msg, _ = check_package(str(tmp_path / 'nope.zip'))
    assert ok is False and 'غير موجود' in msg


def test_space_check_survives_an_unreadable_disk(tmp_path, monkeypatch):
    import tools.updater as u

    def boom(_p):
        raise OSError('no such device')

    monkeypatch.setattr(u.shutil, 'disk_usage', boom)
    ok, _ = enough_space(str(tmp_path), 1000)
    assert ok is True, 'مُنع التحديث لأن قياس المساحة تعذّر'


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
