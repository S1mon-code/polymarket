/**
 * Fee Statistics — Query fee data from SQLite for revenue reporting.
 * Uses the synchronous better-sqlite3 DB from src/db/sqlite.ts.
 */

import { DailyRevenue } from '../trading/types';
import { getDb } from '../db/sqlite';

type Period = 'day' | 'week' | 'month';

/**
 * Get the Unix timestamp (ms) for the start of a given period.
 */
function getPeriodStartIso(period: Period): string {
  const now = Date.now();
  let since: number;
  switch (period) {
    case 'day':
      since = now - 24 * 60 * 60 * 1000;
      break;
    case 'week':
      since = now - 7 * 24 * 60 * 60 * 1000;
      break;
    case 'month':
      since = now - 30 * 24 * 60 * 60 * 1000;
      break;
  }
  return new Date(since).toISOString().replace('T', ' ').slice(0, 19);
}

/**
 * Get total fees collected, optionally filtered by period.
 *
 * @param period - 'day', 'week', or 'month' (optional, defaults to all-time)
 * @returns Total fee amount in lamports
 */
export async function getTotalFees(period?: Period): Promise<number> {
  const db = getDb();

  if (period) {
    const since = getPeriodStartIso(period);
    const result = db.prepare(
      'SELECT COALESCE(SUM(fee_amount_lamports), 0) as total FROM fee_records WHERE collected_at >= ?',
    ).get(since) as { total: number } | undefined;
    return result?.total ?? 0;
  }

  const result = db.prepare(
    'SELECT COALESCE(SUM(fee_amount_lamports), 0) as total FROM fee_records',
  ).get() as { total: number } | undefined;
  return result?.total ?? 0;
}

/**
 * Get total fees a specific user has generated.
 */
export async function getTotalFeesByUser(telegramUserId: string): Promise<number> {
  // fee_records table doesn't store telegram_user_id, so we join via transactions
  const db = getDb();
  const result = db.prepare(
    `SELECT COALESCE(SUM(fr.fee_amount_lamports), 0) as total
     FROM fee_records fr
     INNER JOIN transactions t ON fr.tx_hash = t.tx_hash
     WHERE t.telegram_user_id = ?`,
  ).get(telegramUserId) as { total: number } | undefined;
  return result?.total ?? 0;
}

/**
 * Get daily revenue breakdown for the last N days.
 *
 * @param days - Number of days to look back (default 30)
 * @returns Array of DailyRevenue entries
 */
export async function getDailyRevenue(days: number = 30): Promise<DailyRevenue[]> {
  const db = getDb();
  const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000)
    .toISOString()
    .replace('T', ' ')
    .slice(0, 19);

  const rows = db.prepare(
    `SELECT
      date(collected_at) as date,
      SUM(fee_amount_lamports) as total_lamports,
      COUNT(*) as tx_count
    FROM fee_records
    WHERE collected_at >= ?
    GROUP BY date(collected_at)
    ORDER BY date DESC`,
  ).all(since) as Array<{
    date: string;
    total_lamports: number;
    tx_count: number;
  }>;

  return rows.map((row) => ({
    date: row.date,
    totalFeeLamports: row.total_lamports,
    totalFeeUsd: 0, // USD conversion not available in fee_records table
    txCount: row.tx_count,
  }));
}

/**
 * Get total transaction count, optionally filtered by period.
 */
export async function getTransactionCount(period?: Period): Promise<number> {
  const db = getDb();

  if (period) {
    const since = getPeriodStartIso(period);
    const result = db.prepare(
      'SELECT COUNT(*) as count FROM fee_records WHERE collected_at >= ?',
    ).get(since) as { count: number } | undefined;
    return result?.count ?? 0;
  }

  const result = db.prepare(
    'SELECT COUNT(*) as count FROM fee_records',
  ).get() as { count: number } | undefined;
  return result?.count ?? 0;
}
