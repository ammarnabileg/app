/// ما يمكن فحصه بلا جهاز: منطق العميل نفسه.
///
/// الكاميرا والـGPS والخدمة الأمامية تحتاج هاتفًا، وأقولها صراحةً
/// ولا أدّعي فحصها. لكن ما بينها — حمل الكعكة، وإعادة الدخول عند
/// انتهاء الجلسة، وقراءة رسالة الخطأ من الخادم — منطقٌ خالص يُفحص
/// هنا، وهو حيث تقع أكثر العلل.
///
/// يُشغَّل:  flutter test
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:onz_field/api.dart';

/// خادمٌ مزيّف يسجّل ما وصله، ليُسأل بعدُ: ماذا أرسل العميل فعلًا؟
class FakeServer {
  FakeServer();

  final List<http.BaseRequest> seen = [];
  String cookie = 'session=FIRST';
  bool sessionValid = true;
  int logins = 0;

  http.Client get client => MockClient.streaming((req, body) async {
        seen.add(req);

        if (req.url.path == '/login') {
          logins++;
          sessionValid = true;
          cookie = 'session=AFTER_LOGIN_$logins';
          return http.StreamedResponse(const Stream.empty(), 302, headers: {
            'set-cookie': '$cookie; HttpOnly; Path=/; SameSite=Lax',
            'location': '/',
          });
        }

        // جلسةٌ منتهية: Flask يحوّل إلى صفحة الدخول ولا يردّ 401.
        if (!sessionValid) {
          return http.StreamedResponse(const Stream.empty(), 302,
              headers: {'location': '/login'});
        }

        final payload = handler?.call(req) ?? {'success': true};
        return http.StreamedResponse(
          Stream.value(utf8.encode(jsonEncode(payload))),
          status,
          headers: {'content-type': 'application/json'},
        );
      });

  Map<String, dynamic> Function(http.BaseRequest req)? handler;
  int status = 200;
}

Api _api(FakeServer s) => Api(
      baseUrl: 'https://co.onz.one',
      username: 'rep',
      password: 'secret',
      client: s.client,
    );

