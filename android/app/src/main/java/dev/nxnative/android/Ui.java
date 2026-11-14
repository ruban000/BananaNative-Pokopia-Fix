package dev.nxnative.android;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.media.AudioManager;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

/**
 * The launcher's building blocks, in code like the rest of the launcher (no layouts, no AndroidX), in the banana palette (res/values/colors.xml, values-night for dark
 * mode; themes.xml's Theme.Banana). One small design system so every screen looks the same:
 * <ul>
 * <li>Type: page title 24 sp heavy, heading 18 sp bold, body 15 sp, small 13 sp, section label 12 sp caps; buttons 15 sp semibold, one line.</li>
 * <li>Shapes: cards 20 dp corners with a hairline edge; buttons and icon buttons 14 dp corners (a card's big action 16 dp).</li>
 * <li>Spacing: every page has the same gutter (20 dp, 16 dp on a narrow screen), the same header (Ui.header: 56 dp, 8 dp under the status bar); cards 18 dp inside,
 * 12 dp between cards, 24 dp before a section label.</li>
 * <li>Button and header icons are vector drawables (res/drawable/ic_*.xml) tinted from the palette, so they look the same on every phone; list rows keep their
 * emoji tiles.</li>
 * </ul>
 */
final class Ui {
    private Ui() {}

    enum Kind { PRIMARY, TONAL, SOFT, DANGER, QUIET }   // SOFT: an outlined button for use on a tinted surface

    static final int GAP = 12, SECTION_GAP = 24;

    static int dp(Context c, float v) { return Math.round(v * c.getResources().getDisplayMetrics().density); }

    // Two crisp, high-pitched ticks with a fast decay: a light console-style UI response.
    private static AudioTrack uiTickTrack;
    private static long lastClickAt;
    private static synchronized void click(View view) {
        long now = android.os.SystemClock.uptimeMillis();
        if (now - lastClickAt < 55) return;
        lastClickAt = now;
        try {
            if (uiTickTrack == null) {
                final int rate = 44100;
                short[] samples = new short[(int) (rate * 0.085f)];
                for (int i = 0; i < samples.length; i++) {
                    float t = i / (float) rate;
                    float local;
                    float frequency;
                    if (t < 0.010f) { local = t; frequency = 2850f; }
                    else if (t >= 0.032f && t < 0.044f) { local = t - 0.032f; frequency = 3450f; }
                    else continue;
                    float envelope = (float) Math.exp(-local * 360f);
                    float wave = (float) Math.sin(2.0 * Math.PI * frequency * local);
                    samples[i] = (short) (wave * envelope * 5200f);
                }
                uiTickTrack = new AudioTrack(AudioManager.STREAM_MUSIC, rate,
                        AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
                        samples.length * 2, AudioTrack.MODE_STATIC);
                uiTickTrack.write(samples, 0, samples.length);
                uiTickTrack.setVolume(0.55f);
            }
            if (uiTickTrack.getPlayState() == AudioTrack.PLAYSTATE_PLAYING) uiTickTrack.stop();
            uiTickTrack.setPlaybackHeadPosition(0);
            uiTickTrack.play();
        } catch (RuntimeException ignored) {
            // Sound is optional; never interrupt navigation if audio is unavailable.
        }
    }
    static int col(Context c, int res) { return c.getColor(res); }

