# -*- coding: utf-8 -*-
"""Dahua — HTTP CGI بمصادقة Digest. **تجريبيّ: لم يُجرَّب على جهاز.**

من توثيق Dahua HTTP API العامّ (أجهزة ASI…):

    فحص       GET  /cgi-bin/magicBox.cgi?action=getSystemInfo
    موظّفون   POST /cgi-bin/AccessUser.cgi?action=insertMulti | updateMulti   (JSON)
              GET  /cgi-bin/AccessUser.cgi?action=removeMulti&UserIDList[0]=…
              POST /cgi-bin/AccessUser.cgi?action=startFind → doFind → stopFind
    حضور      GET  /cgi-bin/recordFinder.cgi?action=find&name=AccessControlCardRec&StartTime=…&EndTime=…
    وجه       POST /cgi-bin/AccessFace.cgi?action=insertMulti | updateMulti    (صورة base64)
    وقت       GET  /cgi-bin/global.cgi?action=setCurrentTime&time=…

**الإيقاف** = انتهاءُ الصلاحيّة (`ValidTo`) بالأمس؛ والتفعيل يعيدها لعام 2037 —
الأوضح والأثبت بين الطرازات من حقل الحالة.
لا استقبالَ لحظيًّا في هذه النسخة: المزامنةُ تسحب الحضور.
"""
import base64 as b64
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from . import base

VALID_FROM = '2000-01-01 00:00:00'
VALID_TO = '2037-12-31 23:59:59'


def _kv(text):
    out = {}
    for line in (text or '').splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
    return out


class DahuaDriver(base.HttpDriver):
    key = 'dahua'
    label = 'Dahua (HTTP API)'
    brand = 'Dahua'
    default_port = 80
    capabilities = frozenset({base.TEST, base.PULL_ATTENDANCE, base.USERS, base.DISABLE, base.DELETE,
                              base.FACE_PHOTO, base.SET_TIME})

    def test(self):
        r = self._check(self._req('GET', '/cgi-bin/magicBox.cgi?action=getSystemInfo'), 'فحص الاتصال')
        info = _kv(getattr(r, 'text', ''))
        return {'ok': True, 'message': f"متصل: {info.get('deviceType') or 'Dahua'} "
                                       f"({info.get('serialNumber') or '-'})", 'info': info}

    # ---------------------------------------------------- موظّفون
    def _user(self, pin, name, enabled):
        valid_to = VALID_TO if enabled else (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
        return {'UserID': str(pin), 'UserName': str(name or pin)[:32], 'UserType': 0, 'UserStatus': 0,
                'Authority': 2, 'Doors': [0], 'TimeSections': [255], 'ValidFrom': VALID_FROM, 'ValidTo': valid_to}

    def list_users(self):
        r = self._check(self._req('POST', '/cgi-bin/AccessUser.cgi?action=startFind', json={'Condition': {}}),
                        'قراءة الموظفين')
        token = self._json(r).get('Token')
        out, offset = [], 0
        try:
            while token is not None:
                r = self._check(self._req('GET', f'/cgi-bin/AccessUser.cgi?action=doFind&Token={token}'
                                                 f'&Offset={offset}&Count=100'), 'قراءة الموظفين')
                rows = self._json(r).get('Info') or []
                for u in rows:
                    out.append(base.DeviceUser(pin=str(u.get('UserID', '')), name=u.get('UserName', ''), raw=u))
                if len(rows) < 100:
                    break
                offset += len(rows)
        finally:
            if token is not None:
                self._req('GET', f'/cgi-bin/AccessUser.cgi?action=stopFind&Token={token}')
        return out

    def upsert_user(self, pin, name, card='', enabled=True):
        body = {'UserList': [self._user(pin, name, enabled)]}
        r = self._req('POST', '/cgi-bin/AccessUser.cgi?action=insertMulti', json=body)
        if getattr(r, 'status_code', 0) >= 400:
            self._check(self._req('POST', '/cgi-bin/AccessUser.cgi?action=updateMulti', json=body), 'إضافة موظف')
        if card and str(card).strip() not in ('', '0'):
            self._req('POST', '/cgi-bin/AccessCard.cgi?action=insertMulti', json={
                'CardList': [{'UserID': str(pin), 'CardNo': str(card), 'CardType': 0, 'CardStatus': 0}]})
        return True

    def set_enabled(self, pin, enabled):
        # الصلاحيّةُ وحدها — بلا اسمٍ يكتب فوق اسم الموظّف على الجهاز.
        u = self._user(pin, '', enabled)
        body = {'UserList': [{k: u[k] for k in ('UserID', 'ValidFrom', 'ValidTo')}]}
        self._check(self._req('POST', '/cgi-bin/AccessUser.cgi?action=updateMulti', json=body), 'إيقاف/تفعيل موظف')
        return True

    def delete_user(self, pin):
        self._check(self._req('GET', f'/cgi-bin/AccessUser.cgi?action=removeMulti&UserIDList[0]={quote(str(pin))}'),
                    'حذف موظف')
        return True

    def enroll_face_photo(self, pin, jpeg_bytes):
        body = {'FaceList': [{'UserID': str(pin), 'PhotoData': [b64.b64encode(jpeg_bytes).decode('ascii')]}]}
        r = self._req('POST', '/cgi-bin/AccessFace.cgi?action=insertMulti', json=body)
        if getattr(r, 'status_code', 0) >= 400:
            self._check(self._req('POST', '/cgi-bin/AccessFace.cgi?action=updateMulti', json=body), 'تسجيل الوش')
        return True

    def set_time(self, when):
        self._check(self._req('GET', '/cgi-bin/global.cgi?action=setCurrentTime&time='
                                     + quote(when.strftime('%Y-%m-%d %H:%M:%S'))), 'ضبط الوقت')
        return True

    # ---------------------------------------------------- حضور
    def pull_attendance(self, since, until=None):
        until = until or datetime.now()
        since = since or (until - timedelta(days=31))
        r = self._check(self._req('GET', '/cgi-bin/recordFinder.cgi?action=find&name=AccessControlCardRec'
                                         f'&StartTime={int(time.mktime(since.timetuple()))}'
                                         f'&EndTime={int(time.mktime(until.timetuple()))}&count=4000'),
                        'سحب الحضور')
        kv = _kv(getattr(r, 'text', ''))
        recs = {}
        for k, v in kv.items():
            if k.startswith('records[') and '].' in k:
                idx, field = k[8:].split('].', 1)
                recs.setdefault(idx, {})[field] = v
        out = []
        for rec in recs.values():
            if str(rec.get('Status', '1')) != '1':          # 1 = مسموح؛ غيرُه محاولةٌ مرفوضة
                continue
            pin = str(rec.get('UserID') or '').strip()
            try:
                t = datetime.fromtimestamp(int(rec.get('CreateTime')))
            except (TypeError, ValueError):
                t = None
            if pin and t:
                out.append(base.Punch(pin=pin, time=t, check_type=1 if str(rec.get('Type')) == 'Exit' else 0,
                                      verify=str(rec.get('Method') or ''), raw=rec))
        return out
