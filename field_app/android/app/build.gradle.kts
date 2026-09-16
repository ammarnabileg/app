plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "one.onz.onz_field"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "one.onz.field"

        // ٢٣ لا الافتراضي: أذونات وقت التشغيل (الموقع والكاميرا)
        // مضمونة من هذا الإصدار، والهواتف الأقدم لا تُستعمل ميدانيًّا.
        minSdk = 23
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    // مفتاح التوقيع لا يدخل المستودع: يُقرأ من android/key.properties
    // إن وُجد، وإلا يُوقَّع بمفتاح التجربة ليعمل `flutter run --release`.
    // ولا يُنشر بمفتاح تجربة — تحذيرٌ في README لا تعليقٌ هنا وحده.
    signingConfigs {
        create("release") {
            val props = java.util.Properties()
            val f = rootProject.file("key.properties")
            if (f.exists()) {
                props.load(f.inputStream())
                storeFile = file(props.getProperty("storeFile"))
                storePassword = props.getProperty("storePassword")
                keyAlias = props.getProperty("keyAlias")
                keyPassword = props.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = if (rootProject.file("key.properties").exists()) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
