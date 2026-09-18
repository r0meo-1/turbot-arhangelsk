package ru.r0meo1.turbot;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public final class MainActivity extends Activity {
    private static final String APP_URL =
            "https://r0meo1.ru/apreltour/?utm_source=android&utm_medium=app&utm_campaign=turbot_native";
    private static final String FIRST_PARTY_HOST = "r0meo1.ru";

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.webview);
        configureWebView(webView);

        Button privacyButton = findViewById(R.id.privacy_button);
        configurePrivacyControls(privacyButton);

        if (savedInstanceState == null) {
            webView.loadUrl(APP_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    private void configurePrivacyControls(Button privacyButton) {
        if (!StartIoManager.isConfigured()) {
            privacyButton.setVisibility(View.GONE);
            return;
        }

        privacyButton.setVisibility(View.VISIBLE);
        privacyButton.setOnClickListener(
                view -> AdPrivacyDialog.show(this, false, this::applyAdvertisingChoice)
        );

        if (AdConsentStore.read(this) == AdConsentStore.Decision.UNKNOWN) {
            AdPrivacyDialog.show(this, true, this::applyAdvertisingChoice);
        } else {
            StartIoManager.initializeIfAllowed(this);
        }
    }

    private void applyAdvertisingChoice() {
        StartIoManager.applyCurrentConsent(this);
    }

    private void configureWebView(WebView view) {
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);

        view.setWebChromeClient(new WebChromeClient());
        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isFirstPartyHttps(uri)) {
                    return false;
                }

                Intent external = new Intent(Intent.ACTION_VIEW, uri);
                startActivity(external);
                return true;
            }
        });
    }

    private boolean isFirstPartyHttps(Uri uri) {
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (!"https".equalsIgnoreCase(scheme) || host == null) {
            return false;
        }
        return host.equals(FIRST_PARTY_HOST) || host.endsWith("." + FIRST_PARTY_HOST);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (webView != null) {
            webView.saveState(outState);
        }
        super.onSaveInstanceState(outState);
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
