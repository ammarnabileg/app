"""حزمة التحديث تُتحقَّق قبل أن تُركَّب.

المحدِّث يفكّ ما يُعطى له **فوق مجلّد التركيب** ثم يشغّل ما فيه. فملفٌ
لم يُتحقَّق منه هو تنفيذُ شيفرةٍ على كل جهاز عميل — ومن يتحكّم في الرابط
يومًا يتحكّم فيهم جميعًا.

والبصمة كانت تُحسب عند النشر وتُخزَّن في `versions.file_sha256` منذ أول
يوم، ويعرضها بورتال العميل. لم تكن تخرج من مسار التحديث فحسب، ولم يكن
العميل يقارنها بشيء.

يُشغَّل:  python -m pytest tests/test_update_integrity.py -v
"""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def um(tmp_path, monkeypatch):
    """مدير التحديث، وشبكةٌ مزيَّفة تسلّم حزمةً معروفة."""
    import importlib

    import utils.update_manager as m
    importlib.reload(m)

    payload = b'PK\x03\x04' + b'\x00' * 2048          # «حزمة»
    digest = hashlib.sha256(payload).hexdigest()

    class FakeResponse:
        status_code = 200

        def iter_content(self, chunk_size=8192):
            for i in range(0, len(payload), chunk_size):
                yield payload[i:i + chunk_size]

    monkeypatch.setattr(m.requests, 'get',
                        lambda *a, **k: FakeResponse())

    return {'m': m, 'digest': digest, 'dir': str(tmp_path),
            'zip': os.path.join(str(tmp_path), 'update_pkg.zip')}


def test_a_matching_package_is_accepted(um, monkeypatch):
    launched = {}
    monkeypatch.setattr(um['m'].subprocess, 'Popen',
                        lambda *a, **k: launched.setdefault('yes', a))

    ok = um['m'].download_and_install_update(
        'https://onz.one/x.zip', um['dir'], expected_sha256=um['digest'])

    assert ok is True
    assert 'yes' in launched, 'لم يُشغَّل المحدِّث رغم صحّة البصمة'


def test_a_tampered_package_is_refused_and_deleted(um, monkeypatch):
    """الحالة التي يوجد هذا الفحص من أجلها."""
    launched = {}
    monkeypatch.setattr(um['m'].subprocess, 'Popen',
                        lambda *a, **k: launched.setdefault('yes', a))

    ok = um['m'].download_and_install_update(
        'https://onz.one/x.zip', um['dir'], expected_sha256='a' * 64)

    assert ok is False
    assert 'yes' not in launched, 'شُغِّل المحدِّث على حزمة لا تطابق بصمتها'
    # ملفٌ مرفوض يبقى في مجلّد التركيب هو ملفٌ سيُفَكّ يومًا بالخطأ.
    assert not os.path.exists(um['zip'])


def test_no_checksum_means_no_install(um, monkeypatch):
    """الفشل مغلق: لو سقطنا إلى التركيب لأسقط المهاجم البصمة من ردّه."""
    monkeypatch.setattr(um['m'], '_EXPECTED_SHA256', '')
    launched = {}
    monkeypatch.setattr(um['m'].subprocess, 'Popen',
                        lambda *a, **k: launched.setdefault('yes', a))

    for bad in [None, '', 'short', 'z' * 64, um['digest'][:63]]:
        assert um['m'].download_and_install_update(
            'https://onz.one/x.zip', um['dir'], expected_sha256=bad) is False

    assert 'yes' not in launched


def test_plain_http_is_refused(um, monkeypatch):
    launched = {}
    monkeypatch.setattr(um['m'].subprocess, 'Popen',
                        lambda *a, **k: launched.setdefault('yes', a))

    ok = um['m'].download_and_install_update(
        'http://onz.one/x.zip', um['dir'], expected_sha256=um['digest'])

    assert ok is False
    assert 'yes' not in launched


def test_the_check_carries_the_checksum_from_the_server(um, monkeypatch):
    """البصمة تصل مع الرابط في الردّ نفسه، لا تُطلب في نداء ثانٍ."""
    m = um['m']

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {'success': True, 'version': '99.0.0.0',
                    'download_url': 'https://onz.one/x.zip',
                    'release_notes': '', 'mandatory': False,
                    'sha256': um['digest']}

    monkeypatch.setattr(m.requests, 'get', lambda *a, **k: Resp())

    available, url, _notes, _mand = m.check_for_updates()

    assert available is True
    assert url == 'https://onz.one/x.zip'
    assert m._EXPECTED_SHA256 == um['digest']


def test_file_sha256_matches_hashlib(um, tmp_path):
    p = tmp_path / 'blob.bin'
    data = os.urandom(3_000_000)          # أكبر من دفعة القراءة
    p.write_bytes(data)

    assert um['m'].file_sha256(str(p)) == hashlib.sha256(data).hexdigest()


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
