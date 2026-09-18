from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"


def test_android_shell_targets_current_play_baseline():
    gradle = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "compileSdk = 36" in gradle
    assert "targetSdk = 36" in gradle
    assert 'applicationId = "ru.r0meo1.turbot"' in gradle


def test_android_shell_blocks_cleartext_and_uses_https():
    manifest = (ANDROID / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    activity = (
        ANDROID
        / "app"
        / "src"
        / "main"
        / "java"
        / "ru"
        / "r0meo1"
        / "turbot"
        / "MainActivity.java"
    ).read_text(encoding="utf-8")

    assert 'android:usesCleartextTraffic="false"' in manifest
    assert "https://r0meo1.ru/apreltour/" in activity
    assert "setAllowFileAccess(false)" in activity
    assert "setAllowContentAccess(false)" in activity


def test_startio_dependency_is_pinned_and_disabled_by_default():
    gradle = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")

    assert 'implementation("com.startapp:inapp-sdk:5.3.1")' in gradle
    assert 'providers.gradleProperty("STARTIO_ENABLED")' in gradle
    assert ".orElse(false)" in gradle
    assert 'providers.gradleProperty("STARTIO_APP_ID")' in gradle
    assert '.orElse("")' in gradle


def test_startio_cannot_auto_initialize():
    manifest = (ANDROID / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8"
    )

    assert "com.startapp.sdk.adsbase.StartAppInitProvider" in manifest
    assert 'tools:node="remove"' in manifest
    assert "com.startapp.sdk.APPLICATION_ID" not in manifest
    assert 'android:name="com.startapp.sdk.SPLASH_ENABLED"' in manifest
    assert 'android:value="false"' in manifest
    assert 'android:name="com.startapp.sdk.RETURN_ADS_ENABLED"' in manifest


def test_startio_is_fail_closed_until_consent_is_recorded():
    manager = (
        ANDROID
        / "app"
        / "src"
        / "main"
        / "java"
        / "ru"
        / "r0meo1"
        / "turbot"
        / "StartIoManager.java"
    ).read_text(encoding="utf-8")

    assert "BuildConfig.STARTIO_ENABLED" in manager
    assert "BuildConfig.STARTIO_APP_ID" in manager
    assert "Decision.UNKNOWN" in manager
    assert "return false;" in manager
    assert 'StartAppSDK.setUserConsent(' in manager
