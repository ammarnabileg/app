# -*- coding: utf-8 -*-
"""المرحلة ١ (جانب البرنامج): صحّة الشركة للوحة، ونسخة خارج السيرفر، وقناة التحديثات."""
import os
from datetime import datetime, timedelta

from tests.test_device_access import env  # noqa: F401
from tests.test_device_drivers import web  # noqa: F401


def test_health_numbers_only(env):
    db, da, con = env
    from utils import deployment
    now = datetime(2026, 10, 9, 12, 0)
    con.execute("UPDATE fingerprint_devices SET last_activity = '2026-10-09 11:00:00' WHERE id = 1")
    con.execute("UPDATE fingerprint_devices SET last_activity = '2026-10-01 11:00:00' WHERE id = 2")
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) "
                "VALUES (1, 1, '2026-10-09 08:00:00', 0)")
    con.execute("INSERT INTO device_sync_log (ok, message) VALUES (0, 'K40: timed out')")
    con.commit()
    h = deployment.health(now=now)
    assert h['devices_active'] == 2 and h['devices_silent'] == 1
    assert h['last_punch_at'] == '2026-10-09 08:00:00' and h['employees_active'] == 1
    assert h['last_sync_error'] == 'K40: timed out' and h['db_mb'] >= 0 and h['disk_free_mb'] > 0
    assert set(h) <= set(deployment.HEALTH_KEYS)
    r = deployment.report()
    assert r['devices_active'] == 2, 'بيتبعت مع فحص الترخيص'
    for v in r.values():
        assert 'موظف 101' not in str(v), 'مفيش أسماء'


class Put:
    def __init__(self, code=201):
        self.code, self.calls = code, []

    def request(self, method, url, data=None, auth=None, timeout=None):
        self.calls.append((method, url, auth, len(data.read())))
        return type('R', (), {'status_code': self.code})()


def test_offsite_copy_and_webdav(env, tmp_path):
    db, da, con = env
    from utils import backup as bk
    from utils.db import get_setting
    src = tmp_path / 'auto-2026-10-09-120000.db'
    src.write_bytes(b'x' * 100)
    assert bk.offsite_push(str(src)) is None, 'مش مضبوط = مفيش حاجة'
    import pytest
    with pytest.raises(ValueError):
        bk.save_offsite_settings('', 'http://cloud.example.com/dav', 'u', 'p')
    off = tmp_path / 'nas'
    bk.save_offsite_settings(str(off), 'https://cloud.example.com/dav/', 'hr', 'S3cret')
    assert get_setting('backup_offsite_password') != 'S3cret', 'متشفّرة'
    http = Put()
    ok, msg = bk.offsite_push(str(src), keep=2, http=http)
    assert ok and (off / src.name).exists()
    assert http.calls == [('PUT', 'https://cloud.example.com/dav/' + src.name, ('hr', 'S3cret'), 100)]
    for i in range(3):
        p = tmp_path / f'auto-2026-10-1{i}-120000.db'
        p.write_bytes(b'y')
        bk.offsite_push(str(p), keep=2, http=http)
    assert sorted(os.listdir(off)) == ['auto-2026-10-11-120000.db', 'auto-2026-10-12-120000.db'], 'نفس العدد'
    ok, msg = bk.offsite_push(str(src), http=Put(507))
    assert not ok and 'WebDAV' in msg and bk.offsite_settings()['last_ok'] is False
    from utils import deployment
    assert deployment.health()['offsite_backup_ok'] is False


def test_auto_backup_pushes_offsite(env, tmp_path):
    db, da, con = env
    from utils import backup as bk
    bk.save_offsite_settings(str(tmp_path / 'nas'), '', '')
    ok, msg = bk.run_auto_backup(force=True)
    assert ok and 'خارج السيرفر' in msg
    assert len(os.listdir(tmp_path / 'nas')) == 1


def test_update_channel_goes_to_the_panel(web, monkeypatch):
    c, con, A = web
    from utils import update_manager as um
    assert 'channel' not in um._license_key_param()
    c.post('/settings/update-channel', data={'update_channel': 'beta'})
    assert um._license_key_param()['channel'] == 'beta'
    assert 'تجريبية (beta)' in c.get('/settings').get_data(as_text=True)
