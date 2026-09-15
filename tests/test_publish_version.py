"""النشر: ما يُعلَن للعملاء يجب أن يُطابق ما يُنزَّل.

العميل ينزّل حزمة zip ويحسب بصمتها ويقارنها بالمعلَن، ويرفض التركيب إن
لم تتطابق أو لم تُعلَن. فالنشر ببصمة ملف آخر — أو بلا بصمة — يُنتج
إصدارًا يراه كل عميل ويحاول تركيبه ويفشل.

يُشغَّل:  python -m pytest tests/test_publish_version.py -v
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.publish_version as pv


def _zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for n, b in files.items():
            z.writestr(n, b)
    return str(path)


# ------------------------------------------- البصمة لملف العميل لا لغيره

def test_the_checksum_is_of_the_package_the_client_downloads(tmp_path):
    """بيت القصيد.

    كان يحسب بصمة dist/HRSystem/HRSystem.exe، والعميل يحسب بصمة حزمة
    zip. بصمتان لملفين مختلفين لا تتطابقان أبدًا.
    """
    pkg = _zip(tmp_path / 'HRSystem.zip', {'HRSystem.exe': 'BINARY', 'app.py': '# x'})

    from utils.update_manager import file_sha256 as client_side

    assert pv.calculate_checksum(pkg) == client_side(pkg), \
        'ما ينشره الخادم لا يساوي ما يحسبه العميل'


def test_an_exe_is_not_a_package(tmp_path):
    """الرابط المنشور فعلًا ينتهي بـ.exe، والمُركِّب يفكّ zip وحدها."""
    exe = tmp_path / 'OnPointHR2.5.exe'
    exe.write_bytes(b'MZ\x90\x00' + b'\x00' * 500)

    ok, why = pv.check_package(str(exe))
    assert ok is False
    assert 'zip' in why


def test_a_corrupt_or_empty_package_is_refused(tmp_path):
    empty = _zip(tmp_path / 'empty.zip', {})
    assert pv.check_package(empty)[0] is False
    assert pv.check_package(str(tmp_path / 'nope.zip'))[0] is False


# ----------------------------------------- لا نشر بلا بصمة يتحقّق منها

def test_publishing_without_a_package_is_refused(tmp_path, monkeypatch, capsys):
    """كان يطبع تحذيرًا وينشر ببصمة فارغة — وهكذا صار 2.5 بلا بصمة."""
    monkeypatch.setattr(pv, 'parse_latest_changelog',
                        lambda: {'version': '2.11.0.0', 'notes': ['x']})
    monkeypatch.setattr(pv, 'API_SECRET', 'secret')

    sent = []
    monkeypatch.setattr(pv.requests, 'post',
                        lambda *a, **k: sent.append(a) or _fail())

    code = pv.publish_release(package_path=str(tmp_path / 'missing.zip'),
                              download_url='https://onz.one/d/pkg.zip')

    assert code != 0
    assert not sent, 'أُرسل النشر رغم غياب الحزمة'
    assert 'لا يُنشر إصدار بلا حزمة' in capsys.readouterr().out


def _fail():
    raise AssertionError('ما كان ينبغي الإرسال')


def test_a_missing_url_is_refused(tmp_path, monkeypatch, capsys):
    """اللوحة كانت تركّب الرابط بنفسها منتهيًا بـ.exe."""
    pkg = _zip(tmp_path / 'p.zip', {'a': '1'})
    monkeypatch.setattr(pv, 'parse_latest_changelog',
                        lambda: {'version': '2.11.0.0', 'notes': []})
    monkeypatch.setattr(pv, 'API_SECRET', 'secret')
    monkeypatch.setattr(pv, 'DOWNLOAD_URL', '')

    assert pv.publish_release(package_path=pkg, download_url=None) != 0
    assert 'رابط التنزيل' in capsys.readouterr().out


def test_an_http_url_is_refused(tmp_path, monkeypatch):
    """update_manager يرفض غير https، فالنشر برابط http إصدار ميّت."""
    pkg = _zip(tmp_path / 'p.zip', {'a': '1'})
    monkeypatch.setattr(pv, 'parse_latest_changelog',
                        lambda: {'version': '2.11.0.0', 'notes': []})
    monkeypatch.setattr(pv, 'API_SECRET', 'secret')

    assert pv.publish_release(package_path=pkg,
                              download_url='http://onz.one/d/pkg.zip') != 0


def test_no_secret_means_no_attempt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pv, 'API_SECRET', '')
    assert pv.publish_release(package_path=str(tmp_path / 'p.zip'),
                              download_url='https://x/p.zip') != 0
    assert 'PUBLISH_API_SECRET' in capsys.readouterr().out


# ------------------------------------------------- ما يصل اللوحة فعلًا

def test_the_payload_carries_checksum_url_and_size():
    p = pv.build_payload('2.11.0.0', ['ملاحظة'], 'a' * 64, 12345,
                         'https://onz.one/d/pkg.zip')

    assert p['sha256'] == 'a' * 64
    assert p['checksum'] == 'a' * 64      # الاسم القديم، للوحة لم تُحدَّث
    assert p['size'] == 12345
    assert p['download_url'] == 'https://onz.one/d/pkg.zip'
    assert p['version'] == '2.11.0.0'


def test_the_signature_covers_body_timestamp_and_nonce():
    import hashlib
    import hmac

    body = '{"a":1}'
    h = pv.sign(body, 'the-secret')
    expected = hmac.new(b'the-secret',
                        (body + h['X-Timestamp'] + h['X-Nonce']).encode(),
                        hashlib.sha256).hexdigest()

    assert h['X-HMAC-Signature'] == expected


def test_a_dry_run_sends_nothing(tmp_path, monkeypatch, capsys):
    pkg = _zip(tmp_path / 'p.zip', {'a': '1'})
    monkeypatch.setattr(pv, 'parse_latest_changelog',
                        lambda: {'version': '2.11.0.0', 'notes': []})
    monkeypatch.setattr(pv.requests, 'post', lambda *a, **k: _fail())

    code = pv.publish_release(package_path=pkg,
                              download_url='https://onz.one/d/pkg.zip',
                              dry_run=True)
    assert code == 0
    assert 'تجربة بلا إرسال' in capsys.readouterr().out


# ------------------------------------------------------- بناء الحزمة

def test_the_package_is_rooted_at_the_installation(tmp_path):
    """المُركِّب يفكّ في مجلد التركيب، فلا مجلد أعلى داخل الحزمة."""
    build = tmp_path / 'dist' / 'HRSystem'
    (build / 'tools').mkdir(parents=True)
    (build / 'HRSystem.exe').write_text('BIN')
    (build / 'tools' / 'updater.exe').write_text('UPD')

    out = pv.build_package(str(build), str(tmp_path / 'pkg.zip'))
    names = sorted(zipfile.ZipFile(out).namelist())

    assert names == ['HRSystem.exe', 'tools/updater.exe']
    assert not any(n.startswith('HRSystem/') for n in names)


def test_a_missing_build_dir_is_reported_not_guessed(tmp_path):
    assert pv.build_package(str(tmp_path / 'nope'), str(tmp_path / 'p.zip')) is None


def test_no_half_written_package_is_left_behind(tmp_path):
    build = tmp_path / 'b'
    build.mkdir()
    (build / 'f').write_text('x')
    out = pv.build_package(str(build), str(tmp_path / 'p.zip'))

    assert os.path.exists(out)
    assert not os.path.exists(out + '.part')


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
