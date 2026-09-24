"""أوراكل لا تُحاوَل على نظامٍ لم يُضبط فيه.

## ما وقع

مزامنة أوراكل مفتوحة افتراضيًّا، والمضيف الافتراضي `localhost`. فكل
نظامٍ لم يُضبط فيه شيء — وهو حال أكثر العملاء — كان يحاول الاتصال
بـ`localhost:1521` كل خمس دقائق ويسجّل `Connection refused`. مدى
الحياة.

وليست الضجّة وحدها الضرر: سجلٌّ يمتلئ بخطأٍ معروفٍ لا يعني شيئًا
يُتعلَّم تجاهلُه، فيُتجاهَل يوم يحمل عطبًا حقيقيًّا. وقد وقع ذلك
هنا: أخطاء أوراكل ملأت سجلّ عميل فأخفت تشخيص ADMS.

## وأخطر ما في هذا الإصلاح ليس ما يُسكِت، بل ما يجب ألّا يُسكِت

من يشغّل أوراكل على الجهاز نفسه — تركيبٌ محلّي بـXE — مضيفُه
`localhost` عن قصد. فقطعُه بحجّة أن العنوان افتراضيّ يُعطّل ميزةً
يدفع ثمنها. ولهذا القياس على «هل ضبط أحدٌ شيئًا» لا على «أهو
localhost».

يُشغَّل:  python -m pytest tests/test_oracle_quiet.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


ENV_KEYS = ('ORACLE_HOST', 'ORACLE_USER', 'ORACLE_PASSWORD', 'ORACLE_SERVICE')


@pytest.fixture
def oracle(tmp_path, monkeypatch):
    """نظامٌ نظيف: لا إعداد مخزَّن ولا متغيّر بيئة."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.oracle_db as o
    importlib.reload(o)
    return o, db


def _bust(o):
    """يُبطل الكاش بين الحالات — عمرُه ثلاثون ثانية."""
    o._oracle_configured_cache['ts'] = 0
    o._oracle_enabled_cache['ts'] = 0


# ------------------------------------------------ الصمت

def test_a_system_nobody_configured_is_not_enabled(oracle):
    o, _db = oracle
    assert o.oracle_configured() is False
    assert o.is_oracle_enabled() is False


def test_it_never_reaches_for_a_connection(oracle, monkeypatch):
    """لا محاولة أصلًا — لا محاولةٌ تفشل بهدوء.

    الفرق ليس تجميليًّا: كل محاولة تنتظر مهلة الاتصال، وتُنفَّذ في
    خيطٍ يعمل ما دام النظام يعمل.
    """
    o, _db = oracle
    tried = []
    monkeypatch.setattr(o.oracledb, 'connect',
                        lambda **kw: tried.append(kw))

    assert o.is_oracle_enabled() is False

    # المسارات التي كانت تتصل: الطابور، والنبض.
    o.add_to_sync_queue(1, '2026-09-18 08:00:00', 'IN', 1, '10.0.0.5')
    assert tried == [], f'حاول الاتصال رغم أنه غير مضبوط: {tried}'


def test_the_reason_is_logged_once_not_every_five_minutes(oracle, caplog):
    """السبب يُقال مرّة — فيُعرف — ولا يصير هو نفسه الضجّة."""
    import logging

    o, _db = oracle
    o._unconfigured_said['done'] = False

    with caplog.at_level(logging.INFO, logger='oracle_sync'):
        for _ in range(5):
            _bust(o)
            o.oracle_configured()

    assert caplog.text.count('غير مضبوطة') == 1


# ------------------------------- ومن ضبطها لا يُقطع عنه

def test_a_stored_host_counts_as_configured(oracle):
    o, db = oracle
    db.set_setting('oracle_host', '10.0.0.9')
    _bust(o)

    assert o.oracle_configured() is True
    assert o.is_oracle_enabled() is True


