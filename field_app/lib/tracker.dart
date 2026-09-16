/// التتبّع: التقاط الموقع، وحفظه، ثم إرساله.
///
/// هذا هو سبب وجود التطبيق أصلًا. شاشة الويب تتوقّف عن قراءة الموقع
/// حين يُقفل الهاتف — ومندوبٌ يقود سيارته يُقفل هاتفه. فالخطّ الذي
/// كان يظهر للمدير مقطوعًا يظهر الآن متّصلًا، لا لأننا رسمنا ما لم
/// نره، بل لأننا صرنا نراه.
///
/// والترتيب مقصود: **يُحفظ ثم يُرسَل**، لا العكس. فما التُقط لا يضيع
/// بانقطاع شبكة ولا بقتل النظام للتطبيق.
library;

import 'dart:async';
import 'dart:io' show Platform;

import 'package:geolocator/geolocator.dart';
import 'package:intl/intl.dart';

import 'api.dart';
import 'store.dart';

/// صيغة الخادم: وقتٌ محليّ بلا منطقة زمنية، كما يقرؤه `_parse`.
final _fmt = DateFormat('yyyy-MM-dd HH:mm:ss');

class TrackerState {
  const TrackerState({
    this.running = false,
    this.tripId,
    this.queued = 0,
    this.lastSentAt,
    this.lastError,
  });

  final bool running;
  final int? tripId;
  final int queued;
  final DateTime? lastSentAt;
  final String? lastError;

  TrackerState copy({
    bool? running,
    int? tripId,
    int? queued,
    DateTime? lastSentAt,
    String? lastError,
    bool clearError = false,
  }) =>
      TrackerState(
        running: running ?? this.running,
        tripId: tripId ?? this.tripId,
        queued: queued ?? this.queued,
        lastSentAt: lastSentAt ?? this.lastSentAt,
        lastError: clearError ? null : (lastError ?? this.lastError),
      );
}

class Tracker {
  Tracker(this.api);

  final Api api;

  final _states = StreamController<TrackerState>.broadcast();
  Stream<TrackerState> get states => _states.stream;
  TrackerState state = const TrackerState();

  StreamSubscription<Position>? _sub;
  Timer? _flusher;

  void _emit(TrackerState s) {
    state = s;
    if (!_states.isClosed) _states.add(s);
  }

