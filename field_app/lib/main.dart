/// تطبيق المندوب.
///
/// الواجهة عربية بالكامل واتجاهها من اليمين — فُرض `Directionality`
/// على مستوى التطبيق لا على كل شاشة، لأن النسيان في شاشةٍ واحدة
/// يُخرج سطرًا مقلوبًا أمام مستخدم.
library;

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'api.dart';
import 'screens/login_screen.dart';
import 'screens/shell_screen.dart';
import 'store.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const FieldApp());
}

class FieldApp extends StatefulWidget {
  const FieldApp({super.key});

  @override
  State<FieldApp> createState() => _FieldAppState();
}

class _FieldAppState extends State<FieldApp> {
  Api? _api;
  bool _checking = true;

  /// معرّف الجهاز: يربط الرحلة بجهازٍ واحد، فيُكشف من يفتح الرحلة
  /// نفسها على جهازين.
  final _deviceUuid = const Uuid().v4();

  @override
  void initState() {
    super.initState();
    _restore();
  }

  /// استعادة الجلسة المحفوظة: المندوب لا يكتب كلمته كل صباح.
  Future<void> _restore() async {
    final c = await Store.creds();
    if (c != null) {
      final api = Api(
          baseUrl: c.baseUrl, username: c.username, password: c.password)
        ..cookie = await Store.cookie();
      // لا يُتحقّق من الكعكة هنا: الشبكة قد تكون مقطوعة عند الفتح،
      // وأول طلبٍ يفشل سيعيد الدخول وحده.
      if (mounted) setState(() => _api = api);
    }
    if (mounted) setState(() => _checking = false);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'تطبيق المندوب',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF1B6EF3),
        fontFamily: 'Roboto',
      ),
      builder: (context, child) => Directionality(
        textDirection: TextDirection.rtl,
        child: child ?? const SizedBox.shrink(),
      ),
      home: _checking
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : _api == null
              ? LoginScreen(onDone: (api) => setState(() => _api = api))
              : ShellScreen(
                  api: _api!,
                  deviceUuid: _deviceUuid,
                  onSignOut: () => setState(() => _api = null),
                ),
    );
  }
}
