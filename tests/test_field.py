"""نظام المناديب: خط السير، والمحطات، والصورة الحيّة.

النظام كلّه يقوم على بيانات يرسلها جهاز المندوب، وهي قابلة للتزوير.
فالاختبارات هنا تسأل سؤالًا واحدًا: هل يظهر ما لا يُصدَّق، أم يُبتلع
بصمت؟

يُشغَّل:  python -m pytest tests/test_field.py -v
"""
import io
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import field


def _t(minutes_ago):
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S')


def _pt(lat, lon, minutes_ago, acc=10):
    return {'latitude': lat, 'longitude': lon, 'accuracy': acc,
            'recorded_at': _t(minutes_ago)}


# ------------------------------------------------- خط السير: ما لا يُخفى

def test_a_gap_is_drawn_as_a_gap_not_as_a_straight_line():
    """بيت القصيد.

    المتصفح يتوقّف عن قراءة الموقع حين تُقفل الشاشة. فلو وُصلت النقطتان
    اللتان قبل الانقطاع وبعده بخطٍّ متّصل، بدا المندوب كأنه سار الطريق
    كلّه — وهو أسوأ من ألّا يُعرف شيء، لأنه يوهم بمعرفة.
    """
    pts = [_pt(29.33, 48.00, 60), _pt(29.331, 48.001, 59),
           _pt(29.35, 48.02, 20), _pt(29.351, 48.021, 19)]

    segs, stats = field.build_path(pts)
    kinds = [s['kind'] for s in segs]

    assert 'gap' in kinds, 'لم تُعلَّم الفجوة'
    assert stats['gaps'] == 1
    gap = [s for s in segs if s['kind'] == 'gap'][0]
    assert gap['seconds'] > field.GAP_SECONDS


def test_the_gap_distance_is_not_counted_as_walked():
    """ما لم يُرَ لا يُحسب مسافةً مقطوعة."""
    close = field.build_path([_pt(29.33, 48.00, 3), _pt(29.34, 48.01, 2)])[1]
    apart = field.build_path([_pt(29.33, 48.00, 60), _pt(29.34, 48.01, 2)])[1]

    assert close['distance_meters'] > 1000
    assert apart['distance_meters'] == 0, 'حُسبت مسافة الفجوة سيرًا'


def test_an_impossible_speed_is_flagged():
    """قفزة ٦٠ كم في دقيقة: إمّا تزوير أو خلل. تُعلَّم في الحالين."""
    segs, stats = field.build_path([_pt(29.33, 48.00, 2), _pt(29.90, 48.60, 1)])

    assert stats['jumps'] == 1
    jump = [s for s in segs if s['kind'] == 'jump'][0]
    assert jump['kmh'] > field.IMPOSSIBLE_KMH


def test_normal_driving_is_not_flagged():
    """١٠٠ كم/س سيرٌ عادي — لا يجوز أن يُعلَّم."""
    stats = field.build_path([_pt(29.3300, 48.0000, 2),
                              _pt(29.3450, 48.0000, 1)])[1]
    assert stats['jumps'] == 0


def test_points_out_of_order_are_sorted():
    segs, stats = field.build_path([_pt(29.34, 48.01, 1), _pt(29.33, 48.00, 5)])
    assert stats['first_at'] < stats['last_at']


def test_a_single_point_does_not_crash():
    segs, stats = field.build_path([_pt(29.33, 48.00, 1)])
    assert stats['points'] == 1 and stats['distance_meters'] == 0

    assert field.build_path([])[1]['points'] == 0


# ------------------------------------------------- ما يُقبل من الجهاز

def _conn(tmp_path):
    import importlib
    import sqlite3

    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    importlib.reload(field)
    db.init_db()

    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.row_factory = sqlite3.Row
    field.init_schema(con)
    return con


