"""يثبت إغلاق ثغرة التفعيل، ويثبت أن المفاتيح القائمة لم تتأثر.

الثغرة كانت نصفين يعملان معًا:
  1. LICENSE_WORDS في verify_license_key تتخطّى التحقق من التوقيع.
  2. مهلة "Server Unreachable => Allow" بلا حدّ في check_online_license_secure.

فمن يفصل الشبكة ويكتب  L-20991231-Maped$  كان يجتاز الفحصين معًا.

يُشغَّل:  python -m pytest tests/test_license_backdoor.py -v
أو:      python tests/test_license_backdoor.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------- الثغرة

def test_typed_backdoor_key_is_rejected():
    """المفتاح الذي كان يمنح ترخيصًا حتى ٢٠٩٩ بمجرد كتابته صار مرفوضًا.

    هذا هو الاستغلال العملي: لا يحتاج مفتاحًا ولا أدوات، فقط الكتابة في
    خانة التفعيل. الكلمات نفسها ما زالت مقبولة داخل غلاف LE2- المشفَّر،
    لأن ٣٩ من ٦٠ مفتاح إنتاج موقَّعة بها — انظر الاختبار التالي.
    """
    from utils.license import verify_license_key

    for word in ['Maped$', 'Mabed #', 'comaped#']:
        ok, msg = verify_license_key(f'L-20991231-{word}')
        assert ok is False, f'{word} ما زالت تُقبل مكتوبةً يدويًا'


def test_words_still_accepted_inside_the_encrypted_envelope():
    """الكلمات مقبولة داخل LE2- — وإلا توقّف ٣٩ عميلًا.

    اختبار متعمَّد لسلوك مؤقت. حين تُرحَّل كل المفاتيح إلى ONZ1. يُحذف هذا
    الاختبار مع LICENSE_WORDS نفسها.
    """
    from utils.license import encode_license_payload_fernet, verify_license_key

    future = (datetime.now() + timedelta(days=365)).strftime('%Y%m%d')
    key = encode_license_payload_fernet(future, 'comaped#')
    ok, msg = verify_license_key(key)
    assert ok is True, f'مفتاح إنتاج نموذجي رُفض: {msg}'


def test_legacy_plain_format_is_rejected_even_when_correctly_signed():
    """حتى التوقيع الصحيح بصيغة L- مرفوض — الصيغة نفسها أُلغيت.

    وهذا مقصود: توقيعها sha256(date + LICENSE_SECRET)، والمفتاح داخل
    المثبِّت، فمن يملكه يولّد ما يشاء.
    """
    import hashlib
    from utils.license import verify_license_key, LICENSE_SECRET

    date_part = '20991231'
    sig = hashlib.sha256((date_part + LICENSE_SECRET).encode()).hexdigest()[:12]
    ok, _ = verify_license_key(f'L-{date_part}-{sig}')
    assert ok is False


# ------------------------------------------------- المفاتيح القائمة سليمة

def _make_le2(date_part):
    """مفتاح LE2 موقَّع توقيعًا صحيحًا لتاريخ معيّن.

    ملحوظة: encode_license_payload_fernet تُعيد السلسلة كاملة ببادئة LE2-
    بالفعل، فإضافة البادئة مرة أخرى تنتج LE2-LE2-… وتُرفض لخطأ صيغة — وهو ما
    يجعل اختبار «انتهت الصلاحية» ينجح لسبب خاطئ.
    """
    import hashlib
    from utils.license import encode_license_payload_fernet, LICENSE_SECRET

    sig = hashlib.sha256((date_part + LICENSE_SECRET).encode()).hexdigest()[:12]
    return encode_license_payload_fernet(date_part, sig)


def test_existing_LE2_keys_still_validate():
    """صيغة LE2- — وهي صيغة المفاتيح الستين كلها — ما زالت تُقبل."""
    from utils.license import verify_license_key

    future = (datetime.now() + timedelta(days=365)).strftime('%Y%m%d')
    ok, msg = verify_license_key(_make_le2(future))
    assert ok is True, f'مفتاح LE2 سليم رُفض: {msg}'


def test_expired_LE2_key_is_rejected_for_the_right_reason():
    """يُرفض لانتهاء المدة تحديدًا، لا لخطأ في الصيغة."""
    from utils.license import verify_license_key

    past = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    ok, msg = verify_license_key(_make_le2(past))
    assert ok is False
    assert 'انتهت' in msg, f'رُفض لسبب آخر: {msg}'


def test_every_real_production_key_still_passes_the_local_check():
    """الاختبار الحاسم: المفاتيح الستون الحقيقية من قاعدة الإنتاج.

    يفصل بين نتيجتين مقبولتين: مفتاح سارٍ يُقبل، ومفتاح انتهت مدته يُرفض
    برسالة الانتهاء. أي رفض لسبب ثالث — وخاصة «تنسيق المفتاح غير صحيح» —
    يعني أن التعديل كسر عميلًا قائمًا.
    """
    import json
    from utils.license import verify_license_key

    path = os.path.join(os.path.dirname(__file__), 'fixtures_license_keys.json')
    if not os.path.exists(path):
        return  # لا تُشغَّل إن لم تُصدَّر بيانات الإنتاج

    import base64
    import hashlib
    from cryptography.fernet import Fernet
    from utils.license import LICENSE_SECRET, LICENSE_WORDS

    keys = json.load(open(path, encoding='utf-8'))
    assert len(keys) == 60

    fern = Fernet(base64.urlsafe_b64encode(
        hashlib.sha256(LICENSE_SECRET.encode()).digest()))

    def old_verdict(key):
        """حكم الكود قبل التعديل، على نفس المفتاح.

        المقارنة بالسلوك السابق لا بمعيار مطلق: المطلوب إثبات أن التعديل لم
        يُسقط مفتاحًا كان يُقبل، لا أن كل مفاتيح الإنتاج سليمة — وبعضها ليس
        كذلك أصلًا.
        """
        try:
            pt = fern.decrypt(key[4:].encode()).decode('utf-8', 'ignore')
            date_part, sig = pt.split('|', 1)
        except Exception:
            return False
        if '|' in sig:
            sig = sig.split('|')[0]
        expected = hashlib.sha256(
            (date_part + LICENSE_SECRET).encode()).hexdigest()[:12].lower()
        if sig.lower() != expected and sig not in LICENSE_WORDS:
            return False
        return date.today() <= datetime.strptime(date_part, '%Y%m%d').date()

    regressions, already_broken = [], []
    for row in keys:
        new_ok, _ = verify_license_key(row['key'])
        was_ok = old_verdict(row['key'])
        if was_ok and not new_ok:
            regressions.append(row['id'])       # التعديل كسره
        elif not was_ok and not new_ok:
            already_broken.append(row['id'])    # كان مكسورًا قبله

    assert not regressions, f'مفاتيح كانت تعمل وتوقّفت بسبب التعديل: {regressions}'
    print(f'      [٠ انحدار · {len(already_broken)} مفتاحًا كان مرفوضًا قبل التعديل '
          f'أصلًا: {already_broken}]')


# ------------------------------------------------------- مهلة عدم الاتصال

def _fresh_db(last_ok=None):
    """قاعدة بيانات مؤقتة بجدول license_settings كما يبنيه init_license_table."""
    path = tempfile.mktemp(suffix='.sqlite')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    import utils.license as lic
    lic.init_license_table(conn)

    if last_ok is not None:
        conn.execute("UPDATE license_settings SET last_ok_check = ? WHERE id=1",
                     (last_ok.strftime('%Y-%m-%d %H:%M:%S'),))
        conn.commit()
    return conn, path


def _offline_check(conn):
    """يشغّل check_online_license_secure والشبكة مقطوعة."""
    import requests
    import utils.license as lic

    with mock.patch.object(lic, 'get_db_connection', return_value=conn), \
         mock.patch.object(lic, 'get_system_hwid', return_value='HW'), \
         mock.patch.object(lic.requests, 'post',
                           side_effect=requests.exceptions.ConnectionError('no network')):
        return lic.check_online_license_secure('LE2-anything')


def test_offline_without_any_successful_check_is_refused():
    """مفتاح لم يقبله الخادم قط لا يُمنح مهلة — هذا هو مسار الثغرة."""
    conn, path = _fresh_db(last_ok=None)
    try:
        ok, msg = _offline_check(conn)
        assert ok is False, f'مُنح ترخيصًا بلا أي تحقق ناجح: {msg}'
    finally:
        conn.close()
        os.unlink(path)


def test_offline_within_grace_is_allowed():
    """العميل الذي تحقق بنجاح قبل أيام يواصل العمل أثناء الانقطاع."""
    from utils.license import OFFLINE_GRACE_DAYS

    conn, path = _fresh_db(last_ok=datetime.now() - timedelta(days=OFFLINE_GRACE_DAYS - 2))
    try:
        ok, msg = _offline_check(conn)
        assert ok is True, msg
        assert 'Grace' in msg
    finally:
        conn.close()
        os.unlink(path)


def test_offline_beyond_grace_is_refused():
    """بعد انتهاء المهلة يلزم اتصال واحد."""
    from utils.license import OFFLINE_GRACE_DAYS

    conn, path = _fresh_db(last_ok=datetime.now() - timedelta(days=OFFLINE_GRACE_DAYS + 1))
    try:
        ok, _ = _offline_check(conn)
        assert ok is False
    finally:
        conn.close()
        os.unlink(path)


def test_clock_set_backwards_does_not_extend_grace():
    """ساعة مضبوطة على المستقبل لا تُطيل المهلة."""
    conn, path = _fresh_db(last_ok=datetime.now() + timedelta(days=900))
    try:
        import utils.license as lic
        assert lic._days_since_last_ok(conn) == 0
    finally:
        conn.close()
        os.unlink(path)


def test_migration_seeds_grace_from_existing_successful_check():
    """تثبيت قائم بفحص ناجح لا يُرفض فور الترقية.

    بدون هذا الترحيل يكون last_ok_check فارغًا عند أول تشغيل للنسخة
    الجديدة، فيُرفض العميل الشرعي أول مرة تنقطع فيها شبكته.
    """
    import utils.license as lic

    path = tempfile.mktemp(suffix='.sqlite')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # جدول بالصيغة القديمة: بلا عمود last_ok_check.
        conn.execute('''CREATE TABLE license_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            license_key TEXT, api_key TEXT,
            last_check DATETIME, last_status_ok INTEGER DEFAULT 0)''')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO license_settings (id, last_check, last_status_ok) VALUES (1, ?, 1)",
                     (yesterday,))
        conn.commit()

        lic.init_license_table(conn)

        assert lic._days_since_last_ok(conn) == 1
        ok, msg = _offline_check(conn)
        assert ok is True, f'عميل قائم رُفض بعد الترقية: {msg}'
    finally:
        conn.close()
        os.unlink(path)


if __name__ == '__main__':
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_') or not callable(fn):
            continue
        try:
            fn()
            passed += 1
            print(f'  ok    {name}')
        except AssertionError as e:
            failed += 1
            print(f'  FAIL  {name}: {e}')
        except Exception as e:
            failed += 1
            print(f'  ERROR {name}: {type(e).__name__}: {e}')
    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)
