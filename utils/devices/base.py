# -*- coding: utf-8 -*-
"""ما يقدّمه كلّ سوّاق — والشاشةُ لا تعرف غيرَ هذا.

كلُّ سوّاقٍ يعلن **قدراته** (`capabilities`)، والشاشةُ تُظهر لكلّ جهازٍ ما يقدر
عليه وحده: لا زرَّ «سحب البصمات» لجهازٍ لا يسمح به. وما لا يقدر عليه يرمي
`NotSupported` برسالةٍ تُعرض كما هي.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ------------------------------------------------------------ القدرات

TEST = 'test'                    # فحص الاتّصال
PULL_ATTENDANCE = 'pull'         # سحب الحضور من الجهاز
PUSH_EVENTS = 'push_events'      # الجهاز يرسل الحضور لحظيًّا إلى البرنامج
USERS = 'users'                  # إضافة/تعديل/قراءة الموظّفين على الجهاز
DISABLE = 'disable'              # إيقاف موظّف دون حذفه
DELETE = 'delete'                # حذف موظّف من الجهاز
TEMPLATES = 'templates'          # سحب البصمات ورفعها (نفس الماركة)
FACE_PHOTO = 'face_photo'        # تسجيل الوجه من صورة الموظّف
SET_TIME = 'set_time'            # ضبط ساعة الجهاز

CAPABILITY_LABELS = {
    TEST: 'فحص الاتصال', PULL_ATTENDANCE: 'سحب الحضور', PUSH_EVENTS: 'الحضور لحظيًا',
    USERS: 'الموظفين', DISABLE: 'إيقاف موظف', DELETE: 'حذف موظف', TEMPLATES: 'نقل البصمات',
    FACE_PHOTO: 'الوش من الصورة', SET_TIME: 'ضبط الوقت',
}


class DriverError(Exception):
    """فشلٌ من الجهاز أو في الاتّصال به — الرسالةُ تُعرض للمستخدم."""


class NotSupported(DriverError):
    """الماركةُ لا تقدّم هذه العمليّة (أو لم تُبنَ بعد)."""


@dataclass
class Punch:
    """بصمةُ حضورٍ معياريّة — من أيّ ماركة."""
    pin: str
    time: datetime
    check_type: int = 0                 # 0 دخول، 1 خروج (إن عرفه الجهاز)
    verify: str = ''                    # بصمة / وجه / كارت …
    raw: dict = field(default_factory=dict)


@dataclass
class DeviceUser:
    pin: str
    name: str = ''
    card: str = ''
    enabled: bool = True
    raw: dict = field(default_factory=dict)


class BaseDriver:
    key = ''
    label = ''
    brand = ''
    experimental = True
    capabilities = frozenset()
    default_port = 80
    needs_login = True                  # اسم مستخدم وكلمة مرور للجهاز

    def __init__(self, device, password: Optional[str] = None, http=None):
        """device: صفُّ fingerprint_devices (أو قاموس). password: مفكوكةً.
        http: بديلُ requests في الاختبارات."""
        self.device = dict(device) if not isinstance(device, dict) else device
        self.password = password or ''
        self._http = http

    # ---- معلومات
    def supports(self, cap):
        return cap in self.capabilities

    def _need(self, cap, what):
        if cap not in self.capabilities:
            raise NotSupported(f'{self.label}: {what} غير مدعوم')

    @property
    def username(self):
        return (self.device.get('auth_user') or '').strip()

    def base_url(self):
        d = self.device
        scheme = 'https' if int(d.get('use_https') or 0) else 'http'
        host = (d.get('device_ip') or '').strip()
        port = int(d.get('device_port') or self.default_port)
        default = 443 if scheme == 'https' else 80
        return f'{scheme}://{host}' + ('' if port == default else f':{port}')

    # ---- العمليّات (تُعاد كتابتُها في كلّ سوّاق بحسب قدراته)
    def test(self) -> dict:
        """{'ok': bool, 'message': str, 'info': {...}}"""
        raise NotSupported(f'{self.label}: فحص الاتصال غير مدعوم')

    def pull_attendance(self, since: Optional[datetime], until: Optional[datetime] = None) -> list:
        self._need(PULL_ATTENDANCE, 'سحب الحضور')

    def list_users(self) -> list:
        self._need(USERS, 'قراءة الموظفين')

    def upsert_user(self, pin, name, card='', enabled=True):
        self._need(USERS, 'إضافة موظف')

    def set_enabled(self, pin, enabled):
        self._need(DISABLE, 'إيقاف/تفعيل موظف')

    def delete_user(self, pin):
        self._need(DELETE, 'حذف موظف')

    def enroll_face_photo(self, pin, jpeg_bytes):
        self._need(FACE_PHOTO, 'تسجيل الوش من صورة')

    def set_time(self, when: datetime):
        self._need(SET_TIME, 'ضبط الوقت')

    @classmethod
    def parse_push(cls, request):
        """حدثٌ يرسله الجهازُ بنفسه → (معرّف الجهاز: dict، [Punch])."""
        raise NotSupported(f'{cls.label}: الاستقبال اللحظي غير مدعوم')


# ------------------------------------------------------------ HTTP مشترك (Hikvision / Dahua / Suprema)

class HttpDriver(BaseDriver):
    """طلبات HTTP بمصادقة الجهاز. `http` في الاختبارات: كائنٌ له request(method, url, **kw)."""
    digest = True                       # Hikvision وDahua: Digest؛ Suprema: جلسة
    timeout = 15

    def _options(self):
        import json
        try:
            return json.loads(self.device.get('driver_options') or '{}') or {}
        except Exception:
            return {}

    def _verify_tls(self):
        # شهادةٌ ذاتيّة على جهازٍ داخل الشبكة: يختار المستخدم تجاهلَها صراحةً من شاشة الجهاز.
        return not bool(self._options().get('insecure_tls'))

    def _req(self, method, path, **kw):
        url = path if path.startswith('http') else self.base_url() + path
        kw.setdefault('timeout', self.timeout)
        if self._http is not None:
            return self._http.request(method, url, **kw)
        import requests
        if self.digest and self.username and 'auth' not in kw:
            kw['auth'] = requests.auth.HTTPDigestAuth(self.username, self.password)
        kw.setdefault('verify', self._verify_tls())
        try:
            return requests.request(method, url, **kw)
        except requests.exceptions.SSLError as e:
            raise DriverError('شهادة HTTPS على الجهاز غير موثوقة — فعّل «تجاهل شهادة HTTPS» '
                              'لو الجهاز داخل شبكتك وشهادته ذاتية') from e
        except requests.exceptions.RequestException as e:
            raise DriverError(f'تعذّر الاتصال بالجهاز {url}: {str(e)[:120]}') from e

    @staticmethod
    def _check(resp, what):
        code = getattr(resp, 'status_code', 0)
        if code == 401:
            raise DriverError(f'{what}: اسم المستخدم أو كلمة المرور غلط')
        if code >= 400:
            body = (getattr(resp, 'text', '') or '')[:200]
            raise DriverError(f'{what}: الجهاز رد بخطأ {code} {body}')
        return resp

    @staticmethod
    def _json(resp):
        try:
            return resp.json()
        except Exception:
            return {}
