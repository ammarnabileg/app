# -*- coding: utf-8 -*-
"""«مزامنة الآن» و«مزامنة المستخدمين» وحدهما كلَّ ساعة (٢.٢٨).

- قفلٌ واحد للزرّ وللخيط: مزامنتان معًا تتداخل سجلّاتهما.
- الموعد: أوّلَ مرّة فورًا، ثم لا قبل ساعة.
- بعد التشغيل: سطرُ حالةٍ في صفحة البصمة، ويُوقَظ الرفعُ السحابيّ.
- أجهزةُ ADMS: التشغيلُ المتكرّر لا يُكدّس أوامرَ معلّقة.
"""
import importlib
import os
import sqlite3
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.device_autosync as das
    importlib.reload(das)
    return db, das, str(tmp_path)


def test_one_lock_for_the_button_and_the_hourly_run(env, monkeypatch):
    db, das, _ = env
    import fingerprint_sync
    gate, started = threading.Event(), threading.Event()

    def slow():
        started.set(); gate.wait(5)
        return {'success': True, 'attendance_synced': 3, 'successful_devices': 1, 'total_devices': 1}
    monkeypatch.setattr(fingerprint_sync, 'sync_all_fingerprint_devices', slow)
    monkeypatch.setattr(fingerprint_sync, 'sync_users_to_employees', lambda: {'employees_added': 0})

    t = threading.Thread(target=das.run_punches); t.start()
    assert started.wait(2)
    assert das.busy()
    assert das.run_users() is None, 'مزامنتان معًا'
    assert das.run_punches() is None
    gate.set(); t.join(2)
    assert not das.busy()
    assert das.run_users() == {'employees_added': 0}


def test_due_first_time_then_not_before_an_hour(env):
    db, das, _ = env
    now = time.time()
    assert das.due(now=now, last='')
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now - 1800))
    assert not das.due(now=now, last=stamp)
    stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now - 3601))
    assert das.due(now=now, last=stamp)


def test_a_run_records_its_summary_and_wakes_the_cloud_upload(env, monkeypatch):
    db, das, _ = env
    import fingerprint_sync
    from utils import cloud_sync
    monkeypatch.setattr(fingerprint_sync, 'sync_all_fingerprint_devices',
                        lambda: {'success': True, 'attendance_synced': 12, 'successful_devices': 2, 'total_devices': 2})
    monkeypatch.setattr(fingerprint_sync, 'sync_users_to_employees', lambda: {'employees_added': 1})
    cloud_sync.WAKE.clear()
    das.run_once()
    st = das.status()
    assert st['last'] and st['enabled']
    assert 'البصمات: 12 جديدة من 2/2 جهاز' in st['result']
    assert 'أُضيف 1 موظّف' in st['result']
    assert cloud_sync.WAKE.is_set(), 'البصماتُ الجديدة تنتظر دورةَ الرفع التالية'
    assert not das.due(), 'بعد التشغيل مباشرةً لا يحين موعدٌ آخر'


def test_disabled_or_no_devices_means_no_run(env, monkeypatch):
    db, das, _ = env
    calls = []
    monkeypatch.setattr(das, 'run_once', lambda: calls.append(1))
    monkeypatch.setattr(das, 'START_DELAY', 0)
    monkeypatch.setattr(das, 'CHECK_EVERY', 0.05)
    t = threading.Thread(target=das.background_worker, daemon=True); t.start()
    time.sleep(0.3)
    assert not calls, 'لا أجهزة نشطة'
    conn = sqlite3.connect(os.path.join(env[2], 'hr_system.db'))
    conn.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active) VALUES ('A', '10.0.0.9', 1)")
    conn.commit(); conn.close()
    db.set_setting(das.SETTING_ENABLED, '0')
    time.sleep(0.3)
    assert not calls, 'مُطفأة'
    db.set_setting(das.SETTING_ENABLED, '1')
    time.sleep(0.3)
    assert calls, 'مفعّلةٌ وفيها جهاز: تعمل'


def test_repeated_adms_user_sync_does_not_pile_up_commands(env):
    db, das, tmp = env
    path = os.path.join(tmp, 'hr_system.db')
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active, is_adms) VALUES ('ADMS', 'SN123', 1, 1)")
    for n in ('101', '102', '103'):
        conn.execute("INSERT INTO employees (name, employee_number, department, position, hire_date, salary, is_active)"
                     " VALUES (?, ?, 'D', 'P', '2026-01-01', 100, 1)", ('x' + n, n))
    conn.commit()
    import fingerprint_sync
    m = fingerprint_sync.FingerprintSyncManager(db_path=path)
    count = lambda: conn.execute("SELECT COUNT(*) FROM adms_commands WHERE status = 'PENDING'").fetchone()[0]
    m.sync_users_to_employees()
    first = count()
    assert first == 6, first                  # أمران لكلّ موظّف
    for _ in range(3):                        # ثلاث ساعات والجهازُ لم يأخذها
        m.sync_users_to_employees()
    assert count() == first, 'تراكمت الأوامرُ المعلّقة'
    conn.execute("UPDATE adms_commands SET status = 'DONE'")
    conn.commit()
    m.sync_users_to_employees()
    assert count() == 6, 'بعد أن أخذها الجهاز تُرسَل من جديد'
    conn.close()


def test_the_page_shows_the_hourly_status_and_a_switch():
    html = open(os.path.join(ROOT, 'templates', 'fingerprint_dashboard.html'), encoding='utf-8').read()
    assert 'id="deviceAutosync"' in html and "url_for('attendance.toggle_device_autosync')" in html
    src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert 'device_autosync import background_worker' in src
    routes = open(os.path.join(ROOT, 'routes', 'attendance_routes.py'), encoding='utf-8').read()
    assert 'device_autosync.run_punches()' in routes and 'target=device_autosync.run_users' in routes