void main() {
  test('الدخول يلتقط الكعكة ويحملها في الطلب التالي', () async {
    final s = FakeServer();
    final api = _api(s);

    await api.login();
    expect(api.cookie, 'session=AFTER_LOGIN_1');

    s.handler = (_) => {'success': true, 'plan': [], 'summary': {}};
    await api.plan();

    final last = s.seen.last;
    expect(last.headers['Cookie'], 'session=AFTER_LOGIN_1');
  });

  test('الجلسة المنتهية يُعاد الدخول لها تلقائيًّا ويُعاد الطلب', () async {
    // بيت القصيد: خدمةٌ تعمل في الخلفية لا تجد من يضغط «دخول».
    // فلو لم يُعَد الدخول وحده، توقّف التتبّع بصمت حتى يفتح المندوب
    // التطبيق — وهو قد لا يفتحه طوال اليوم.
    final s = FakeServer();
    final api = _api(s);
    await api.login();

    s.sessionValid = false; // انتهت أثناء العمل
    s.handler = (_) => {'success': true, 'accepted': 3};

    final n = await api.track(7, [
      {'latitude': 29.3, 'longitude': 48.0, 'recorded_at': '2026-09-16 10:00:00'}
    ]);

    expect(n, 3);
    expect(s.logins, 2, reason: 'لم يُعَد الدخول');
    expect(api.cookie, 'session=AFTER_LOGIN_2', reason: 'حُمِلت الكعكة القديمة');
  });

  test('جلسةٌ منتهية لا يُصلحها الدخول تُبلَّغ لا تُكرَّر إلى الأبد', () async {
    final s = FakeServer();
    final api = _api(s);
    await api.login();

    // دخولٌ ينجح ظاهرًا والجلسة تبقى مرفوضة — حلقة لا نهائية لولا
    // أن المحاولة واحدة.
    s.sessionValid = false;
    s.handler = (_) => {'success': true};
    final failing = FakeServer()..sessionValid = false;
    failing.handler = (_) => {'success': true};

    var threw = false;
    try {
      await Api(
        baseUrl: 'https://co.onz.one',
        username: 'x',
        password: 'y',
        client: MockClient.streaming((req, body) async {
          if (req.url.path == '/login') {
            return http.StreamedResponse(const Stream.empty(), 302,
                headers: {'set-cookie': 'session=Z; Path=/'});
          }
          return http.StreamedResponse(const Stream.empty(), 302,
              headers: {'location': '/login'});
        }),
      ).plan();
    } on ApiError catch (e) {
      threw = true;
      expect(e.needsLogin, isTrue);
    }
    expect(threw, isTrue, reason: 'دار في حلقة بدل أن يُبلّغ');
  });

  test('رسالة الخطأ من الخادم تُعرض كما هي', () async {
    // رسائل الخادم عربية ومكتوبة بعناية («أنت خارج نطاق المحطة (تبعد
    // ٤٠ متراً)»). فابتلاعها واستبدالها بـ«حدث خطأ» يُضيع ما يحتاجه
    // المندوب ليتصرّف.
    final s = FakeServer();
    final api = _api(s);
    await api.login();

    s.status = 400;
    s.handler = (_) =>
        {'success': false, 'message': 'أنت خارج نطاق المحطة (تبعد 240 متراً)'};

    expect(
      () => api.plan(),
      throwsA(isA<ApiError>()
          .having((e) => e.message, 'message', contains('خارج نطاق'))),
    );
  });

  test('كلمة مرور خاطئة تُميَّز عن انقطاع الشبكة', () async {
    // الفرق يغيّر ما يفعله المستخدم: الأولى يصحّح كلمته، والثانية
    // ينتظر. فخلطهما يُرسله إلى الطريق الخطأ.
    final wrong = Api(
      baseUrl: 'https://co.onz.one',
      username: 'rep',
      password: 'bad',
      client: MockClient.streaming((req, body) async =>
          http.StreamedResponse(Stream.value(utf8.encode('<html>login</html>')), 200)),
    );
    try {
      await wrong.login();
      fail('قُبل دخولٌ خاطئ');
    } on ApiError catch (e) {
      expect(e.needsLogin, isTrue);
      expect(e.isOffline, isFalse);
    }

    final down = Api(
      baseUrl: 'https://co.onz.one',
      username: 'rep',
      password: 'secret',
      client: MockClient.streaming((req, body) async {
        throw const SocketExceptionLike();
      }),
    );
    try {
      await down.login();
      fail('نجح والشبكة مقطوعة');
    } on ApiError catch (e) {
      expect(e.isOffline, isTrue);
    }
  });

  test('عنوان الخادم يُنظَّف من الشرطة الزائدة', () async {
    // المستخدم يلصق العنوان بشرطة في آخره، فينتج //portal ويردّ
    // الخادم 404. تُقصّ بدل أن يُلام المستخدم.
    final s = FakeServer();
    final api = Api(
        baseUrl: 'https://co.onz.one///',
        username: 'a',
        password: 'b',
        client: s.client);
    await api.login();
    s.handler = (_) => {'success': true, 'plan': [], 'summary': {}};
    await api.plan();

    expect(s.seen.last.url.toString(),
        'https://co.onz.one/portal/api/field/plan');
  });

  test('النقاط تُرسَل بالأسماء التي يقرؤها الخادم', () async {
    // `add_points` تقرأ latitude/longitude/recorded_at. واسمٌ مختلف
    // يعني نقاطًا تُهمَل بصمت ولا يظهر الخطأ إلا في خريطة فارغة.
    final s = FakeServer();
    final api = _api(s);
    await api.login();
    s.handler = (_) => {'success': true, 'accepted': 1};

    await api.track(5, [
      {
        'latitude': 29.3759,
        'longitude': 47.9774,
        'accuracy': 12.0,
        'recorded_at': '2026-09-16 10:15:00',
      }
    ]);

    final req = s.seen.last as http.Request;
    final body = jsonDecode(req.body) as Map<String, dynamic>;
    expect(body['trip_id'], 5);
    final p = (body['points'] as List).first as Map<String, dynamic>;
    expect(p.keys, containsAll(['latitude', 'longitude', 'recorded_at']));
    expect(p['recorded_at'], matches(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$'));
  });
}

/// استثناء شبكة يُحاكي انقطاعًا حقيقيًّا.
class SocketExceptionLike implements Exception {
  const SocketExceptionLike();
}
