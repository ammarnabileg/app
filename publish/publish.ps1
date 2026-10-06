# انشر نسخة جديدة من البرنامج بضغطة واحدة — يُشغَّل من PUBLISH.bat.
#
# ما يفعله بالترتيب:
#   1) يتأكّد من Git ومن أنك على main، ويسحب آخر نسخة من GitHub.
#   2) يرفض النشر لو بين التعديلات قاعدةُ بيانات أو مفتاح (.db / .local.php / sk_live_ …).
#   3) يسألك: إصلاح ولا ميزة ولا بناء؟ ويسألك عن سطرٍ يصف التعديل.
#   4) يرفع رقم النسخة في utils/version_info.py، ويكتب السطر في CHANGELOG.md.
#   5) يشغّل الاختبارات (تقدر تتخطّاها).
#   6) commit + push إلى main وإلى فرع v<النسخة> — والفرعُ يشغّل البناء على GitHub،
#      والبناءُ ينشئ Release بلينك تنزيل ثابت بعد ما اختبار التشغيل ينجح.
#   7) يستنّى الـRelease ويفتحه لك في المتصفّح.
#
# لا يرفع أي حاجة من غير ما تأكّد، ولا ينشر التحديث للعملاء — ده لسه خطوة
# publish_version.py بمفتاح اللوحة (docs/RELEASE.md).

param([switch]$NoWait)    # -NoWait: لا يستنّى الـRelease (للتجربة)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Fail([string]$t) {
    Say ''
    Say "✗ $t" 'Red'
    exit 1
}
# git يكتب تقدّمه على stderr، وPowerShell 5.1 مع 'Stop' يعدّ ذلك خطأً ولو نجح الأمر.
# فيُشغَّل هنا بـ'Continue' ويُحكم عليه برمز الخروج وحده.
function Invoke-GitRaw([string[]]$GitArgs) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = @(& git @GitArgs 2>&1 | ForEach-Object { "$_" }) }
    finally { $ErrorActionPreference = $prev }
    return @{ Code = $LASTEXITCODE; Out = $out }
}
function Invoke-Git([string[]]$GitArgs) {
    $r = Invoke-GitRaw $GitArgs
    if ($r.Code -ne 0) { throw ("git " + ($GitArgs -join ' ') + "`n" + ($r.Out -join "`n")) }
    return $r.Out
}

Say '══════════ نشر نسخة جديدة ══════════' 'Cyan'

# ---------------------------------------------------------------- (١) Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'Git مش متسطّب على الجهاز — نزّله من https://git-scm.com/download/win وبعدين شغّل الزرار تاني.'
}
try { $null = Invoke-Git @('rev-parse', '--is-inside-work-tree') } catch { Fail "الفولدر ده مش جوّه الريبو: $Repo" }

