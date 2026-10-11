# -*- coding: utf-8 -*-
"""ساعة أجهزة ADMS (utils/device_clock) — جهاز AJE1262000048 كان بيطلع ساعة قدّام:

سطر السجلّ اتكتب 13:02:49 بساعة السيرفر، والجهاز ختم نفس الحدث 14:02:49. ويندوز متظبط (Arab Standard
Time) والجهاز بيفهم الأمر — بس بيضيف على الرقم أكتر من +3. فالرقم المبعوث بقى من الساعة الظاهرة على
السيرفر وإعداد «المنطقة الزمنية على الأجهزة»، والفرق بيتقاس من أحداث الجهاز نفسه.
"""
import calendar
import importlib
import re
from datetime import datetime, timedelta

import pytest

from tests.test_device_access import env  # noqa: F401


def test_epoch_makes_the_device_show_the_server_wall_clock():
    from utils import device_clock as dc
    wall = datetime(2026, 10, 11, 13, 2, 49)
    for off in (180, 240, -300, 330):
        sent = dc.device_epoch(now=wall, offset=off)
        # الجهاز: ساعته = الرقم (UTC) + منطقته المتظبطة عليه.
        shown = datetime(1970, 1, 1) + timedelta(seconds=sent + off * 60)
        assert shown == wall, off
    assert dc.label(180) == 'GMT+3' and dc.label(330) == 'GMT+5:30' and dc.label(-300) == 'GMT-5'


def test_default_offset_is_the_company_zone_kuwait(env):
    from utils import device_clock as dc
    from utils.db import set_setting
    assert dc.offset_minutes() == 180
    set_setting(dc.SETTING, '240')
    assert dc.offset_minutes() == 240


def test_skew_is_measured_only_from_future_or_live_events(env):
    db, da, con = env
    from utils import device_clock as dc
    now = datetime(2026, 10, 11, 13, 2, 49)
    body = 'OPLOG 108\t0\t2026-10-11 14:02:49\ttimeFormat\t0\t0\t0\n'
    assert dc.record_skew(con, 2, body, now=now) == 3600
    assert dc.skew_label(3600) == 'متقدّم 1 ساعة'
    con.commit()
    # بصمات قديمة متأخرة في الرفع: مش مقياس — القياس اللي قبلها يفضل.
    assert dc.record_skew(con, 2, '101\t2026-10-11 09:00:00\t0\t1\n', now=now) is None
    assert con.execute('SELECT clock_skew_seconds FROM fingerprint_devices WHERE id = 2').fetchone()[0] == 3600
    # بعد الضبط: حدث لحظي مظبوط ← صفر.
    assert dc.record_skew(con, 2, '101\t2026-10-11 13:02:40\t0\t1\n', now=now) == 0
    assert dc.skew_label(0) == 'مظبوط'


@pytest.fixture
def adms(env):
    db, da, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    c = adms_server.app.test_client()
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    return c, con


def _sent_epoch(c, con):
    con.execute("UPDATE adms_commands SET status = 'OK' WHERE command_type != 'SET TIME'")
    con.commit()
    for _ in range(5):
        body = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
        m = re.search(r'SET OPTIONS DateTime=(\d+)', body)
        if m:
            return int(m.group(1))
    raise AssertionError('مفيش أمر ضبط وقت')


def test_set_time_command_follows_the_setting(adms):
    c, con = adms
    from utils.db import set_setting
    from utils import device_clock as dc
    set_setting(dc.SETTING, '240')                   # الجهاز بيضيف +4 (أو توقيت صيفي)
    from utils.fingerprint_utils import queue_adms_set_time
    queue_adms_set_time(2)
    before = datetime.now()
    sent = _sent_epoch(c, con)
    shown = datetime(1970, 1, 1) + timedelta(seconds=sent + 240 * 60)
    assert abs((shown - before).total_seconds()) < 5, 'الجهاز اللي بيضيف +4 هيطلع بساعة السيرفر'
    # والفرق القديم (+3) كان هيطلع الجهاز ساعة قدّام:
    assert sent == calendar.timegm(before.timetuple()) - 240 * 60 or abs(
        sent - (calendar.timegm(before.timetuple()) - 240 * 60)) < 5


def test_attlog_from_a_fast_device_shows_up_as_skew(adms):
    c, con = adms
    future = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=f'101\t{future}\t0\t1\t0\t0\n',
           content_type='text/plain')
    skew = con.execute('SELECT clock_skew_seconds FROM fingerprint_devices WHERE id = 2').fetchone()[0]
    assert 3500 < skew < 3700


def test_settings_page_saves_offset_and_queues_sync(env):
    db, da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    c = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    page = c.get('/settings').get_data(as_text=True)
    assert 'ساعة أجهزة البصمة' in page and 'GMT+3' in page
    c.post('/settings/device-clock', data={'device_tz_offset_minutes': '240', 'sync_now': '1'})
    from utils import device_clock as dc
    assert dc.offset_minutes() == 240
    assert con.execute("SELECT COUNT(*) FROM adms_commands WHERE command_type = 'SET TIME' AND status = 'PENDING'"
                       ).fetchone()[0] >= 1
