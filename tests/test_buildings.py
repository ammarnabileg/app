"""المباني: «في أيّ فرع كان؟» — سؤال لا يجيبه نطاقٌ بمئة متر.

مجمّع تجاري واحد يسع عشرة فروع داخل نطاق المحطة. فالجواب في مضلَّعات
المباني وأسمائها، لا في المسافة عن نقطة.

**ما لم أتحقّق منه:** خدمة Overpass محجوبة في بيئة التطوير هذه، فلم
أُجرِ طلبًا حيًّا واحدًا. المُختبَر هنا تحليلُ الردّ وتخزينه وتحديد
المبنى — على ردٍّ بصيغة الخدمة الحقيقية.

يُشغَّل:  python -m pytest tests/test_buildings.py -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _b(tmp_path):
    import importlib
    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    from utils import buildings as b
    importlib.reload(b)
    return b


# ردٌّ بصيغة Overpass الحقيقية: طريقٌ باسم عربي، وآخر بلا اسم،
# وعلاقة (مبنى مركّب)، ومدخلة مشوَّهة.
SAMPLE = {
    'version': 0.6,
    'elements': [
        {'type': 'way', 'id': 111,
         'tags': {'building': 'retail', 'name:ar': 'صيدلية الروضة',
                  'name': 'Rawda Pharmacy', 'shop': 'chemist',
                  'addr:street': 'شارع بيروت', 'building:levels': '2'},
         'geometry': [{'lat': 29.3300, 'lon': 48.0000}, {'lat': 29.3300, 'lon': 48.0010},
                      {'lat': 29.3310, 'lon': 48.0010}, {'lat': 29.3310, 'lon': 48.0000},
                      {'lat': 29.3300, 'lon': 48.0000}]},
        {'type': 'way', 'id': 222,
         'tags': {'building': 'yes'},
         'geometry': [{'lat': 29.3320, 'lon': 48.0000}, {'lat': 29.3320, 'lon': 48.0008},
                      {'lat': 29.3326, 'lon': 48.0008}, {'lat': 29.3326, 'lon': 48.0000}]},
        {'type': 'relation', 'id': 333,
         'tags': {'building': 'commercial', 'name': 'مجمّع الرايـة'},
         'members': [
             {'type': 'way', 'role': 'inner',
              'geometry': [{'lat': 29.3340, 'lon': 48.0002}, {'lat': 29.3341, 'lon': 48.0003},
                           {'lat': 29.3342, 'lon': 48.0002}]},
             {'type': 'way', 'role': 'outer',
              'geometry': [{'lat': 29.3330, 'lon': 48.0000}, {'lat': 29.3330, 'lon': 48.0020},
                           {'lat': 29.3350, 'lon': 48.0020}, {'lat': 29.3350, 'lon': 48.0000},
                           {'lat': 29.3330, 'lon': 48.0000}]}]},
        {'type': 'way', 'id': 444, 'tags': {'building': 'yes'},
         'geometry': [{'lat': 29.33, 'lon': 48.0}]},          # نقطتان: ليست مضلَّعًا
        {'type': 'node', 'id': 555, 'tags': {'building': 'yes'}},
        'ليست مدخلة أصلًا',
    ],
}


# ------------------------------------------------------------ التحليل

def test_a_real_shaped_response_parses(tmp_path):
    b = _b(tmp_path)
    got = b.parse(SAMPLE)

    assert len(got) == 3, [g['name'] for g in got]
    names = [g['name'] for g in got]
    assert 'صيدلية الروضة' in names
    assert 'مجمّع الرايـة' in names


def test_the_arabic_name_wins(tmp_path):
    """الشاشة عربية؛ ومبنًى له الاسمان يُعرض بالعربي."""
    b = _b(tmp_path)
    pharmacy = [g for g in b.parse(SAMPLE) if g['id'] == 111][0]

    assert pharmacy['name'] == 'صيدلية الروضة'
    assert pharmacy['kind'] == 'chemist'
    assert pharmacy['street'] == 'شارع بيروت'


def test_a_relation_uses_its_outer_ring(tmp_path):
    """المبنى المركّب حلقاتٌ عدة — والداخلية فناءٌ لا جدار."""
    b = _b(tmp_path)
    mall = [g for g in b.parse(SAMPLE) if g['id'] == 333][0]

    assert len(mall['points']) == 5
    assert [29.3330, 48.0020] in mall['points']     # من الحلقة الخارجية


def test_an_unnamed_building_is_kept_without_a_name(tmp_path):
    """يُرسم شكلًا: من يرى الخريطة يعرف أن هناك مبنًى ولو جُهل اسمه."""
    b = _b(tmp_path)
    plain = [g for g in b.parse(SAMPLE) if g['id'] == 222][0]
    assert plain['name'] == ''
    assert len(plain['points']) == 4


def test_garbage_is_dropped_not_crashed(tmp_path):
    b = _b(tmp_path)

    assert b.parse({}) == []
    assert b.parse(None) == []
    assert b.parse({'elements': None}) == []
    assert b.parse({'elements': ['x', 5, {}]}) == []
    assert b.parse({'elements': [{'type': 'way', 'geometry': [{'lat': 'س', 'lon': 1}] * 4}]}) == []


# ------------------------------------------------ أيّ مبنًى هذه النقطة

def _seed(b, tmp_path):
    """يزرع نتيجة التحليل في المخزن، فلا حاجة إلى شبكة."""
    parsed = b.parse(SAMPLE)
    for key in b._cells_for(29.329, 47.999, 29.336, 48.003):
        b.write_cell(key, parsed)
    return parsed


def test_a_point_inside_a_building_is_named(tmp_path):
    """بيت القصيد: النقطة داخل الصيدلية تعطي اسمها."""
    b = _b(tmp_path)
    _seed(b, tmp_path)

    hits = b.at_point(29.3305, 48.0005, allow_fetch=False)
    assert hits and hits[0]['name'] == 'صيدلية الروضة'
    assert hits[0]['inside'] is True

    assert b.describe(29.3305, 48.0005, allow_fetch=False) == 'داخل صيدلية الروضة'


def test_a_point_beside_a_building_says_beside_not_inside(tmp_path):
    """دقّة الهاتف عشرات الأمتار — فمن وقف بجانب مبنًى لا يُقال إنه فيه."""
    b = _b(tmp_path)
    _seed(b, tmp_path)

    # شمال الصيدلية بنحو ٢٠ مترًا، خارج مضلَّعها
    text = b.describe(29.33118, 48.0005, allow_fetch=False)
    assert text.startswith('بجوار'), text
    assert 'صيدلية الروضة' in text


def test_the_containing_building_outranks_a_nearer_neighbour(tmp_path):
    """من هو داخل مبنًى أولى بالذكر ممّن هو بجانب مبنًى أقرب مركزًا."""
    b = _b(tmp_path)
    _seed(b, tmp_path)

    hits = b.at_point(29.3340, 48.0010, allow_fetch=False)   # داخل المجمّع
    assert hits[0]['inside'] is True
    assert hits[0]['name'] == 'مجمّع الرايـة'


def test_a_point_in_open_ground_names_nothing(tmp_path):
    b = _b(tmp_path)
    _seed(b, tmp_path)

    assert b.describe(29.3900, 48.0900, allow_fetch=False) == ''
    assert b.at_point(29.3900, 48.0900, allow_fetch=False) == []


# ------------------------------------------------------------ المخزن

def test_cells_are_reused_not_refetched(tmp_path):
    b = _b(tmp_path)
    _seed(b, tmp_path)

    found, fetched = b.in_bbox(29.330, 48.000, 29.334, 48.002, allow_fetch=False)
    assert found and fetched is False


def test_a_huge_bbox_is_trimmed(tmp_path):
    """طلبٌ لنصف البلد يُثقل خدمةً مجانية ولا يُرسم منه شيء مفيد."""
    b = _b(tmp_path)
    calls = []

    b.fetch = lambda *a, **k: calls.append(a) or []
    b.in_bbox(28.0, 46.0, 31.0, 49.0, allow_fetch=True)

    # الرقعة قُصّت، فعدد الخلايا معقول لا آلاف
    assert 0 < len(calls) <= 64, len(calls)


def test_the_query_is_bounded_and_asks_for_geometry(tmp_path):
    b = _b(tmp_path)
    q = b.build_query(29.30, 48.00, 29.31, 48.01)

    assert 'out tags geom' in q          # الإحداثيات داخل الردّ نفسه
    assert '29.3,48.0,29.31,48.01' in q
    assert 'timeout' in q


def test_a_stale_cell_is_not_used(tmp_path):
    b = _b(tmp_path)
    key = b._cell_key(29.33, 48.00)
    b.write_cell(key, [{'id': 1, 'name': 'قديم', 'points': [[0, 0], [0, 1], [1, 1]]}])

    assert b.read_cell(key) is not None
    assert b.read_cell(key, max_age_days=0) is None


def test_a_corrupt_cell_reads_as_missing(tmp_path):
    b = _b(tmp_path)
    key = b._cell_key(29.33, 48.00)
    os.makedirs(b.cache_dir(), exist_ok=True)
    open(b._cell_path(key), 'w').write('{ليس JSON')

    assert b.read_cell(key) is None


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
