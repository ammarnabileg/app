# -*- coding: utf-8 -*-
"""ZKTeco خلف الوكيل المحلّيّ — للنسخة الأونلاين (utils/connector).

السحابة لا تتّصل بالجهاز: كلُّ عمليّةٍ هنا **أمرٌ في طابور** يسحبه الوكيل وينفّذه
على الجهاز بنفس كود البرنامج المكتبيّ، والحضورُ يرفعه الوكيل بنفسه. وقائمةُ
مستخدمي الجهاز هي آخر ما رفعه الوكيل.
"""
from . import base


class ZkConnectorDriver(base.BaseDriver):
    key = 'zk_connector'
    label = 'ZKTeco — عبر الوكيل المحلي (للنسخة الأونلاين)'
    brand = 'ZKTeco'
    experimental = True
    needs_login = False
    default_port = 4370
    capabilities = frozenset({base.TEST, base.PUSH_EVENTS, base.USERS, base.DISABLE, base.DELETE, base.SET_TIME})

    def _conn(self):
        from utils.db import get_db_connection
        return get_db_connection()

    def _connector(self, conn):
        from utils import connector as C
        cid = C.connector_id_of(self.device)
        if not cid:
            raise base.DriverError('الجهاز ده مش متربط بوكيل محلي — اختار الوكيل من تعديل الجهاز')
        C.ensure_schema(conn)
        return conn.execute('SELECT * FROM connectors WHERE id = ?', (cid,)).fetchone()

    def test(self):
        conn = self._conn()
        try:
            c = self._connector(conn)
        except base.DriverError as e:
            return {'ok': False, 'message': str(e), 'info': {}}
        if not c:
            return {'ok': False, 'message': 'الوكيل المحلي اتمسح', 'info': {}}
        if not c['last_seen']:
            return {'ok': False, 'message': f'الوكيل «{c["name"]}» لسه ما اتصلش — شغّله على جهاز في الشركة', 'info': {}}
        last = self.device.get('last_activity') or '—'
        return {'ok': True, 'message': f'الوكيل «{c["name"]}» آخر اتصال {c["last_seen"]} — آخر بصمات من الجهاز {last}',
                'info': {'agent_version': c['agent_version']}}

    def _enqueue(self, kind, pin=None, payload=None):
        from utils import connector as C
        conn = self._conn()
        self._connector(conn)
        C.enqueue(conn, self.device['id'], kind, pin, payload)

    def list_users(self):
        conn = self._conn()
        from utils import connector as C
        C.ensure_schema(conn)
        return [base.DeviceUser(pin=r['pin'], name=r['name'] or '', card=r['card'] or '', enabled=bool(r['enabled']))
                for r in conn.execute('SELECT * FROM connector_device_users WHERE device_id = ?', (self.device['id'],))]

    def upsert_user(self, pin, name, card='', enabled=True):
        self._enqueue('upsert_user', pin, {'name': str(name or '')[:24], 'card': str(card or ''), 'enabled': bool(enabled)})

    def set_enabled(self, pin, enabled):
        self._enqueue('enable_user' if enabled else 'disable_user', pin)

    def delete_user(self, pin):
        self._enqueue('delete_user', pin)

    def set_time(self, when):
        self._enqueue('set_time', None, {'time': when.strftime('%Y-%m-%d %H:%M:%S')})
