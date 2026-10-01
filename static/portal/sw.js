// Service Worker — بوابة الموظف (onz.one)
// v2: التصميم الزجاجيّ (٢.٢٦). v3: يُخدَم من /portal/sw.js فيتحكّم في البوّابة كلِّها. الملفّاتُ محلّيّةٌ لا من شبكات التوزيع —
// البرنامجُ يعمل في شبكة الشركة، وقد لا يصل الإنترنت.
const CACHE_NAME = 'portal-cache-v3';
const STATIC_ASSETS = [
  '/portal/dashboard',
  '/static/portal/glass.css?v=2.26',
  '/static/portal/icon-192.png',
  '/static/portal/icon-512.png',
  '/static/vendor/bootstrap/bootstrap.rtl.min.css',
  '/static/vendor/fontawesome/css/all.min.css',
  '/static/vendor/fonts/fonts.css'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => console.log('SW caching non-critical error:', err));
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  // Always fetch APIs live over network
  if (event.request.url.includes('/api/')) {
    return;
  }
  
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then(hit =>
        // صفحةٌ بلا نسخة: البوّابةُ المحفوظة خيرٌ من خطأ المتصفّح بلا شبكة.
        hit || (event.request.mode === 'navigate' ? caches.match('/portal/dashboard') : undefined)))
  );
});
