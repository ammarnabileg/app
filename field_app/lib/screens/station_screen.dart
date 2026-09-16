/// المحطة: صورةٌ حيّة ثم تسجيل.
///
/// الترتيب هنا ليس تفصيلًا في الواجهة، هو الضمانة نفسها:
///
///   ١ — يُقرأ الموقع الآن، فلا يُسجَّل من ليس هناك.
///   ٢ — يُطلب الرمز من الخادم، وعمره ثلاث دقائق.
///   ٣ — تُلتقط الصورة **بعد** الرمز، فلا تكون معدّةً من قبل.
///   ٤ — تُرسَل فورًا.
///
/// ولا مدخل من المعرض في هذه الشاشة أصلًا: الكاميرا تُفتح داخل
/// التطبيق، ولا زرّ يقود إلى الملفات. والخادم يفحص عمر الصورة فوق
/// ذلك — فالحارس اثنان لا واحد.
library;

import 'dart:async';
import 'dart:io';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../api.dart';

class StationScreen extends StatefulWidget {
  const StationScreen({
    super.key,
    required this.api,
    required this.station,
    required this.kind,
    required this.tripId,
    this.visitId,
  });

  final Api api;
  final Map<String, dynamic> station;

  /// 'in' دخول، 'out' خروج.
  final String kind;
  final int? tripId;
  final int? visitId;

  @override
  State<StationScreen> createState() => _StationScreenState();
}

class _StationScreenState extends State<StationScreen> {
  CameraController? _cam;
  Future<void>? _camReady;

  bool _busy = false;
  String? _error;
  String _step = '';

  @override
  void initState() {
    super.initState();
    _camReady = _initCamera();
  }

  Future<void> _initCamera() async {
    final cams = await availableCameras();
    if (cams.isEmpty) throw Exception('لا كاميرا في هذا الجهاز');

    // الخلفية أولًا: صورة المكان لا صورة الوجه.
    final back = cams.firstWhere(
      (c) => c.lensDirection == CameraLensDirection.back,
      orElse: () => cams.first,
    );
    final c = CameraController(back, ResolutionPreset.medium,
        enableAudio: false, imageFormatGroup: ImageFormatGroup.jpeg);
    await c.initialize();
    if (!mounted) {
      await c.dispose();
      return;
    }
    setState(() => _cam = c);
  }

  @override
  void dispose() {
    _cam?.dispose();
    super.dispose();
  }

  bool get _isIn => widget.kind == 'in';

  Future<void> _capture() async {
    final cam = _cam;
    if (cam == null || _busy) return;

    setState(() {
      _busy = true;
      _error = null;
      _step = 'يُقرأ موقعك…';
    });

    File? shot;
    try {
      // ١ — الموقع الآن. الخادم يفحص النطاق أيضًا؛ والفحص هنا يوفّر
      // على المندوب صورةً ترفَض بعد التقاطها.
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
            accuracy: LocationAccuracy.best,
            timeLimit: Duration(seconds: 20)),
      );

      final sLat = (widget.station['latitude'] as num).toDouble();
      final sLon = (widget.station['longitude'] as num).toDouble();
      final radius =
          ((widget.station['radius_meters'] as num?) ?? 100).toDouble();
      final away = Geolocator.distanceBetween(
          pos.latitude, pos.longitude, sLat, sLon);

      // هامشٌ بقدر دقّة القراءة: من يقف عند الباب ودقّة هاتفه ٢٥م
      // ليس مخالفًا. والخادم يطبّق الهامش نفسه.
      if (away > radius + pos.accuracy.clamp(0, 30)) {
        throw ApiError('أنت خارج نطاق المحطة (تبعد ${away.round()} متراً)');
      }

      // ٢ — الرمز قبل الصورة.
      setState(() => _step = 'يُطلب رمز الزيارة…');
      final token = await widget.api.visitToken(
          (widget.station['station_id'] as num).toInt(), widget.kind);

      // ٣ — الصورة بعده.
      setState(() => _step = 'تُلتقط الصورة…');
      final x = await cam.takePicture();
      shot = File(x.path);

      // ٤ — الإرسال.
      setState(() => _step = 'يُرسَل…');
      final res = _isIn
          ? await widget.api.checkIn(
              stationId: (widget.station['station_id'] as num).toInt(),
              tripId: widget.tripId,
              lat: pos.latitude,
              lon: pos.longitude,
              accuracy: pos.accuracy,
              token: token,
              photo: shot,
            )
          : await widget.api.checkOut(
              visitId: widget.visitId!,
              lat: pos.latitude,
              lon: pos.longitude,
              accuracy: pos.accuracy,
              token: token,
              photo: shot,
            );

      if (mounted) Navigator.pop(context, res);
    } on TimeoutException {
      if (mounted) {
        setState(() => _error = 'تعذّرت قراءة الموقع — اخرج إلى العراء وأعد');
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'تعذّر التسجيل: $e');
    } finally {
      // الصورة المؤقّتة لا تبقى على الجهاز بعد إرسالها.
      try {
        if (shot != null && await shot.exists()) await shot.delete();
      } catch (_) {}
      if (mounted) {
        setState(() {
          _busy = false;
          _step = '';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = (widget.station['name'] ?? '').toString();

    return Scaffold(
      appBar: AppBar(title: Text('${_isIn ? "دخول" : "خروج"} — $name')),
      body: Column(
        children: [
          Expanded(
            child: FutureBuilder(
              future: _camReady,
              builder: (context, snap) {
                if (snap.hasError) {
                  return const _Message(
                    icon: Icons.no_photography,
                    text: 'تعذّر فتح الكاميرا.\n'
                        'التطبيق لا يسجّل زيارة بلا صورة — امنح إذن '
                        'الكاميرا من إعدادات الهاتف.',
                  );
                }
                final cam = _cam;
                if (cam == null) {
                  return const Center(child: CircularProgressIndicator());
                }
                return Stack(
                  fit: StackFit.expand,
                  children: [
                    CameraPreview(cam),
                    if (_busy)
                      Container(
                        color: Colors.black54,
                        child: Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const CircularProgressIndicator(),
                              const SizedBox(height: 16),
                              Text(_step,
                                  style: const TextStyle(color: Colors.white)),
                            ],
                          ),
                        ),
                      ),
                  ],
                );
              },
            ),
          ),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (_error != null) ...[
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Theme.of(context).colorScheme.errorContainer,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(_error!,
                        style: TextStyle(
                            color: Theme.of(context)
                                .colorScheme
                                .onErrorContainer)),
                  ),
                  const SizedBox(height: 12),
                ],
                Text(
                  'التقط صورة من داخل المكان الآن. لا تُقبل صورة من '
                  'المعرض ولا صورة قديمة.',
                  style: Theme.of(context).textTheme.bodySmall,
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: _busy || _cam == null ? null : _capture,
                  icon: const Icon(Icons.camera_alt),
                  label: Text(_isIn ? 'التقط وسجّل الدخول' : 'التقط وسجّل الخروج'),
                  style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 16)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 48, color: Theme.of(context).disabledColor),
              const SizedBox(height: 16),
              Text(text, textAlign: TextAlign.center),
            ],
          ),
        ),
      );
}
