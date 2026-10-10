# -*- coding: utf-8 -*-
"""نسخةٌ مجهّلة من قاعدة عميل — لاختبارات الظلّ في محرّك الرواتب V4 (docs/PAYROLL_V4_PLAN.md).

    python tools/anonymize_db.py hr_system.db out/anon.db

- **لا تلمس الأصل:** تنسخه أوّلًا ثمّ تعدّل النسخة.
- تمحو ما يدلّ على شخص: الأسماء، والبطاقة المدنيّة، والجواز، والإقامة، والهاتف، والبريد، والعنوان،
  والحسابات البنكيّة، وجهات الطوارئ، وحسابات الدخول وكلمات مرورها، والصور، والمستندات.
- **تُبقي ما يحتاجه الحساب كما هو:** رقم الموظّف، والراتب والبنود، والشفت، والبصمات، والإجازات، والتواريخ —
  وإلّا ما عادت النتيجة تُقارَن.

النسخةُ المجهّلة ما زالت فيها رواتب حقيقيّة: مكانها `tests/fixtures/payroll_private/` (خارج git) ولا تُرفع.
"""
import os
import shutil
import sqlite3
import sys

# (جدول، عمود، قيمة بديلة — '{id}' يُستبدل برقم الصف)
SCRUB = [
    ('employees', 'name', 'موظف {id}'),
    ('employees', 'arabic_name', 'موظف {id}'),
    ('employees', 'national_id', None), ('employees', 'passport_number', None),
    ('employees', 'residency_number', None), ('employees', 'phone', None), ('employees', 'email', None),
    ('employees', 'address', None), ('employees', 'work_email', None), ('employees', 'work_phone', None),
    ('employees', 'emergency_contact_name', None), ('employees', 'emergency_contact_phone', None),
    ('employees', 'emergency_contact_relation', None), ('employees', 'bank_account_number', None),
    ('employees', 'bank_iban', None), ('employees', 'bank_swift', None), ('employees', 'social_security_number', None),
    ('employees', 'tax_id', None), ('employees', 'password', None), ('employees', 'card_number', None),
    ('employees', 'dob', None), ('employees', 'pob', None),
    ('users', 'full_name', 'مستخدم {id}'), ('users', 'username', 'user{id}'), ('users', 'password', 'x'),
    ('fingerprint_users', 'name', None),
    ('leave_requests', 'reason', None), ('attendance_excuses', 'reason', None),
    ('payroll_transactions', 'reason', None), ('employee_loans', 'reason', None), ('employee_loans', 'notes', None),
    ('payroll_run_lines', 'details_json', None),
    ('employee_audit_log', 'old_value', None), ('employee_audit_log', 'new_value', None),
]
# جداولٌ تُفرَّغ كلّها: صور، ومستندات، ومفاتيح، وطوابير رفع، وإشعارات.
EMPTY = ['user_photos', 'documents', 'fingerprint_templates', 'notifications', 'cloud_outbox',
         'license_settings', 'password_reset_tokens', 'message_outbox', 'field_visit_photos']
# إعداداتٌ فيها أسرار.
SECRET_SETTINGS = ('license_token', 'cloud_sync_api_key', 'device_secret', 'survey_salt')


def anonymize(src, dst):
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError('الناتج لازم يبقى ملف تاني — الأصل ما يتلمسش')
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    # نسخةٌ متّسقة حتى لو البرنامج شغّال (backup API لا نسخ ملف).
    s = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    d = sqlite3.connect(dst)
    s.backup(d)
    s.close()
    tables = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    done = 0
    for t, col, val in SCRUB:
        if t not in tables or col not in {r[1] for r in d.execute(f'PRAGMA table_info({t})')}:
            continue
        if val and '{id}' in val:
            pre, post = val.split('{id}')
            d.execute(f"UPDATE {t} SET {col} = ? || id || ?", (pre, post))
        else:
            d.execute(f"UPDATE {t} SET {col} = ?", (val,))
        done += 1
    for t in EMPTY:
        if t in tables:
            d.execute(f'DELETE FROM {t}')
    for st in ('app_settings',):
        if st in tables:
            d.execute(f"DELETE FROM {st} WHERE setting_key IN ({','.join('?' * len(SECRET_SETTINGS))})",
                      SECRET_SETTINGS)
    d.commit()
    d.execute('VACUUM')
    d.close()
    return done


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    n = anonymize(sys.argv[1], sys.argv[2])
    print(f'اتعمل: {sys.argv[2]} ({n} عمود اتمسح)')
