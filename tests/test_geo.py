"""المناطق: مضلَّع من عدة نقاط، لا دائرة حول نقطة.

المنطقة هي التي تجعل السؤال ممكنًا: «هل خرج المندوب عن منطقته؟» — وهو
سؤال لا تجيب عنه محطاتٌ متفرّقة، لأن بين المحطة والأخرى طريقٌ لا يُقاس.

يُشغَّل:  python -m pytest tests/test_geo.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import geo

# مربّع تقريبيّ حول جزء من الكويت — أضلاعه على خطوط الطول والعرض
SQUARE = [(29.30, 47.95), (29.30, 48.05), (29.40, 48.05), (29.40, 47.95)]

# شكل مقعَّر: حرف L. الدائرة لا تصفه، والمضلَّع يصفه.
L_SHAPE = [(29.30, 47.95), (29.30, 48.05), (29.35, 48.05),
           (29.35, 48.00), (29.40, 48.00), (29.40, 47.95)]


# ------------------------------------------------------- داخل أم خارج

def test_a_point_inside_and_outside_a_square():
    assert geo.contains(SQUARE, 29.35, 48.00) is True
    assert geo.contains(SQUARE, 29.45, 48.00) is False      # شمالها
    assert geo.contains(SQUARE, 29.35, 48.10) is False      # شرقها


def test_a_concave_shape_is_respected():
    """بيت القصيد في اختيار المضلَّع على الدائرة.

    نقطةٌ في الركن المقطوع من حرف L تقع داخل أي دائرة تحيط بالشكل،
    وخارج الشكل نفسه. والأحياء مقعَّرة كثيرًا — شارعٌ يفصل حيًّا عن حيّ.
    """
    # داخل الساق العريضة
    assert geo.contains(L_SHAPE, 29.32, 48.02) is True
    # في الركن المقطوع: خارج الشكل، وداخل أي دائرة تحيط به
    assert geo.contains(L_SHAPE, 29.38, 48.03) is False


def test_an_empty_or_short_polygon_contains_nothing():
    assert geo.contains([], 29.35, 48.0) is False
    assert geo.contains([(29.3, 48.0)], 29.3, 48.0) is False
    assert geo.contains([(29.3, 48.0), (29.4, 48.0)], 29.35, 48.0) is False


# --------------------------------------------------------- القراءة

def test_a_polygon_needs_three_points():
    pts, why = geo.parse_polygon([[29.3, 48.0], [29.4, 48.0]])
    assert pts is None and '٣' in why or '3' in why


def test_a_repeated_closing_point_is_dropped():
    """الحساب يغلق المضلَّع ضمنًا؛ وبقاء النقطة يصنع ضلعًا بطول صفر."""
    closed = [[29.30, 47.95], [29.30, 48.05], [29.40, 48.05], [29.30, 47.95]]
    pts, _ = geo.parse_polygon(closed)
    assert len(pts) == 3
    assert pts[0] != pts[-1]


def test_json_and_list_are_both_accepted():
    import json
    a, _ = geo.parse_polygon(SQUARE)
    b, _ = geo.parse_polygon(json.dumps([[x, y] for x, y in SQUARE]))
    assert a == b


def test_dict_points_are_accepted():
    pts, _ = geo.parse_polygon([{'lat': 29.3, 'lon': 48.0},
                                {'lat': 29.3, 'lon': 48.1},
                                {'latitude': 29.4, 'longitude': 48.1}])
    assert pts and len(pts) == 3


def test_coordinates_out_of_range_are_refused():
    for bad in ([[999, 48.0], [29.3, 48.0], [29.4, 48.0]],
                [[29.3, 999], [29.3, 48.0], [29.4, 48.0]],
                [[29.3, 'abc'], [29.3, 48.0], [29.4, 48.0]]):
        pts, why = geo.parse_polygon(bad)
        assert pts is None, why


def test_garbage_is_refused_not_crashed():
    for bad in ('not json', None, 42, [[1]], [{'x': 1}], ''):
        pts, why = geo.parse_polygon(bad)
        assert pts is None
        assert isinstance(why, str) and why


def test_too_many_vertices_are_refused():
    huge = [[29.3 + i * 1e-5, 48.0] for i in range(geo.MAX_VERTICES + 5)]
    assert geo.parse_polygon(huge)[0] is None


def test_a_round_trip_survives():
    pts, _ = geo.parse_polygon(SQUARE)
    again, _ = geo.parse_polygon(geo.dumps_polygon(pts))
    assert again == pts


# ----------------------------------------------------- القياس والحدّ

def test_the_area_is_roughly_right():
    """مربّع ٠٫١ درجة عرض × ٠٫١ درجة طول عند الكويت ≈ ١٠٧ كم².

    الرقم يُعرض لمن يرسم المنطقة — فلو كان بعيدًا عن الحقيقة لأعطاه
    ثقةً في رسمٍ خاطئ.
    """
    km2 = geo.area_km2(SQUARE)
    assert 80 < km2 < 130, km2


def test_the_distance_to_the_edge_is_in_metres():
    # نقطة على بُعد ٠٫٠١ درجة عرض شمال الحدّ ≈ ١١١٠ متر
    d = geo.distance_to_edge(SQUARE, 29.41, 48.00)
    assert 900 < d < 1300, d

    # نقطة في المنتصف: بعيدة عن كل الأضلاع
    assert geo.distance_to_edge(SQUARE, 29.35, 48.00) > 3000


def test_longitude_metres_shrink_away_from_the_equator():
    """درجة طول عند خط عرض ٢٩ أقصر من درجة عرض بنحو ١٣٪.

    من يتجاهل هذا يحسب المسافة شرقًا أكبر مما هي، فيقول «داخل المنطقة»
    عمّن هو خارجها.
    """
    north = geo.distance_to_edge(SQUARE, 29.41, 48.00)     # ٠٫٠١ درجة عرض
    east = geo.distance_to_edge(SQUARE, 29.35, 48.06)      # ٠٫٠١ درجة طول
    assert east < north
    assert 0.80 < east / north < 0.92, east / north


def test_the_centroid_is_inside_a_convex_shape():
    lat, lon = geo.centroid(SQUARE)
    assert geo.contains(SQUARE, lat, lon)


# ------------------------------------------------- الخروج عن المنطقة

def test_leaving_the_territory_is_counted():
    track = [(29.35, 48.00), (29.36, 48.01),        # داخل
             (29.60, 48.30), (29.61, 48.31)]        # بعيدًا خارج
    out, ratio = geo.outside_ratio(SQUARE, track)

    assert out == 2
    assert ratio == 0.5


def test_a_point_just_past_the_edge_is_not_called_a_breach():
    """دقّة جهاز الهاتف عشرات الأمتار.

    نقطةٌ خارج الحدّ بمترين لا تقول شيئًا، والحكم عليها بالمخالفة اتهامٌ
    لخطأ القياس.
    """
    just_out = (29.4000_2, 48.00)      # شمال الحدّ ببضعة أمتار
    out, _ = geo.outside_ratio(SQUARE, [just_out], margin_meters=60)
    assert out == 0

    # وبلا هامش تُعدّ خارجًا — فالفرق من الهامش لا من الحساب
    out2, _ = geo.outside_ratio(SQUARE, [just_out], margin_meters=0)
    assert out2 == 1


def test_no_track_or_no_polygon_measures_nothing():
    assert geo.outside_ratio(SQUARE, []) == (0, 0.0)
    assert geo.outside_ratio([], [(29.35, 48.0)]) == (0, 0.0)


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