    static GradientDrawable round(int fill, float radiusPx, int stroke, int strokePx) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(radiusPx);
        if (strokePx > 0) g.setStroke(strokePx, stroke);
        return g;
    }

    /** A touch ripple in peel brown over a shape (the shape is its mask) */
    static Drawable ripple(Context c, GradientDrawable shape) {
        GradientDrawable mask = new GradientDrawable();   // an opaque copy of the shape: a transparent button (QUIET) still shows its ripple
        mask.setColor(Color.BLACK);
        mask.setCornerRadius(shape.getCornerRadius());
        return new RippleDrawable(ColorStateList.valueOf(0x336B4423), shape, mask);
    }

    /** Keep the scaled focus ring inside a 16dp viewport gutter, even on narrow screens. */
    private static float safeFocusScaleX(View view, Context c, float requested) {
        if (view.getWidth() <= 0 || view.getParent() == null) return 1f;
        View root = view.getRootView();
        int[] pos = new int[2], rootPos = new int[2];
        view.getLocationOnScreen(pos);
        root.getLocationOnScreen(rootPos);
        float center = pos[0] + view.getWidth() * 0.5f;
        float gutter = dp(c, 16);
        float availableHalf = Math.min(center - (rootPos[0] + gutter),
                rootPos[0] + root.getWidth() - gutter - center);
        float safe = (2f * availableHalf) / view.getWidth();
        return Math.max(1f, Math.min(requested, safe));
    }

    /** PS5-style focus for settings rows: an inset ring leaves a clear 3dp glass gap. */
    static void settingFocus(Context c, View v, float radiusDp) {
        final Drawable normalBackground = v.getBackground();
        v.setFocusable(true);
        v.setFocusableInTouchMode(true);
        if (v instanceof ViewGroup) {
            ((ViewGroup) v).setClipChildren(false);
            ((ViewGroup) v).setClipToPadding(false);
        }
        v.setOnTouchListener((view, event) -> {
            if (event.getAction() == android.view.MotionEvent.ACTION_DOWN) {
                view.requestFocus();
                view.animate().cancel();
                view.animate().scaleX(0.97f).scaleY(0.97f).setDuration(85)
                        .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
            } else if (event.getAction() == android.view.MotionEvent.ACTION_UP
                    || event.getAction() == android.view.MotionEvent.ACTION_CANCEL) {
                view.animate().cancel();
                view.animate().scaleX(view.hasFocus() ? 1.03f : 1f)
                        .scaleY(view.hasFocus() ? 1.03f : 1f).setDuration(160)
                        .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
            }
            return false;
        });
        v.setOnFocusChangeListener((view, focused) -> {
            Drawable current = view.getForeground();
            if (current instanceof SettingFocusDrawable) ((SettingFocusDrawable) current).stop();
            view.animate().cancel();
            if (focused) {
                GradientDrawable glass = round(0x14FFFFFF, dp(c, radiusDp), 0, 0);
                view.setBackground(new RippleDrawable(ColorStateList.valueOf(0x22FFFFFF),
                        glass, round(Color.WHITE, dp(c, radiusDp), 0, 0)));
                SettingFocusDrawable highlight = new SettingFocusDrawable(dp(c, radiusDp), dp(c, 2), dp(c, 3));
                view.setForeground(highlight);
                highlight.start();
                view.setPivotX(view.getWidth() * 0.5f);
                float safeScale = safeFocusScaleX(view, c, 1.03f);
                view.animate().scaleX(safeScale).scaleY(1.03f).setDuration(220)
                        .setInterpolator(new android.view.animation.PathInterpolator(0.2f, 0.8f, 0.2f, 1f)).start();
                view.setElevation(dp(c, 8));
                if (view instanceof ViewGroup) {
                    ViewGroup group = (ViewGroup) view;
                    for (int i = 0; i < group.getChildCount(); i++) {
                        View child = group.getChildAt(i);
                        if (child instanceof ImageView) {
                            child.animate().translationX(dp(c, 4)).setDuration(180)
                                    .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
                            child.setAlpha(1f);
                        }
                    }
                    if (group.getChildCount() > 0) {
                        View iconTile = group.getChildAt(0);
                        iconTile.setBackground(round(0x44FFFFFF, dp(c, 11), 0, 0));
                    }
                }
            } else {
                view.setBackground(normalBackground);
                view.animate().scaleX(1f).scaleY(1f).setDuration(180)
                        .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
                view.setElevation(0f);
                if (view instanceof ViewGroup) {
                    ViewGroup group = (ViewGroup) view;
                    for (int i = 0; i < group.getChildCount(); i++) {
                        View child = group.getChildAt(i);
                        child.animate().translationX(0).setDuration(160).start();
                    }
                    if (group.getChildCount() > 0) {
                        View iconTile = group.getChildAt(0);
                        iconTile.setBackground(round(0x00000000, dp(c, 11), 0, 0));
                    }
                }
                view.post(() -> {
                    if (!view.hasFocus()) {
                        Drawable fg = view.getForeground();
                        if (fg instanceof SettingFocusDrawable) {
                            ((SettingFocusDrawable) fg).stop();
                            view.setForeground(null);
                        }
                    }
                });
            }
        });
    }

    /** A 2dp white focus edge, soft ambient halo, and 45-degree sheen; both animations are frame-paced. */
    private static final class SettingFocusDrawable extends Drawable implements android.graphics.drawable.Animatable {
        private final android.graphics.Paint edge = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        private final android.graphics.Paint shimmer = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        private final android.graphics.RectF bounds = new android.graphics.RectF();
        private final android.graphics.Path pill = new android.graphics.Path();
        private final android.graphics.Matrix shimmerMatrix = new android.graphics.Matrix();
        private android.graphics.LinearGradient sheen;
        private final float radius, stroke, gap;
        private final android.animation.ValueAnimator sheenAnimator;
        private final android.animation.ValueAnimator fadeAnimator;
        private float phase;
        private float focusAlpha;

        SettingFocusDrawable(float radius, float stroke, float gap) {
            this.radius = radius;
            this.stroke = stroke;
            this.gap = gap;
            edge.setStyle(android.graphics.Paint.Style.STROKE);
            edge.setStrokeWidth(stroke);
            shimmer.setStyle(android.graphics.Paint.Style.FILL);

            sheenAnimator = android.animation.ValueAnimator.ofFloat(-1.25f, 1.25f);
            sheenAnimator.setDuration(2700);
            sheenAnimator.setRepeatCount(android.animation.ValueAnimator.INFINITE);
            sheenAnimator.setInterpolator(new android.view.animation.LinearInterpolator());
            sheenAnimator.addUpdateListener(a -> {
                phase = (Float) a.getAnimatedValue();
                invalidateSelf();
            });

            fadeAnimator = android.animation.ValueAnimator.ofFloat(0f, 1f);
            fadeAnimator.setDuration(200);
            fadeAnimator.setInterpolator(new android.view.animation.DecelerateInterpolator());
            fadeAnimator.addUpdateListener(a -> {
                focusAlpha = (Float) a.getAnimatedValue();
                invalidateSelf();
            });
        }

        @Override public void draw(android.graphics.Canvas canvas) {
            android.graphics.Rect b = getBounds();
            float inset = gap + stroke * 0.5f;
            bounds.set(b.left + inset, b.top + inset, b.right - inset, b.bottom - inset);
            if (bounds.width() <= 0 || bounds.height() <= 0) return;
            pill.reset();
            pill.addRoundRect(bounds, radius, radius, android.graphics.Path.Direction.CW);

            int save = canvas.save();
            canvas.clipPath(pill);

            // A faint 45-degree white streak sweeps left-to-right every 2.7 seconds.
            float w = bounds.width(), h = bounds.height();
            if (sheen == null) {
                sheen = new android.graphics.LinearGradient(
                        -h, 0, 0, h,
                        new int[]{0x00FFFFFF, 0x0AFFFFFF, 0x20FFFFFF, 0x0AFFFFFF, 0x00FFFFFF},
                        new float[]{0f, 0.38f, 0.5f, 0.62f, 1f},
                        android.graphics.Shader.TileMode.CLAMP);
                shimmer.setShader(sheen);
            }
            shimmer.setAlpha(Math.round(255f * focusAlpha));
            float travel = (phase + 1.25f) * (w + h) * 0.5f - h;
            shimmerMatrix.setTranslate(bounds.left + travel, bounds.top);
            sheen.setLocalMatrix(shimmerMatrix);
            canvas.drawRect(bounds, shimmer);
            canvas.restoreToCount(save);

            // The ambient halo is drawn first; the clean solid white 2dp edge stays sharp.
            int borderAlpha = Math.round(242f * focusAlpha);
            edge.setColor(android.graphics.Color.argb(borderAlpha, 255, 255, 255));
            edge.setShadowLayer(stroke * 3.0f, 0, 0,
                    android.graphics.Color.argb(Math.round(112f * focusAlpha), 255, 255, 255));
            canvas.drawRoundRect(bounds, radius, radius, edge);
            edge.clearShadowLayer();
        }

        @Override protected void onBoundsChange(android.graphics.Rect b) {
            sheen = null; // Rebuild the gradient only when the row's measured size changes.
            shimmer.setShader(null);
            invalidateSelf();
        }
        @Override public void setAlpha(int alpha) { edge.setAlpha(alpha); shimmer.setAlpha(alpha); invalidateSelf(); }
        @Override public void setColorFilter(android.graphics.ColorFilter filter) { edge.setColorFilter(filter); shimmer.setColorFilter(filter); invalidateSelf(); }
        @Override public int getOpacity() { return android.graphics.PixelFormat.TRANSLUCENT; }
        @Override public void start() {
            if (!sheenAnimator.isStarted()) sheenAnimator.start();
            fadeAnimator.cancel();
            fadeAnimator.setFloatValues(focusAlpha, 1f);
            fadeAnimator.start();
        }
        @Override public void stop() { sheenAnimator.cancel(); fadeAnimator.cancel(); }
        @Override public boolean isRunning() { return sheenAnimator.isRunning() || fadeAnimator.isRunning(); }
    }

    /** Shared focus treatment for tabs and navigation items. */
    static void focusMotion(Context c, View v, float radiusDp) {
        v.setFocusable(true);
        v.setFocusableInTouchMode(true);
        v.setOnTouchListener((view, event) -> {
            if (event.getAction() == android.view.MotionEvent.ACTION_DOWN) view.requestFocus();
            return false;
        });
        v.setOnFocusChangeListener((view, focused) -> {
            view.animate().cancel();
            if (focused) {
                SettingFocusDrawable border = new SettingFocusDrawable(dp(c, radiusDp), dp(c, 2), dp(c, 3));
                view.setForeground(border);
                border.start();
                view.animate().scaleX(1.006f).scaleY(1.006f).setDuration(220)
                        .setInterpolator(new android.view.animation.DecelerateInterpolator()).start();
            } else {
                Drawable current = view.getForeground();
                if (current instanceof SettingFocusDrawable) ((SettingFocusDrawable) current).stop();
                view.animate().scaleX(1f).scaleY(1f).setDuration(170)
                        .withEndAction(() -> { if (!view.hasFocus()) view.setForeground(null); }).start();
            }
        });
    }

    /** Let focus effects draw outside every containing layout, not just the focused control. */
    private static void allowFocusOverflow(View v) {
        if (v instanceof ViewGroup) {
            ((ViewGroup) v).setClipChildren(false);
            ((ViewGroup) v).setClipToPadding(false);
        }
        android.view.ViewParent parent = v.getParent();
        while (parent instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) parent;
            group.setClipChildren(false);
            group.setClipToPadding(false);
            parent = group.getParent();
        }
    }

    /** Banana Native focus: a moving white highlight travels around the focused outline. */
    static void ps5Focus(Context c, View v, float radiusDp) {
        v.setFocusable(true);
        v.setFocusableInTouchMode(true);
        // Standard buttons and action controls all get the same unclipped focus halo.
        allowFocusOverflow(v);
        v.setOnTouchListener((view, event) -> {
            if (event.getAction() == android.view.MotionEvent.ACTION_DOWN) view.requestFocus();
            return false;
        });
        v.setOnFocusChangeListener((view, focused) -> {
            Drawable current = view.getForeground();
            if (current instanceof SettingFocusDrawable) ((SettingFocusDrawable) current).stop();
            view.animate().cancel();
            if (focused) {
                SettingFocusDrawable border = new SettingFocusDrawable(dp(c, radiusDp), dp(c, 2), dp(c, 3));
                view.setForeground(border);
                border.start();
                view.setElevation(dp(c, 8));
                view.setPivotX(view.getWidth() * 0.5f);
                float safeScale = safeFocusScaleX(view, c, 1.025f);
                view.animate().translationY(-dp(c, 3)).scaleX(safeScale).scaleY(1.025f)
                        .setDuration(220).setInterpolator(new android.view.animation.PathInterpolator(0.2f, 0.8f, 0.2f, 1f)).start();
            } else {
                view.animate().translationY(0).scaleX(1f).scaleY(1f).setDuration(180)
                        .withEndAction(() -> {
                            if (!view.hasFocus()) {
                                Drawable fg = view.getForeground();
                                if (fg instanceof SettingFocusDrawable) {
                                    ((SettingFocusDrawable) fg).stop();
                                    view.setForeground(null);
                                }
                            }
                        }).start();
                view.setElevation(0f);
            }
        });
    }

    static Typeface face(int weight) { return Typeface.create(Typeface.SANS_SERIF, weight, false); }

    /** A vector icon (res/drawable/ic_*.xml) tinted with a palette color */
    static Drawable icon(Context c, int drawable, int colorRes) {
        Drawable d = c.getDrawable(drawable).mutate();
        d.setTint(col(c, colorRes));
        return d;
    }

    // ---- Text ----

    static TextView text(Context c, CharSequence s, float sp, int colorRes, int weight) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(col(c, colorRes));
        t.setTypeface(face(weight));
        t.setLineSpacing(0, 1.1f);
        return t;
    }

    static TextView title(Context c, CharSequence s) {
        TextView t = text(c, s, 24, R.color.bn_text, 800);
        t.setLetterSpacing(-0.01f);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        return t;
    }
    static TextView heading(Context c, CharSequence s) { return text(c, s, 18, R.color.bn_text, 700); }
    static TextView body(Context c, CharSequence s) { return text(c, s, 15, R.color.bn_text, 400); }
    static TextView small(Context c, CharSequence s) { return text(c, s, 13, R.color.bn_text_2, 400); }

    /** A rounded little label (the build tag, a step) */
    static TextView chip(Context c, CharSequence s) {
        TextView t = text(c, s, 12, R.color.bn_text_2, 600);
        t.setBackground(round(col(c, R.color.bn_tint), dp(c, 999), 0, 0));
        t.setPadding(dp(c, 10), dp(c, 5), dp(c, 10), dp(c, 5));
        return t;
    }

    /** A group's label above its card (the settings page, a sheet) */
    static TextView section(Context c, CharSequence s) {
        TextView t = text(c, s, 12, R.color.bn_text_2, 700);
        t.setAllCaps(true);
        t.setLetterSpacing(0.08f);
        t.setPadding(dp(c, 4), 0, 0, 0);
        t.setAccessibilityHeading(true);
        return t;
    }

    /** A short state: a colored dot and a word (Ready, Shaders needed, …) */
    static TextView status(Context c, CharSequence s, int colorRes) {
        TextView t = text(c, "", 13, colorRes, 600);
        status(t, s, colorRes);
        return t;
    }

    static void status(TextView t, CharSequence s, int colorRes) {
        t.setText("●  " + s);
        t.setContentDescription(s);   // the dot is decoration: a screen reader says the state only
        t.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);   // the state changes while the screen shows (Checking… → Ready): said when it does
        t.setTextColor(col(t.getContext(), colorRes));
    }

    /**
     * Details, closed by default: a "Details" text button that opens the technical part of a message (file paths, a tool's last error line, build IDs, the logs'
     * folder) as selectable text with Copy, so the main text stays short and plain and the rest can still be read, copied and sent with a report. set("") hides it all
     */
    static final class Details extends LinearLayout {
        final Button toggle;
        final TextView text;
        final Button copy;

        Details(Context c) {
            super(c);
            setOrientation(VERTICAL);
            toggle = button(c, "Details", Kind.QUIET, v -> open(!isOpen()));
            toggle.setPadding(dp(c, 4), 0, dp(c, 4), 0);
            addView(toggle, wrap());
            text = small(c, "");
            text.setTextIsSelectable(true);
            text.setVisibility(GONE);
            addView(text, fill());
            copy = button(c, "Copy details", Kind.QUIET, v -> {
                android.content.ClipboardManager cm = c.getSystemService(android.content.ClipboardManager.class);
                if (cm != null) cm.setPrimaryClip(android.content.ClipData.newPlainText("Banana Native details", text.getText()));
                android.widget.Toast.makeText(c, "Details copied", android.widget.Toast.LENGTH_SHORT).show();
            });
            copy.setPadding(dp(c, 4), 0, dp(c, 4), 0);
            copy.setVisibility(GONE);
            addView(copy, wrap());
            open(false);
        }

        boolean isOpen() { return text.getVisibility() == VISIBLE; }

        void open(boolean on) {
            text.setVisibility(on ? VISIBLE : GONE);
            copy.setVisibility(on ? VISIBLE : GONE);
            toggle.setText(on ? "Hide details" : "Details");
            toggle.setContentDescription(on ? "Hide details" : "Show details");
        }

        /** The details to show (the view hides when there are none); an open view stays open */
        Details set(CharSequence s) {
            boolean none = s == null || s.length() == 0;
            if (!none && !s.toString().contentEquals(text.getText())) text.setText(s);
            setVisibility(none ? GONE : VISIBLE);
            return this;
        }
    }

    static Details details(Context c, CharSequence s) { return new Details(c).set(s); }

    /**
     * A message with its details apart: the plain text (what happened, what to do) and Details below it (closed). The caller adds the buttons; with none it gets OK
     */
    static AlertDialog.Builder explain(Activity a, CharSequence title, CharSequence message, CharSequence detail) {
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(body(a, message));
        if (detail != null && detail.length() > 0) add(box, details(a, detail), 8);
        return sheet(a, title, box);
    }

    /** A small warning chip ("! Pack from other tools") whose tap shows the whole sentence in a small dialog */
    static TextView warnChip(Activity a, CharSequence s, CharSequence title, CharSequence message) {
        TextView t = text(a, "!  " + s, 12, R.color.bn_text, 600);
        t.setBackground(ripple(a, round(col(a, R.color.bn_warn_tint), dp(a, 999), 0, 0)));
        t.setPadding(dp(a, 10), dp(a, 5), dp(a, 12), dp(a, 5));
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setContentDescription(s + ": tap for details");
        t.setOnClickListener(v -> new AlertDialog.Builder(a).setTitle(title).setMessage(message).setPositiveButton("OK", null).show());
        return t;
    }

    /** A game's tile: the first letter of its name on banana yellow, rounded */
    static TextView tile(Context c, String name, int sizeDp) {
        String n = name == null ? "" : name.trim();
        TextView t = text(c, n.isEmpty() ? "?" : n.substring(0, n.offsetByCodePoints(0, 1)).toUpperCase(java.util.Locale.ROOT), sizeDp * 0.4f, R.color.bn_on_banana, 800);
        t.setGravity(Gravity.CENTER);
        t.setIncludeFontPadding(false);
        t.setBackground(round(col(c, R.color.bn_banana), dp(c, 16), 0, 0));
        t.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        t.setLayoutParams(new LinearLayout.LayoutParams(dp(c, sizeDp), dp(c, sizeDp)));
        return t;
    }

    /** Shows a picture (the game's icon) in a tile made by tile(), in place of its letter and with the same rounded corners. Pass null to get the letter back */
    static void tileIcon(TextView t, android.graphics.Bitmap b) {
        Context c = t.getContext();
        if (b == null) { t.setTextColor(col(c, R.color.bn_on_banana)); t.setBackground(round(col(c, R.color.bn_banana), dp(c, 16), 0, 0)); return; }
        android.graphics.drawable.Drawable d = new android.graphics.drawable.BitmapDrawable(c.getResources(), b);
        t.setBackground(d);
        t.setTextColor(android.graphics.Color.TRANSPARENT);   // only hidden, so the letter is still there if the tile is reused for another game
        t.setOutlineProvider(new android.view.ViewOutlineProvider() {
            @Override public void getOutline(View v, android.graphics.Outline o) { o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), dp(c, 16)); }
        });
        t.setClipToOutline(true);
    }

    // ---- Containers ----

    /** A card: rounded, a hairline peel edge, vertical, 18 dp inside */
    static LinearLayout card(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setBackground(round(col(c, R.color.bn_card), dp(c, 20), col(c, R.color.bn_card_stroke), dp(c, 1)));
        int p = dp(c, 18);
        l.setPadding(p, p, p, p);
        return l;
    }

    /** A card for list rows (settings, the Readiness check): the rows reach close to its edges */
    static LinearLayout listCard(Context c) {
        LinearLayout l = card(c);
        l.setPadding(dp(c, 6), dp(c, 6), dp(c, 6), dp(c, 6));
        return l;
    }

    static LinearLayout settingsList(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        // Keep a 16dp expansion buffer around scaled focus rings; padding belongs to the list, not each row.
        l.setPadding(dp(c, 16), 0, dp(c, 16), 0);
        l.setClipChildren(false);
        l.setClipToPadding(false);
        l.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE);
        l.setDividerDrawable(new android.graphics.drawable.ColorDrawable(0x14FFFFFF));
        l.setDividerPadding(dp(c, 2));
        return l;
    }

    /** A card inside a card: the tint color, no edge */
    static LinearLayout inner(Context c, int fillRes) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setBackground(round(col(c, fillRes), dp(c, 16), 0, 0));
        int p = dp(c, 14);
        l.setPadding(p, p, p, p);
        return l;
    }

    static LinearLayout row(Context c) {
        LinearLayout r = new LinearLayout(c);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        return r;
    }

    static LinearLayout.LayoutParams fill() { return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT); }
    static LinearLayout.LayoutParams wrap() { return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT); }
    static LinearLayout.LayoutParams weight() { return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1); }

    /** Adds v to a vertical parent with a gap above it (none for the first child) */
    static <V extends View> V add(LinearLayout parent, V v, int gapDp) { return add(parent, v, gapDp, fill()); }
    static <V extends View> V add(LinearLayout parent, V v, int gapDp, LinearLayout.LayoutParams lp) {
        if (parent.getChildCount() > 0) lp.topMargin = dp(parent.getContext(), gapDp);
        parent.addView(v, lp);
        return v;
    }

    static View spacer(Context c) { View v = new View(c); v.setLayoutParams(new LinearLayout.LayoutParams(0, 1, 1)); return v; }

    /** A thin line between a sheet's groups */
    static View divider(Context c) {
        View v = new View(c);
        v.setBackgroundColor(col(c, R.color.bn_card_stroke));
        v.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, 1)));
        return v;
    }

    // ---- Pages ----

    /**
     * A page's scrolling column, added to a frame: the same gutter and top spacing on every screen, clear of the status bar, the navigation bar and a cutout (edge
     * to edge, targetSdk 35), at a readable width centered on a wide window, with a cream scrim under the status bar so scrolled cards never run under the clock.
     * Returns the column to fill.
     */
    static LinearLayout page(Activity a, FrameLayout frame, int maxWidthDp) {
        // Edge to edge on every Android: from Android 15 targetSdk 35 makes it so, but before it the window keeps its content clear of the bars itself and
        // consumes the insets, so the listener below never ran and the column had no gutter and no width limit (cards from edge to edge on Android 13)
        a.getWindow().setDecorFitsSystemWindows(false);
        View scrim = new View(a);
        scrim.setBackgroundColor((col(a, R.color.bn_bg) & 0x00FFFFFF) | 0xEE000000);
        ScrollView scroll = new ScrollView(a);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.setClipChildren(false);
        scroll.setScrollBarStyle(View.SCROLLBARS_OUTSIDE_OVERLAY);
        LinearLayout content = new LinearLayout(a);
        content.setOrientation(LinearLayout.VERTICAL);
        // Let focused rows draw beyond their own bounds; the 16dp list gutters below keep them inside the viewport. 
        content.setClipChildren(false);
        content.setClipToPadding(false);
        scroll.addView(content, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        android.graphics.drawable.GradientDrawable ps5Background = new android.graphics.drawable.GradientDrawable(
                android.graphics.drawable.GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xFF050A14, 0xFF0D192E});
        scroll.setBackground(ps5Background);
        content.setBackgroundColor(Color.TRANSPARENT);
        frame.addView(scroll, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        frame.addView(scrim, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0));
        scroll.setOnApplyWindowInsetsListener((v, in) -> {
            Insets i = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            Insets navigation = in.getInsets(WindowInsets.Type.navigationBars());
            int w = a.getResources().getDisplayMetrics().widthPixels;
            int gutter = dp(a, a.getResources().getConfiguration().screenWidthDp < 360 ? 16 : 20);
            int side = Math.max(gutter, (w - i.left - i.right - dp(a, maxWidthDp)) / 2);
            // Explicit navigation-bar inset: the final list row (including Readiness check) stays above Android's system buttons.
            v.setPadding(side + i.left, i.top + dp(a, 8), side + i.right,
                    Math.max(i.bottom, navigation.bottom) + dp(a, 24));
            scrim.getLayoutParams().height = i.top;
            scrim.requestLayout();
            return in;
        });
        frame.requestApplyInsets();   // a page built after the first layout gets the insets again (else it starts under the status bar)
        return content;
    }

    /** A page's header, the same on every screen: 56 dp high; a leading view (the banana badge, or a back button), the title, and an optional trailing button */
    static LinearLayout header(Context c, View leading, CharSequence title, View trailing) { return header(c, leading, title, null, trailing); }

    /** The same, with a small line under the title in the accent yellow (Home: the build identifier, Prefs.buildId), or none when null */
    static LinearLayout header(Context c, View leading, CharSequence title, CharSequence subtitle, View trailing) {
        LinearLayout h = row(c);
        h.setMinimumHeight(dp(c, 56));
        if (leading != null) h.addView(leading);
        TextView t = title(c, title);
        t.setAccessibilityHeading(true);
        int pad = dp(c, leading != null ? 14 : 4);
        if (subtitle == null) {
            t.setPadding(pad, 0, dp(c, 8), 0);
            h.addView(t, weight());
        } else {
            LinearLayout col = new LinearLayout(c);
            col.setOrientation(LinearLayout.VERTICAL);
            col.setPadding(pad, 0, dp(c, 8), 0);
            col.addView(t);
            TextView s = text(c, subtitle, 11, R.color.bn_accent, 600);
            s.setSingleLine(true);
            s.setEllipsize(TextUtils.TruncateAt.END);
            s.setTextIsSelectable(true);
            col.addView(s);
            h.addView(col, weight());
        }
        if (trailing != null) h.addView(trailing);
        return h;
    }

    /** The back button of a page's header */
    static ImageView back(Context c, View.OnClickListener l) { return iconButton(c, R.drawable.ic_back, "Back", Kind.TONAL, l); }

    /**
     * Makes a card one touch target that a D-pad or controller can reach: focusable, with an accent ring while focused (the ring replaces the edge, so nothing moves),
     * and l on a tap or the controller's confirm button. The card's own buttons stay separate stops
     */
    static void focusCard(Context c, LinearLayout card, View.OnClickListener l) {
        // The animated white glass border is the only focus outline; do not add the old blue focus stroke.
        card.setFocusable(true);
        card.setClickable(true);
        card.setOnClickListener(v -> { click(v); if (l != null) l.onClick(v); });
    }

    /** A state line as a pill (the tint, a hairline edge), like the state chip of the settings pages; the words and their color are set by status() */
    static TextView pill(TextView t) {
        Context c = t.getContext();
        t.setBackground(round(col(c, R.color.bn_tint), dp(c, 999), col(c, R.color.bn_card_stroke), dp(c, 1)));
        t.setPadding(dp(c, 10), dp(c, 4), dp(c, 12), dp(c, 4));
        return t;
    }

    /** A group's label with an action at its right ("Your games" and Add game) */
    static LinearLayout sectionHeader(Context c, CharSequence label, View action) {
        LinearLayout top = row(c);
        top.addView(section(c, label), weight());
        top.addView(action);
        return top;
    }

    // ---- Buttons ----

    private static void style(Context c, View b, Kind k, float radiusDp) {
        int fill, stroke = 0;
        switch (k) {
            case PRIMARY: fill = col(c, R.color.bn_banana); break;
            case TONAL: fill = col(c, R.color.bn_tint); break;
            case SOFT: fill = Color.TRANSPARENT; stroke = col(c, R.color.bn_card_stroke); break;
            case DANGER: fill = col(c, R.color.bn_danger_tint); break;
            default: fill = Color.TRANSPARENT; break;
        }
        b.setBackground(ripple(c, round(fill, dp(c, radiusDp), stroke, stroke != 0 ? dp(c, 1.5f) : 0)));
    }

    private static int textColor(Kind k) {
        switch (k) {
            case PRIMARY: return R.color.bn_on_banana;
            case DANGER: return R.color.bn_danger;
            case QUIET: return R.color.bn_accent;
            default: return R.color.bn_text;
        }
    }

    /**
     * A button: 48 dp high, 14 dp corners, one line (labels are kept short; the button grows to fit its label, and a half-width one ellipsizes rather than wraps).
     * PRIMARY banana yellow, TONAL the soft tint, SOFT an outline, DANGER red-orange on its tint, QUIET text only in the accent. Disabled ones fade
     */
    static Button button(Context c, CharSequence s, Kind k, View.OnClickListener l) {
        Button b = new Button(c) {
            @Override public void setEnabled(boolean on) { super.setEnabled(on); setAlpha(on ? 1f : 0.42f); }
        };
        b.setText(s);
        b.setAllCaps(false);
        b.setTextSize(15);
        b.setTypeface(face(k == Kind.PRIMARY ? 700 : 600));
        b.setLetterSpacing(0f);
        b.setStateListAnimator(null);
        b.setMinWidth(dp(c, 64));
        b.setMinimumWidth(dp(c, 64));
        b.setMinHeight(dp(c, 48));
        b.setMinimumHeight(dp(c, 48));
        int h = dp(c, k == Kind.QUIET ? 12 : 18);
        b.setPadding(h, 0, h, 0);
        b.setSingleLine(true);
        b.setEllipsize(TextUtils.TruncateAt.END);
        b.setGravity(Gravity.CENTER);
        b.setIncludeFontPadding(false);
        b.setTextColor(col(c, textColor(k)));
        style(c, b, k, 14);
        b.setOnClickListener(v -> { click(v); if (l != null) l.onClick(v); });
        // Every standard button uses the same animated white PS5-style focus edge.
        ps5Focus(c, b, 14);
        return b;
    }

    /** A card's big action (Play, Prepare, Add game): an icon and a label centered, 56 dp high; setText changes the label, setIcon the icon */
    static final class Action extends LinearLayout {
        final ImageView icon;
        final TextView label;
        final Kind kind;

        Action(Context c, int drawable, CharSequence s, Kind k, View.OnClickListener l) {
            super(c);
            kind = k;
            setOrientation(HORIZONTAL);
            setGravity(Gravity.CENTER);
            setMinimumHeight(dp(c, 56));
            setPadding(dp(c, 20), 0, dp(c, 20), 0);
            style(c, this, k, 16);
            setClickable(true);
            setFocusable(true);
            icon = new ImageView(c);
            icon.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
            addView(icon, new LinearLayout.LayoutParams(dp(c, 22), dp(c, 22)));
            label = text(c, s, 17, textColor(k), 700);
            label.setSingleLine(true);
            label.setIncludeFontPadding(false);
            LinearLayout.LayoutParams lp = wrap();
            lp.leftMargin = dp(c, 10);
            addView(label, lp);
            setIcon(drawable);
            setContentDescription(s);
            setOnClickListener(v -> { click(v); if (l != null) l.onClick(v); });
            ps5Focus(c, this, 16);
        }

        void setText(CharSequence s) { label.setText(s); setContentDescription(s); }

        void setIcon(int drawable) {
            icon.setImageDrawable(drawable == 0 ? null : Ui.icon(getContext(), drawable, textColor(kind)));
            icon.setVisibility(drawable == 0 ? GONE : VISIBLE);
        }

        @Override public void setEnabled(boolean on) { super.setEnabled(on); setAlpha(on ? 1f : 0.42f); }
        @Override public CharSequence getAccessibilityClassName() { return Button.class.getName(); }
    }

    static Action action(Context c, int drawable, CharSequence s, Kind k, View.OnClickListener l) { return new Action(c, drawable, s, k, l); }

    /** A square icon button, 44 dp, 14 dp corners (a page's back and settings buttons, a card's ⋯) */
    static ImageView iconButton(Context c, int drawable, String description, Kind k, View.OnClickListener l) {
        ImageView v = new ImageView(c);
        v.setImageDrawable(icon(c, drawable, R.color.bn_text));
        v.setScaleType(ImageView.ScaleType.CENTER);
        style(c, v, k, 14);
        v.setContentDescription(description);
        v.setClickable(true);
        v.setFocusable(true);
        v.setOnClickListener(x -> { click(x); if (l != null) l.onClick(x); });
        v.setLayoutParams(new LinearLayout.LayoutParams(dp(c, 44), dp(c, 44)));
        // Icon-only controls share the exact same moving white focus treatment.
        ps5Focus(c, v, 14);
        return v;
    }

    /** A card's ⋯ (Manage): borderless */
    static ImageView more(Context c, String description, View.OnClickListener l) { return iconButton(c, R.drawable.ic_more, description, Kind.QUIET, l); }

    /** Two buttons side by side, each half the width */
    static LinearLayout pair(Context c, View a, View b) {
        LinearLayout r = row(c);
        r.addView(a, weight());
        if (b != null) {
            LinearLayout.LayoutParams lp = weight();
            lp.leftMargin = dp(c, 10);
            r.addView(b, lp);
        }
        return r;
    }

    /** The banana with a face in a leaf-green circle (Home's header) */
    static ImageView badge(Context c, int sizeDp) {
        ImageView v = new ImageView(c);
        v.setImageDrawable(new android.graphics.drawable.InsetDrawable(c.getDrawable(R.drawable.ic_banana_happy), -0.16f));   // the banana fills more of the circle
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(0xFF1E5B3A);
        v.setBackground(g);
        v.setScaleType(ImageView.ScaleType.FIT_CENTER);
        v.setLayoutParams(new LinearLayout.LayoutParams(dp(c, sizeDp), dp(c, sizeDp)));
        v.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        return v;
    }

    static ProgressBar progress(Context c) {
        ProgressBar bar = new ProgressBar(c, null, android.R.attr.progressBarStyleHorizontal);
        bar.setProgressTintList(ColorStateList.valueOf(col(c, R.color.bn_banana_deep)));
        bar.setProgressBackgroundTintList(ColorStateList.valueOf(col(c, R.color.bn_card_stroke)));
        bar.setScaleY(1.6f);
        return bar;
    }

    // ---- List rows ----

    /**
     * One row of a list card or a sheet: an emoji tile, a one-line title over its value across the whole width (up to three lines: the value, or for a switch what its
     * state does, says(…)), a short status line when the setting needs something (status(…): "Needs Prepare again: …"), its tags on a line of their own (so they never
     * squeeze the text), a muted note, and a chevron (or a switch); the whole row is the touch target (a switch row's tap flips the switch). Compact: 52 dp high, 36 dp tiles
     */
    private static String monochromeGlyph(String source) {
        if (source == null || source.isEmpty()) return "·";
        switch (source) {
            case "🔄": return "↻"; case "👀": return "◉"; case "🎮": case "🎛️": return "⌘";
            case "🔘": return "●"; case "🖼️": case "📺": return "▣"; case "⏱️": case "⏲️": return "◷";
            case "📳": return "⌁"; case "🚀": return "↗"; case "🔆": return "☼"; case "🅰️": return "A"; case "🕹️": return "⊙";
            case "🔍": return "⌕"; case "✨": return "✧"; case "📐": case "📏": return "⌁";
            case "📁": return "▱"; case "🧹": return "⌫"; case "📋": return "▤"; case "🧪": return "◌";
            case "📨": return "↗"; case "🛠": return "⚙"; case "🪟": return "▢"; case "🎬": return "▷";
            case "🌀": return "◎"; case "🔳": return "▦"; case "🎯": return "⊙"; case "🏃": return "›";
            case "🌿": return "✳"; case "🧩": return "◇"; case "🧱": return "▥"; case "🧵": return "≋";
            case "💾": return "▣"; case "⚡": return "ϟ"; case "⏸️": return "Ⅱ"; case "📝": return "≡";
            case "🌐": return "◎"; case "🔁": return "↻"; case "📦": return "□"; case "📲": return "▯";
            case "🏠": return "⌂"; case "🛠️": return "⚙"; default: return "◇";
        }
    }

    static final class Setting {
        final LinearLayout view;
        final TextView title, value, status, tag, badge, note;   // tag: Recommended / Changed (Ui.tag); badge: a fixed label such as Experimental; note: a muted line under them
        final TextView tile;
        private final LinearLayout tags;   // the line of the badge and the tag
        Switch toggle;

        Setting(Context c, String emoji, String title, View.OnClickListener l) {
            view = row(c);
            view.setPadding(dp(c, 10), dp(c, 7), dp(c, 10), dp(c, 7));
            view.setMinimumHeight(dp(c, 52));
            view.setBackground(ripple(c, round(Color.TRANSPARENT, dp(c, 12), 0, 0)));
            // Foreground bounds track the complete measured row, including wrapped subtitles and tags.
            settingFocus(c, view, 12);
            view.setOnClickListener(v -> { click(v); if (l != null) l.onClick(v); });
            tile = text(c, monochromeGlyph(emoji), 22, R.color.bn_text, 400);
            tile.setTextColor(0xFFFFFFFF);
            tile.setGravity(Gravity.CENTER);
            tile.setIncludeFontPadding(false);
            tile.setBackground(round(0x00000000, dp(c, 11), 0, 0));
            tile.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            LinearLayout.LayoutParams iconLp = new LinearLayout.LayoutParams(dp(c, 22), dp(c, 22));
            iconLp.setMarginEnd(dp(c, 14));
            view.addView(tile, iconLp);
            LinearLayout col = new LinearLayout(c);
            col.setOrientation(LinearLayout.VERTICAL);
            col.setPadding(dp(c, 12), 0, dp(c, 8), 0);
            this.title = text(c, title, 15, R.color.bn_text, 600);
            this.title.setMaxLines(2);   // one line at the normal font scale (titles are kept short); a large font scale wraps it rather than cut it
            this.title.setEllipsize(TextUtils.TruncateAt.END);
            col.addView(this.title);
            value = small(c, "");
            value.setMaxLines(3);
            value.setEllipsize(TextUtils.TruncateAt.END);
            value.setVisibility(View.GONE);   // shown by value(…) when there is one
            LinearLayout.LayoutParams vp = fill();
            vp.topMargin = dp(c, 1);
            col.addView(value, vp);
            status = text(c, "", 12, R.color.bn_danger, 600);
            status.setMaxLines(2);
            status.setVisibility(View.GONE);   // shown by status(…)
            LinearLayout.LayoutParams sp = fill();
            sp.topMargin = dp(c, 2);
            col.addView(status, sp);
            tags = row(c);
            badge = Ui.tag(c, "", true);
            badge.setVisibility(View.GONE);
            LinearLayout.LayoutParams bp = wrap();
            bp.setMarginEnd(dp(c, 6));
            tags.addView(badge, bp);
            tag = Ui.tag(c, "", false);
            tag.setVisibility(View.GONE);
            tags.addView(tag, wrap());
            tags.setVisibility(View.GONE);
            LinearLayout.LayoutParams lp = wrap();
            lp.topMargin = dp(c, 3);
            col.addView(tags, lp);
            note = small(c, "");
            note.setMaxLines(2);
            note.setEllipsize(TextUtils.TruncateAt.END);
            note.setVisibility(View.GONE);   // shown by note(…)
            col.addView(note);
            view.addView(col, weight());
        }

        private void line() {
            tags.setVisibility(tag.getVisibility() == View.VISIBLE || badge.getVisibility() == View.VISIBLE ? View.VISIBLE : View.GONE);
        }

        Setting chevron(Context c) {
            ImageView ch = new ImageView(c);
            ch.setImageDrawable(icon(c, R.drawable.ic_chevron, R.color.bn_text_2));
            ch.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            view.addView(ch, new LinearLayout.LayoutParams(dp(c, 20), dp(c, 20)));
            return this;
        }

        /** A switch at the end; a tap anywhere on a row without its own action flips it (when it is enabled) */
        Setting toggle(Context c, boolean on, android.widget.CompoundButton.OnCheckedChangeListener l) {
            toggle = new Switch(c);
            toggle.setThumbTintList(new ColorStateList(
                    new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                    new int[]{0xFFFFFFFF, 0xFFCBD5E1}));
            toggle.setTrackTintList(new ColorStateList(
                    new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                    new int[]{0x66FFFFFF, 0xFF475569}));
            toggle.setChecked(on);
            toggle.setOnCheckedChangeListener((b, x) -> { if (l != null) l.onCheckedChanged(b, x); says(); });   // the value line follows the switch (says)
            view.addView(toggle);
            if (!view.hasOnClickListeners()) view.setOnClickListener(v -> { if (toggle.isEnabled()) toggle.toggle(); });
            return this;
        }

        Setting value(CharSequence s) { value.setText(s); value.setVisibility(s == null || s.length() == 0 ? View.GONE : View.VISIBLE); return this; }
        /** What the setting needs before it works, in a short line of its own under the value (null: none), so the value still says what the setting does */
        Setting status(CharSequence s) { status.setText(s); status.setVisibility(s == null || s.length() == 0 ? View.GONE : View.VISIBLE); return this; }
        /** The tag on the tags' line (null: none); warn picks the warning tint (Ui.tag) */
        Setting tag(Context c, CharSequence s, boolean warn) {
            if (s == null || s.length() == 0) { tag.setVisibility(View.GONE); line(); return this; }
            tag.setText(s); styleTag(c, tag, warn); tag.setVisibility(View.VISIBLE);
            line();
            return this;
        }
        /** A fixed label before the tag (Experimental), in the warning tint (null: none) */
        Setting badge(CharSequence s) {
            badge.setText(s == null ? "" : s);
            badge.setVisibility(s == null || s.length() == 0 ? View.GONE : View.VISIBLE);
            line();
            return this;
        }
        /** The muted line under the value (null: none) */
        Setting note(CharSequence s) { note.setText(s); note.setVisibility(s == null || s.length() == 0 ? View.GONE : View.VISIBLE); return this; }

        private CharSequence onText, offText;
        /** A switch row's value line in words for each state (what On does, what Off does): it follows the switch, so it never contradicts it */
        Setting says(CharSequence on, CharSequence off) { onText = on; offText = off; says(); return this; }
        private void says() { if (toggle != null && (onText != null || offText != null)) value(toggle.isChecked() ? onText : offText); }

        /** A row that belongs to the row above it (Resolution's Window, the Upscaler's sharpness): set in, with a smaller tile */
        Setting sub() {
            Context c = view.getContext();
            view.setPaddingRelative(dp(c, 30), dp(c, 4), dp(c, 10), dp(c, 4));
            view.setMinimumHeight(dp(c, 46));
            LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) tile.getLayoutParams();
            lp.width = lp.height = dp(c, 22);
            lp.setMarginEnd(dp(c, 6));
            tile.setLayoutParams(lp);
            tile.setTextSize(22);
            title.setTextSize(14);
            return this;
        }
    }

    /** A choice of a few words side by side in one rounded track (Recommended | Custom): the picked one on banana yellow. pick gets the index when the player picks another */
    static final class Segmented extends LinearLayout {
        private final TextView[] items;
        private int selected;

        Segmented(Context c, String[] labels, int sel, java.util.function.IntConsumer pick) {
            super(c);
            setOrientation(HORIZONTAL);
            setBackground(round(col(c, R.color.bn_tint), dp(c, 14), col(c, R.color.bn_card_stroke), dp(c, 1)));
            int p = dp(c, 3);
            setPadding(p, p, p, p);
            items = new TextView[labels.length];
            for (int k = 0; k < labels.length; k++) {
                int i = k;
                TextView t = text(c, labels[k], 14, R.color.bn_text, 700);
                t.setGravity(Gravity.CENTER);
                t.setSingleLine(true);
                t.setEllipsize(TextUtils.TruncateAt.END);
                t.setMinHeight(dp(c, 40));
                t.setPadding(dp(c, 10), 0, dp(c, 10), 0);
                t.setClickable(true);
                t.setFocusable(true);
                t.setOnClickListener(v -> { if (i != selected) { select(i); pick.accept(i); } });
                items[k] = t;
                addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            }
            select(sel);
        }

        void select(int k) {
            selected = k;
            Context c = getContext();
            for (int i = 0; i < items.length; i++) {
                boolean on = i == k;
                items[i].setBackground(on ? round(col(c, R.color.bn_banana), dp(c, 11), 0, 0) : ripple(c, round(Color.TRANSPARENT, dp(c, 11), 0, 0)));
                items[i].setTextColor(col(c, on ? R.color.bn_on_banana : R.color.bn_text_2));
                items[i].setSelected(on);
            }
        }
    }

    /**
     * A page's tabs under its header (a game's page: Performance, Saves, Storage): the words in a row that scrolls sideways when they do not fit, the current
     * one in the accent over a short bar, a hairline under them all. pick gets the index when the player picks another
     */
    static LinearLayout tabs(Context c, String[] labels, int sel, java.util.function.IntConsumer pick) {
        LinearLayout out = new LinearLayout(c);
        out.setOrientation(LinearLayout.VERTICAL);
        android.widget.HorizontalScrollView hs = new android.widget.HorizontalScrollView(c);
        hs.setHorizontalScrollBarEnabled(false);
        hs.setOverScrollMode(View.OVER_SCROLL_NEVER);
        LinearLayout row = row(c);
        for (int k = 0; k < labels.length; k++) {
            int i = k;
            boolean on = k == sel;
            LinearLayout tab = new LinearLayout(c);
            tab.setOrientation(LinearLayout.VERTICAL);
            TextView t = text(c, labels[k], 15, on ? R.color.bn_accent : R.color.bn_text_2, on ? 700 : 600);
            t.setSingleLine(true);
            t.setGravity(Gravity.CENTER);
            t.setPadding(dp(c, 12), dp(c, 10), dp(c, 12), dp(c, 8));
            tab.addView(t);
            View bar = new View(c);
            bar.setBackground(round(col(c, R.color.bn_accent), dp(c, 2), 0, 0));
            bar.setVisibility(on ? View.VISIBLE : View.INVISIBLE);
            LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, 3));
            bl.leftMargin = bl.rightMargin = dp(c, 8);
            tab.addView(bar, bl);
            tab.setBackground(ripple(c, round(Color.TRANSPARENT, dp(c, 10), 0, 0)));
            tab.setClickable(true);
            tab.setFocusable(true);
            tab.setSelected(on);
            tab.setContentDescription(labels[k] + (on ? ", tab, selected" : ", tab"));
            focusMotion(c, tab, 10);
            tab.setOnClickListener(v -> { if (i != sel) pick.accept(i); });
            row.addView(tab, wrap());
        }
        hs.addView(row);
        out.addView(hs, fill());
        out.addView(divider(c));
        return out;
    }

    /** One entry of a page's side rail (wide screens: the sections of Game settings): an emoji and a label, the current one on the tint in the accent */
    static TextView navItem(Context c, String emoji, CharSequence label, boolean selected, View.OnClickListener l) {
        TextView t = text(c, emoji + "   " + label, 15, selected ? R.color.bn_accent : R.color.bn_text, selected ? 700 : 600);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setGravity(Gravity.CENTER_VERTICAL);
        t.setMinHeight(dp(c, 46));
        t.setPadding(dp(c, 14), 0, dp(c, 12), 0);
        t.setBackground(ripple(c, round(selected ? col(c, R.color.bn_tint) : Color.TRANSPARENT, dp(c, 12), 0, 0)));
        t.setClickable(true);
        t.setFocusable(true);
        t.setSelected(selected);
        t.setContentDescription(label);
        focusMotion(c, t, 12);
        t.setOnClickListener(l);
        return t;
    }

    /** A page's state chip under its header ("Adreno 840 · Qualcomm 842.44"): a leaf dot and the words, on the tint */
    static TextView statusChip(Context c, CharSequence s) {
        TextView t = text(c, "", 12, R.color.bn_text, 600);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        t.setBackground(round(col(c, R.color.bn_tint), dp(c, 999), col(c, R.color.bn_card_stroke), dp(c, 1)));
        t.setPadding(dp(c, 10), dp(c, 5), dp(c, 12), dp(c, 5));
        statusChip(t, s);
        return t;
    }

    /** A state chip's words again (they become known later) */
    static void statusChip(TextView t, CharSequence s) {
        android.text.SpannableString sp = new android.text.SpannableString("●  " + s);
        sp.setSpan(new android.text.style.ForegroundColorSpan(col(t.getContext(), R.color.bn_leaf)), 0, 1, 0);
        t.setText(sp);
        t.setContentDescription(s);
    }

    /** A small rounded tag (a row's "Recommended" or "Changed"): the accent on the tint, or with warn the danger colour on the warning tint; both read in light and dark */
    static TextView tag(Context c, CharSequence s, boolean warn) {
        TextView t = text(c, s, 11, R.color.bn_accent, 700);
        t.setSingleLine(true);
        t.setPadding(dp(c, 8), dp(c, 2), dp(c, 8), dp(c, 2));
        styleTag(c, t, warn);
        return t;
    }
    private static void styleTag(Context c, TextView t, boolean warn) {
        t.setTextColor(col(c, warn ? R.color.bn_danger : R.color.bn_accent));
        t.setBackground(round(col(c, warn ? R.color.bn_warn_tint : R.color.bn_tint), dp(c, 999), 0, 0));
    }

    static AlertDialog choiceSheet(Activity a, CharSequence title, String[] labels, int checked,
                                   java.util.function.IntConsumer choose) {
        return choiceSheet(a, title, null, labels, checked, choose);
    }

    static AlertDialog choiceSheet(Activity a, CharSequence title, CharSequence note, String[] labels, int checked,
                                   java.util.function.IntConsumer choose) {
        LinearLayout options = new LinearLayout(a);
        options.setOrientation(LinearLayout.VERTICAL);
        if (note != null && note.length() > 0) {
            TextView intro = small(a, note);
            intro.setTextColor(0xFFD4DCE8);
            add(options, intro, 0);
            add(options, divider(a), 10);
        }
        AlertDialog[] dialog = new AlertDialog[1];
        for (int i = 0; i < labels.length; i++) {
            final int index = i;
            LinearLayout item = row(a);
            item.setMinimumHeight(dp(a, 54));
            item.setPadding(dp(a, 16), dp(a, 8), dp(a, 12), dp(a, 8));
            item.setBackground(round(Color.TRANSPARENT, dp(a, 12), 0, 0));
            item.addView(text(a, labels[i], 15, R.color.bn_text, 600), weight());
            TextView tick = text(a, i == checked ? "✓" : "", 18, R.color.bn_text, 700);
            tick.setTextColor(Color.WHITE);
            item.addView(tick, new LinearLayout.LayoutParams(dp(a, 28), ViewGroup.LayoutParams.WRAP_CONTENT));
            ps5Focus(a, item, 12);
            item.setOnClickListener(v -> {
                click(v);
                choose.accept(index);
                if (dialog[0] != null) dialog[0].dismiss();
            });
            options.addView(item, fill());
            if (i + 1 < labels.length) {
                View line = divider(a);
                line.setBackgroundColor(0x20FFFFFF);
                options.addView(line);
            }
        }
        dialog[0] = sheet(a, title, options).setNegativeButton("Cancel", null).create();
        dialog[0].setOnShowListener(v -> {
            if (dialog[0].getWindow() != null) {
                android.graphics.drawable.GradientDrawable dialogBg = new android.graphics.drawable.GradientDrawable(
                        android.graphics.drawable.GradientDrawable.Orientation.TOP_BOTTOM,
                        new int[]{0xFF050A14, 0xFF0D192E});
                dialogBg.setCornerRadius(dp(a, 22));
                dialogBg.setStroke(dp(a, 1), 0x44FFFFFF);
                dialog[0].getWindow().setBackgroundDrawable(dialogBg);
                if (android.os.Build.VERSION.SDK_INT >= 31) dialog[0].getWindow().setBackgroundBlurRadius(dp(a, 24));
                dialog[0].getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND);
                android.view.WindowManager.LayoutParams lp = dialog[0].getWindow().getAttributes();
                lp.dimAmount = 0.62f;
                dialog[0].getWindow().setAttributes(lp);
            }
            Button cancel = dialog[0].getButton(AlertDialog.BUTTON_NEGATIVE);
            if (cancel != null) cancel.setTextColor(Color.WHITE);
            if (checked >= 0 && checked < labels.length) options.getChildAt(checked * 2).requestFocus();
        });
        dialog[0].show();
        return dialog[0];
    }

    // ---- Dialogs ----

    /** A dialog whose content scrolls (a short screen, the folded phone, landscape): every custom dialog view goes through here */
    static AlertDialog.Builder sheet(Activity a, CharSequence title, View content) {
        ScrollView sv = new ScrollView(a);
        sv.setFillViewport(true);
        sv.setClipToPadding(false);
        android.graphics.drawable.GradientDrawable ps5Sheet = new android.graphics.drawable.GradientDrawable(
                android.graphics.drawable.GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xFF050A14, 0xFF0D192E});
        ps5Sheet.setCornerRadius(dp(a, 22));
        ps5Sheet.setStroke(dp(a, 1), 0x33FFFFFF);
        sv.setBackground(ps5Sheet);
        content.setBackgroundColor(Color.TRANSPARENT);
        int p = dp(a, 20);
        content.setPadding(p, dp(a, 4), p, dp(a, 8));
        sv.addView(content);
        return new AlertDialog.Builder(a).setTitle(title).setView(sv);
    }

    /**
     * Asks before something is deleted or forgotten: what goes and what stays, in the message; the yes button in the danger color, Cancel the default.
     */
    static void confirmDanger(Activity a, String title, CharSequence message, String yes, Runnable r) {
        AlertDialog d = new AlertDialog.Builder(a).setTitle(title).setMessage(message)
                .setPositiveButton(yes, (x, w) -> r.run()).setNegativeButton("Cancel", null).create();
        d.setOnShowListener(x -> {
            Button b = d.getButton(AlertDialog.BUTTON_POSITIVE);
            if (b != null) b.setTextColor(col(a, R.color.bn_danger));
            Button n = d.getButton(AlertDialog.BUTTON_NEGATIVE);
            if (n != null) n.requestFocus();
        });
        d.show();
    }
}
