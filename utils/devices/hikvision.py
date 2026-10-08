# -*- coding: utf-8 -*-
"""Hikvision — ISAPI على HTTP(S) بمصادقة Digest. **تجريبيّ: لم يُجرَّب على جهاز.**

من توثيق ISAPI العامّ (أجهزة التحكّم بالدخول/الحضور DS-K1T…):

    فحص       GET  /ISAPI/System/deviceInfo                       (XML)
    موظّفون   POST /ISAPI/AccessControl/UserInfo/Search?format=json
              PUT  /ISAPI/AccessControl/UserInfo/SetUp?format=json   (إضافة أو تعديل)
              PUT  /ISAPI/AccessControl/UserInfo/Modify?format=json
              PUT  /ISAPI/AccessControl/UserInfo/Delete?format=json
    كارت      PUT  /ISAPI/AccessControl/CardInfo/SetUp?format=json
    حضور      POST /ISAPI/AccessControl/AcsEvent?format=json
    وجه       PUT  /ISAPI/Intelligent/FDLib/FDSetUp?format=json      (صورة JPEG)
    وقت       PUT  /ISAPI/System/time                             (XML)
    لحظيًّا   الجهاز يرسل لعنوانٍ نضبطه فيه (httpHosts) — /devices/push/hikvision/<رمز الجهاز>

**الإيقاف** = `Valid.enable = false`: يبقى الموظّف ووجهُه على الجهاز ولا يُفتح له.
**الحضور**: الأحداث الرئيسة 5 بالأنواع الفرعيّة الناجحة — 1 كارت، 38 بصمة، 75 وجه
(قابلة للتغيير من خيارات الجهاز `minors`).
"""
import json
import re
from datetime import datetime, timedelta

from . import base

PASS_MINORS = (1, 38, 75)
VALID_FROM = '2000-01-01T00:00:00'
VALID_TO = '2037-12-31T23:59:59'


def _iso(dt):
    """وقتٌ محلّيّ بإزاحته (+03:00) كما يطلبه ISAPI."""
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec='seconds')


