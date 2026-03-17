/**
 * Fee Statistics — Query fee data from SQLite for revenue reporting.
 */

import { DailyRevenue, FeeRecord } from '../trading/types';

// We use a lightweight DB interface; the actual DB module is in src/db/.
// This module expects a db object with a query method to be injected.

type Period = 'day' | 'week' | 'month';

interface DbQueryFn {
  all<T>(sql: string, params?: unknown[]): Promise<T[]>;
  get<T>(sql: string, params?: unknown[]): Promise<T | undefined>;
}

let db: DbQueryFn | null = null;

/**
 * Inject the database connection. Must be called before using stats functions.
 */
export function initStatsDb(database: DbQueryFn): void {
  db = database;
}

function getDb(): DbQueryFn {
  if (!db) {
    throw new Error('[FeeStats] Database not initialized. Call initStatsDb() first.');
  }
  return db;
}

/**
 * Get the Unix timestamp for the start of a given period.
 */
function getPeriodStart(period: Period): number {
  const now = Date.now();
  switch (period) {
    case 'day':
      return now - 24 * 60 * 60 * 1000;
    case 'week':
      return now - 7 * 24 * 60 * 60 * 1000;
    case 'month':
      return now - 30 * 24 * 60 * 60 * 1000;
  }
}

/**
 * Get total fees collected, optionally filtered by period.
 *
 * @param period - 'day', 'week', or 'month' (optional, defaults to all-time)
 * @returns Total fee amount in lamports
 */
export async function getTotalFees(period?: Period): Promise<number> {
  const database = getDb();

  if (period) {
    const since = getPeriodStart(period);
    const result = await database.get<{ total: number }>(
      'SELECT COALESCE(SUM(fee_amount), 0) as total FROM fee_records WHERE timestamp >= ?',
      [since],
    );
    return result?.total ?? 0;
  }

  const result = await database.get<{ total: number }>(
    'SELECT COALESCE(SUM(fee_amount), 0) as total FROM fee_records',
  );
  return result?.total ?? 0;
}

/**
 * Get fees collected from a specific Telegram user.
 *
 * @param telegramUserId - The user's Telegram ID
 * @returns Array of FeeRecords for that user
 */
export async function getFeesByUser(telegramUserId: string): Promise<FeeRecord[]> {
  const database = getDb();

  const rows = await database.all<FeeRecord>(
    'SELECT * FROM fee_records WHERE telegram_user_id = ? ORDER BY timestamp DESC',
    [telegramUserId],
  );

  return rows;
}

/**
 * Get total fees a specific user has generated.
 */
export async function getTotalFeesByUser(telegramUserId: string): Promise<number> {
  const database = getDb();

  const result = await database.get<{ total: number }>(
    'SELECT COALESCE(SUM(fee_amount), 0) as total FROM fee_records WHERE telegram_user_id = ?',
    [telegramUserId],
  );

  return result?.total ?? 0;
}

/**
 * Get daily revenue breakdown for the last N days.
 *
 * @param days - Number of days to look back (default 30)
 * @returns Array of DailyRevenue entries
 */
export async function getDailyRevenue(days: number = 30): Promise<DailyRevenue[]> {
  const database = getDb();
  const since = Date.now() - days * 24 * 60 * 60 * 1000;

  const rows = await database.all<{
    date: string;
    total_lamports: number;
    total_usd: number;
    tx_count: number;
  }>(
    `SELECT
      date(timestamp / 1000, 'unixepoch') as date,
      SUM(fee_amount) as total_lamports,
      COALESCE(SUM(fee_usd), 0) as total_usd,
      COUNT(*) as tx_count
    FROM fee_records
    WHERE timestamp >= ?
    GROUP BY date(timestamp / 1000, 'unixepoch')
    ORDER BY date DESC`,
    [since],
  );

  return rows.map((row) => ({
    date: row.date,
    totalFeeLamports: row.total_lamports,
    totalFeeUsd: row.total_usd,
    txCount: row.tx_count,
  }));
}

/**
 * Get total transaction count, optionally filtered by period.
 */
export async function getTransactionCount(period?: Period): Promise<number> {
  const database = getDb();

  if (period) {
    const since = getPeriodStart(period);
    const result = await database.get<{ count: number }>(
      'SELECT COUNT(*) as count FROM fee_records WHERE timestamp >= ?',
      [since],
    );
    return result?.count ?? 0;
  }

  const result = await database.get<{ count: number }>(
    'SELECT COUNT(*) as count FROM fee_records',
  );
  return result?.count ?? 0;
}