def test_nonsense_points_are_dropped_not_stored(tmp_path):
    """نقطة بلا زمن أو بإحداثيات مستحيلة تُفسد الخط كلّه لو خُزّنت."""
    con = _conn(tmp_path)
    trip_id, _ = field.open_trip(con, 7)

    n = field.add_points(con, trip_id, 7, [
        {'lat': 29.33, 'lon': 48.00, 'at': _t(5)},          # سليمة
        {'lat': 0, 'lon': 999, 'at': _t(4)},                # خارج المدى
        {'lat': 91, 'lon': 48, 'at': _t(4)},                # خارج المدى
        {'lat': 29.33, 'lon': 48.00},                       # بلا زمن
        {'lat': 'abc', 'lon': 48.00, 'at': _t(3)},          # ليست رقمًا
        {'lat': 29.33, 'lon': 48.00,                        # من المستقبل
         'at': (datetime.now() + timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')},
    ])

    assert n == 1
    assert con.execute('SELECT COUNT(*) FROM field_track_points').fetchone()[0] == 1


def test_a_batch_is_bounded(tmp_path):
    """دفعة ضخمة من جهاز واحد لا تملأ القاعدة."""
    con = _conn(tmp_path)
    trip_id, _ = field.open_trip(con, 7)
    huge = [{'lat': 29.33, 'lon': 48.0, 'at': _t(1)}] * (field.MAX_POINTS_PER_BATCH + 200)

    assert field.add_points(con, trip_id, 7, huge) == field.MAX_POINTS_PER_BATCH


def test_the_day_trip_is_resumed_not_duplicated(tmp_path):
    con = _conn(tmp_path)
    a, created_a = field.open_trip(con, 7)
    b, created_b = field.open_trip(con, 7)

    assert a == b
    assert created_a is True and created_b is False


# ------------------------------------------------------- رمز الزيارة

def test_a_token_works_once(tmp_path):
    con = _conn(tmp_path)
    tok = field.issue_token(con, 7, 3, 'in')

    assert field.consume_token(con, tok, 7, 3, 'in')[0] is True
    ok, why = field.consume_token(con, tok, 7, 3, 'in')
    assert ok is False and 'استُعمل' in why


def test_a_token_belongs_to_one_employee_station_and_step(tmp_path):
    con = _conn(tmp_path)
    tok = field.issue_token(con, 7, 3, 'in')

    assert field.consume_token(con, tok, 8, 3, 'in')[0] is False     # موظف آخر
    assert field.consume_token(con, tok, 7, 4, 'in')[0] is False     # محطة أخرى
    assert field.consume_token(con, tok, 7, 3, 'out')[0] is False    # خطوة أخرى
    assert field.consume_token(con, tok, 7, 3, 'in')[0] is True      # وما زال صالحًا


def test_an_expired_token_is_refused(tmp_path):
    """الصورة تُلتقط الآن، لا تُحضَّر قبل الزيارة."""
    con = _conn(tmp_path)
    tok = field.issue_token(con, 7, 3, 'in')
    old = (datetime.now() - timedelta(seconds=field.TOKEN_TTL_SECONDS + 60)
           ).strftime('%Y-%m-%d %H:%M:%S')
    con.execute('UPDATE field_visit_tokens SET issued_at = ? WHERE token = ?', (old, tok))

    ok, why = field.consume_token(con, tok, 7, 3, 'in')
    assert ok is False and 'مهلة' in why


def test_an_unknown_or_empty_token_is_refused(tmp_path):
    con = _conn(tmp_path)
    assert field.consume_token(con, 'made-up', 7, 3, 'in')[0] is False
    assert field.consume_token(con, '', 7, 3, 'in')[0] is False
    assert field.consume_token(con, None, 7, 3, 'in')[0] is False


# --------------------------------------------------------- الصورة

def _jpeg(exif=False, size=(640, 480)):
    from PIL import Image
    img = Image.new('RGB', size, (40, 90, 140))
    buf = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[271] = 'TestPhone'
        img.save(buf, 'JPEG', exif=e)
    else:
        img.save(buf, 'JPEG')
    return buf.getvalue()


def test_a_gallery_photo_is_recognised_by_its_exif():
    """كاميرا الصفحة تُخرج canvas بلا EXIF؛ صور المعرض تحمله."""
    assert field.has_exif(_jpeg(exif=True)) is True
    assert field.has_exif(_jpeg(exif=False)) is False
    assert field.has_exif(b'not an image') is False


def test_the_stamp_is_readable_arabic():
    """الختم بلا حروف موصولة نصٌّ لا يُقرأ، فلا قيمة له عند المراجعة.

    وقعتُ في هذا: كنت أُشكّل دائمًا بـarabic_reshaper، فحين وُجد raqm
    شُكّل النصّ مرّتين وخرج مفكّكًا مقلوبًا. الاختبار يقارن الرسم
    بمربّع «لا glyph» — وهو ما أغفله فحصي الأول.
    """
    from PIL import Image

    out = field.stamp_photo(_jpeg(), [
        'موظف — صيدلية الروضة',
        'دخول · 2026-09-15 13:30:26',
        '29.33000, 48.00000 · دقّة 10م · بُعد 0م',
    ])

    img = Image.open(io.BytesIO(out))
    assert img.format == 'JPEG'
    assert img.size[0] <= 1280 and img.size[1] <= 1280

    font = field._font(20)
    assert field._covers(font, 'م-·—/:,5'), 'الخط المختار لا يغطّي محارف الختم'


def test_the_chosen_font_is_not_a_notdef_trap():
    """مربّع .notdef له حبر، فـgetbbox() ترى «موجودًا» ما ليس موجودًا."""
    from PIL import ImageFont

    f = field._font(24)
    assert field._covers(f, 'م5') is True
    # محرف في منطقة الاستعمال الخاص مفقود في كل خط — لو مرّ فالفحص كاذب
    assert field._covers(f, '') is False


def test_a_huge_photo_is_shrunk_not_stored_whole():
    """عشرات الزيارات يوميًّا بصور هاتف حديثة تملأ قرص العميل."""
    big = _jpeg(size=(4000, 3000))
    out = field.stamp_photo(big, ['اختبار'])

    from PIL import Image
    assert max(Image.open(io.BytesIO(out)).size) <= 1280
    assert len(out) < len(big)


def test_an_oversized_upload_is_refused(tmp_path):
    con = _conn(tmp_path)
    try:
        field.save_visit_photo(con, 1, 'in', b'x' * (field.MAX_PHOTO_BYTES + 1), {})
        assert False, 'قُبلت صورة فوق الحدّ'
    except ValueError:
        pass


def test_a_saved_photo_is_hashed_and_on_disk(tmp_path):
    con = _conn(tmp_path)
    con.execute("INSERT INTO field_visits (id, employee_id, station_id) VALUES (1, 7, 3)")

    pid, rel, digest, exif = field.save_visit_photo(con, 1, 'in', _jpeg(), {
        'lat': 29.33, 'lon': 48.0, 'accuracy': 10, 'distance': 5,
        'station_name': 'صيدلية الروضة', 'employee_name': 'موظف',
    })
    con.commit()

    assert len(digest) == 64
    assert exif is False
    full = os.path.join(field.photos_dir(), rel)
    assert os.path.exists(full)
    assert not os.path.exists(full + '.part'), 'بقي ملف نصفه'

    import hashlib
    assert hashlib.sha256(open(full, 'rb').read()).hexdigest() == digest


# ------------------------------------------------- خطة اليوم والالتزام

def _station(con, sid, name, lat=29.33, lon=48.0):
    con.execute('''INSERT INTO field_stations (id, name, latitude, longitude, radius_meters)
                   VALUES (?, ?, ?, ?, 100)''', (sid, name, lat, lon))


def test_the_plan_reports_what_was_missed(tmp_path):
    con = _conn(tmp_path)
    today = datetime.now().strftime('%Y-%m-%d')
    _station(con, 1, 'محطة أولى')
    _station(con, 2, 'محطة ثانية')
    for i, sid in enumerate((1, 2)):
        con.execute('''INSERT INTO field_assignments (employee_id, station_id, visit_date, sort_order)
                       VALUES (7, ?, ?, ?)''', (sid, today, i))
    con.execute('''INSERT INTO field_visits (employee_id, station_id, check_in_at,
                   check_out_at, status) VALUES (7, 1, ?, ?, 'closed')''',
                (today + ' 09:00:00', today + ' 09:20:00'))
    con.commit()

    s = field.day_summary(con, 7, today)
    assert s['stations_total'] == 2
    assert s['stations_done'] == 1
    assert s['missed'] == ['محطة ثانية']


def test_an_open_visit_counts_as_neither_done_nor_missed(tmp_path):
    con = _conn(tmp_path)
    today = datetime.now().strftime('%Y-%m-%d')
    _station(con, 1, 'محطة')
    con.execute('''INSERT INTO field_assignments (employee_id, station_id, visit_date)
                   VALUES (7, 1, ?)''', (today,))
    con.execute('''INSERT INTO field_visits (employee_id, station_id, check_in_at, status)
                   VALUES (7, 1, ?, 'open')''', (today + ' 09:00:00',))
    con.commit()

    plan = field.day_plan(con, 7, today)
    assert plan[0]['state'] == 'inside'
    s = field.day_summary(con, 7, today)
    assert s['stations_done'] == 0 and s['open'] == ['محطة']


def test_an_inactive_station_leaves_the_plan(tmp_path):
    con = _conn(tmp_path)
    today = datetime.now().strftime('%Y-%m-%d')
    _station(con, 1, 'محطة مغلقة')
    con.execute('UPDATE field_stations SET is_active = 0 WHERE id = 1')
    con.execute('''INSERT INTO field_assignments (employee_id, station_id, visit_date)
                   VALUES (7, 1, ?)''', (today,))
    con.commit()

    assert field.day_plan(con, 7, today) == []


# ----------------------------------------------------------- المسافة

def test_haversine_matches_a_known_distance():
    """الكويت → الدمام ≈ ٣٦٠ كم."""
    d = field.haversine(29.3759, 47.9774, 26.4207, 50.0888)
    assert 350000 < d < 400000

    assert field.haversine(29.33, 48.0, 29.33, 48.0) == 0


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
