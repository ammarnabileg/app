/// ما يبقى على الجهاز: بيانات الدخول، وطابور ما لم يُرسَل بعد.
///
/// المندوب يدخل الأقبية والمخازن ومناطق بلا تغطية. فالنقطة التي
/// تُلتقط ولا تُرسَل يجب أن **تبقى** حتى تُرسَل — وإلّا ظهر في خريطة
/// المدير خطٌّ مقطوع لرجلٍ كان يعمل.
///
/// ولذا قاعدةٌ على القرص لا قائمةٌ في الذاكرة: التطبيق يُقتل في
/// الخلفية — أندرويد يقتله عند ضيق الذاكرة، وiOS أسرع — وما في
/// الذاكرة يضيع مع القتل.
library;

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:sqflite/sqflite.dart';

class Creds {
  const Creds(this.baseUrl, this.username, this.password);
  final String baseUrl;
  final String username;
  final String password;
}

class Store {
  static const _secure = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  // ------------------------------------------------ بيانات الدخول

  /// في مخزن النظام (Keystore/Keychain) لا في ملفٍّ عادي: هاتفٌ
  /// مسروق لا يُعطي كلمة مرور حساب شركة.
  static Future<void> saveCreds(Creds c) async {
    await _secure.write(key: 'base_url', value: c.baseUrl);
    await _secure.write(key: 'username', value: c.username);
    await _secure.write(key: 'password', value: c.password);
  }

  static Future<Creds?> creds() async {
    final b = await _secure.read(key: 'base_url');
    final u = await _secure.read(key: 'username');
    final p = await _secure.read(key: 'password');
    if (b == null || u == null || p == null) return null;
    return Creds(b, u, p);
  }

  static Future<void> clearCreds() async {
    for (final k in ['base_url', 'username', 'password', 'cookie']) {
      await _secure.delete(key: k);
    }
  }

  static Future<void> saveCookie(String? c) async =>
      c == null ? _secure.delete(key: 'cookie') : _secure.write(key: 'cookie', value: c);

  static Future<String?> cookie() => _secure.read(key: 'cookie');

  // ------------------------------------------------ طابور النقاط

  static Database? _db;

  static Future<Database> _open() async {
    if (_db != null) return _db!;
    _db = await openDatabase(
      '${await getDatabasesPath()}/onz_field.db',
      version: 1,
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            accuracy REAL,
            speed REAL,
            heading REAL,
            recorded_at TEXT NOT NULL
          )
        ''');
        await db.execute('CREATE INDEX idx_queue_trip ON queue (trip_id, id)');
      },
    );
    return _db!;
  }

  static Future<void> enqueue(int tripId, Map<String, dynamic> p) async {
    final db = await _open();
    await db.insert('queue', {
      'trip_id': tripId,
      'lat': p['latitude'],
      'lon': p['longitude'],
      'accuracy': p['accuracy'],
      'speed': p['speed'],
      'heading': p['heading'],
      'recorded_at': p['recorded_at'],
    });
  }

  /// أقدم دفعة بانتظار الإرسال. الأقدم أولًا فيبقى الخطّ مرتّبًا.
  static Future<List<Map<String, Object?>>> pending(int tripId,
      {int limit = 200}) async {
    final db = await _open();
    return db.query('queue',
        where: 'trip_id = ?', whereArgs: [tripId], orderBy: 'id ASC', limit: limit);
  }

  /// تُحذف **بعد** أن يؤكّد الخادم استلامها لا قبله.
  static Future<void> drop(List<Object?> ids) async {
    if (ids.isEmpty) return;
    final db = await _open();
    final marks = List.filled(ids.length, '?').join(',');
    await db.delete('queue', where: 'id IN ($marks)', whereArgs: ids);
  }

  static Future<int> queueLength() async {
    final db = await _open();
    return Sqflite.firstIntValue(
            await db.rawQuery('SELECT COUNT(*) FROM queue')) ??
        0;
  }

  /// طابورٌ لا سقف له يملأ القرص في يومٍ بلا تغطية. فالأقدم يُسقط
  /// أوّلًا — وفقدُ بداية الخطّ أهون من تطبيق يتوقّف.
  static Future<void> trim({int keep = 20000}) async {
    final db = await _open();
    final n = await queueLength();
    if (n <= keep) return;
    await db.rawDelete(
        'DELETE FROM queue WHERE id IN '
        '(SELECT id FROM queue ORDER BY id ASC LIMIT ?)',
        [n - keep]);
  }
}
