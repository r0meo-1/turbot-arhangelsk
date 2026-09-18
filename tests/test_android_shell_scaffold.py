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


def test_startio_is_not_enabled_without_real_app_id_and_consent():
    all_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in ANDROID.rglob("*")
        if p.is_file() and p.suffix in {".java", ".kt", ".kts", ".xml", ".properties"}
    )
    assert "com.startapp:inapp-sdk" not in all_text
    assert "com.startapp.sdk.APPLICATION_ID" not in all_text
