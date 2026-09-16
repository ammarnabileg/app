/// شاشة الدخول.
///
/// عنوان الخادم حقلٌ لأن كل شركة على تركيبها: السحابة أو خادمٌ في
/// مكتبها. فلا عنوان مُضمَّن في التطبيق، وتركيبٌ واحد يخدم الجميع.
library;

import 'package:flutter/material.dart';

import '../api.dart';
import '../store.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onDone});

  final void Function(Api api) onDone;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _url = TextEditingController(text: 'https://');
  final _user = TextEditingController();
  final _pass = TextEditingController();
  final _form = GlobalKey<FormState>();

  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    Store.creds().then((c) {
      if (c == null || !mounted) return;
      _url.text = c.baseUrl;
      _user.text = c.username;
      setState(() {});
    });
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });

    final api = Api(
      baseUrl: _url.text.trim(),
      username: _user.text.trim(),
      password: _pass.text,
    );

    try {
      await api.login();
      await Store.saveCreds(
          Creds(api.baseUrl, api.username, api.password));
      await Store.saveCookie(api.cookie);
      if (mounted) widget.onDone(api);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _form,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Icon(Icons.route, size: 56),
                  const SizedBox(height: 12),
                  Text('تطبيق المندوب',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.headlineSmall),
                  const SizedBox(height: 28),
                  TextFormField(
                    controller: _url,
                    textDirection: TextDirection.ltr,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      labelText: 'عنوان النظام',
                      hintText: 'https://company.onz.one',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) {
                      final t = (v ?? '').trim();
                      final u = Uri.tryParse(t);
                      if (u == null || !u.isAbsolute || u.host.isEmpty) {
                        return 'عنوان غير صحيح';
                      }
                      // كلمة مرور وصورٌ لموقع موظف: لا تُرسَل بلا تعمية.
                      if (u.scheme != 'https') return 'العنوان يجب أن يبدأ بـ https';
                      return null;
                    },
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _user,
                    textDirection: TextDirection.ltr,
                    autofillHints: const [AutofillHints.username],
                    decoration: const InputDecoration(
                      labelText: 'اسم المستخدم',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) =>
                        (v ?? '').trim().isEmpty ? 'مطلوب' : null,
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _pass,
                    obscureText: true,
                    autofillHints: const [AutofillHints.password],
                    decoration: const InputDecoration(
                      labelText: 'كلمة المرور',
                      border: OutlineInputBorder(),
                    ),
                    validator: (v) => (v ?? '').isEmpty ? 'مطلوبة' : null,
                    onFieldSubmitted: (_) => _submit(),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 16),
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
                  ],
                  const SizedBox(height: 24),
                  FilledButton(
                    onPressed: _busy ? null : _submit,
                    style: FilledButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 16)),
                    child: _busy
                        ? const SizedBox(
                            height: 20,
                            width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Text('دخول'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