  /// إعدادات الموقع لكل منصّة.
  ///
  /// على أندرويد: إشعارٌ دائم وخدمةٌ أمامية — وهو شرط النظام لقراءة
  /// الموقع والشاشة مقفلة، وهو أيضًا **الصواب**: من يُتتبَّع يرى أنه
  /// يُتتبَّع، ولا يُخفى ذلك عنه.
  ///
  /// على iOS: تحديثات الخلفية مع المؤشّر الأزرق ظاهرًا، ومنع النظام
  /// من إيقافها تلقائيًّا حين يظنّ أن المستخدم توقّف.
  LocationSettings _settings() {
    return AndroidSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 15, // مترًا: الوقوف لا يملأ القاعدة بنقاط متطابقة
      intervalDuration: const Duration(seconds: 20),
      foregroundNotificationConfig: const ForegroundNotificationConfig(
        notificationTitle: 'رحلة عمل جارية',
        notificationText: 'يُسجَّل خط سيرك أثناء الدوام',
        notificationChannelName: 'تتبّع الرحلة',
        enableWakeLock: true,
        setOngoing: true,
      ),
    );
  }

  LocationSettings _appleSettings() => AppleSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 15,
        allowBackgroundLocationUpdates: true,
        showBackgroundLocationIndicator: true,
        pauseLocationUpdatesAutomatically: false,
        activityType: ActivityType.automotiveNavigation,
      );

  /// يطلب الأذونات بالترتيب الذي يفرضه النظام: «أثناء الاستعمال»
  /// أولًا، ثم «دائمًا». وطلبُ «دائمًا» ابتداءً يُرفض صامتًا على
  /// أندرويد ١١ فما فوق.
  Future<String?> ensurePermission() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return 'خدمة الموقع مُطفأة — فعّل GPS';
    }
    var p = await Geolocator.checkPermission();
    if (p == LocationPermission.denied) {
      p = await Geolocator.requestPermission();
    }
    if (p == LocationPermission.denied) {
      return 'التطبيق يحتاج إذن الموقع';
    }
    if (p == LocationPermission.deniedForever) {
      return 'إذن الموقع مرفوض نهائيًّا — افتح الإعدادات وامنحه';
    }
    return null;
  }

  Future<void> start(int tripId, {required String deviceUuid}) async {
    if (state.running) return;

    _emit(state.copy(running: true, tripId: tripId, clearError: true));

    _sub = Geolocator.getPositionStream(
      locationSettings: Platform.isIOS ? _appleSettings() : _settings(),
    ).listen(
      (pos) => _onPosition(tripId, pos),
      onError: (e) => _emit(state.copy(lastError: 'تعذّرت قراءة الموقع')),
      cancelOnError: false,
    );

    // الإرسال دوريّ لا عند كل نقطة: دفعةٌ واحدة كل دقيقة أرفق
    // بالبطارية وبالشبكة من ستّين طلبًا.
    _flusher = Timer.periodic(const Duration(seconds: 60), (_) => flush());
    await flush();
  }

  Future<void> _onPosition(int tripId, Position pos) async {
    // يُحفظ أولًا. لو انقطعت الشبكة أو قُتل التطبيق بعد هذا السطر،
    // النقطة باقية.
    await Store.enqueue(tripId, {
      'latitude': pos.latitude,
      'longitude': pos.longitude,
      'accuracy': pos.accuracy,
      'speed': pos.speed,
      'heading': pos.heading,
      'recorded_at': _fmt.format(DateTime.now()),
    });
    await Store.trim();
    _emit(state.copy(queued: await Store.queueLength()));
  }

  /// يرسل ما في الطابور. لا يرمي: يُنادى من مؤقّت لا من زرّ.
  Future<void> flush() async {
    final tripId = state.tripId;
    if (tripId == null) return;

    try {
      while (true) {
        final rows = await Store.pending(tripId);
        if (rows.isEmpty) break;

        final points = rows
            .map((r) => {
                  'latitude': r['lat'],
                  'longitude': r['lon'],
                  'accuracy': r['accuracy'],
                  'speed': r['speed'],
                  'heading': r['heading'],
                  'recorded_at': r['recorded_at'],
                })
            .toList();

        await api.track(tripId, points);

        // تُحذف بعد التأكيد لا قبله: لو سقط الطلب بقيت النقاط.
        await Store.drop(rows.map((r) => r['id']).toList());
        _emit(state.copy(
            queued: await Store.queueLength(),
            lastSentAt: DateTime.now(),
            clearError: true));

        if (rows.length < 200) break;
      }
    } on ApiError catch (e) {
      // الانقطاع حالٌ عادي في الميدان: يُعرض ولا يُوقف شيئًا.
      _emit(state.copy(lastError: e.message));
    } catch (_) {
      _emit(state.copy(lastError: 'تعذّر الإرسال'));
    }
  }

  Future<void> stop({String reason = 'manual'}) async {
    await _sub?.cancel();
    _sub = null;
    _flusher?.cancel();
    _flusher = null;

    await flush(); // آخر دفعة قبل الإغلاق
    final tripId = state.tripId;
    if (tripId != null) {
      try {
        await api.stopTrip(tripId, reason: reason);
      } on ApiError catch (e) {
        _emit(state.copy(lastError: e.message));
      }
    }
    _emit(const TrackerState());
  }

  void dispose() {
    _sub?.cancel();
    _flusher?.cancel();
    _states.close();
  }
}