def test_oracle_on_the_same_machine_is_not_cut_off(oracle):
    """التركيب المحلّي بـXE: المضيف `localhost` عن قصد.

    لو كان القياس «أهو localhost» لانقطعت مزامنةُ من يستعملها فعلًا.
    """
    o, db = oracle
    db.set_setting('oracle_host', 'localhost')
    _bust(o)

    assert o.oracle_configured() is True
    assert o.is_oracle_enabled() is True


def test_an_environment_variable_counts_too(oracle, monkeypatch):
    """النشر بالحاويات يمرّر الإعداد بيئةً لا بقاعدة."""
    o, _db = oracle
    monkeypatch.setenv('ORACLE_HOST', 'oracle.internal')
    _bust(o)

    assert o.oracle_configured() is True


def test_a_user_alone_counts(oracle, db_setting=None):
    """من ملأ المستخدم وحده بدأ الضبط — فلا يُعامَل معاملة من لم يبدأ."""
    o, db = oracle
    db.set_setting('oracle_user', 'HR_PROD')
    _bust(o)

    assert o.oracle_configured() is True


# ------------------------------- والمفتاح يبقى فوق ذلك

def test_the_kill_switch_still_wins_over_a_configured_system(oracle):
    """من ضبطها ثم أطفأها يُطاع: الإطفاء قرارٌ صريح."""
    o, db = oracle
    db.set_setting('oracle_host', '10.0.0.9')
    db.set_setting('oracle_enabled', '0')
    _bust(o)

    assert o.oracle_configured() is True
    assert o.is_oracle_enabled() is False


def test_an_unreadable_settings_table_does_not_stop_a_real_user(oracle, monkeypatch):
    """الفشل مفتوح: قاعدةٌ متعثّرة لحظةً لا توقف مزامنةَ من يعتمد عليها.

    والضجّة أهون من بصماتٍ لا تصل نظام العميل.
    """
    o, _db = oracle
    monkeypatch.setattr(o, 'get_setting',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    _bust(o)

    assert o.oracle_configured() is True


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))


# ------------------------------------------------ دفعُ الطابور

class _FakeOracle:
    def __init__(self):
        self.rows, self.closed, self.committed = [], False, False

    def cursor(self):
        outer = self

        class C:
            def execute(self, sql, params):
                outer.rows.append(params)
        return C()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_an_empty_queue_is_not_an_error(oracle, monkeypatch, caplog):
    """كان سطرٌ يسمّي متغيّرًا غير معرَّف (`sqlite_pass`) يرمي NameError عند
    الطابور الفارغ — فكلُّ نبضةٍ بلا جديد تُسجَّل «Error processing sync queue»."""
    o, db = oracle
    monkeypatch.setattr(o, 'is_oracle_enabled', lambda: True)
    import logging
    with caplog.at_level(logging.ERROR, logger='oracle_sync'):
        r = o.process_sync_queue(oracle_conn=_FakeOracle())
    assert r is not False
    assert 'Error processing sync queue' not in caplog.text


def test_a_flush_closes_the_connection_it_opened(oracle, monkeypatch, caplog):
    """واتصالُ Oracle الذي فتحته الدالةُ تغلقه — وكان في موضعه اسمٌ غير معرَّف
    (`oracle_pass`): يتسرّب الاتصال، ويُسجَّل خطأٌ بعد دفعٍ ناجح."""
    o, db = oracle
    fake = _FakeOracle()
    monkeypatch.setattr(o, 'is_oracle_enabled', lambda: True)
    monkeypatch.setattr(o, 'get_oracle_connection', lambda: fake)
    conn = db.get_db_connection()
    conn.execute("INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id) "
                 "VALUES ('77', '2026-09-24 08:00:00', '0', '1', 3)")
    conn.commit()

    import logging
    with caplog.at_level(logging.ERROR, logger='oracle_sync'):
        r = o.process_sync_queue()

    assert r is True
    assert len(fake.rows) == 1 and fake.committed
    assert fake.closed, 'الاتصالُ الذي فتحته الدالةُ يُغلق'
    st = db.get_db_connection().execute("SELECT status FROM oracle_sync_queue").fetchone()[0]
    assert st == 'SYNCED'
    assert 'Error processing sync queue' not in caplog.text