def _parse_time(s):
    """'2026-10-08T08:01:02+03:00' → datetime محلّيّ بلا منطقة (كما تُحفظ البصمات)."""
    s = str(s or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _check_type(status):
    s = str(status or '').lower()
    if s in ('checkout', 'breakout', 'overtimeout'):
        return 1
    return 0


class HikvisionDriver(base.HttpDriver):
    key = 'hikvision'
    label = 'Hikvision (ISAPI)'
    brand = 'Hikvision'
    default_port = 80
    capabilities = frozenset({base.TEST, base.PULL_ATTENDANCE, base.PUSH_EVENTS, base.USERS,
                              base.DISABLE, base.DELETE, base.FACE_PHOTO, base.SET_TIME})

    # ---------------------------------------------------- فحص
    def test(self):
        r = self._check(self._req('GET', '/ISAPI/System/deviceInfo'), 'فحص الاتصال')
        xml = getattr(r, 'text', '') or ''
        info = {k: (re.search(rf'<{k}>([^<]*)</{k}>', xml) or [None, ''])[1]
                for k in ('deviceName', 'model', 'serialNumber', 'firmwareVersion')}
        return {'ok': True, 'message': f"متصل: {info.get('model') or 'Hikvision'} "
                                       f"({info.get('serialNumber') or '-'})", 'info': info}

    # ---------------------------------------------------- موظّفون
    def list_users(self):
        out, pos = [], 0
        while True:
            r = self._check(self._req('POST', '/ISAPI/AccessControl/UserInfo/Search?format=json', json={
                'UserInfoSearchCond': {'searchID': 'onz', 'searchResultPosition': pos, 'maxResults': 30}}),
                'قراءة الموظفين')
            body = self._json(r).get('UserInfoSearch', {})
            rows = body.get('UserInfo') or []
            for u in rows:
                out.append(base.DeviceUser(pin=str(u.get('employeeNo', '')), name=u.get('name', ''),
                                           enabled=bool((u.get('Valid') or {}).get('enable', True)), raw=u))
            pos += len(rows)
            if body.get('responseStatusStrg') != 'MORE' or not rows:
                return out

    def _user_body(self, pin, name, enabled):
        return {'UserInfo': {
            'employeeNo': str(pin), 'name': str(name or pin)[:32], 'userType': 'normal',
            'Valid': {'enable': bool(enabled), 'beginTime': VALID_FROM, 'endTime': VALID_TO, 'timeType': 'local'},
            'doorRight': '1', 'RightPlan': [{'doorNo': 1, 'planTemplateNo': '1'}]}}

    def upsert_user(self, pin, name, card='', enabled=True):
        body = self._user_body(pin, name, enabled)
        r = self._req('PUT', '/ISAPI/AccessControl/UserInfo/SetUp?format=json', json=body)
        if getattr(r, 'status_code', 0) >= 400:
            # أجهزةٌ أقدم بلا SetUp: إضافةٌ ثمّ تعديلٌ إن كان موجودًا.
            r = self._req('POST', '/ISAPI/AccessControl/UserInfo/Record?format=json', json=body)
            if getattr(r, 'status_code', 0) >= 400:
                r = self._check(self._req('PUT', '/ISAPI/AccessControl/UserInfo/Modify?format=json',
                                          json=body), 'إضافة موظف')
        if card and str(card).strip() not in ('', '0'):
            self._req('PUT', '/ISAPI/AccessControl/CardInfo/SetUp?format=json', json={
                'CardInfo': {'employeeNo': str(pin), 'cardNo': str(card), 'cardType': 'normalCard'}})
        return True

    def set_enabled(self, pin, enabled):
        self._check(self._req('PUT', '/ISAPI/AccessControl/UserInfo/Modify?format=json', json={
            'UserInfo': {'employeeNo': str(pin), 'Valid': {'enable': bool(enabled), 'beginTime': VALID_FROM,
                                                            'endTime': VALID_TO, 'timeType': 'local'}}}),
            'إيقاف/تفعيل موظف')
        return True

    def delete_user(self, pin):
        self._check(self._req('PUT', '/ISAPI/AccessControl/UserInfo/Delete?format=json', json={
            'UserInfoDelCond': {'EmployeeNoList': [{'employeeNo': str(pin)}]}}), 'حذف موظف')
        return True

    def enroll_face_photo(self, pin, jpeg_bytes):
        meta = json.dumps({'faceLibType': 'blackFD', 'FDID': '1', 'FPID': str(pin)})
        files = {'FaceDataRecord': (None, meta, 'application/json'),
                 'img': ('face.jpg', jpeg_bytes, 'image/jpeg')}
        r = self._req('PUT', '/ISAPI/Intelligent/FDLib/FDSetUp?format=json', files=files)
        if getattr(r, 'status_code', 0) >= 400:
            r = self._req('POST', '/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json', files=files)
        self._check(r, 'تسجيل الوش')
        return True

    def set_time(self, when):
        # منطقةُ الوقت المُعطى إن حمل منطقة، وإلّا منطقةُ الجهاز الذي يشغّل البرنامج.
        aware = when if when.tzinfo is not None else when.astimezone()
        off = aware.utcoffset() or timedelta(0)
        mins = int(off.total_seconds() // 60)
        # صيغة منطقة Hikvision معكوسة الإشارة كـ POSIX: الكويت (+3) = CST-3:00:00
        tz = f"CST{'-' if mins >= 0 else '+'}{abs(mins) // 60}:{abs(mins) % 60:02d}:00"
        xml = (f'<?xml version="1.0" encoding="UTF-8"?><Time><timeMode>manual</timeMode>'
               f'<localTime>{when.strftime("%Y-%m-%dT%H:%M:%S")}</localTime><timeZone>{tz}</timeZone></Time>')
        self._check(self._req('PUT', '/ISAPI/System/time', data=xml.encode('utf-8'),
                              headers={'Content-Type': 'application/xml'}), 'ضبط الوقت')
        return True

    # ---------------------------------------------------- حضور
    def pull_attendance(self, since, until=None):
        until = until or datetime.now()
        since = since or (until - timedelta(days=31))
        minors = self._options().get('minors') or PASS_MINORS
        out = []
        for minor in minors:
            pos = 0
            while True:
                r = self._check(self._req('POST', '/ISAPI/AccessControl/AcsEvent?format=json', json={
                    'AcsEventCond': {'searchID': f'onz{minor}', 'searchResultPosition': pos, 'maxResults': 30,
                                     'major': 5, 'minor': int(minor),
                                     'startTime': _iso(since), 'endTime': _iso(until)}}), 'سحب الحضور')
                body = self._json(r).get('AcsEvent', {})
                rows = body.get('InfoList') or []
                for ev in rows:
                    p = self._punch(ev)
                    if p:
                        out.append(p)
                pos += len(rows)
                if body.get('responseStatusStrg') != 'MORE' or not rows:
                    break
        return out

    @staticmethod
    def _punch(ev):
        pin = str(ev.get('employeeNoString') or ev.get('employeeNo') or '').strip()
        t = _parse_time(ev.get('time') or ev.get('dateTime'))
        if not pin or not t:
            return None
        return base.Punch(pin=pin, time=t, check_type=_check_type(ev.get('attendanceStatus')),
                          verify=str(ev.get('currentVerifyMode') or ev.get('minor') or ''), raw=ev)

    # ---------------------------------------------------- لحظيًّا (httpHosts)
    @classmethod
    def parse_push(cls, request):
        """multipart (event_log / AccessControllerEvent) أو JSON مباشر → (معرّف، [Punch])."""
        payload = None
        for name in ('event_log', 'AccessControllerEvent', 'eventLog'):
            if name in request.form:
                payload = request.form.get(name)
                break
        if payload is None:
            for f in request.files.values():
                if (f.mimetype or '').endswith('json'):
                    payload = f.read().decode('utf-8', 'ignore')
                    break
        if payload is None:
            payload = request.get_data(as_text=True)
        try:
            data = json.loads(payload) if payload else {}
        except ValueError:
            return {}, []
        ev = data.get('AccessControllerEvent') or {}
        ident = {'ip': data.get('ipAddress'), 'mac': data.get('macAddress')}
        if not ev or int(ev.get('majorEventType') or 0) != 5:
            return ident, []
        minor = int(ev.get('subEventType') or 0)
        if minor not in PASS_MINORS:
            return ident, []
        p = cls._punch({'employeeNoString': ev.get('employeeNoString'), 'time': data.get('dateTime'),
                        'attendanceStatus': ev.get('attendanceStatus'),
                        'currentVerifyMode': ev.get('currentVerifyMode') or minor})
        return ident, [p] if p else []
