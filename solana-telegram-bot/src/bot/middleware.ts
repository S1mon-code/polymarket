import { Context } from 'telegraf';

// ── Rate Limiter ──────────────────────────────────────────────────────
// Max 30 messages per minute per user. Uses a sliding window.

interface RateBucket {
  timestamps: number[];
}

const rateBuckets = new Map<number, RateBucket>();

const RATE_WINDOW_MS = 60_000;
const RATE_MAX = 30;

/**
 * Middleware: reject messages that exceed 30/min per user.
 */
export async function rateLimiter(ctx: Context, next: () => Promise<void>) {
  const userId = ctx.from?.id;
  if (!userId) return next();

  const now = Date.now();
  let bucket = rateBuckets.get(userId);

  if (!bucket) {
    bucket = { timestamps: [] };
    rateBuckets.set(userId, bucket);
  }

  // Prune timestamps outside the window
  bucket.timestamps = bucket.timestamps.filter(
    (ts) => now - ts < RATE_WINDOW_MS
  );

  if (bucket.timestamps.length >= RATE_MAX) {
    await ctx.reply(
      '⏳ <b>Slow down!</b>\nYou\'re sending messages too fast. Please wait a moment.',
      { parse_mode: 'HTML' }
    );
    return; // drop the update
  }

  bucket.timestamps.push(now);
  return next();
}

// Periodically clean stale buckets (every 5 min)
setInterval(() => {
  const cutoff = Date.now() - RATE_WINDOW_MS * 2;
  for (const [uid, bucket] of rateBuckets) {
    if (bucket.timestamps.every((ts) => ts < cutoff)) {
      rateBuckets.delete(uid);
    }
  }
}, 300_000).unref();

// ── User Tracker ──────────────────────────────────────────────────────

/**
 * Middleware: log every user interaction for analytics.
 */
export async function userTracker(ctx: Context, next: () => Promise<void>) {
  const userId = ctx.from?.id;
  const username = ctx.from?.username ?? 'unknown';
  const updateType = ctx.updateType;

  const text =
    ctx.message && 'text' in ctx.message ? ctx.message.text : undefined;

  console.log(
    `[${new Date().toISOString()}] user=${userId} @${username} type=${updateType}${text ? ` text="${text}"` : ''}`
  );

  return next();
}

// ── Error Handler ─────────────────────────────────────────────────────

/**
 * Middleware: catch any downstream error and reply with a user-friendly
 * message instead of crashing.
 */
export async function errorHandler(ctx: Context, next: () => Promise<void>) {
  try {
    await next();
  } catch (err: unknown) {
    const message =
      err instanceof Error ? err.message : 'An unexpected error occurred';

    console.error(`[ERROR] user=${ctx.from?.id}`, err);

    try {
      await ctx.reply(
        `❌ <b>Something went wrong</b>\n\n<code>${escapeHtml(message)}</code>\n\nPlease try again or contact support.`,
        { parse_mode: 'HTML' }
      );
    } catch {
      // If even the error reply fails, just log it
      console.error('[ERROR] Failed to send error message to user');
    }
  }
}

/** Escape HTML special chars for Telegram HTML mode */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
