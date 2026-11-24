package com.shawn.localvideotv;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.text.TextUtils;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

public final class MainActivity extends Activity {
    private static final String LOG_TAG = "LocalVideoTV";
    private static final String PREFERENCES = "local_video_tv";
    private static final String SERVER_URL = "server_url";
    private static final String SETTINGS_SCHEME = "localvideotv";

    private FrameLayout rootLayout;
    private WebView webView;
    private View fullScreenView;
    private WebChromeClient.CustomViewCallback fullScreenCallback;
    private SharedPreferences preferences;
    private String configuredUrl;
    private String errorPageUrl = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemBars();

        preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        configuredUrl = loadConfiguredUrl();

        rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(Color.BLACK);
        webView = createWebView();
        rootLayout.addView(
            webView,
            new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        );
        setContentView(rootLayout);
        webView.loadUrl(configuredUrl);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private WebView createWebView() {
        WebView view = new WebView(this);
        view.setBackgroundColor(Color.BLACK);
        view.setFocusable(true);
        view.setFocusableInTouchMode(true);
        view.requestFocus();

        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setUserAgentString(
            settings.getUserAgentString() + " LocalVideoTV/1.0"
        );

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(view, false);

        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(
                WebView webView,
                WebResourceRequest request
            ) {
                return handleNavigation(request.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView webView, String url) {
                return handleNavigation(Uri.parse(url));
            }

            @Override
            public void onPageStarted(WebView webView, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(webView, url, favicon);
                if (!url.startsWith("data:")) {
                    errorPageUrl = "";
                }
            }

            @Override
            public void onPageFinished(WebView webView, String url) {
                super.onPageFinished(webView, url);
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    injectTvMode();
                }
            }

            @Override
            public void onReceivedError(
                WebView webView,
                WebResourceRequest request,
                WebResourceError error
            ) {
                super.onReceivedError(webView, request, error);
                if (request.isForMainFrame()) {
                    showNetworkError(request.getUrl().toString(), error.getDescription().toString());
                }
            }
        });

        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (fullScreenView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                fullScreenView = view;
                fullScreenCallback = callback;
                webView.setVisibility(View.GONE);
                view.setBackgroundColor(Color.BLACK);
                rootLayout.addView(
                    view,
                    new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                    )
                );
                hideSystemBars();
            }

            @Override
            public void onHideCustomView() {
                hideFullScreenView();
            }
        });

        return view;
    }

    private String loadConfiguredUrl() {
        String saved = preferences.getString(SERVER_URL, "");
        String normalized = normalizeUrl(saved);
        return normalized == null ? BuildConfig.DEFAULT_URL : normalized;
    }

    private String normalizeUrl(String value) {
        String target = value == null ? "" : value.trim();
        if (target.isEmpty()) {
            return null;
        }
        if (!target.matches("^[a-zA-Z][a-zA-Z0-9+.-]*://.*$")) {
            target = "http://" + target;
        }
        Uri uri = Uri.parse(target);
        String scheme = uri.getScheme() == null
            ? ""
            : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!("http".equals(scheme) || "https".equals(scheme)) || TextUtils.isEmpty(uri.getHost())) {
            return null;
        }
        if (TextUtils.isEmpty(uri.getPath())) {
            target += "/";
        }
        return target;
    }

    private boolean handleNavigation(Uri target) {
        String scheme = target.getScheme() == null
            ? ""
            : target.getScheme().toLowerCase(Locale.ROOT);
        if (SETTINGS_SCHEME.equals(scheme)) {
            if ("settings".equals(target.getHost())) {
                showServerDialog();
            }
            return true;
        }
        if (!("http".equals(scheme) || "https".equals(scheme))) {
            return true;
        }
        if (sameOrigin(target, Uri.parse(configuredUrl))) {
            return false;
        }
        Toast.makeText(
            this,
            "已阻止跳转到其他网站",
            Toast.LENGTH_SHORT
        ).show();
        return true;
    }

    private boolean sameOrigin(Uri first, Uri second) {
        return first.getScheme() != null
            && second.getScheme() != null
            && first.getScheme().equalsIgnoreCase(second.getScheme())
            && TextUtils.equals(first.getHost(), second.getHost())
            && effectivePort(first) == effectivePort(second);
    }

    private int effectivePort(Uri uri) {
        if (uri.getPort() != -1) {
            return uri.getPort();
        }
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
    }

    private void injectTvMode() {
        try (InputStream stream = getAssets().open("tv-mode.js");
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            while ((read = stream.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            String script = output.toString(StandardCharsets.UTF_8.name());
            webView.evaluateJavascript(script, ignored -> webView.postDelayed(() -> {
                webView.evaluateJavascript(
                    "JSON.stringify({"
                        + "title:document.title,"
                        + "url:location.href,"
                        + "tvMode:document.body.classList.contains('tv-shell'),"
                        + "active:(document.activeElement&&("
                        + "document.activeElement.tagName+'#'+document.activeElement.id"
                        + "+'.'+document.activeElement.className))||'',"
                        + "searchTabIndex:(function(){var q=document.querySelector("
                        + "\".topbar-search input[type='search'],input[type='search'][name='q']\");"
                        + "return q?q.tabIndex:null;})(),"
                        + "focusable:document.querySelectorAll("
                        + "\"a[href],button,input,select,[tabindex]\").length"
                        + "})",
                    state -> Log.i(LOG_TAG, "Page ready " + state)
                );
            }, 350));
        } catch (Exception exception) {
            Toast.makeText(this, "无法启用电视遥控器增强模式", Toast.LENGTH_SHORT).show();
        }
    }

    private void showNetworkError(String failedUrl, String detail) {
        if (failedUrl.equals(errorPageUrl)) {
            return;
        }
        errorPageUrl = failedUrl;
        String safeUrl = TextUtils.htmlEncode(failedUrl);
        String safeDetail = TextUtils.htmlEncode(
            detail == null || detail.isEmpty() ? "无法连接服务器" : detail
        );
        String html = "<!doctype html><html lang=\"zh-CN\"><head>"
            + "<meta charset=\"utf-8\">"
            + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            + "<style>"
            + "html,body{margin:0;min-height:100%;background:#0b0d12;color:#f4f6fb;"
            + "font-family:sans-serif}main{max-width:920px;margin:0 auto;padding:150px 48px}"
            + "h1{font-size:54px;margin:0 0 20px}p{font-size:24px;line-height:1.65;color:#b7bcc8}"
            + "code{color:#ff8796;word-break:break-all}"
            + "button{margin-top:34px;padding:20px 44px;border:0;border-radius:14px;"
            + "background:#ff4f64;color:white;font-size:24px;font-weight:700}"
            + "button:focus{outline:5px solid white;outline-offset:6px}</style></head><body><main>"
            + "<h1>视频服务器暂时无法访问</h1>"
            + "<p>请确认 Mac 上的视频站已经运行，电视和 Mac 位于同一局域网。</p>"
            + "<p><code>" + safeUrl + "</code></p>"
            + "<p>错误：<code>" + safeDetail + "</code></p>"
            + "<button autofocus onclick=\"location.href='" + safeUrl + "'\">重新连接</button>"
            + "</main></body></html>";
        webView.loadDataWithBaseURL(failedUrl, html, "text/html", "UTF-8", null);
    }

    private void showServerDialog() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(configuredUrl);
        input.setSelection(input.getText().length());
        input.setTextSize(20);
        input.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_VARIATION_URI
                | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        );
        input.setHint(BuildConfig.DEFAULT_URL);

        FrameLayout container = new FrameLayout(this);
        int padding = dp(28);
        container.setPadding(padding, dp(10), padding, 0);
        container.addView(
            input,
            new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        );

        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("视频服务器地址")
            .setMessage("例如 " + BuildConfig.DEFAULT_URL)
            .setView(container)
            .setPositiveButton("连接", null)
            .setNeutralButton("恢复默认", null)
            .setNegativeButton("取消", null)
            .create();

        dialog.setOnShowListener(ignored -> {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
                String normalized = normalizeUrl(input.getText().toString());
                if (normalized == null) {
                    input.setError("请输入有效的 http:// 或 https:// 地址");
                    return;
                }
                saveAndOpen(normalized);
                dialog.dismiss();
            });
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(button -> {
                saveAndOpen(BuildConfig.DEFAULT_URL);
                dialog.dismiss();
            });
            input.requestFocus();
            if (dialog.getWindow() != null) {
                dialog.getWindow().setSoftInputMode(
                    WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
                );
            }
        });
        dialog.show();
    }

    private void saveAndOpen(String url) {
        configuredUrl = url;
        preferences.edit().putString(SERVER_URL, url).apply();
        errorPageUrl = "";
        webView.loadUrl(url);
        webView.requestFocus();
    }

    private void sendWebKey(String key) {
        String quotedKey = JSONObject.quote(key);
        String script = "(function(){var e=document.activeElement||document.body;"
            + "e.dispatchEvent(new KeyboardEvent('keydown',{key:" + quotedKey
            + ",bubbles:true,cancelable:true}));})();";
        webView.evaluateJavascript(script, null);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int keyCode = event.getKeyCode();
        boolean handledKey = keyCode == KeyEvent.KEYCODE_MENU
            || keyCode == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
            || keyCode == KeyEvent.KEYCODE_MEDIA_FAST_FORWARD
            || keyCode == KeyEvent.KEYCODE_MEDIA_REWIND;

        if (handledKey) {
            if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0) {
                if (keyCode == KeyEvent.KEYCODE_MENU) {
                    showServerDialog();
                } else if (keyCode == KeyEvent.KEYCODE_MEDIA_REWIND) {
                    sendWebKey("j");
                } else if (keyCode == KeyEvent.KEYCODE_MEDIA_FAST_FORWARD) {
                    sendWebKey("l");
                } else {
                    sendWebKey(" ");
                }
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    public void onBackPressed() {
        if (fullScreenView != null) {
            hideFullScreenView();
            return;
        }
        if (webView == null) {
            super.onBackPressed();
            return;
        }
        webView.evaluateJavascript(
            "(function(){"
                + "var a=document.activeElement;"
                + "if(a&&a.matches('input,textarea,select,[contenteditable=true]')){"
                + "a.blur();"
                + "var t=document.querySelector('.video-card-media,.episode-nav-button,"
                + ".season-episode-button,.category-button,a[href],button');"
                + "if(t){t.focus();}"
                + "return true;"
                + "}"
                + "return false;"
                + "})()",
            result -> runOnUiThread(() -> {
                if ("true".equals(result)) {
                    return;
                }
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    finish();
                }
            })
        );
    }

    private void hideFullScreenView() {
        if (fullScreenView == null) {
            return;
        }
        rootLayout.removeView(fullScreenView);
        fullScreenView = null;
        webView.setVisibility(View.VISIBLE);
        webView.requestFocus();
        if (fullScreenCallback != null) {
            fullScreenCallback.onCustomViewHidden();
            fullScreenCallback = null;
        }
        hideSystemBars();
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        );
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemBars();
        if (webView != null) {
            webView.onResume();
            webView.requestFocus();
        }
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        if (webView != null) {
            webView.onPause();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (fullScreenView != null) {
            rootLayout.removeView(fullScreenView);
            fullScreenView = null;
        }
        if (webView != null) {
            webView.stopLoading();
            rootLayout.removeView(webView);
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemBars();
        }
    }
}
