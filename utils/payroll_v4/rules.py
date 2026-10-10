# -*- coding: utf-8 -*-
"""طبقة الشركة (Company Regulation Layer) — كلُّ قرارٍ في الحساب يأتي من إعدادات الشركة.

تُقرأ من `salary_settings_v2` (نفس جدول إعدادات الرواتب الحاليّ). **الافتراضيّ لكلّ مفتاحٍ جديد = ما يفعله
المسيرُ الحاليّ** (utils/payroll_engine) — فلا يتغيّر رقمٌ عند أيّ عميل حتى يغيّر إعداده بنفسه:

| المفتاح | معناه | الافتراضيّ (= الحاليّ) |
|---|---|---|
| `daily_rate_basis`        | أساس اليوم: `26` / `30` / `working` (أيام الدوام الفعليّة) | 26 (كالمحرّك الحاليّ) |
| `ot_requires_approval`    | الأوفرتايم لا يُحسب إلا باعتماد                        | 0 — يُحسب تلقائيًّا |
| `ot_grace_minutes`        | دقائق بعد نهاية الشفت لا تُحسب أوفرتايم                 | 0 |
| `ot_requires_full_shift`  | لا أوفرتايم إلا لمن أكمل ساعات الشفت                    | 0 |
| `overtime_round_to_minutes` | تقريب الأوفرتايم                                       | 0 |
| `weekday/weekend/holiday_ot_multiplier` | المعاملات                                  | 1.25 / 1.5 / 2.0 |
| `retro_requires_approval` | الفرق الرجعيّ ينتظر اعتماد مسؤول الرواتب                | 1 (قرارُ صاحب المنتج) |
| `retro_back_months`       | أقصى عدد شهور للخلف                                     | 3 |
"""
from dataclasses import dataclass


def _f(v, d):
    try:
        return float(v) if v not in (None, '') else d
    except (TypeError, ValueError):
        return d


def _b(v, d):
    if v in (None, ''):
        return d
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class CompanyRules:
    day_basis: str = '26'
    ot_requires_approval: bool = False
    ot_grace_minutes: int = 0
    ot_requires_full_shift: bool = False
    ot_round_to_minutes: int = 0
    weekday_ot_multiplier: float = 1.25
    weekend_ot_multiplier: float = 1.5
    holiday_ot_multiplier: float = 2.0
    retro_requires_approval: bool = True
    retro_back_months: int = 3

    @property
    def matches_current_engine(self):
        """الإعدادات الجديدة على قيمها الافتراضيّة؟ (V4 لازم يطلع زي الحاليّ بالظبط)."""
        return not self.ot_requires_approval and not self.ot_grace_minutes and not self.ot_requires_full_shift

    def daily_divisor(self, working_days=None):
        if self.day_basis == '30':
            return 30.0
        if self.day_basis == 'working' and working_days:
            return float(working_days)
        return 26.0


def load(conn):
    from utils.settings_utils import get_salary_settings_v2
    s = get_salary_settings_v2(conn) or {}
    basis = str(s.get('daily_rate_basis', '26') or '26').strip()
    if basis not in ('26', '30', 'working'):
        basis = '30'                    # كالمحرّك الحاليّ: قيمةٌ غير معروفة = 30
    return CompanyRules(
        day_basis=basis,
        ot_requires_approval=_b(s.get('ot_requires_approval'), False),
        ot_grace_minutes=int(_f(s.get('ot_grace_minutes'), 0)),
        ot_requires_full_shift=_b(s.get('ot_requires_full_shift'), False),
        ot_round_to_minutes=int(_f(s.get('overtime_round_to_minutes'), 0)),
        weekday_ot_multiplier=_f(s.get('weekday_ot_multiplier'), 1.25),
        weekend_ot_multiplier=_f(s.get('weekend_ot_multiplier'), 1.5),
        holiday_ot_multiplier=_f(s.get('holiday_ot_multiplier'), 2.0),
        retro_requires_approval=_b(s.get('retro_requires_approval'), True),
        retro_back_months=max(0, int(_f(s.get('retro_back_months'), 3))),
    )
