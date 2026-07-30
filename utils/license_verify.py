# -*- coding: utf-8 -*-
"""التحقق من الرخصة الموقّعة — يعمل أوفلاين بالكامل.

## لماذا هذا الملف موجود

النسخة السابقة كانت تتحقق بـ `sha256(تاريخ + سرّ مشترك)`، والسرّ نفسه مكتوب
في الكود المُسلَّم للعميل. أي شخص معه الملف يستطيع توليد تراخيص صالحة إلى
أي تاريخ — وهو ما جرى إثباته عمليًّا قبل هذا التغيير.

## المبدأ

التحقق أوفلاين **لا يحتاج** أن يملك العميل سرّ التوقيع. يحتاج فقط
**المفتاح العام**:

    الخادم (PHP)  ← المفتاح الخاص  ← يُصدر ويوقّع
    العميل (هنا)  ← المفتاح العام  ← يتحقق فقط

المفتاح العام أدناه مكشوف عمدًا؛ كشفه غير ضار لأنه لا يُوقِّع. فالنتيجة:
تحقّق كامل بلا إنترنت، مع استحالة التزوير بلا المفتاح الخاص.

## ما تحتويه الرخصة

بند `max_devices` جزء من النصّ الموقَّع لا إعدادًا محليًّا — لأن الإعداد
المحلي كان يُعدَّل بصفٍّ واحد في قاعدة البيانات.

## الحدّ الصريح

هذا يمنع **تزوير** الرخص، ولا يمنع من يعدّل الكود نفسه ليتخطّى الفحص. لا
حماية في كود يعمل على جهاز العميل تمنع ذلك. الفارق أن أخطر سيناريو — عميل
يولّد رخصًا ويبيعها — يصبح مستحيلًا.
"""
import base64
import json
from datetime import date, datetime

# المفتاح العام. مكشوف عمدًا: يتحقق ولا يوقّع.
LICENSE_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA83ULWktJ4lwBPhoU/deS
BTz7geE+7DlPYiRce06H3x0/XeAWB+zxcnk6M/F/g3VBiubpZWKVhXpOi1eKyZJo
kWagZxyxzzIBaOrTVf8hqunxdRXr59WZu2qdD7cXNN6OZ3qRv6ZlLK5/WokhRCYO
/8ngLw3e4YrA6/ybX4OGrmCSwL88KjH7uJseJSEBtEXu5TMHqSHZVm+1pekLC1Nr
j/WZMy94gcip2ojWfzsZQL05s4H08Z6O9GH/h7qZDJ1JwG1ANt7Lk4I6iuWfFWpX
0JXrEPBlNisupGF4zUauWbmvQIsRY3kd8fxq8we/TR4f2FpecT4jComFsowqLx3w
tQIDAQAB
-----END PUBLIC KEY-----"""

TOKEN_PREFIX = 'ONZ1.'


class LicenseError(Exception):
    pass


def _b64d(s):
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _verify_signature(payload_bytes, signature_bytes):
    """التحقق من التوقيع بالمفتاح العام."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature

    pub = serialization.load_pem_public_key(LICENSE_PUBLIC_KEY.encode('utf-8'))
    try:
        pub.verify(signature_bytes, payload_bytes,
                   padding.PKCS1v15(), hashes.SHA256())
        return True
    except InvalidSignature:
        return False


def parse_license_token(token):
    """يفكّ الرمز ويتحقق من توقيعه. يُرجع الحمولة أو يرفع LicenseError.

    الصيغة: ONZ1.<payload_b64url>.<signature_b64url>
    """
    if not token or not isinstance(token, str):
        raise LicenseError('no_token')
    token = token.strip()
    if not token.startswith(TOKEN_PREFIX):
        raise LicenseError('bad_format')

    parts = token[len(TOKEN_PREFIX):].split('.')
    if len(parts) != 2:
        raise LicenseError('bad_format')

    payload_b64, sig_b64 = parts
    try:
        payload_bytes = _b64d(payload_b64)
        signature = _b64d(sig_b64)
    except Exception:
        raise LicenseError('bad_encoding')

    # التوقيع يُتحقق منه على البايتات كما وردت، قبل أي تحليل — حتى لا
    # يُفسَّر محتوى غير موثوق.
    if not _verify_signature(payload_bytes, signature):
        raise LicenseError('bad_signature')

    try:
        data = json.loads(payload_bytes.decode('utf-8'))
    except Exception:
        raise LicenseError('bad_payload')

    for field in ('key', 'expiry', 'max_devices', 'issued_at'):
        if field not in data:
            raise LicenseError('missing_field:' + field)
    return data


def verify_license_token(token, current_hwid=None, today=None):
    """التحقق الكامل: توقيع + تاريخ + جهاز.

    يُرجع dict فيه ok والسبب والتفاصيل — ولا يرفع استثناءً، لأن مسار
    الإقلاع لا يجوز أن ينهار بسبب رخصة تالفة.
    """
    result = {'ok': False, 'reason': None, 'data': None,
              'days_left': None, 'max_devices': 0}
    try:
        data = parse_license_token(token)
    except LicenseError as e:
        result['reason'] = str(e)
        return result
    except Exception as e:
        result['reason'] = 'verify_error:' + type(e).__name__
        return result

    result['data'] = data

    # الجهاز: يُتحقق فقط إن كانت الرخصة مربوطةً بجهاز
    bound = data.get('hwid')
    if bound and current_hwid and bound != current_hwid:
        result['reason'] = 'hwid_mismatch'
        return result

    today = today or date.today()
    try:
        expiry = datetime.strptime(str(data['expiry'])[:10], '%Y-%m-%d').date()
    except ValueError:
        result['reason'] = 'bad_expiry'
        return result

    result['days_left'] = (expiry - today).days
    if today > expiry:
        result['reason'] = 'expired'
        return result

    try:
        result['max_devices'] = int(data.get('max_devices', 0))
    except (TypeError, ValueError):
        result['max_devices'] = 0

    result['ok'] = True
    return result


def get_licensed_max_devices(token, current_hwid=None):
    """حدّ الأجهزة من الرخصة الموقّعة.

    يُرجع 0 عند أي فشل — لا قيمة افتراضية سخيّة. القيمة الافتراضية السخيّة
    عند الفشل تعني أن إتلاف الرخصة يرفع الحدّ بدل أن يخفضه.
    """
    r = verify_license_token(token, current_hwid=current_hwid)
    return r['max_devices'] if r['ok'] else 0
