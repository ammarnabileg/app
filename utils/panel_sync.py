# -*- coding: utf-8 -*-
"""ما يصل المستأجرَ من لوحة onz.one: الترخيص عند أول إقلاع، وبيانات
الدخول كلّما تغيّرت.

القناة واحدة وقائمة أصلًا — فحص الترخيص الدوري في
`check_online_license_secure` — فلا منفذ جديد يُفتح ولا طلب وارد يصل
المستأجر من الخارج. اللوحة تردّ على الفحص، والمستأجر يطبّق.

## لماذا لا يُمرَّر كل شيء في متغيّرات البيئة

متغيّرات البيئة تُقرأ مرة واحدة عند إنشاء قاعدة المستأجر. تكفي للبداية
ولا تكفي للمزامنة: العميل يغيّر كلمة مروره على الموقع بعد شهر، والمتغيّر
في Coolify يبقى كما هو — وتغييرُه يحتاج إعادة نشر، وإعادة النشر لا تُعيد
إنشاء القاعدة فلا أثر لها أصلًا.

## ولا تُمرَّر كلمة مرور قطّ

اللوحة تخزّن bcrypt ولا تحتفظ بالأصل، وما يُنقل هو البصمة. فمن قرأ
الشبكة أو السجلّ أو قاعدة المستأجر لا يجد كلمةً يستعملها في مكان آخر.
"""
import os
import sqlite3

from utils.db import get_db_connection


def _panel_admin_username():
    """اسم الحساب الذي أنشأته اللوحة، أو None في التركيب اليدوي.

    المتغيّر باقٍ في الحاوية بعد الإقلاع، فهو ما يميّز «الحساب المرتبط
    بالموقع» عن حسابٍ أنشأه العميل بنفسه. وغيابه يعني تركيبًا يدويًّا،
    وحينها لا تُمسّ أي كلمة مرور — لا شيء هنا يخصّ اللوحة.
    """
    name = (os.environ.get('HR_ADMIN_USERNAME') or '').strip()
    return name or None


def _synced_username(conn):
    """آخر اسم مستخدم زامنّاه، لنتعرّف على الصفّ بعد تغيير الاسم."""
    try:
        row = conn.execute(
            "SELECT value FROM system_settings_kv WHERE key = 'panel_admin_username'"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _remember_username(conn, username):
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS system_settings_kv (
                            key TEXT PRIMARY KEY, value TEXT)""")
        conn.execute("INSERT OR REPLACE INTO system_settings_kv (key, value) VALUES (?, ?)",
                     ('panel_admin_username', username))
    except sqlite3.Error as e:
        print(f'[sync] تعذّر حفظ اسم الحساب المزامَن: {e}')


def apply_admin_sync(payload):
    """يطبّق بيانات دخول العميل الآتية مع ردّ فحص الترخيص.

    payload: {'username': ..., 'password_hash': ...} أو أي شيء آخر فيُهمَل.

    يُرجِع True إن تغيّر شيء فعلًا. لا يرمي أبدًا: فشل المزامنة لا يجوز
    أن يُسقِط فحص الترخيص الذي يجري داخله، وإلا توقّف نظام العميل بسبب
    ميزة راحة.
    """
    if not isinstance(payload, dict):
        return False

    new_hash = (payload.get('password_hash') or '').strip()
    new_user = (payload.get('username') or '').strip()
    if not new_hash or not new_user:
        return False

    # تركيب يدوي: لا حساب مرتبط باللوحة، فلا شيء يُزامَن.
    env_user = _panel_admin_username()
    if not env_user:
        return False

    try:
        conn = get_db_connection()

        # الصفّ المقصود: آخر اسم زامنّاه إن وُجد، وإلا الاسم الذي أُنشئ
        # به الحساب. هذا ما يجعل تغيير اسم المستخدم على الموقع قابلًا
        # للمتابعة بدل أن يفقد النظام أثر الحساب.
        target = _synced_username(conn) or env_user

        row = conn.execute(
            "SELECT id, username, password FROM users WHERE LOWER(username) = LOWER(?)",
            (target,)
        ).fetchone()
        if not row:
            return False

        current_hash = row['password'] if not isinstance(row, tuple) else row[2]
        current_user = row['username'] if not isinstance(row, tuple) else row[1]
        user_id = row['id'] if not isinstance(row, tuple) else row[0]

        if current_hash == new_hash and current_user == new_user:
            return False                      # لا جديد

        conn.execute("UPDATE users SET username = ?, password = ? WHERE id = ?",
                     (new_user, new_hash, user_id))
        _remember_username(conn, new_user)
        conn.commit()
        print('[sync] حُدِّثت بيانات دخول الحساب المرتبط بالموقع.')
        return True

    except Exception as e:
        print(f'[sync] تعذّرت مزامنة بيانات الدخول: {e}')
        return False


def bootstrap_license_from_environment():
    """يفعّل الترخيص الذي مرّرته اللوحة، إن لم يكن مفعَّلًا بعد.

    بدون هذا يُفتح نظام العميل الجديد على شاشة «أدخل مفتاح الترخيص»،
    والمفتاح موجود عندنا أصلًا وقت التجهيز — فمطالبتُه بنسخه من بريده
    خطوةٌ لا سبب لها.

    لا يُنفَّذ إن كان هناك مفتاح محفوظ: إعادة النشر لا يجوز أن تعيد
    المستأجر إلى مفتاح قديم بعد تجديد.

    يُرجِع (تمّ؟, رسالة).
    """
    key = (os.environ.get('HR_LICENSE_KEY') or '').strip()
    if not key:
        return False, 'لا مفتاح في البيئة'

    try:
        from utils.license import (get_saved_license_key, save_license_key,
                                   verify_license_full_flow)

        if get_saved_license_key():
            return False, 'مفعَّل بالفعل'

        ok, msg = verify_license_full_flow(key)
        if not ok:
            # لا يُحفَظ مفتاح لم يُقبل: حفظُه يجعل الشاشة تقول «مفعَّل»
            # ثم يُمنع العميل عند أول فحص، وهو أسوأ من مطالبته بالمفتاح.
            print(f'[sync] مفتاح البيئة لم يُقبل: {msg}')
            return False, str(msg)

        save_license_key(key)
        print('[sync] فُعِّل الترخيص من البيئة تلقائيًّا.')
        return True, 'تمّ'

    except Exception as e:
        print(f'[sync] تعذّر تفعيل الترخيص من البيئة: {e}')
        return False, str(e)
