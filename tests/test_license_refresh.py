# -*- coding: utf-8 -*-
"""«حدّث الترخيص من onz.one الآن»: تعديل في اللوحة (زي حد الأجهزة) يوصل على طول من غير ما يستنّى ساعة."""
import importlib
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


@pytest.fixture
def lic(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.license as L
    importlib.reload(L)
    monkeypatch.setattr(L, 'get_system_hwid', lambda: 'a' * 32)
    L.save_license_key('LE2-test-key')
    conn = db.get_db_connection()
    # آخر فحص ناجح من دقيقة — يعني الكاش ساري لساعة
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('UPDATE license_settings SET last_check = ?, last_ok_check = ?, last_status_ok = 1 WHERE id=1',
                 (now, now))
    conn.commit()
    L.save_secure_max_devices(3)
    calls = []

    def post(url, json=None, timeout=None):
        calls.append(json)
        return _Resp({'success': True, 'message': 'Valid', 'max_devices': 9})
    monkeypatch.setattr(L.requests, 'post', post)
    return L, calls


def test_cached_check_does_not_see_panel_change(lic):
    L, calls = lic
    ok, msg = L.check_online_license_secure('LE2-test-key')
    assert ok and 'Cached' in msg and calls == []
    assert L.get_effective_max_devices() == 3, 'من غير تحديث: القديم لحد ما الكاش يخلص'


def test_refresh_asks_panel_now(lic):
    L, calls = lic
    ok, msg = L.refresh_license_now()
    assert ok, msg
    assert len(calls) == 1 and calls[0]['license_key'] == 'LE2-test-key'
    assert L.get_effective_max_devices() == 9
    assert '9' in msg


def test_refresh_without_license(lic, monkeypatch):
    L, calls = lic
    L.logout_license()
    ok, msg = L.refresh_license_now()
    assert not ok and calls == []


def test_refresh_offline_keeps_grace(lic, monkeypatch):
    """الزرار مايقفلش البرنامج لو النت واقع: المهلة شغالة زي ما هي."""
    L, calls = lic
    import requests

    def down(*a, **k):
        raise requests.exceptions.ConnectionError('offline')
    monkeypatch.setattr(L.requests, 'post', down)
    ok, msg = L.refresh_license_now()
    assert ok, msg
