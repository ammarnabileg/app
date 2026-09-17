/// مسارات البوابة: الأسماء والصيغ التي يقرؤها الخادم.
///
/// أكثر ما يُعطب في عميلٍ كهذا ليس منطقًا معقّدًا، بل اسم حقلٍ مختلف
/// أو وحدة قياسٍ مختلفة — ولا يظهر العطل في رسالة الخطأ، إنما في
/// سلوكٍ غريب يصعب ردّه إلى سببه.
///
/// يُشغَّل:  flutter test
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:onz_field/api.dart';
import 'package:onz_field/portal_api.dart';

class _Spy {
  final List<http.BaseRequest> seen = [];
  final List<String> bodies = [];
  Map<String, dynamic> reply = {'success': true};

  http.Client get client => MockClient.streaming((req, body) async {
        seen.add(req);
        if (req is http.Request) bodies.add(req.body);
        if (req.url.path == '/login') {
          return http.StreamedResponse(const Stream.empty(), 302,
              headers: {'set-cookie': 'session=S; Path=/'});
        }
        return http.StreamedResponse(
          Stream.value(utf8.encode(jsonEncode(reply))),
          200,
          headers: {'content-type': 'application/json'},
        );
      });

  Map<String, dynamic> get lastBody =>
      jsonDecode(bodies.last) as Map<String, dynamic>;

  String get lastPath => seen.last.url.path;
  String get lastQuery => seen.last.url.query;
}

Future<Api> _api(_Spy s) async {
  final a = Api(
      baseUrl: 'https://co.onz.one',
      username: 'u',
      password: 'p',
      client: s.client);
  await a.login();
  return a;
}

