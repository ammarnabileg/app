/// الاتصال بالخادم.
///
/// النظام يستعمل جلسة Flask بكعكة موقَّعة، لا رمزًا حاملًا. فالعميل
/// هنا يحمل الكعكة بنفسه: يسجّل الدخول مرّةً، ويحفظها، ويعيد الدخول
/// وحده حين تنتهي — لأن خدمةً تعمل في الخلفية لا تجد من يضغط «دخول».
///
/// ولا مسار جديد في الخادم لأجل التطبيق: هذه المسارات نفسها تخدم
/// شاشة الويب. فما يُصلَح لأحدهما يُصلَح للآخر، ولا تُصان واجهتان.
library;

import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

/// خطأٌ يعرف سببه: الشاشة تتصرّف بحسبه، والمستخدم يقرأ نصّه.
class ApiError implements Exception {
  ApiError(this.message, {this.status = 0, this.needsLogin = false});

  final String message;
  final int status;
  final bool needsLogin;

  /// أهو انقطاعٌ يُعاد معه المحاولة، أم رفضٌ لا يُصلحه التكرار؟
  bool get isOffline => status == 0;

  @override
  String toString() => message;
}

class Api {
  Api({
    required this.baseUrl,
    required this.username,
    required this.password,
    http.Client? client,
  }) : _client = client ?? http.Client();

  String baseUrl;
  String username;
  String password;

  /// كعكة الجلسة. تُحفظ خارج الذاكرة أيضًا فلا يُعاد الدخول عند كل
  /// تشغيل.
  String? cookie;

  final http.Client _client;

  Uri _u(String path) => Uri.parse('${baseUrl.replaceAll(RegExp(r'/+$'), '')}$path');

  Map<String, String> _headers([Map<String, String>? extra]) => {
        'Accept': 'application/json',
        'User-Agent': 'OnPointHR-FieldApp/1.0',
        'Cookie': ?cookie,
        ...?extra,
      };

  void _remember(http.BaseResponse r) {
    final raw = r.headers['set-cookie'];
    if (raw == null) return;
    // أوّل جزء قبل الفاصلة المنقوطة هو الزوج نفسه؛ ما بعده خصائص.
    final session = raw
        .split(',')
        .map((c) => c.trim().split(';').first)
        .where((c) => c.startsWith('session='))
        .toList();
    if (session.isNotEmpty) cookie = session.last;
  }

  /// تسجيل الدخول. يعيد الكعكة أو يرمي `ApiError`.
  Future<void> login() async {
    http.Response r;
    try {
      r = await _client
          .post(_u('/login'),
              headers: _headers(
                  {'Content-Type': 'application/x-www-form-urlencoded'}),
              body: {'username': username, 'password': password, 'remember': '1'})
          .timeout(const Duration(seconds: 25));
    } on SocketException {
      throw ApiError('لا اتصال بالخادم');
    } on HttpException {
      throw ApiError('لا اتصال بالخادم');
    } catch (_) {
      throw ApiError('تعذّر الوصول إلى الخادم');
    }

    _remember(r);

    // الدخول الناجح يحوّل؛ وإعادة صفحة الدخول تعني أن البيانات خطأ.
    final ok = (r.statusCode == 302 || r.statusCode == 303) && cookie != null;
    if (!ok) {
      throw ApiError('اسم المستخدم أو كلمة المرور غير صحيحة',
          status: r.statusCode, needsLogin: true);
    }
  }

