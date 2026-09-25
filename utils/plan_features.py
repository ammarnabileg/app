# -*- coding: utf-8 -*-
"""مزايا الخطّة — أيُّ الشاشات يفتحها الاشتراك.

## من أين

من **الرخصة الموقَّعة** (`features`): اللوحةُ تكتب فيها مزايا خطّة العميل
كما هي الآن في «خطط الاشتراك»، وتتجدّد مع كل تحقّق (كل ساعة). فتغييرُ
الخطّة أو ترقيتُها يصل النظامَ خلال ساعة بلا خطوةٍ من العميل.

**ولا قيدَ حين لا يحمل الرمزُ الحقل**: الرخصُ القديمة (قبل الاشتراكات) تفتح
كلَّ شيء — تحديثٌ لا يُقفل على عميلٍ اشترى النظامَ كاملًا.

## ما يُقفل

الشاشاتُ وواجهاتُها فقط. **البياناتُ باقية**: من نزل من «احترافيّ» إلى «حضور»
تبقى كشوفُه ورواتبُه في قاعدته، وتعود حين يرقّي. والحضورُ والتقاريرُ والعطلُ
الرسميّة **أساسٌ لا يُقفل**: بدونها لا نظام.

## الخريطة

بالـblueprint حين يكون كلُّه لميزة، وبالـendpoint حين يخلط (`salary` فيه
الرواتب **وأنواع الورديات** — والورديات حضور)، وبالمسار حين تخدم دالّةٌ
واحدة شاشتين (`salary_report_api` تخدم تقرير الحضور المفصّل أيضًا).
"""
import os
import time

SUBSCRIPTION_URL = 'https://onz.one/client/subscription'

# لا تُقفل أبدًا — ولو غابت عن الخطّة.
CORE = frozenset({'attendance', 'reports', 'otp_required', 'priority_support'})

BLUEPRINT_FEATURE = {
    'payroll': 'payroll',
    'eos': 'eos',
    'leave_settings': 'leaves',
    'attendance_excuses': 'excuses',
    'field': 'field_app',
}

ENDPOINT_FEATURE = {
    # السلف داخل blueprint الرواتب — ميزةٌ مستقلّة.
    'payroll.loans': 'loans',
    'payroll.add_loan': 'loans',
    'payroll.add_loan_payment': 'loans',
    'payroll.set_loan_status': 'loans',
    # salary: الرواتبُ وحدها (أنواعُ الورديات حضور).
    'salary.salaries': 'payroll',
    'salary.add_salary': 'payroll',
    'salary.add_salary_from_report': 'payroll',
    'salary.salary_settings_v2_route': 'payroll',
    'report.detailed_salary_report': 'payroll',
    'report.salary_reports': 'payroll',
    'report.salary_report_api': 'payroll',
    # الإجازات — عدا العطل الرسميّة (يقرؤها حسابُ الحضور).
    'leave.leaves': 'leaves',
    'leave.add_leave_request': 'leaves',
    'leave.update_leave_status': 'leaves',
    'leave.process_approval': 'leaves',
    'leave.cancel_leave': 'leaves',
    'leave.edit_leave': 'leaves',
    'leave.leave_cancellations': 'leaves',
    'leave.api_leave_balance': 'leaves',
    'leave.api_leave_balance_alerts': 'leaves',
    'leave.my_approvals': 'leaves',
    'leave.leave_balance_page': 'leaves',
    'leave.api_leave_balance_adjust': 'leaves',
    'leave.api_leave_balance_adjustments': 'leaves',
    # البوّابة: البصمةُ أساس، والطلباتُ ميزاتُها.
    'portal.api_request_leave': 'leaves',
    'portal.api_request_excuse': 'excuses',
}

# مساراتٌ تخدمها دالّةٌ مقفولة وهي أساس.
OPEN_RULES = frozenset({'/fingerprint/employee_report/api'})

LABELS = {
    'payroll': 'الرواتب', 'loans': 'القروض والسلف', 'eos': 'نهاية الخدمة',
    'leaves': 'الإجازات', 'excuses': 'الأعذار والأذونات', 'field_app': 'التطبيق الميدانيّ',
    'portal': 'بوّابة الموظّفين', 'whatsapp': 'إشعارات واتساب',
}

_CACHE = {'at': 0.0, 'value': None, 'key': None}
_TTL = 60


def licensed_features():
    """مزايا الخطّة (set) — أو None: بلا قيد.

    يُخزَّن دقيقة: القراءةُ تتحقّق من توقيع RSA وتفتح قاعدة، ولا تكون مع كل طلب.
    ولا ترمي: خطأٌ هنا لا يُسقط صفحة.
    """
    now = time.time()
    # مفتاحُه مجلّدُ البيانات: عمليّةٌ واحدة قد تخدم تركيبين (الاختبارات، أو
    # نقلُ مجلّد البيانات) — ولا تُقرأ مزايا تركيبٍ من كاش آخر.
    key = os.environ.get('HR_DATA_DIR', '')
    if now - _CACHE['at'] < _TTL and _CACHE['key'] == key:
        return _CACHE['value']
    value = None
    try:
        from utils.license import get_saved_license_key, signed_license_for
        r = signed_license_for(get_saved_license_key())
        feats = (r.get('data') or {}).get('features') if r else None
        if isinstance(feats, list):
            value = frozenset(str(f) for f in feats)
    except Exception as e:
        print(f"[features] تعذّر قراءة مزايا الخطّة: {e}")
    _CACHE['at'], _CACHE['value'], _CACHE['key'] = now, value, key
    return value


def invalidate():
    _CACHE['at'] = 0.0


def has_feature(feature):
    if feature in CORE:
        return True
    feats = licensed_features()
    return feats is None or feature in feats


def feature_for(endpoint, rule=None):
    """الميزةُ التي تحتاجها الشاشة — أو None (أساس)."""
    if not endpoint:
        return None
    if rule in OPEN_RULES:
        return None
    if endpoint in ENDPOINT_FEATURE:
        return ENDPOINT_FEATURE[endpoint]
    return BLUEPRINT_FEATURE.get(endpoint.split('.', 1)[0])


def missing_for_request(endpoint, rule=None):
    """الميزةُ الناقصة لهذا الطلب — أو None."""
    f = feature_for(endpoint, rule)
    if f is None or has_feature(f):
        return None
    return f
