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


def test_a_device_that_adds_an_hour_is_corrected_automatically_with_the_setting_on_plus3(adms):
    """السيناريو بالظبط: الإعداد +3 (الكويت)، والجهاز بعد كل ضبط بيطلع ساعة قدّام."""
    c, con = adms
    from utils import device_clock as dc
    from utils.fingerprint_utils import queue_adms_set_time
    assert dc.offset_minutes() == 180
    queue_adms_set_time(2)
    wall = datetime.now()
    sent = _sent_epoch(c, con)
    assert abs(sent - (calendar.timegm(wall.timetuple()) - 3 * 3600)) < 5, 'التعويض صفر في الأول'
    # الجهاز بيأكّد كل أوامر الضبط اللي استلمها (اليومي + اللي من الزرار).
    for (cmd,) in con.execute("SELECT id FROM adms_commands WHERE command_type = 'SET TIME'").fetchall():
        c.post('/iclock/devicecmd?SN=SNADMS1', data=f'ID={cmd}&Return=0&CMD=SET OPTIONS', content_type='text/plain')
    assert {r[0] for r in con.execute("SELECT status FROM adms_commands WHERE command_type = 'SET TIME'")} == {'OK'}
    # الجهاز (فيرمويره +1) بيبعت بصمة بساعة زيادة:
    ahead = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=f'101\t{ahead}\t0\t1\t0\t0\n',
           content_type='text/plain')
    assert con.execute('SELECT clock_correction_seconds FROM fingerprint_devices WHERE id = 2').fetchone()[0] == 3600
    wall = datetime.now()
    sent = _sent_epoch(c, con)
    # الجهاز: الرقم + 3 + ساعته الزيادة = ساعة السيرفر.
    assert abs((sent + 3 * 3600 + 3600) - calendar.timegm(wall.timetuple())) < 5
    # بعد الضبط: بصمة مظبوطة ← «مظبوط»، والتعويض ما يزيدش.
    now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=f'101\t{now_s}\t0\t1\t0\t0\n',
           content_type='text/plain')
    row = con.execute('SELECT clock_skew_seconds, clock_correction_seconds FROM fingerprint_devices WHERE id = 2').fetchone()
    assert tuple(row) == (0, 3600)
    assert dc.offset_minutes() == 180, 'الإعداد فضل +3'


def test_no_learning_without_a_confirmed_sync(adms):
    """ساعة متقدّمة من غير ضبط اتأكّد (ممكن حد ظبطها بإيده) ← بتتعرض بس، مفيش تعويض."""
    c, con = adms
    ahead = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    con.execute("DELETE FROM adms_commands")
    con.commit()
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=f'101\t{ahead}\t0\t1\t0\t0\n',
           content_type='text/plain')
    row = con.execute('SELECT clock_skew_seconds, clock_correction_seconds FROM fingerprint_devices WHERE id = 2').fetchone()
    assert row[0] > 3000 and not row[1]


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
    c.post('/settings/device-clock', data={'device_tz_offset_minutes': '180', 'sync_now': '1'})
    from utils import device_clock as dc
    assert dc.offset_minutes() == 180
    con.execute('UPDATE fingerprint_devices SET clock_correction_seconds = 3600 WHERE id = 2')
    con.commit()
    assert 'إلغاء التعويض' in c.get('/settings').get_data(as_text=True)
    c.post('/settings/device-clock', data={'reset_device': '2'})
    assert not con.execute('SELECT clock_correction_seconds FROM fingerprint_devices WHERE id = 2').fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM adms_commands WHERE command_type = 'SET TIME' AND status = 'PENDING'"
                       ).fetchone()[0] >= 1


def test_ntp_device_is_flagged_and_never_auto_corrected(adms):
    """جهاز AJE1262000033: «OPLOG 108 … NTPService» — بياخد الوقت من NTP بمنطقته هو، فالتعويض
    هيتخانق معاه ويتراكم كل مرة. بيتعلّم عليه ويتلغي تعويضه، والحل بيتقال على الجهاز."""
    c, con = adms
    from utils import device_clock as dc
    dc.ensure_schema(con)
    con.execute('UPDATE fingerprint_devices SET clock_correction_seconds = 3600 WHERE id = 2')
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status, updated_at) "
                "VALUES (2, 'SET TIME', '\"x\"', 'OK', CURRENT_TIMESTAMP)")
    con.commit()
    ahead = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=OPERLOG&Stamp=1', data=f'OPLOG 108\t365\t{ahead}\tNTPService\t0\t0\t0\n',
           content_type='text/plain')
    row = con.execute('SELECT clock_skew_seconds, clock_correction_seconds, clock_ntp_at FROM fingerprint_devices '
                      'WHERE id = 2').fetchone()
    assert row[0] > 3000 and row[1] == 0 and row[2]
    # عملية تانية متقدّمة من غير NTP في نفس السطر: برضه مفيش تعلّم (الجهاز معلَّم NTP).
    c.post('/iclock/cdata?SN=SNADMS1&table=OPERLOG&Stamp=1', data=f'OPLOG 4\t365\t{ahead}\t365\t0\t0\t0\n',
           content_type='text/plain')
    assert con.execute('SELECT clock_correction_seconds FROM fingerprint_devices WHERE id = 2').fetchone()[0] == 0


def test_correction_is_capped():
    from utils import device_clock as dc
    assert dc.MAX_CORRECTION == 3 * 3600