$branch = ((Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')) | Out-String).Trim()
if ($branch -ne 'main') { Fail "أنت على فرع «$branch» — ارجع لـ main الأول:  git checkout main" }

$remote = ((Invoke-Git @('remote', 'get-url', 'origin')) | Out-String).Trim()
if ($remote -notmatch 'github\.com[:/](?<owner>[^/]+)/(?<name>[^/.]+)') { Fail "الريبو مش على GitHub: $remote" }
$Owner = $Matches['owner']; $RepoName = $Matches['name']

Say '… بجيب آخر نسخة من GitHub' 'Cyan'
try { $null = Invoke-Git @('pull', '--rebase', '--autostash', 'origin', 'main') }
catch { Fail "تعذّر سحب آخر نسخة — غالبًا تعارض بين تعديلك وتعديل اتعمل على GitHub. حلّه وبعدين جرّب تاني.`n$_" }

# ---------------------------------------------------------------- (٢) التعديلات والحماية
$changes = @(Invoke-Git @('status', '--porcelain', '--untracked-files=all') | Where-Object { $_ })
if ($changes.Count -eq 0) { Fail 'مفيش أي تعديل تتنشره — عدّل ملفات الأول.' }

# ممنوع يترفع: قواعد البيانات والمفاتيح وبيانات العملاء. (والـ.gitignore بيحمي معظمها
# أصلًا — ده قفل تاني لو ملف اتحط بالغلط.)
$Blocked = @('\.db$', '\.db-(wal|shm)$', '\.sqlite3?$', '\.local\.php$', '(^|/)\.env($|\.)',
             '\.(pem|key|pfx|p12)$', '(^|/)secrets?[^/]*$', '(^|/)hr_system', '(^|/)backups?/',
             '(^|/)field_photos/')
$files = $changes | ForEach-Object { ($_.Substring(3) -replace '^.* -> ', '').Trim('"') }
$bad = @($files | Where-Object { $f = $_; @($Blocked | Where-Object { $f -match $_ }).Count -gt 0 })
if ($bad.Count) { Fail ("ملفات ممنوع تترفع (قاعدة بيانات أو مفتاح):`n  " + ($bad -join "`n  ") + "`nشيلها من الفولدر أو ضيفها لـ .gitignore.") }

Say ''
Say "التعديلات ($($changes.Count) ملف):" 'Yellow'
$changes | Select-Object -First 40 | ForEach-Object { Say "  $_" }
if ($changes.Count -gt 40) { Say "  … و$($changes.Count - 40) غيرهم" }

# ---------------------------------------------------------------- (٣) الأسئلة
Say ''
Say 'نوع التعديل:' 'Cyan'
Say '  1) إصلاح        (2.31.0.0 ← 2.31.1.0)   [الافتراضي]'
Say '  2) ميزة جديدة   (2.31.0.0 ← 2.32.0.0)'
Say '  3) بناء فقط     (2.31.0.0 ← 2.31.0.1)'
$kind = (Read-Host 'اكتب الرقم واضغط Enter').Trim()
if ($kind -eq '') { $kind = '1' }
if ($kind -notin @('1', '2', '3')) { Fail 'اختيار غير معروف.' }

# الوصفُ في نافذةٍ صغيرة لا في شاشة الأوامر: شاشةُ الأوامر القديمة في الويندوز
# لا تقرأ الكتابة العربيّة صح (تطلع علامات استفهام).
$desc = $null
try {
    Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
    $desc = [Microsoft.VisualBasic.Interaction]::InputBox(
        'اكتب في سطر واحد إيه اللي اتغيّر (هيظهر في الـRelease وسجل التغييرات):', 'نشر نسخة جديدة', '')
} catch { $desc = $null }
if ($null -eq $desc) { $desc = Read-Host 'اكتب في سطر واحد إيه اللي اتغيّر' }
$desc = "$desc".Trim()
if ($desc.Length -lt 3) { Fail 'لازم تكتب وصف للتعديل (هيظهر في الـRelease وسجل التغييرات).' }
$desc = $desc -replace '[\r\n]+', ' '

# ---------------------------------------------------------------- (٤) رقم النسخة والسجل
$Utf8 = New-Object Text.UTF8Encoding $false
$verPath = Join-Path $Repo 'utils/version_info.py'
$logPath = Join-Path $Repo 'CHANGELOG.md'
$verText = [IO.File]::ReadAllText($verPath, [Text.Encoding]::UTF8)
$logText = [IO.File]::ReadAllText($logPath, [Text.Encoding]::UTF8)

function Part([string]$name) {
    if ($verText -notmatch "(?m)^$name\s*=\s*(\d+)") { Fail "مش لاقي $name في utils/version_info.py" }
    return [int]$Matches[1]
}
$maj = Part 'VERSION_MAJOR'; $min = Part 'VERSION_MINOR'; $pat = Part 'VERSION_PATCH'; $bld = Part 'VERSION_BUILD'
$old = "$maj.$min.$pat.$bld"
switch ($kind) {
    '1' { $pat++; $bld = 0 }
    '2' { $min++; $pat = 0; $bld = 0 }
    '3' { $bld++ }
}
$new = "$maj.$min.$pat.$bld"

$tagTaken = ((Invoke-GitRaw @('ls-remote', '--heads', '--tags', 'origin', "v$new")).Out -join '').Trim()
if ($tagTaken) { Fail "النسخة $new موجودة على GitHub بالفعل — اسحب آخر نسخة وجرّب تاني." }

$newVer = $verText
foreach ($pair in @(@('VERSION_MINOR', $min), @('VERSION_PATCH', $pat), @('VERSION_BUILD', $bld))) {
    $newVer = [regex]::Replace($newVer, "(?m)^($($pair[0])\s*=\s*)\d+", "`${1}$($pair[1])")
}
$safeName = $desc -replace '"', "'"
if ($safeName.Length -gt 60) { $safeName = $safeName.Substring(0, 60) }
$newVer = [regex]::Replace($newVer, '(?m)^(RELEASE_NAME\s*=\s*)".*"', "`${1}""$safeName""")

$today = Get-Date -Format 'yyyy-MM-dd'
$entry = "## $new — $today · «$safeName»`n`n- $desc`n`n---`n`n"
$at = $logText.IndexOf("`n## ")
$newLog = if ($at -ge 0) { $logText.Substring(0, $at + 1) + $entry + $logText.Substring($at + 1) } else { $logText + "`n" + $entry }

Say ''
Say "النسخة: $old  ←  $new" 'Green'
Say "الوصف:  $desc" 'Green'
$ok = (Read-Host 'أكمّل النشر؟ (Y/n)').Trim().ToLower()
if ($ok -eq 'n') { Fail 'اتلغى — مفيش حاجة اتغيّرت.' }

[IO.File]::WriteAllText($verPath, $newVer, $Utf8)
[IO.File]::WriteAllText($logPath, $newLog, $Utf8)

function Revert {
    [IO.File]::WriteAllText($verPath, $verText, $Utf8)
    [IO.File]::WriteAllText($logPath, $logText, $Utf8)
}

# ---------------------------------------------------------------- (٥) الاختبارات
$run = (Read-Host 'أشغّل الاختبارات قبل النشر؟ بتاخد حوالي ٥ دقايق (Y/n)').Trim().ToLower()
if ($run -ne 'n') {
    $py = $null
    foreach ($c in @('py', 'python', 'python3')) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
    if (-not $py) { Revert; Fail 'Python مش متسطّب — مقدرش أشغّل الاختبارات. (تقدر تتخطّاها بـ n)' }
    Say '… بشغّل الاختبارات' 'Cyan'
    $pyArgs = @('-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider')
    if ($py -eq 'py') { $pyArgs = @('-3') + $pyArgs }
    & $py @pyArgs
    if ($LASTEXITCODE -ne 0) { Revert; Fail 'فيه اختبار فشل — النشر اتلغى ورقم النسخة رجع زي ما كان. صلّح الغلط وجرّب تاني.' }
    Say '✓ كل الاختبارات عدّت' 'Green'
}

# ---------------------------------------------------------------- (٦) الرفع
try {
    $null = Invoke-Git @('add', '-A')
    # قفلٌ أخير على المحتوى: مفتاح دفع حيّ داخل أي ملف يوقف كل حاجة.
    $diff = ((Invoke-GitRaw @('diff', '--cached', '-U0')).Out -join "`n")
    if ($diff -match 'sk_live_[A-Za-z0-9]{8,}') {
        $null = Invoke-Git @('reset', '-q'); Revert
        Fail 'لقيت مفتاح دفع (sk_live_…) جوّه التعديلات — شيله من الملف. مفيش حاجة اترفعت.'
    }
    $null = Invoke-Git @('commit', '-q', '-m', "${new}: $desc")
    Say '… برفع على GitHub' 'Cyan'
    $null = Invoke-Git @('push', '-q', 'origin', 'HEAD:main')
    $null = Invoke-Git @('push', '-q', 'origin', "HEAD:refs/heads/v$new")
} catch {
    Fail "تعذّر الرفع: $_`nلو ده أول مرة، Git هيطلب تسجيل الدخول على GitHub — سجّل وجرّب تاني."
}
Say "✓ اترفعت النسخة $new — البناء بدأ على GitHub" 'Green'

# ---------------------------------------------------------------- (٧) الـRelease
$relPage = "https://github.com/$Owner/$RepoName/releases/tag/v$new"
$runsPage = "https://github.com/$Owner/$RepoName/actions"
Say "متابعة البناء: $runsPage"
if ($NoWait) { Say "الـRelease هيبقى هنا: $relPage"; exit 0 }
Say '… مستنّي الـRelease (حوالي ٤–٦ دقايق). تقدر تقفل الشباك — الـRelease هيتعمل لوحده.' 'Cyan'

$api = "https://api.github.com/repos/$Owner/$RepoName"
$deadline = (Get-Date).AddMinutes(25)
$i = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 45
    $i++
    try {
        $rel = Invoke-RestMethod -Uri "$api/releases/tags/v$new" -Headers @{ 'User-Agent' = 'onz-publish' } -TimeoutSec 20
        $zip = $rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
        if ($zip) {
            Say ''
            Say "✓ الـRelease جاهز: $relPage" 'Green'
            Say "  لينك التنزيل: $($zip.browser_download_url)" 'Green'
            try { Start-Process $relPage } catch { }
            exit 0
        }
    } catch { }
    if ($i % 3 -eq 0) {
        try {
            $runs = Invoke-RestMethod -Uri "$api/actions/runs?branch=v$new&per_page=1" -Headers @{ 'User-Agent' = 'onz-publish' } -TimeoutSec 20
            $r = $runs.workflow_runs | Select-Object -First 1
            if ($r -and $r.status -eq 'completed' -and $r.conclusion -ne 'success') {
                Fail "البناء فشل على GitHub — افتح: $($r.html_url)"
            }
        } catch { }
    }
    Write-Host '.' -NoNewline
}
Say ''
Say "لسه البناء ماخلصش — هيكمّل لوحده. تابعه من: $runsPage" 'Yellow'
