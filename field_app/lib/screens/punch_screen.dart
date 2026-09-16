/// البصمة الذاتية من الهاتف.
///
/// الخادم هو الحَكَم: يفحص الشبكة المسموحة، والنطاق حول الفرع، ومهلة
/// التكرار، وطزاجة قراءة الموقع. فلا تُكرَّر أحكامه هنا — تُعرض
/// رسائله كما هي، لأنها مكتوبة بعناية وتقول للموظف ما يفعل.
///
/// وما يفعله التطبيق فوق ذلك واحد: يقرأ الموقع **الآن** ويرسل زمن
/// القراءة بالميلي ثانية. والخادم يردّ ما تجاوز فرقه ٣٥ ثانية — فلو
/// أُرسلت الثواني بدل الميلي، رُدّت كل بصمة بحجّة أن الموقع قديم،
/// وهو عطلٌ لا تدلّ رسالتُه عليه.
library;

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../api.dart';
import '../portal_api.dart';
import 'widgets.dart';

class PunchScreen extends StatefulWidget {
  const PunchScreen({super.key, required this.api, required this.deviceUuid});

  final Api api;
  final String deviceUuid;

  @override
  State<PunchScreen> createState() => _PunchScreenState();
}

class _PunchScreenState extends State<PunchScreen> {
  Map<String, dynamic>? _status;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  String? _result;
  bool _resultOk = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final j = await widget.api.punchStatus();
      if (mounted) {
        setState(() {
          _status = j;
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _punch() async {
    setState(() {
      _busy = true;
      _result = null;
    });
    try {
      final service = await Geolocator.isLocationServiceEnabled();
      if (!service) throw ApiError('خدمة الموقع مُطفأة — فعّل GPS');

      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied ||
          perm == LocationPermission.deniedForever) {
        throw ApiError('التطبيق يحتاج إذن الموقع لتسجيل البصمة');
      }

      // قراءة لحظية لا آخر موقع معروف: الخادم يرفض القديم، وهو محقّ.
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
            accuracy: LocationAccuracy.best, timeLimit: Duration(seconds: 25)),
      );

      final j = await widget.api.punch(
        lat: pos.latitude,
        lon: pos.longitude,
        accuracy: pos.accuracy,
        timestampMillis: DateTime.now().millisecondsSinceEpoch,
        deviceUuid: widget.deviceUuid,
      );

      if (mounted) {
        setState(() {
          _result = (j['message'] ?? 'سُجّلت البصمة').toString();
          _resultOk = true;
        });
      }
      await _load();
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          _result = e.message;
          _resultOk = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _result = 'تعذّرت قراءة موقعك — اخرج إلى العراء وأعد';
          _resultOk = false;
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _status;
    if (_loading && s == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (s == null) {
      return ErrorPane(message: _error ?? 'تعذّر التحميل', onRetry: _load);
    }

    // مُطفأة بسياسة الشركة: يُقال ذلك ولا يُعرض زرٌّ يُردّ عند الضغط.
    if (s['enabled'] != true) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.phonelink_lock, size: 44),
              SizedBox(height: 16),
              Text(
                'البصمة الذاتية من الهاتف غير مفعّلة وفق سياسة شركتك.\n'
                'استعمل جهاز البصمة في المكتب.',
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
    }

    final punches = (s['today_punches'] as List?) ?? [];
    final next = (s['next_action'] ?? 'حضور').toString();

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                children: [
                  Text('الخطوة التالية',
                      style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: 4),
                  Text(next, style: Theme.of(context).textTheme.headlineSmall),
                  const SizedBox(height: 20),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      onPressed: _busy ? null : _punch,
                      icon: _busy
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.fingerprint),
                      label: Text(_busy ? 'يُقرأ موقعك…' : 'سجّل $next'),
                      style: FilledButton.styleFrom(
                          padding: const EdgeInsets.symmetric(vertical: 18)),
                    ),
                  ),
                  if (s['geofence_enabled'] == true) ...[
                    const SizedBox(height: 10),
                    Text(
                      'يجب أن تكون داخل نطاق ${s['radius'] ?? 150} متر من مقرّ العمل.',
                      style: Theme.of(context).textTheme.bodySmall,
                      textAlign: TextAlign.center,
                    ),
                  ],
                ],
              ),
            ),
          ),
          if (_result != null) ...[
            const SizedBox(height: 12),
            Card(
              color: _resultOk
                  ? Colors.green.withValues(alpha: 0.15)
                  : Theme.of(context).colorScheme.errorContainer,
              child: ListTile(
                leading: Icon(_resultOk ? Icons.check_circle : Icons.error_outline),
                title: Text(_result!),
              ),
            ),
          ],
          const SizedBox(height: 20),
          const SectionTitle('بصمات اليوم'),
          if (punches.isEmpty)
            const Card(
              child: ListTile(
                leading: Icon(Icons.schedule),
                title: Text('لا بصمات اليوم'),
              ),
            )
          else
            ...punches.map((raw) {
              final p = (raw as Map).cast<String, dynamic>();
              return Card(
                child: ListTile(
                  dense: true,
                  leading: const Icon(Icons.fiber_manual_record, size: 12),
                  title: Text('${p['type']} — ${p['time']}'),
                  subtitle: Text('المصدر: ${p['source'] ?? 'device'}'),
                ),
              );
            }),
          const SizedBox(height: 32),
        ],
      ),
    );
  }
}
