/// مسارات البوابة: البيانات، والبصمة، والحضور، والطلبات، والفريق.
///
/// امتدادٌ على `Api` لا صنفٌ ثانٍ: الجلسة واحدة وإعادة الدخول واحدة،
/// فلا يُكرَّر ذلك ولا يفترق.
library;

import 'api.dart';

extension PortalApi on Api {
  /// ما تعرفه الشاشة عند فتحها: من أنت، وأنواع الإجازات، وهل أنت
  /// مدير، وهل أنت مندوب.
  Future<Map<String, dynamic>> bootstrap() => get('/portal/api/bootstrap');

  Future<Map<String, dynamic>> myData() => get('/portal/api/my-data');

  Future<Map<String, dynamic>> attendance({int? month, int? year}) {
    final q = <String>[
      if (month != null) 'month=$month',
      if (year != null) 'year=$year',
    ].join('&');
    return get('/portal/api/attendance${q.isEmpty ? '' : '?$q'}');
  }

  // --------------------------------------------------- البصمة

  Future<Map<String, dynamic>> punchStatus() =>
      get('/portal/api/punch-status');

  /// البصمة الذاتية.
  ///
  /// `timestamp` **بالميلي ثانية**، والخادم يردّ القراءة التي يزيد
  /// فرقها عن ٣٥ ثانية. فإرسال الثواني بدل الميلي يجعل كل بصمة
  /// تُردّ بحجّة أن الموقع «قديم» — وهو خطأٌ يصعب تشخيصه من الرسالة.
  ///
  /// و`punchType` هو ما يختاره الموظف: `check_in` أو `presence` أو
  /// `check_out`. وتركُه يجعل الخادم يستنتج النوع من **عدد** بصمات
  /// اليوم — فبصمةٌ زائدة تقلب الباقي، ويُسجَّل انصرافٌ مكان تواجد.
  /// شاشة الويب ترسله، فالتطبيق يرسله.
  Future<Map<String, dynamic>> punch({
    required double lat,
    required double lon,
    required double accuracy,
    required int timestampMillis,
    required String deviceUuid,
    String? punchType,
  }) =>
      postJson('/portal/api/punch', {
        'latitude': lat,
        'longitude': lon,
        'accuracy': accuracy,
        'timestamp': timestampMillis,
        'device_uuid': deviceUuid,
        if (punchType != null) 'punch_type': punchType,
      });

  // --------------------------------------------------- الطلبات

  Future<Map<String, dynamic>> requestLeave({
    required int leaveTypeId,
    required String startDate,
    required String endDate,
    String reason = '',
  }) =>
      postJson('/portal/api/request-leave', {
        'leave_type_id': leaveTypeId,
        'start_date': startDate,
        'end_date': endDate,
        'reason': reason,
      });

  Future<Map<String, dynamic>> requestExcuse({
    required String date,
    required String type,
    String reason = '',
  }) =>
      postJson('/portal/api/request-excuse', {
        'date': date,
        'type': type,
        'reason': reason,
      });

  // ---------------------------------------------------- الفريق

  Future<Map<String, dynamic>> teamSummary() => get('/portal/api/team-summary');

  Future<Map<String, dynamic>> teamApprovals() =>
      get('/portal/api/team-approvals');

  Future<Map<String, dynamic>> approveRequest(int requestId, String action) =>
      postJson('/portal/api/approve-request', {
        'request_id': requestId,
        'action': action, // approve | reject
      });
}