  /// يُنفِّذ الطلب، ويعيد الدخول مرّةً واحدة إن انتهت الجلسة.
  Future<Map<String, dynamic>> _send(
    Future<http.StreamedResponse> Function() build, {
    bool retried = false,
  }) async {
    http.StreamedResponse r;
    try {
      r = await build().timeout(const Duration(seconds: 40));
    } on SocketException {
      throw ApiError('لا اتصال بالخادم');
    } catch (_) {
      throw ApiError('تعذّر الوصول إلى الخادم');
    }

    _remember(r);
    final body = await r.stream.bytesToString();

    // جلسةٌ انتهت: الخادم يحوّل إلى صفحة الدخول بدل أن يردّ 401.
    // فالتحويل هنا يُقرأ «ادخل من جديد» لا «نجح».
    final expired = r.statusCode == 302 || r.statusCode == 303;
    if (expired && !retried) {
      await login();
      return _send(build, retried: true);
    }
    if (expired) {
      throw ApiError('انتهت الجلسة', status: r.statusCode, needsLogin: true);
    }

    Map<String, dynamic> json;
    try {
      json = jsonDecode(body) as Map<String, dynamic>;
    } catch (_) {
      throw ApiError('ردٌّ غير مفهوم من الخادم', status: r.statusCode);
    }

    if (json['success'] != true) {
      throw ApiError((json['message'] ?? 'تعذّر تنفيذ الطلب').toString(),
          status: r.statusCode);
    }
    return json;
  }

  /// مكشوفتان ليبني عليهما امتداد البوابة (`portal_api.dart`)
  /// بدل أن يُنشئ عميلًا ثانيًا بجلسةٍ ثانية.
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) {
    return _send(() {
      final req = http.Request('POST', _u(path))
        ..headers.addAll(_headers({'Content-Type': 'application/json'}))
        ..body = jsonEncode(body);
      return _client.send(req);
    });
  }

  Future<Map<String, dynamic>> get(String path) {
    return _send(() {
      final req = http.Request('GET', _u(path))..headers.addAll(_headers());
      return _client.send(req);
    });
  }

  // ------------------------------------------------------- الرحلة

  Future<Map<String, dynamic>> startTrip(String deviceUuid) =>
      postJson('/portal/api/field/start', {'device_uuid': deviceUuid});

  Future<int> track(int tripId, List<Map<String, dynamic>> points) async {
    final j = await postJson('/portal/api/field/track', {
      'trip_id': tripId,
      'points': points,
    });
    return (j['accepted'] as num?)?.toInt() ?? 0;
  }

  Future<Map<String, dynamic>> stopTrip(int tripId, {String reason = 'manual'}) =>
      postJson('/portal/api/field/stop', {'trip_id': tripId, 'reason': reason});

  Future<Map<String, dynamic>> plan() => get('/portal/api/field/plan');

  // ------------------------------------------------------ المحطة

  /// رمز الزيارة — يُطلب قبل الصورة مباشرةً، وعمره دقائق.
  Future<String> visitToken(int stationId, String kind) async {
    final j = await postJson('/portal/api/field/token', {
      'station_id': stationId,
      'kind': kind,
    });
    return j['token'].toString();
  }

  Future<Map<String, dynamic>> checkIn({
    required int stationId,
    required int? tripId,
    required double lat,
    required double lon,
    required double accuracy,
    required String token,
    required File photo,
  }) {
    return _multipart('/portal/api/field/check-in', {
      'station_id': '$stationId',
      if (tripId != null) 'trip_id': '$tripId',
      'latitude': '$lat',
      'longitude': '$lon',
      'accuracy': '$accuracy',
      'token': token,
    }, photo);
  }

  Future<Map<String, dynamic>> checkOut({
    required int visitId,
    required double lat,
    required double lon,
    required double accuracy,
    required String token,
    required File photo,
  }) {
    return _multipart('/portal/api/field/check-out', {
      'visit_id': '$visitId',
      'latitude': '$lat',
      'longitude': '$lon',
      'accuracy': '$accuracy',
      'token': token,
    }, photo);
  }

  Future<Map<String, dynamic>> _multipart(
      String path, Map<String, String> fields, File photo) {
    return _send(() async {
      final req = http.MultipartRequest('POST', _u(path))
        ..headers.addAll(_headers())
        ..fields.addAll(fields)
        ..files.add(await http.MultipartFile.fromPath('photo', photo.path));
      return req.send();
    });
  }

  void close() => _client.close();
}
