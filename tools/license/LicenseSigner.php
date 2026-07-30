<?php
namespace App\Services;

/**
 * إصدار الرخص الموقّعة — الجانب الذي يملك المفتاح الخاص.
 *
 * ## لماذا تغيّر التصميم
 *
 * كان التوقيع sha256(تاريخ + سرّ مشترك)، والسرّ نفسه موجود في كود العميل
 * (utils/license.py). فأي عميل يستطيع توليد رخص صالحة إلى أي تاريخ.
 *
 * الآن: توقيع غير متماثل. المفتاح الخاص هنا ولا يخرج من الخادم أبدًا؛
 * والعميل يحمل المفتاح العام فيتحقق ولا يستطيع التوقيع.
 *
 * ## لماذا يبقى العمل بلا إنترنت ممكنًا
 *
 * الرخصة نصّ موقَّع مكتفٍ بذاته: يحمل تاريخ الانتهاء وحدّ الأجهزة ومعرّف
 * الجهاز. العميل يتحقق منه محليًّا كل تشغيل بلا أي اتصال. الاتصال يلزم
 * للتجديد فقط، لا للتشغيل — وهو شرط أساسي لنظام رواتب.
 *
 * ## حماية المفتاح الخاص
 *
 * - خارج جذر الويب (لا تحت public/)
 * - صلاحيات 600
 * - لا يُرفع إلى مستودع الشيفرة أبدًا
 * - تسريبه يعني إمكانية تزوير كل الرخص ← يلزم تدويره وإعادة إصدار الجميع
 */
class LicenseSigner
{
    private $privateKeyPath;

    public function __construct($privateKeyPath = null)
    {
        $this->privateKeyPath = $privateKeyPath
            ?: (defined('LICENSE_PRIVATE_KEY_PATH')
                ? LICENSE_PRIVATE_KEY_PATH
                : dirname(__DIR__, 2) . '/storage/keys/private_key.pem');
    }

    /** ترميز base64url بلا حشو — ليكون الرمز صالحًا في الروابط */
    private function b64u($bin)
    {
        return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
    }

    /**
     * إصدار رخصة موقّعة.
     *
     * @param array $license صف الرخصة من قاعدة البيانات
     * @param string|null $hwid معرّف جهاز العميل (الربط اختياري)
     * @return string رمز بصيغة ONZ1.<payload>.<signature>
     */
    public function issue(array $license, $hwid = null)
    {
        if (!is_readable($this->privateKeyPath)) {
            throw new \RuntimeException('License private key not readable');
        }

        // الحمولة: كل ما يجب أن يكون غير قابل للتعديل عند العميل.
        // max_devices هنا تحديدًا — كان إعدادًا محليًّا يُغيَّر بصفٍّ واحد
        // في قاعدة بيانات العميل.
        $payload = [
            'key'         => $license['license_key'],
            'client'      => $license['client_name'] ?? null,
            'expiry'      => substr($license['expiry_date'], 0, 10),
            'max_devices' => (int)($license['max_devices'] ?? 3),
            'hwid'        => $hwid ?: ($license['hardware_id_hash'] ?? null),
            'issued_at'   => gmdate('Y-m-d\TH:i:s\Z'),
            // nonce يجعل كل إصدار فريدًا، فيمكن تتبّع أي نسخة تسرّبت
            'nonce'       => bin2hex(random_bytes(8)),
            'v'           => 1,
        ];

        // ترتيب المفاتيح مثبَّت: التوقيع يقع على البايتات، فأي اختلاف في
        // الترتيب يغيّر التوقيع ويكسر التحقق.
        $json = json_encode($payload,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

        $pkey = openssl_pkey_get_private(file_get_contents($this->privateKeyPath));
        if ($pkey === false) {
            throw new \RuntimeException('Invalid license private key');
        }

        $signature = '';
        if (!openssl_sign($json, $signature, $pkey, OPENSSL_ALGO_SHA256)) {
            throw new \RuntimeException('License signing failed');
        }

        return 'ONZ1.' . $this->b64u($json) . '.' . $this->b64u($signature);
    }

    /**
     * تحقّق محلي على الخادم — للاختبار ولوحة التحكّم، لا لمسار العميل.
     */
    public function verify($token, $publicKeyPath)
    {
        if (strpos($token, 'ONZ1.') !== 0) {
            return false;
        }
        $parts = explode('.', substr($token, 5));
        if (count($parts) !== 2) {
            return false;
        }
        $b64d = function ($s) {
            return base64_decode(strtr($s, '-_', '+/')
                . str_repeat('=', (4 - strlen($s) % 4) % 4));
        };
        $json = $b64d($parts[0]);
        $sig  = $b64d($parts[1]);
        $pub  = openssl_pkey_get_public(file_get_contents($publicKeyPath));
        if ($pub === false) {
            return false;
        }
        return openssl_verify($json, $sig, $pub, OPENSSL_ALGO_SHA256) === 1
            ? json_decode($json, true)
            : false;
    }
}
