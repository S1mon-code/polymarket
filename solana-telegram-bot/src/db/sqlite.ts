import Database from "better-sqlite3";
import path from "path";
import { v4 as uuidv4 } from "uuid";
import type { Wallet, Transaction, FeeRecord } from "./models";

const DB_PATH = path.resolve(process.cwd(), "data", "bot.db");

let db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    initSchema(db);
  }
  return db;
}

function initSchema(database: Database.Database): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS wallets (
      telegram_user_id TEXT PRIMARY KEY,
      public_key TEXT NOT NULL,
      encrypted_private_key TEXT NOT NULL,
      iv TEXT NOT NULL,
      auth_tag TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS transactions (
      id TEXT PRIMARY KEY,
      telegram_user_id TEXT NOT NULL,
      type TEXT NOT NULL CHECK (type IN ('buy', 'sell')),
      token_mint TEXT NOT NULL,
      amount_in REAL NOT NULL,
      amount_out REAL NOT NULL,
      fee_amount REAL NOT NULL,
      tx_hash TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'failed')),
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (telegram_user_id) REFERENCES wallets(telegram_user_id)
    );

    CREATE TABLE IF NOT EXISTS fee_records (
      id TEXT PRIMARY KEY,
      tx_hash TEXT NOT NULL,
      fee_amount_lamports INTEGER NOT NULL,
      fee_token_mint TEXT NOT NULL,
      collected_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_transactions_user
      ON transactions(telegram_user_id);
    CREATE INDEX IF NOT EXISTS idx_transactions_status
      ON transactions(status);
    CREATE INDEX IF NOT EXISTS idx_fee_records_tx
      ON fee_records(tx_hash);
  `);
}

// ── Wallet CRUD ──

export function insertWallet(wallet: Omit<Wallet, "created_at">): void {
  const db = getDb();
  const stmt = db.prepare(`
    INSERT INTO wallets (telegram_user_id, public_key, encrypted_private_key, iv, auth_tag)
    VALUES (@telegram_user_id, @public_key, @encrypted_private_key, @iv, @auth_tag)
  `);
  stmt.run(wallet);
}

export function getWalletByUserId(telegramUserId: string): Wallet | undefined {
  const db = getDb();
  const stmt = db.prepare(`SELECT * FROM wallets WHERE telegram_user_id = ?`);
  return stmt.get(telegramUserId) as Wallet | undefined;
}

export function getAllWallets(): Wallet[] {
  const db = getDb();
  const stmt = db.prepare(`SELECT * FROM wallets`);
  return stmt.all() as Wallet[];
}

export function deleteWallet(telegramUserId: string): void {
  const db = getDb();
  const stmt = db.prepare(`DELETE FROM wallets WHERE telegram_user_id = ?`);
  stmt.run(telegramUserId);
}

// ── Transaction CRUD ──

export function insertTransaction(
  tx: Omit<Transaction, "id" | "created_at">
): string {
  const db = getDb();
  const id = uuidv4();
  const stmt = db.prepare(`
    INSERT INTO transactions (id, telegram_user_id, type, token_mint, amount_in, amount_out, fee_amount, tx_hash, status)
    VALUES (@id, @telegram_user_id, @type, @token_mint, @amount_in, @amount_out, @fee_amount, @tx_hash, @status)
  `);
  stmt.run({ id, ...tx });
  return id;
}

export function getTransactionById(id: string): Transaction | undefined {
  const db = getDb();
  const stmt = db.prepare(`SELECT * FROM transactions WHERE id = ?`);
  return stmt.get(id) as Transaction | undefined;
}

export function getTransactionsByUserId(
  telegramUserId: string
): Transaction[] {
  const db = getDb();
  const stmt = db.prepare(
    `SELECT * FROM transactions WHERE telegram_user_id = ? ORDER BY created_at DESC`
  );
  return stmt.all(telegramUserId) as Transaction[];
}

export function updateTransactionStatus(
  id: string,
  status: Transaction["status"]
): void {
  const db = getDb();
  const stmt = db.prepare(`UPDATE transactions SET status = ? WHERE id = ?`);
  stmt.run(status, id);
}

// ── Fee Record CRUD ──

export function insertFeeRecord(
  fee: Omit<FeeRecord, "id" | "collected_at">
): string {
  const db = getDb();
  const id = uuidv4();
  const stmt = db.prepare(`
    INSERT INTO fee_records (id, tx_hash, fee_amount_lamports, fee_token_mint)
    VALUES (@id, @tx_hash, @fee_amount_lamports, @fee_token_mint)
  `);
  stmt.run({ id, ...fee });
  return id;
}

export function getFeeRecordByTxHash(txHash: string): FeeRecord | undefined {
  const db = getDb();
  const stmt = db.prepare(`SELECT * FROM fee_records WHERE tx_hash = ?`);
  return stmt.get(txHash) as FeeRecord | undefined;
}

export function getAllFeeRecords(): FeeRecord[] {
  const db = getDb();
  const stmt = db.prepare(
    `SELECT * FROM fee_records ORDER BY collected_at DESC`
  );
  return stmt.all() as FeeRecord[];
}

export function closeDb(): void {
  if (db) {
    db.close();
    db = null;
  }
}
