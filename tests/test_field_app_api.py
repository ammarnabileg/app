"""واجهة التطبيق: هل يستطيع تطبيقٌ أصليّ أن يسجّل زيارة أصلًا؟

التطبيق يستعمل مسارات البوابة نفسها بلا تغيير — هذا وعدٌ قطعتُه حين
صمّمتُ الواجهة. وهذه الاختبارات تفحص الوعد بالمرور على المسارات كما
يمرّ التطبيق: طلب رمز، ثم صورة من **كاميرا أصلية** (بـEXIF كما تُخرجه
كل كاميرا هاتف)، ثم تسجيل الدخول.

والعطل الذي أمسكه هذا الملف: `_check_photo` كانت ترفض كل صورة تحمل
EXIF، على أساس أن شاشة الويب تُخرج canvas بلا EXIF فوجودُه يعني ملفًا
من المعرض. وهي قاعدة تصلح للويب وحده — وكانت سترفض كل صورة يلتقطها
التطبيق، أي أن التطبيق ما كان ليعمل يومًا واحدًا.

يُشغَّل:  python -m pytest tests/test_field_app_api.py -v
"""
import io
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATION = (29.3759, 47.9774)        # مدينة الكويت


def _camera_jpeg(taken_at, size=(320, 240)):
    """ما تُخرجه كاميرا هاتف حقيقية: JPEG وفيه EXIF."""
    from PIL import Image
    from PIL.ExifTags import Base

    img = Image.new('RGB', size, (90, 120, 160))
    ex = img.getexif()
    ex[Base.Orientation.value] = 1
    ex[Base.Make.value] = 'Samsung'
    ex[Base.Model.value] = 'SM-A546E'
    ex[Base.DateTimeOriginal.value] = taken_at.strftime('%Y:%m:%d %H:%M:%S')
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=ex.tobytes())
    return buf.getvalue()


@pytest.fixture
def rep(tmp_path):
    """مندوبٌ حقيقي، ومحطةٌ في خطة يومه، وجلسةٌ كجلسة التطبيق."""
    import importlib

    os.environ['HR_DATA_DIR'] = str(tmp_path)

    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [
        f for f in A.app.before_request_funcs.get(None, [])
        if f.__name__ != 'check_license_globally'
    ]
    A.app.config['TESTING'] = True

    path = os.path.join(str(tmp_path), 'hr_system.db')
    con = sqlite3.connect(path)
    cur = con.cursor()

    from utils import field
    field.init_schema(con)
    field.set_module_enabled(con, True)

    required = [r for r in cur.execute('PRAGMA table_info(employees)')
                if r[3] == 1 and r[4] is None and r[1] != 'id']
    v = {r[1]: (1 if r[2].upper() in ('INTEGER', 'REAL', 'NUMERIC') else 'x')
         for r in required}
    v.update({'employee_number': 'F100', 'name': 'مندوب الاختبار', 'is_active': 1})
    cur.execute(f"INSERT INTO employees ({', '.join(v)}) "
                f"VALUES ({', '.join('?' * len(v))})", list(v.values()))
    emp_id = cur.lastrowid

    cur.execute('INSERT INTO field_stations (name, latitude, longitude, radius_meters,'
                ' is_active) VALUES (?, ?, ?, ?, 1)',
                ('صيدلية الروضة', STATION[0], STATION[1], 100))
    station_id = cur.lastrowid

    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('INSERT INTO field_assignments (employee_id, station_id, visit_date)'
                ' VALUES (?, ?, ?)', (emp_id, station_id, today))

    from werkzeug.security import generate_password_hash
    cur.execute('INSERT INTO users (username, password, full_name, role, is_active,'
                ' employee_id) VALUES (?,?,?,?,1,?)',
                ('rep', generate_password_hash('x'), 'مندوب', 'user', emp_id))
    user_id = cur.lastrowid
    con.commit()
    con.close()

    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': user_id, 'role': 'user', 'employee_id': emp_id})

    def token(kind='in'):
        r = c.post('/portal/api/field/token',
                   json={'station_id': station_id, 'kind': kind})
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()['token']

    def check_in(photo, lat=STATION[0], lon=STATION[1], tok=None):
        return c.post('/portal/api/field/check-in', data={
            'station_id': str(station_id), 'latitude': str(lat), 'longitude': str(lon),
            'accuracy': '12', 'token': tok or token('in'),
            'photo': (io.BytesIO(photo), 'shot.jpg', 'image/jpeg'),
        }, content_type='multipart/form-data')

    return {'client': c, 'emp_id': emp_id, 'station_id': station_id,
            'token': token, 'check_in': check_in, 'db': path}


# ------------------------------------------------------ بيت القصيد

def test_a_native_camera_photo_is_accepted(rep):
    """لو سقط هذا، فالتطبيق لا يعمل — مهما أُتقن كوده.

    كل كاميرا هاتف تكتب EXIF. والخادم كان يرفض كل ما يحمله.
    """
    r = rep['check_in'](_camera_jpeg(datetime.now() - timedelta(seconds=20)))
    body = r.get_json()

    assert r.status_code == 200, body
    assert body['success'] is True, body.get('message')
    assert body['visit_id'] > 0