void main() {
  test('البصمة تُرسل الزمن بالميلي ثانية لا بالثانية', () async {
    // الخادم يردّ القراءة التي يزيد فرقها عن ٣٥ ثانية. وهو يقسم
    // الوارد على ألف ليحوّله إلى ثوانٍ — فإرسال الثواني يجعل الفرق
    // خمسةً وخمسين عامًا، وتُردّ كل بصمة برسالة «الموقع قديم»، وهي
    // رسالة لا تدلّ على السبب إطلاقًا.
    final s = _Spy();
    final api = await _api(s);
    final before = DateTime.now().millisecondsSinceEpoch;

    await api.punch(
      lat: 29.3759,
      lon: 47.9774,
      accuracy: 11,
      timestampMillis: DateTime.now().millisecondsSinceEpoch,
      deviceUuid: 'dev-1',
    );

    final ts = s.lastBody['timestamp'] as int;
    final after = DateTime.now().millisecondsSinceEpoch;

    expect(ts, greaterThanOrEqualTo(before));
    expect(ts, lessThanOrEqualTo(after));
    // بالثواني يكون الرقم نحو ١٫٨ مليار؛ بالميلي نحو ١٫٨ تريليون.
    expect(ts, greaterThan(1000000000000),
        reason: 'أُرسلت الثواني بدل الميلي');
  });

  test('البصمة تحمل الحقول التي يقرؤها الخادم', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.punch(
        lat: 29.1,
        lon: 48.2,
        accuracy: 9.5,
        timestampMillis: 1,
        deviceUuid: 'abc');

    expect(s.lastPath, '/portal/api/punch');
    expect(s.lastBody.keys,
        containsAll(['latitude', 'longitude', 'accuracy', 'timestamp', 'device_uuid']));
    expect(s.lastBody['latitude'], 29.1);
    expect(s.lastBody['device_uuid'], 'abc');
  });

  test('طلب الإجازة يرسل التواريخ بصيغة YYYY-MM-DD', () async {
    // الخادم يقرؤها بـ strptime('%Y-%m-%d') ويردّ ما خالفها. وصيغة
    // التاريخ المحلّية على هاتفٍ عربي قد تختلف تمامًا.
    final s = _Spy();
    final api = await _api(s);

    await api.requestLeave(
        leaveTypeId: 3,
        startDate: '2026-10-01',
        endDate: '2026-10-05',
        reason: 'سفر');

    expect(s.lastPath, '/portal/api/request-leave');
    expect(s.lastBody['leave_type_id'], 3);
    expect(s.lastBody['start_date'], matches(r'^\d{4}-\d{2}-\d{2}$'));
    expect(s.lastBody['end_date'], matches(r'^\d{4}-\d{2}-\d{2}$'));
  });

  test('البصمة ترسل النوع الذي اختاره الموظف', () async {
    // بلا `punch_type` يستنتج الخادم النوع من **عدد** بصمات اليوم.
    // فبصمةٌ زائدة تقلب الباقي: يُسجَّل انصرافٌ مكان تواجد، ويُحسب
    // اليوم ناقصًا. وشاشة الويب ترسله منذ البداية.
    final s = _Spy();
    final api = await _api(s);

    await api.punch(
        lat: 29.1,
        lon: 48.2,
        accuracy: 9,
        timestampMillis: 1,
        deviceUuid: 'd',
        punchType: 'presence');

    expect(s.lastBody['punch_type'], 'presence');
  });

  test('بلا اختيارٍ لا يُرسل الحقل فيستنتج الخادم', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.punch(
        lat: 29.1, lon: 48.2, accuracy: 9, timestampMillis: 1, deviceUuid: 'd');

    expect(s.lastBody.containsKey('punch_type'), isFalse);
  });

  test('الاستئذان يرسل النوع والتاريخ', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.requestExcuse(date: '2026-09-16', type: 'mission', reason: 'زيارة');

    expect(s.lastPath, '/portal/api/request-excuse');
    expect(s.lastBody['type'], 'mission');
    expect(s.lastBody['date'], '2026-09-16');
  });

  test('الاعتماد يرسل رقم الطلب والإجراء', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.approveRequest(42, 'approve');

    expect(s.lastPath, '/portal/api/approve-request');
    expect(s.lastBody['request_id'], 42);
    expect(s.lastBody['action'], 'approve');
  });

  test('الحضور يمرّر الشهر والسنة في الاستعلام', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.attendance(month: 3, year: 2026);
    expect(s.lastPath, '/portal/api/attendance');
    expect(s.lastQuery, contains('month=3'));
    expect(s.lastQuery, contains('year=2026'));

    // وبلا وسائط لا يُلحق '?' فارغًا يُربك بعض الخوادم.
    await api.attendance();
    expect(s.seen.last.url.toString(),
        'https://co.onz.one/portal/api/attendance');
  });

  test('تعليم إشعارات بعينها يرسل أرقامها', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.markNotificationsRead(ids: [4, 9]);

    expect(s.lastPath, '/portal/api/notifications/read');
    expect(s.lastBody['ids'], [4, 9]);
  });

  test('تعليم الكل لا يرسل أرقامًا فيفهمها الخادم كلًّا', () async {
    // الخادم يقرأ غياب `ids` على أنه «الكلّ». وإرسال قائمة فارغة
    // بدلها يعني «لا شيء» — فلا يُعلَّم شيء والزرّ يبدو معطوبًا.
    final s = _Spy();
    final api = await _api(s);

    await api.markNotificationsRead();

    expect(s.lastBody.containsKey('ids'), isFalse);
  });

  test('الإشعارات غير المقروءة تُطلب بمعامل صريح', () async {
    final s = _Spy();
    final api = await _api(s);

    await api.notifications(unreadOnly: true);
    expect(s.lastPath, '/portal/api/notifications');
    expect(s.lastQuery, contains('unread=1'));

    await api.notifications();
    expect(s.seen.last.url.toString(),
        'https://co.onz.one/portal/api/notifications');
  });

  test('كل مسارات البوابة تحت /portal/api', () async {
    // خطأٌ في مسارٍ واحد يظهر 404 فقط عند فتح تلك الشاشة، وقد لا
    // يُفتَح إلا عند العميل.
    final s = _Spy();
    final api = await _api(s);

    s.reply = {'success': true, 'plan': [], 'summary': {}, 'requests': []};
    await api.bootstrap();
    expect(s.lastPath, '/portal/api/bootstrap');
    await api.myData();
    expect(s.lastPath, '/portal/api/my-data');
    await api.punchStatus();
    expect(s.lastPath, '/portal/api/punch-status');
    await api.teamSummary();
    expect(s.lastPath, '/portal/api/team-summary');
    await api.teamApprovals();
    expect(s.lastPath, '/portal/api/team-approvals');
    await api.plan();
    expect(s.lastPath, '/portal/api/field/plan');
  });
}
