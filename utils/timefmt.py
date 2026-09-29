# -*- coding: utf-8 -*-
"""الساعاتُ كما يقرؤها الناس: «ساعات:دقائق».

8.85 ساعةً تُقرأ «8 ساعات و85 دقيقة» وهي 8:51 — الكسرُ جزءٌ من ستّين لا
من مئة. فكلُّ عرضٍ لساعات العمل يمرّ من هنا (ومقابلُه في المتصفّح
`fmtHM` في base.html، وفي القوالب مرشِّح `hm`). والحسابُ يبقى عشريًّا:
هذا للعرض وحده.
"""


def hours_hm(value):
    """8.85 ← '8:51'. وما ليس رقمًا يعود كما هو ('--' مثلًا)."""
    try:
        total = int(round(float(value) * 60))
    except (TypeError, ValueError):
        return value
    sign = '-' if total < 0 else ''
    total = abs(total)
    return f"{sign}{total // 60}:{total % 60:02d}"


# Excel: الساعاتُ قيمةُ وقتٍ حقيقيّة (ساعات ÷ 24) لا نصّ — فتبقى رقمًا يُجمع
# ويُفرز، وتُعرض 12:30. و`[h]` يسمح بأكثر من 24 ساعة (185:31 لا 17:31).
EXCEL_HOURS_FORMAT = '[h]:mm'


def excel_hours(value):
    try:
        return float(value) / 24.0
    except (TypeError, ValueError):
        return value
