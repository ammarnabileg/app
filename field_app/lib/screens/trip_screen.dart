/// شاشة اليوم: ابدأ الرحلة، وزُر محطاتك، وأنهِ.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../store.dart';
import '../tracker.dart';
import 'station_screen.dart';

class TripScreen extends StatefulWidget {
  const TripScreen({
    super.key,
    required this.api,
    required this.deviceUuid,
    required this.onSignOut,
    this.embedded = false,
  });

  final Api api;
  final String deviceUuid;
  final VoidCallback onSignOut;

  /// داخل الإطار: الشريط العلوي والخروج للإطار لا لهذه الشاشة.
  final bool embedded;

  @override
  State<TripScreen> createState() => _TripScreenState();
}

class _TripScreenState extends State<TripScreen> with WidgetsBindingObserver {
  late final Tracker _tracker = Tracker(widget.api);

  List<dynamic> _plan = [];
  Map<String, dynamic> _summary = {};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _tracker.states.listen((_) => mounted ? setState(() {}) : null);
    _load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _tracker.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    // العودة إلى الشاشة: الخطة قد تغيّرت، وما في الطابور يُدفع.
    if (s == AppLifecycleState.resumed) {
      _load();
      _tracker.flush();
    }
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final j = await widget.api.plan();
      if (!mounted) return;
      setState(() {
        _plan = (j['plan'] as List?) ?? [];
        _summary = (j['summary'] as Map?)?.cast<String, dynamic>() ?? {};
        _error = null;
      });
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.needsLogin) {
        widget.onSignOut();
        return;
      }
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _startTrip() async {
    final why = await _tracker.ensurePermission();
    if (why != null) {
      _toast(why);
      return;
    }
    try {
      final j = await widget.api.startTrip(widget.deviceUuid);
      await _tracker.start((j['trip_id'] as num).toInt(),
          deviceUuid: widget.deviceUuid);
      await _load();
    } on ApiError catch (e) {
      _toast(e.message);
    }
  }

  Future<void> _stopTrip() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('إنهاء الرحلة'),
        content: const Text('سيتوقّف تسجيل خط السير. أمتأكّد؟'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('تراجع')),
          FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('إنهاء')),
        ],
      ),
    );
    if (yes != true) return;
    await _tracker.stop();
    await _load();
  }

  Future<void> _openStation(Map<String, dynamic> st, String kind) async {
    final res = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => StationScreen(
          api: widget.api,
          station: st,
          kind: kind,
          tripId: _tracker.state.tripId,
          visitId: (st['visit_id'] as num?)?.toInt(),
        ),
      ),
    );
    if (res != null) _toast(res['message']?.toString() ?? 'تمّ');
    await _load();
  }

  void _toast(String m) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(content: Text(m)));
  }

  @override
  Widget build(BuildContext context) {
    final t = _tracker.state;

    final body = RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _StatusCard(
              state: t,
              onStart: _startTrip,
              onStop: _stopTrip,
              onFlush: _tracker.flush,
            ),
            const SizedBox(height: 16),
            if (_error != null)
              Card(
                color: Theme.of(context).colorScheme.errorContainer,
                child: ListTile(
                  leading: const Icon(Icons.warning_amber),
                  title: Text(_error!),
                ),
              ),
            if (_summary.isNotEmpty) ...[
              Text('اليوم', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 8),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Wrap(
                    spacing: 20,
                    runSpacing: 8,
                    children: [
                      _Stat(label: 'محطات', value: '${_summary['stations'] ?? 0}'),
                      _Stat(label: 'زيارات', value: '${_summary['visits'] ?? 0}'),
                      _Stat(
                          label: 'مسافة',
                          value:
                              '${(((_summary['distance_meters'] as num?) ?? 0) / 1000).toStringAsFixed(1)} كم'),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
            ],
            Text('محطات اليوم',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (_loading && _plan.isEmpty)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_plan.isEmpty)
              const Card(
                child: ListTile(
                  leading: Icon(Icons.event_busy),
                  title: Text('لا محطات في خطة اليوم'),
                  subtitle: Text('راجع مسؤولك إن كان يجب أن تكون هناك محطات.'),
                ),
              )
            else
              ..._plan.map((raw) {
                final st = (raw as Map).cast<String, dynamic>();
                return _StationTile(
                  station: st,
                  tripRunning: t.running,
                  onCheckIn: () => _openStation(st, 'in'),
                  onCheckOut: () => _openStation(st, 'out'),
                );
              }),
            const SizedBox(height: 32),
          ],
        ),
      );

    if (widget.embedded) return body;

    return Scaffold(
      appBar: AppBar(
        title: const Text('رحلة اليوم'),
        actions: [
          IconButton(
            onPressed: _load,
            icon: const Icon(Icons.refresh),
            tooltip: 'تحديث',
          ),
          IconButton(
            onPressed: () async {
              if (t.running) {
                _toast('أنهِ الرحلة أولًا');
                return;
              }
              await Store.clearCreds();
              widget.onSignOut();
            },
            icon: const Icon(Icons.logout),
            tooltip: 'خروج',
          ),
        ],
      ),
      body: body,
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.state,
    required this.onStart,
    required this.onStop,
    required this.onFlush,
  });

  final TrackerState state;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onFlush;

  @override
  Widget build(BuildContext context) {
    final on = state.running;
    return Card(
      color: on
          ? Theme.of(context).colorScheme.primaryContainer
          : Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(on ? Icons.gps_fixed : Icons.gps_off, size: 28),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    on ? 'الرحلة جارية — يُسجَّل خط سيرك' : 'الرحلة متوقّفة',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            if (on && state.queued > 0) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.cloud_upload_outlined, size: 18),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'بانتظار الإرسال: ${state.queued} نقطة — محفوظة ولن تضيع',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                  TextButton(onPressed: onFlush, child: const Text('أرسل الآن')),
                ],
              ),
            ],
            if (state.lastError != null) ...[
              const SizedBox(height: 4),
              Text(state.lastError!,
                  style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: on ? onStop : onStart,
              icon: Icon(on ? Icons.stop : Icons.play_arrow),
              label: Text(on ? 'إنهاء الرحلة' : 'ابدأ الرحلة'),
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
            ),
          ],
        ),
      ),
    );
  }
}

