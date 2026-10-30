# -*- coding: utf-8 -*-
"""Suprema — عبر خادم **BioStar 2** عند العميل (REST). **تجريبيّ: لم يُجرَّب.**

أجهزة Suprema في الشركات تُدار غالبًا من BioStar 2، وهو يوزّع الموظّفين على
الأجهزة بنفسه. فالجهاز هنا **خادمُ BioStar** (عنوانه ومنفذه وحساب API)، لا كلّ
قارئٍ وحده. من توثيق BioStar 2 API العامّ:

    دخول      POST /api/login {User: {login_id, password}} → ترويسة bs-session-id
    فحص       GET  /api/devices
    موظّفون   GET  /api/users · POST /api/users · PUT /api/users/{id} · DELETE /api/users/{id}
    حضور      POST /api/events/search   (datetime بين وقتين)

**الحضور** = أحداثُ التحقّق الناجحة: الرموز 4096–4111 (1:1) و4864–4879 (1:N).
**الإيقاف** = `disabled: "true"`.
"""
from datetime import datetime, timedelta, timezone

from . import base


def _ok_code(code):
    try:
        c = int(code)
    except (TypeError, ValueError):
        return False
    return 4096 <= c <= 4111 or 4864 <= c <= 4879


def _utc(dt):
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.00Z')


class SupremaBioStarDriver(base.HttpDriver):
    key = 'suprema'
    label = 'Suprema (BioStar 2)'
    brand = 'Suprema'
    default_port = 443
    digest = False
    capabilities = frozenset({base.TEST, base.PULL_ATTENDANCE, base.USERS, base.DISABLE, base.DELETE})

    _session = None

    def _login(self):
        if self._session:
            return self._session
        r = self._req('POST', '/api/login', json={'User': {'login_id': self.username, 'password': self.password}},
                      _raw=True)
        if getattr(r, 'status_code', 0) in (401, 403):
            raise base.DriverError('BioStar: اسم المستخدم أو كلمة المرور غلط')
        self._check(r, 'الدخول على BioStar')
        sid = (getattr(r, 'headers', {}) or {}).get('bs-session-id')
        if not sid:
            raise base.DriverError('BioStar ما رجّعش جلسة (bs-session-id)')
        self._session = sid
        return sid

    def _req(self, method, path, **kw):
        if not kw.pop('_raw', False):
            headers = dict(kw.pop('headers', {}) or {})
            headers['bs-session-id'] = self._login()
            kw['headers'] = headers
        return super()._req(method, path, **kw)

    def test(self):
        r = self._check(self._req('GET', '/api/devices'), 'فحص الاتصال')
        rows = (self._json(r).get('DeviceCollection') or {}).get('rows') or []
        return {'ok': True, 'message': f'متصل بـ BioStar 2 — {len(rows)} جهاز مسجّل عليه', 'info': {'devices': len(rows)}}

    def list_users(self):
        r = self._check(self._req('GET', '/api/users?limit=0&offset=0'), 'قراءة الموظفين')
        rows = (self._json(r).get('UserCollection') or {}).get('rows') or []
        return [base.DeviceUser(pin=str(u.get('user_id', '')), name=u.get('name', ''),
                                enabled=str(u.get('disabled', 'false')).lower() != 'true', raw=u) for u in rows]

    def upsert_user(self, pin, name, card='', enabled=True):
        exists = getattr(self._req('GET', f'/api/users/{pin}'), 'status_code', 404) < 400
        user = {'name': str(name or pin)[:48], 'disabled': 'false' if enabled else 'true'}
        if exists:
            self._check(self._req('PUT', f'/api/users/{pin}', json={'User': user}), 'تعديل موظف')
        else:
            user.update({'user_id': str(pin), 'user_group_id': {'id': 1},
                         'start_datetime': '2001-01-01T00:00:00.00Z', 'expiry_datetime': '2037-12-31T23:59:00.00Z'})
            self._check(self._req('POST', '/api/users', json={'User': user}), 'إضافة موظف')
        return True

    def set_enabled(self, pin, enabled):
        self._check(self._req('PUT', f'/api/users/{pin}', json={'User': {'disabled': 'false' if enabled else 'true'}}),
                    'إيقاف/تفعيل موظف')
        return True

    def delete_user(self, pin):
        self._check(self._req('DELETE', f'/api/users/{pin}'), 'حذف موظف')
        return True

    def pull_attendance(self, since, until=None):
        until = until or datetime.now()
        since = since or (until - timedelta(days=31))
        r = self._check(self._req('POST', '/api/events/search', json={'Query': {
            'limit': 10000,
            'conditions': [{'column': 'datetime', 'operator': 3, 'values': [_utc(since), _utc(until)]}],
            'orders': [{'column': 'datetime', 'descending': False}]}}), 'سحب الحضور')
        rows = (self._json(r).get('EventCollection') or {}).get('rows') or []
        out = []
        for ev in rows:
            if not _ok_code((ev.get('event_type_id') or {}).get('code')):
                continue
            pin = str((ev.get('user_id') or {}).get('user_id') or '').strip()
            s = str(ev.get('datetime') or '')
            try:
                t = datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
            except ValueError:
                t = None
            if pin and t:
                out.append(base.Punch(pin=pin, time=t.replace(microsecond=0),
                                      verify=str((ev.get('event_type_id') or {}).get('code')), raw=ev))
        return out
