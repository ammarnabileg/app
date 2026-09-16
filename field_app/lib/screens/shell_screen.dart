/// الإطار: الأقسام التي تَظهر لهذا المستخدم بعينه.
///
/// البوابة تُخفي أقسامها بحسب من يفتحها — «فريقي» للمدير، و«رحلة
/// اليوم» للمندوب، و«البصمة» لمن يبصم في المكتب. والتطبيق يقرأ
/// القرار نفسه من `/portal/api/bootstrap` بدل أن يُعيد استنتاجه:
/// استنتاجٌ ثانٍ يفترق عن الأول عند أول تغيير في القواعد.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../portal_api.dart';
import '../store.dart';
import 'attendance_screen.dart';
import 'home_screen.dart';
import 'profile_screen.dart';
import 'punch_screen.dart';
import 'requests_screen.dart';
import 'team_screen.dart';
import 'trip_screen.dart';
import 'widgets.dart';

class _Tab {
  const _Tab(this.label, this.icon, this.build);

  final String label;
  final IconData icon;
  final Widget Function() build;
}

class ShellScreen extends StatefulWidget {
  const ShellScreen({
    super.key,
    required this.api,
    required this.deviceUuid,
    required this.onSignOut,
  });

  final Api api;
  final String deviceUuid;
  final VoidCallback onSignOut;

  @override
  State<ShellScreen> createState() => _ShellScreenState();
}

class _ShellScreenState extends State<ShellScreen> {
  Map<String, dynamic>? _boot;
  String? _error;
  int _index = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final j = await widget.api.bootstrap();
      if (mounted) setState(() => _boot = j);
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.needsLogin) {
        widget.onSignOut();
        return;
      }
      setState(() => _error = e.message);
    }
  }

  List<_Tab> _tabs(Map<String, dynamic> boot) {
    final isRep = boot['is_field_rep'] == true;
    final officePunch = boot['office_punch'] == true;
    final isManager = boot['is_manager'] == true;
    final hasRecord = boot['has_employee_record'] == true;

    return [
      _Tab('الرئيسية', Icons.home_outlined,
          () => HomeScreen(api: widget.api, boot: boot)),

      // المندوب الخالص لا يبصم في المكتب، فلا يُعرض له زرٌّ يردّه
      // الخادم. والذي يعمل في الحالين (`both`) يرى الاثنين.
      if (officePunch && hasRecord)
        _Tab('البصمة', Icons.fingerprint,
            () => PunchScreen(api: widget.api, deviceUuid: widget.deviceUuid)),

      if (isRep)
        _Tab('رحلة اليوم', Icons.route_outlined,
            () => TripScreen(
                  api: widget.api,
                  deviceUuid: widget.deviceUuid,
                  onSignOut: widget.onSignOut,
                  embedded: true,
                )),

      _Tab('الحضور', Icons.calendar_month_outlined,
          () => AttendanceScreen(api: widget.api)),

      _Tab('الطلبات', Icons.description_outlined,
          () => RequestsScreen(api: widget.api, boot: boot)),

      if (isManager)
        _Tab('فريقي', Icons.groups_outlined, () => TeamScreen(api: widget.api)),

      _Tab('حسابي', Icons.person_outline,
          () => ProfileScreen(api: widget.api, boot: boot)),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final boot = _boot;
    if (boot == null) {
      return Scaffold(
        body: _error == null
            ? const Center(child: CircularProgressIndicator())
            : ErrorPane(message: _error!, onRetry: _load),
      );
    }

    final tabs = _tabs(boot);
    // الأقسام تتغيّر بتغيّر صفة المستخدم، فقد يقع المؤشّر خارجها.
    final index = _index.clamp(0, tabs.length - 1);

    return Scaffold(
      appBar: AppBar(
        title: Text(tabs[index].label),
        actions: [
          IconButton(
            onPressed: _load,
            icon: const Icon(Icons.refresh),
            tooltip: 'تحديث',
          ),
          IconButton(
            onPressed: () async {
              final yes = await showDialog<bool>(
                context: context,
                builder: (c) => AlertDialog(
                  title: const Text('خروج'),
                  content: const Text(
                      'سيُنسى حسابك على هذا الجهاز. وإن كانت رحلةٌ جارية '
                      'فأنهِها أولًا.'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(c, false),
                        child: const Text('تراجع')),
                    FilledButton(
                        onPressed: () => Navigator.pop(c, true),
                        child: const Text('خروج')),
                  ],
                ),
              );
              if (yes != true) return;
              await Store.clearCreds();
              widget.onSignOut();
            },
            icon: const Icon(Icons.logout),
            tooltip: 'خروج',
          ),
        ],
      ),
      body: tabs[index].build(),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: [
          for (final t in tabs)
            NavigationDestination(icon: Icon(t.icon), label: t.label),
        ],
      ),
    );
  }
}