class _StationTile extends StatelessWidget {
  const _StationTile({
    required this.station,
    required this.tripRunning,
    required this.onCheckIn,
    required this.onCheckOut,
  });

  final Map<String, dynamic> station;
  final bool tripRunning;
  final VoidCallback onCheckIn;
  final VoidCallback onCheckOut;

  @override
  Widget build(BuildContext context) {
    final status = (station['status'] ?? 'pending').toString();
    final done = status == 'closed' || station['check_out_at'] != null;
    final open = status == 'open' || (station['check_in_at'] != null && !done);

    return Card(
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: done
              ? Colors.green.shade100
              : open
                  ? Colors.orange.shade100
                  : Theme.of(context).colorScheme.surfaceContainerHighest,
          child: Icon(
            done
                ? Icons.check
                : open
                    ? Icons.timer_outlined
                    : Icons.store_outlined,
            color: done
                ? Colors.green.shade800
                : open
                    ? Colors.orange.shade900
                    : null,
          ),
        ),
        title: Text((station['name'] ?? '').toString()),
        subtitle: Text(
          done
              ? 'اكتملت'
              : open
                  ? 'أنت بالداخل — سجّل الخروج عند المغادرة'
                  : (station['address'] ?? station['customer_name'] ?? '')
                      .toString(),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: done
            ? null
            : FilledButton.tonal(
                onPressed: tripRunning ? (open ? onCheckOut : onCheckIn) : null,
                child: Text(open ? 'خروج' : 'دخول'),
              ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: Theme.of(context).textTheme.titleLarge),
          Text(label, style: Theme.of(context).textTheme.bodySmall),
        ],
      );
}
