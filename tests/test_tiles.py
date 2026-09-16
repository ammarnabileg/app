"""خلفية الخريطة: مخزن المربّعات، وحدوده، وحزم النقل.

المكتبات صارت محليّة فعملت الخريطة بلا اتصال — إلا خلفيّتها. وهذه
صورٌ للعالم، والحلّ ليس حملَ العالم بل رقعةَ العميل.

يُشغَّل:  python -m pytest tests/test_tiles.py -v
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 200
KUWAIT = (28.52, 46.55, 30.10, 48.43)


def _tiles(tmp_path):
    import importlib
    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    db.init_db()            # جدول الإعدادات يلزم لفحص مصدر الخرائط
    from utils import tiles as t
    importlib.reload(t)
    return t


# --------------------------------------------------- حساب الرقعة

def test_the_world_is_not_downloadable_but_a_district_is(tmp_path):
    """الرقم هو الحجّة: حيٌّ ٥٨٠ مربّعًا، وبلدٌ بتقريب ١٦ مئةٌ وخمسون ألفًا."""
    t = _tiles(tmp_path)

    district = t.tiles_for_bbox(29.30, 47.95, 29.40, 48.05, 11, 16)
    country = t.tiles_for_bbox(*KUWAIT, 11, 16)

    assert len(district) < 1000
    assert len(country) > 100000
    assert len(country) > len(district) * 100


def test_each_zoom_level_roughly_quadruples(tmp_path):
    """كل درجة تقريب تُربّع العدد — وهذا ما يجعل ١٦ مستحيلًا و١٣ سهلًا."""
    t = _tiles(tmp_path)
    a = len(t.tiles_for_bbox(*KUWAIT, 13, 13))
    b = len(t.tiles_for_bbox(*KUWAIT, 14, 14))
    assert 3 < b / a < 5


def test_the_projection_matches_known_tiles(tmp_path):
    """إسقاط ويب-مركاتور القياسي: خطأ فيه يضع الكويت في الصومال."""
    t = _tiles(tmp_path)

    assert t.deg2tile(0, 0, 1) == (1, 1)          # مركز العالم
    assert t.deg2tile(0, -180, 1) == (0, 1)       # الغرب الأقصى

    # لندن عند تقريب ١٢ = 2046/1362 في خرائط OSM — قيمة معروفة يُقاس
    # عليها، لا رقمٌ حسبتُه بنفسي ثم اختبرتُ به نفسي.
    assert t.deg2tile(51.5074, -0.1278, 12) == (2046, 1362)
    assert t.deg2tile(29.3759, 47.9774, 12) == (2593, 1698)   # مدينة الكويت

    # وشمالًا يصغر y، وشرقًا يكبر x
    assert t.deg2tile(30.0, 47.9, 12)[1] < t.deg2tile(28.6, 47.9, 12)[1]
    assert t.deg2tile(29.3, 46.6, 12)[0] < t.deg2tile(29.3, 48.4, 12)[0]


# ------------------------------------------- المسار لا يخرج عن المخزن

def test_a_tile_path_never_escapes_the_cache(tmp_path):
    """الأرقام تأتي من عنوان يطلبه المتصفح."""
    t = _tiles(tmp_path)

    for z, x, y in [('14', '../../etc', '1'), ('99', '1', '1'), ('14', '-5', '1'),
                    ('abc', '1', '1'), ('14', '1', '99999999'), (None, 1, 1)]:
        assert t.tile_path(z, x, y) is None, (z, x, y)

    good = t.tile_path(14, 16383, 1)
    assert good and os.path.abspath(good).startswith(os.path.abspath(t.cache_dir()))


def test_store_and_read_round_trip(tmp_path):
    t = _tiles(tmp_path)

    assert t.store(14, 100, 200, PNG) is True
    assert t.read(14, 100, 200) == PNG
    assert t.have(14, 100, 200) is True
    assert t.read(14, 100, 201) is None

    # ولا يبقى ملف نصفه
    assert not os.path.exists(t.tile_path(14, 100, 200) + '.part')


def test_store_refuses_a_bad_address(tmp_path):
    t = _tiles(tmp_path)
    assert t.store(99, 1, 1, PNG) is False
    assert t.store(14, 1, 1, b'') is False


# ------------------------------------------------------ السقف والسياسة

def test_the_default_source_refuses_a_country(tmp_path):
    """خوادم OpenStreetMap تطوّعية وشروطها تمنع التنزيل الجملي.

    فالمنع في الشيفرة لا في الشاشة وحدها — ورسالتُه تقول السبب والبديل،
    لا «مرفوض» فحسب.
    """
    t = _tiles(tmp_path)
    started, msg = t.download([KUWAIT], zmax=16)

    assert started is False
    assert 'التنزيل الجملي' in msg
    assert 'مصدر خرائط خاصًّا' in msg


def test_a_custom_source_raises_the_cap(tmp_path):
    """من يشترك بمزوّد أو يملك خادمًا فالرخصة رخصته."""
    t = _tiles(tmp_path)
    assert t.max_tiles() == t.MAX_TILES_OSM

    import sqlite3
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.execute('''INSERT INTO salary_settings_v2 (setting_name, setting_value)
                   VALUES (?, ?)''', (t.SETTING_URL, 'https://x.example/{z}/{x}/{y}.png'))
    con.commit()

    url, attr, is_default = t.tile_source(con)
    assert is_default is False
    assert url.startswith('https://x.example')
    assert t.max_tiles(con) == t.MAX_TILES_CUSTOM


def test_a_malformed_custom_url_falls_back_to_the_default(tmp_path):
    """رابطٌ بلا {z}/{x}/{y} يعطي خريطةً بيضاء بلا سبب ظاهر."""
    t = _tiles(tmp_path)

    import sqlite3
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    for bad in ('https://x.example/tiles.png', 'ليس رابطًا', ''):
        con.execute('''INSERT INTO salary_settings_v2 (setting_name, setting_value)
                       VALUES (?, ?) ON CONFLICT(setting_name)
                       DO UPDATE SET setting_value = excluded.setting_value''',
                    (t.SETTING_URL, bad))
        con.commit()
        assert t.tile_source(con)[2] is True, bad


# ------------------------------------------------------ حزم النقل

def test_a_pack_round_trips(tmp_path):
    t = _tiles(tmp_path)
    for tile in [(14, 100, 200), (14, 101, 200), (15, 300, 400)]:
        t.store(*tile, PNG)

    pack = str(tmp_path / 'pack.zip')
    t.export_pack(pack)
    assert zipfile.ZipFile(pack).namelist()

    t.clear()
    assert t.cache_stats()['tiles'] == 0

    added, _ = t.import_pack(pack)
    assert added == 3
    assert t.have(15, 300, 400)


def test_a_malicious_pack_cannot_write_outside(tmp_path):
    """الحزمة ملفٌّ يأتي من خارج النظام — كل مدخلة فيها تُفحص."""
    t = _tiles(tmp_path)

    bad = str(tmp_path / 'bad.zip')
    with zipfile.ZipFile(bad, 'w') as z:
        z.writestr('../../../evil.png', PNG)          # يخرج عن المخزن
        z.writestr('14/99/1.png', b'NOT A PNG')       # ليس صورة
        z.writestr('99/1/1.png', PNG)                 # تقريب مستحيل
        z.writestr('14/10/2.png', PNG)                # الصالح الوحيد

    added, _ = t.import_pack(bad)

    assert added == 1
    assert t.have(14, 10, 2) is True
    assert not os.path.exists(str(tmp_path / 'evil.png'))
    assert not os.path.exists('/evil.png')


def test_a_broken_pack_is_reported_not_crashed(tmp_path):
    t = _tiles(tmp_path)
    junk = str(tmp_path / 'junk.zip')
    open(junk, 'wb').write(b'this is not a zip')

    added, msg = t.import_pack(junk)
    assert added == 0 and 'صالحة' in msg


def test_stats_count_only_tiles(tmp_path):
    t = _tiles(tmp_path)
    t.store(14, 1, 1, PNG)
    os.makedirs(os.path.join(t.cache_dir(), '14'), exist_ok=True)
    open(os.path.join(t.cache_dir(), '14', 'notes.txt'), 'w').write('x' * 500)

    assert t.cache_stats()['tiles'] == 1


# ------------------------------------------- تبديل المزوّد من الشاشة

def _admin_client(tmp_path):
    """نظامٌ حقيقي بمسؤولٍ حقيقي — لأن السؤال هنا عن شاشة الإعدادات."""
    import importlib
    import sqlite3

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
    admin_id = con.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    con.close()

    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': admin_id, 'role': 'admin', 'employee_id': None})
    return c


def test_the_settings_screen_actually_switches_the_provider(tmp_path):
    """قلتُ للمستخدم «بدّله من الإعدادات» — فهذا اختبار قولي لا شيفرتي.

    الحقل كان غائبًا عن الشاشة أصلًا، والقيمة تُقرأ من القاعدة وحدها،
    فكان التبديل يعني تحرير SQLite باليد. والاختبار يمرّ بالنموذج
    كما يمرّ المستخدم: يُرسِل، ثم يسأل `tile_source` ماذا صار المصدر.
    """
    t = _tiles(tmp_path)
    assert t.tile_source()[2] is True, 'الافتراضي ليس OpenStreetMap'

    c = _admin_client(tmp_path)
    mine = 'https://tiles.example.com/{z}/{x}/{y}.png?key=abc'
    r = c.post('/settings/update',
               data={'map_tile_url': mine, 'map_tile_attribution': '© مزوّدي'},
               follow_redirects=False)
    assert r.status_code == 302, 'لم يُقبل النموذج'

    url, attr, is_default = t.tile_source()
    assert url == mine
    assert attr == '© مزوّدي'
    assert is_default is False
    # والسقف يرتفع مع المصدر الخاص: الرخصة صارت رخصة العميل.
    assert t.max_tiles() == t.MAX_TILES_CUSTOM


def test_clearing_the_field_returns_to_the_free_default(tmp_path):
    """من أفرغ الحقل يعود إلى OSM — لا يبقى عالقًا بمزوّدٍ ألغى اشتراكه."""
    t = _tiles(tmp_path)
    c = _admin_client(tmp_path)

    c.post('/settings/update',
           data={'map_tile_url': 'https://tiles.example.com/{z}/{x}/{y}.png'})
    assert t.tile_source()[2] is False

    c.post('/settings/update', data={'map_tile_url': '  ', 'map_tile_attribution': ''})
    url, _attr, is_default = t.tile_source()
    assert is_default is True
    assert url == t.DEFAULT_TILE_URL
    assert t.max_tiles() == t.MAX_TILES_OSM


def test_a_url_without_placeholders_is_ignored_not_obeyed(tmp_path):
    """لو لصق أحدهم رابط صفحة خرائط بدل قالب المربّعات، لا تصير الخريطة
    رمادية: القيمة المعطوبة تُهمَل ويبقى الافتراضي."""
    t = _tiles(tmp_path)
    c = _admin_client(tmp_path)

    c.post('/settings/update', data={'map_tile_url': 'https://maps.google.com/'})

    assert t.tile_source()[2] is True


def test_the_paid_key_never_reaches_a_browser(tmp_path):
    """مفتاح المزوّد يُدفع ثمنه بالطلبات — فمن قرأه أنفق من حساب العميل.

    و`/api/field/maps` كان يعيد `url` كاملًا وفيه `?key=...`، وحارسه
    `login_required` لا `admin` — فكل موظف يفتح البوابة كان يستطيع
    قراءة المفتاح من الشبكة. والواجهة لا تستعمل `url` أصلًا، إنما
    `attribution` وحدها: حقلٌ يُسرِّب ولا يخدم.
    """
    import sqlite3
    t = _tiles(tmp_path)
    c = _admin_client(tmp_path)

    secret = 'sUpErSeCrEtKey123'
    c.post('/settings/update',
           data={'map_tile_url': 'https://api.example.com/{z}/{x}/{y}.png?key=' + secret,
                 'map_tile_attribution': '© مزوّدي'})
    assert t.tile_source()[2] is False

    # الوحدة تُفعَّل، ويُستعمل حسابٌ عاديّ لا مسؤول — هذا هو المهاجم.
    from utils import field
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    field.init_schema(con)
    field.set_module_enabled(con, True)
    from werkzeug.security import generate_password_hash
    con.execute("INSERT INTO users (username, password, full_name, role, is_active,"
                " employee_id) VALUES (?,?,?,'user',1,NULL)",
                ('plain', generate_password_hash('x'), 'موظف عادي'))
    plain = con.execute("SELECT id FROM users WHERE username='plain'").fetchone()[0]
    con.commit()
    con.close()

    import app as A
    c2 = A.app.test_client()
    with c2.session_transaction() as s:
        s.update({'user_id': plain, 'role': 'user', 'employee_id': None})

    r = c2.get('/api/field/maps')
    assert r.status_code == 200, 'المسار لم يُفتح، فالاختبار لا يفحص شيئًا'
    body = r.get_data(as_text=True)

    assert secret not in body, 'المفتاح خرج إلى المتصفح'
    assert 'api.example.com' in body, 'المضيف يجب أن يبقى — به يُشخَّص المزوّد'

    src = r.get_json()['source']
    assert src.get('attribution') == '© مزوّدي', 'النسب يجب أن يبقى — تستعمله الواجهة'
    assert 'url' not in src, 'الرابط ما زال في الردّ'


def _wait_idle(t, seconds=5):
    import time
    for _ in range(int(seconds * 50)):
        if not t.progress()['running']:
            return
        time.sleep(0.02)
    raise AssertionError('التنزيل لم ينتهِ')


def test_a_rejected_key_is_not_reported_as_a_dead_network(tmp_path):
    """٤٠٣ من المزوّد كان يُقال «لا اتصال بخادم الخرائط».

    والمستخدم متّصل. فيذهب يفحص الجدار الناري والـDNS، والعطل سطرٌ في
    لوحة المزوّد: مفتاحٌ مقيَّد بنطاقات، وطلباتنا تأتي من الخادم بلا
    نطاق. رسالةٌ خاطئة أسوأ من لا رسالة، لأنها توجّه البحث إلى مكان
    خالٍ.
    """
    t = _tiles(tmp_path)
    t.fetch_ex = lambda z, x, y, timeout=8, url=None: (None, 'key')

    started, _msg = t.download([(29.30, 47.95, 29.31, 47.96)], zmin=11, zmax=11)
    assert started
    _wait_idle(t)

    msg = t.progress()['message']
    assert '403' in msg
    assert 'لا اتصال' not in msg, 'ما زال يُقال إن الشبكة مقطوعة'


def test_a_real_outage_is_still_called_an_outage(tmp_path):
    """ولا يُقلب الخطأ: انقطاعٌ حقيقي يُقال انقطاعًا."""
    t = _tiles(tmp_path)
    t.fetch_ex = lambda z, x, y, timeout=8, url=None: (None, 'network')

    t.download([(29.30, 47.95, 29.40, 48.05)], zmin=11, zmax=14)
    _wait_idle(t, 15)

    assert 'لا اتصال' in t.progress()['message']


def test_a_rejected_key_stops_at_once_not_after_thirty_tries(tmp_path):
    """المفتاح المرفوض لا يُصلحه التكرار."""
    calls = []

    t = _tiles(tmp_path)

    def counting(z, x, y, timeout=8, url=None):
        calls.append(1)
        return None, 'key'

    t.fetch_ex = counting
    t.download([(29.30, 47.95, 29.40, 48.05)], zmin=11, zmax=14)
    _wait_idle(t, 15)

    assert len(calls) == 1, f'حاول {len(calls)} مرة بمفتاح مرفوض'


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
