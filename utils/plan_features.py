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
FULL = 'full_edition'           # «الميزة» اللي النسخة المجانية ناقصها — للصفحة المقفولة

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
    'portal.payslip': 'payroll',
    'portal.api_request_leave': 'leaves',
    'portal.api_request_excuse': 'excuses',
}

# مساراتٌ تخدمها دالّةٌ مقفولة وهي أساس.
OPEN_RULES = frozenset({'/fingerprint/employee_report/api'})

LABELS = {
    FULL: 'النسخة الكاملة',
    'payroll': 'الرواتب', 'loans': 'القروض والسلف', 'eos': 'نهاية الخدمة',
    'leaves': 'الإجازات', 'excuses': 'الأعذار والأذونات', 'field_app': 'التطبيق الميدانيّ',
    'portal': 'بوّابة الموظّفين', 'whatsapp': 'إشعارات واتساب',
}

_CACHE = {'at': 0.0, 'value': None, 'key': None, 'data': None}
_TTL = 60

# ------------------------------------------------------------ النسخة المجانية (من onz.one)
#
# رخصة الخطّة المجانية فيها `features` بيحتوي FREE_MARKER، ومعاها `trial_until` (تاريخ نهاية
# التجربة الكاملة — ٣٠ يوم من التسجيل). خلال التجربة كل حاجة مفتوحة؛ بعدها البرنامج بيتحوّل
# **لوحده** للمجاني (من غير ما يحتاج نت): المسموح بس هو اللي تحت، والباقي ظاهر ومقفول بقفل
# وزرار «اشترِ وافتحها». الحدود (٢٥ موظف، والأجهزة) من الرخصة نفسها.
FREE_MARKER = 'free_edition'
FREE_MAX_EMPLOYEES = 25

# المجاني = الأجهزة والبصمات والداتا بتاعتها. **قائمة المسموح** (مش المقفول): أي شاشة
# جديدة بتتعمل بعدين بتبقى مقفولة في المجاني لوحدها لحد ما تتضاف هنا.
FREE_BLUEPRINTS = frozenset({'auth', 'adms_mgr', 'adms_iclock', 'backup', 'sensitive'})
FREE_ENDPOINTS = frozenset({
    'index', 'static',
    # الرئيسية والإعدادات والرخصة
    'main.index', 'main.api_stats', 'main.api_recent_activities', 'main.api_dashboard_analytics',
    'main.license_page', 'main.license_state_api', 'main.unbind_license_hwid', 'main.version_api',
    'main.set_language', 'main.settings', 'main.update_settings', 'main.save_device_clock',
    'main.setup_wizard', 'main.setup_wizard_save',
    # الأجهزة
    'attendance.fingerprint', 'attendance.devices', 'attendance.add_device', 'attendance.add_devices_bulk',
    'attendance.edit_device', 'attendance.delete_device', 'attendance.toggle_device', 'attendance.manage_device_limit',
    'attendance.add_branch_quick', 'attendance.test_device_api', 'attendance.test_device_route',
    'attendance.test_duplicate_route', 'attendance.scan_network_api', 'attendance.search_adms_devices_api',
    'attendance.reboot_device_api', 'attendance.factory_reset_device_api', 'attendance.clear_device_users_api',
    'attendance.get_device_user_count_api', 'attendance.device_lookup_page',
    # المزامنة والبصمات ومستخدمين الأجهزة
    'attendance.sync_fingerprint', 'attendance.sync_users', 'attendance.sync_from_device',
    'attendance.sync_from_device_api', 'attendance.adms_sync_users', 'attendance.upload_users_page',
    'attendance.upload_users_api', 'attendance.run_device_autosync_now', 'attendance.toggle_device_autosync',
    'attendance.toggle_device_autosync_templates', 'attendance.punch_shift_page',
    # سجل الحركات الخام وتصديره، وسجلات المزامنة
    'attendance.punches_log', 'attendance.punches_log_export',
    'report.fingerprint_sync_logs', 'report.get_logs_api',
    # الموظفين (البيانات الأساسية)
    'employee.employees', 'employee.add_employee', 'employee.edit_employee', 'employee.delete_employee',
    'employee.api_employees', 'employee.get_employees_api', 'employee.api_search_employees',
    'employee.api_employee_picker', 'employee.api_departments', 'employee.api_positions',
    'employee.add_department_api', 'employee.add_position_api', 'employee.import_employees',
    'employee.download_template', 'employee.device_new_employees_page', 'employee.api_device_new_employees',
    'employee.api_device_new_employee_done',
})
# المزايا اللي «شغالة» في المجاني (للـhas_feature في الشاشات وفي الشغل اللي في الخلفية).
FREE_FEATURES = frozenset({'attendance', 'devices', 'otp_required'})


def _license_data():
    """بيانات الرخصة الموقَّعة (dict) — أو None. نفس كاش المزايا."""
    licensed_features()
    return _CACHE['data']


def _parse_day(v):
    from datetime import datetime
    s = str(v or '').strip().replace('T', ' ')
    for fmt, n in (('%Y-%m-%d %H:%M:%S', 19), ('%Y-%m-%d', 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def edition():
    """'full' (رخصة مدفوعة أو قديمة)، 'trial' (مجاني جوه الـ٣٠ يوم)، أو 'free'."""
    feats = licensed_features()
    if not feats or FREE_MARKER not in feats:
        return 'full'
    from datetime import datetime
    until = _parse_day((_license_data() or {}).get('trial_until'))
    if until and datetime.now() <= until.replace(hour=23, minute=59, second=59):
        return 'trial'
    return 'free'


def trial_days_left():
    if edition() != 'trial':
        return 0
    from datetime import datetime
    until = _parse_day((_license_data() or {}).get('trial_until'))
    return max(0, (until.date() - datetime.now().date()).days + 1) if until else 0


def free_allows(endpoint):
    if not endpoint:
        return True
    if endpoint in FREE_ENDPOINTS:
        return True
    return endpoint.split('.', 1)[0] in FREE_BLUEPRINTS


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
    data = None
    try:
        from utils.license import get_saved_license_key, signed_license_for
        r = signed_license_for(get_saved_license_key())
        data = (r.get('data') or {}) if r else None
        feats = data.get('features') if data else None
        if isinstance(feats, list):
            value = frozenset(str(f) for f in feats)
    except Exception as e:
        print(f"[features] تعذّر قراءة مزايا الخطّة: {e}")
    _CACHE['at'], _CACHE['value'], _CACHE['key'], _CACHE['data'] = now, value, key, data
    return value


def invalidate():
    _CACHE['at'] = 0.0


def has_feature(feature):
    ed = edition()
    if ed == 'trial':
        return True                 # التجربة الكاملة: كل حاجة
    if ed == 'free':
        return feature in FREE_FEATURES
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
    ed = edition()
    if ed == 'trial':
        return None
    if ed == 'free':
        return None if free_allows(endpoint) else FULL
    f = feature_for(endpoint, rule)
    if f is None or has_feature(f):
        return None
    return f


_LOCKED_PATHS = {'v': None}


def free_locked_paths(app):
    """مسارات الشاشات (GET من غير متغيّرات) المقفولة في المجاني — عشان القائمة تحط عليها قفل."""
    if _LOCKED_PATHS['v'] is None:
        out = set()
        for r in app.url_map.iter_rules():
            if 'GET' in (r.methods or ()) and not r.arguments and not free_allows(r.endpoint):
                out.add(r.rule.rstrip('/') or '/')
        _LOCKED_PATHS['v'] = sorted(out)
    return _LOCKED_PATHS['v']
