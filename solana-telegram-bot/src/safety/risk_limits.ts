import { RiskLimitResult, UserRiskLimits } from '../types';

/** Default limits for all users */
const DEFAULT_LIMITS: UserRiskLimits = {
  maxPerTrade: 10,  // SOL
  maxPerDay: 50,    // SOL
};

/** Per-user custom limits (set by admin) */
const customLimits = new Map<string, UserRiskLimits>();

/** Track daily volume per user: userId → { total, resetAt } */
const dailyVolume = new Map<string, { total: number; resetAt: number }>();

/** Get the start-of-day timestamp (UTC midnight) for resetting daily limits */
function getNextResetTimestamp(): number {
  const now = new Date();
  const tomorrow = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)
  );
  return tomorrow.getTime();
}

/**
 * Get the effective risk limits for a user (custom or default).
 */
export function getUserLimits(userId: string): UserRiskLimits {
  return customLimits.get(userId) ?? { ...DEFAULT_LIMITS };
}

/**
 * Admin: set custom risk limits for a specific user.
 */
export function setUserLimits(
  userId: string,
  limits: Partial<UserRiskLimits>
): UserRiskLimits {
  const current = getUserLimits(userId);
  const updated: UserRiskLimits = {
    maxPerTrade: limits.maxPerTrade ?? current.maxPerTrade,
    maxPerDay: limits.maxPerDay ?? current.maxPerDay,
  };
  customLimits.set(userId, updated);
  return updated;
}

/**
 * Check if a trade is within the user's risk limits.
 *
 * @param userId  Telegram user ID as string
 * @param amount  Trade size in SOL
 * @returns       { allowed, reason? }
 */
export function checkTradeLimit(
  userId: string,
  amount: number
): RiskLimitResult {
  if (amount <= 0) {
    return { allowed: false, reason: 'Trade amount must be positive' };
  }

  const limits = getUserLimits(userId);

  // ── Per-trade limit ─────────────────────────────────────────────────
  if (amount > limits.maxPerTrade) {
    return {
      allowed: false,
      reason: `Exceeds per-trade limit of ${limits.maxPerTrade} SOL (requested ${amount} SOL)`,
    };
  }

  // ── Daily volume limit ──────────────────────────────────────────────
  const now = Date.now();
  let vol = dailyVolume.get(userId);

  // Reset if past the UTC midnight boundary
  if (!vol || now >= vol.resetAt) {
    vol = { total: 0, resetAt: getNextResetTimestamp() };
    dailyVolume.set(userId, vol);
  }

  if (vol.total + amount > limits.maxPerDay) {
    const remaining = Math.max(0, limits.maxPerDay - vol.total);
    return {
      allowed: false,
      reason: `Exceeds daily limit of ${limits.maxPerDay} SOL. Remaining today: ${remaining.toFixed(4)} SOL`,
    };
  }

  return { allowed: true };
}

/**
 * Record a completed trade against the user's daily volume.
 * Call this AFTER a successful swap.
 */
export function recordTrade(userId: string, amount: number): void {
  const now = Date.now();
  let vol = dailyVolume.get(userId);

  if (!vol || now >= vol.resetAt) {
    vol = { total: 0, resetAt: getNextResetTimestamp() };
  }

  vol.total += amount;
  dailyVolume.set(userId, vol);
}

/**
 * Get the user's remaining daily volume allowance.
 */
export function getRemainingDaily(userId: string): number {
  const limits = getUserLimits(userId);
  const now = Date.now();
  const vol = dailyVolume.get(userId);

  if (!vol || now >= vol.resetAt) return limits.maxPerDay;
  return Math.max(0, limits.maxPerDay - vol.total);
}

/**
 * Format risk limits info for display.
 */
export function formatLimitsInfo(userId: string): string {
  const limits = getUserLimits(userId);
  const remaining = getRemainingDaily(userId);

  return [
    '<b>📊 Your Risk Limits</b>',
    '',
    `Max per trade: <b>${limits.maxPerTrade} SOL</b>`,
    `Max per day: <b>${limits.maxPerDay} SOL</b>`,
    `Remaining today: <b>${remaining.toFixed(4)} SOL</b>`,
  ].join('\n');
}
