package ru.r0meo1.turbot;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.graphics.Typeface;
import android.text.Html;
import android.text.method.LinkMovementMethod;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

final class AdPrivacyDialog {
    private static final String PRIVACY_URL =
            "https://www.start.io/policy/privacy-policy/";
    private static final String SERVICES_URL =
            "https://www.start.io/startio-services/";
    private static final String PARTNERS_URL =
            "https://www.start.io/startio-data-partners-list/";

    private AdPrivacyDialog() {}

    static void show(
            Activity activity,
            boolean decisionRequired,
            Runnable onDecision
    ) {
        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = dp(activity, 24);
        content.setPadding(padding, dp(activity, 8), padding, 0);

        TextView explanation = new TextView(activity);
        explanation.setText(R.string.ad_privacy_message);
        explanation.setTextSize(16);
        content.addView(explanation);

        TextView links = new TextView(activity);
        links.setPadding(0, dp(activity, 16), 0, 0);
        links.setTextSize(14);
        links.setText(Html.fromHtml(
                "<a href=\"" + PRIVACY_URL + "\">Политика Start.io</a>"
                        + " · <a href=\"" + SERVICES_URL + "\">Условия сервиса</a>"
                        + " · <a href=\"" + PARTNERS_URL + "\">Партнёры данных</a>",
                Html.FROM_HTML_MODE_LEGACY
        ));
        links.setMovementMethod(LinkMovementMethod.getInstance());
        content.addView(links);

        TextView equalService = new TextView(activity);
        equalService.setPadding(0, dp(activity, 16), 0, 0);
        equalService.setText(R.string.ad_privacy_equal_service);
        equalService.setTypeface(null, Typeface.BOLD);
        content.addView(equalService);

        AlertDialog dialog = new AlertDialog.Builder(activity)
                .setTitle(R.string.ad_privacy_title)
                .setView(content)
                .setPositiveButton(R.string.ad_privacy_agree, null)
                .setNegativeButton(R.string.ad_privacy_disagree, null)
                .setCancelable(!decisionRequired)
                .create();

        dialog.setOnShowListener(ignored -> {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                AdConsentStore.write(activity, AdConsentStore.Decision.GRANTED);
                onDecision.run();
                dialog.dismiss();
            });
            dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(view -> {
                AdConsentStore.write(activity, AdConsentStore.Decision.DENIED);
                onDecision.run();
                dialog.dismiss();
            });
        });

        if (decisionRequired) {
            dialog.setCanceledOnTouchOutside(false);
        }

        dialog.show();
    }

    private static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }
}