def test_an_old_photo_from_the_gallery_is_still_refused(rep):
    """ولا يُفتح الباب: صورة الأمس تُردّ، ويُقال عمرها."""
    r = rep['check_in'](_camera_jpeg(datetime.now() - timedelta(hours=5)))
    body = r.get_json()

    assert r.status_code == 400
    assert body['success'] is False
    assert 'ليست جديدة' in body['message']


def test_a_browser_canvas_photo_still_works(rep):
    """شاشة الويب تُخرج canvas بلا EXIF — لا تُكسر بإصلاح التطبيق."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (320, 240), (10, 20, 30)).save(buf, 'JPEG')

    r = rep['check_in'](buf.getvalue())
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True


def test_the_photo_is_recorded_as_carrying_exif(rep):
    """يُحفظ ما قالته البيانات: القرار تغيّر، والدليل يبقى مسجَّلًا."""
    assert rep['check_in'](_camera_jpeg(datetime.now())).status_code == 200

    con = sqlite3.connect(rep['db'])
    had = con.execute('SELECT had_exif FROM field_visit_photos').fetchone()[0]
    con.close()
    assert had == 1


# --------------------------------------- ما يجب ألّا يتغيّر بهذا الإصلاح

def test_being_outside_the_radius_still_fails(rep):
    """النطاق هو الحارس الحقيقي — لا تُضعفه الصورة."""
    r = rep['check_in'](_camera_jpeg(datetime.now()), lat=29.50, lon=48.10)

    assert r.status_code == 400
    assert 'خارج نطاق' in r.get_json()['message']


def test_a_token_is_not_reusable(rep):
    """رمزٌ واحد لزيارة واحدة."""
    tok = rep['token']('in')
    assert rep['check_in'](_camera_jpeg(datetime.now()), tok=tok).status_code == 200

    r = rep['check_in'](_camera_jpeg(datetime.now()), tok=tok)
    assert r.status_code == 400


def test_a_photo_is_mandatory(rep):
    """لا زيارة بلا صورة."""
    r = rep['client'].post('/portal/api/field/check-in', data={
        'station_id': str(rep['station_id']), 'latitude': str(STATION[0]),
        'longitude': str(STATION[1]), 'accuracy': '12', 'token': rep['token']('in'),
    }, content_type='multipart/form-data')

    assert r.status_code == 400
    assert 'الصورة مطلوبة' in r.get_json()['message']


def test_a_file_that_is_not_an_image_is_refused(rep):
    r = rep['check_in'](b'this is definitely not a jpeg')

    assert r.status_code == 400
    assert 'صورة صالحة' in r.get_json()['message']



# ------------------------------------------- تهيئة البوابة للتطبيق

def test_bootstrap_gives_the_app_what_only_the_html_knew(rep):
    """أنواع الإجازات وصفة المدير كانت تُحقن في القالب عند التصيير فقط.

    فما يقرأ JSON لا يعرف منها شيئًا، وكان التطبيق سيُخمّن أو يكشط
    HTML. وكلاهما يفترق عن الصفحة عند أول تغيير فيها.
    """
    r = rep['client'].get('/portal/api/bootstrap')
    j = r.get_json()

    assert r.status_code == 200, j
    assert j['success'] is True
    assert j['employee']['name'] == 'مندوب الاختبار'
    assert j['has_employee_record'] is True
    assert isinstance(j['leave_types'], list) and j['leave_types'], 'لا أنواع إجازات'
    assert all({'id', 'name'} <= set(t) for t in j['leave_types'])
    assert 'enabled' in j['attendance']


def test_bootstrap_marks_a_rep_as_a_rep(rep):
    """وحدة المناديب مُفعَّلة في هذه التهيئة، وحقل work_mode هو الحكم."""
    import sqlite3
    con = sqlite3.connect(rep['db'])
    con.execute('UPDATE employees SET work_mode = ? WHERE id = ?', ('field', rep['emp_id']))
    con.commit()
    con.close()

    j = rep['client'].get('/portal/api/bootstrap').get_json()
    assert j['is_field_rep'] is True


def test_bootstrap_tells_an_account_without_an_employee_it_cannot_write(rep):
    """حسابٌ بلا سجلّ موظف يقرأ ولا يكتب.

    تُقال صراحةً ليُخفي التطبيق أزرار الكتابة، بدل أن يعرضها للمستخدم
    ثم يردّها الخادم بـ400 بعد أن يملأ النموذج.
    """
    import sqlite3
    from werkzeug.security import generate_password_hash
    con = sqlite3.connect(rep['db'])
    con.execute('INSERT INTO users (username, password, full_name, role, is_active,'
                ' employee_id) VALUES (?,?,?,?,1,NULL)',
                ('ghost2', generate_password_hash('x'), 'بلا موظف', 'user'))
    uid = con.execute("SELECT id FROM users WHERE username='ghost2'").fetchone()[0]
    con.commit()
    con.close()

    import app as A
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'user', 'employee_id': None})

    j = c.get('/portal/api/bootstrap').get_json()
    assert j['success'] is True
    assert j['has_employee_record'] is False
    assert j['employee'] is None


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
