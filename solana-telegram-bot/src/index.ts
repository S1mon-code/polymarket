import { Telegraf } from 'telegraf';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import {
  startCommand,
  helpCommand,
  walletCommand,
  buyCommand,
  sellCommand,
  exportCommand,
  settingsCommand,
  revenueCommand,
} from './bot/commands';
import { registerCallbacks } from './bot/callbacks';
import { rateLimiter, userTracker, errorHandler } from './bot/middleware';
import { getDb, closeDb } from './db/sqlite';

dotenv.config();

// ── Ensure data directory exists for SQLite ──────────────────────────
const dataDir = path.resolve(process.cwd(), 'data');
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

// ── Initialize database ──────────────────────────────────────────────
console.log('📦 Initializing database...');
getDb();

// ── Validate env ──────────────────────────────────────────────────────
const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
if (!BOT_TOKEN) {
  console.error('❌ TELEGRAM_BOT_TOKEN is required. Set it in .env');
  process.exit(1);
}

// ── Create bot ────────────────────────────────────────────────────────
const bot = new Telegraf(BOT_TOKEN);

// ── Register middleware (order matters) ───────────────────────────────
bot.use(errorHandler);
bot.use(rateLimiter);
bot.use(userTracker);

// ── Register command handlers ─────────────────────────────────────────
bot.command('start', startCommand);
bot.command('help', helpCommand);
bot.command('wallet', walletCommand);
bot.command('buy', buyCommand);
bot.command('sell', sellCommand);
bot.command('export', exportCommand);
bot.command('settings', settingsCommand);
bot.command('revenue', revenueCommand);

// ── Register callback (inline keyboard) handlers ──────────────────────
registerCallbacks(bot);

// ── Catch-all for unknown messages ────────────────────────────────────
bot.on('text', async (ctx) => {
  await ctx.reply(
    '🤖 I didn\'t understand that. Type /help to see available commands.',
    { parse_mode: 'HTML' }
  );
});

// ── Graceful shutdown ─────────────────────────────────────────────────
const shutdown = (signal: string) => {
  console.log(`\n🛑 Received ${signal}. Shutting down gracefully...`);
  bot.stop(signal);
  closeDb();
  process.exit(0);
};

process.once('SIGINT', () => shutdown('SIGINT'));
process.once('SIGTERM', () => shutdown('SIGTERM'));

// ── Launch ────────────────────────────────────────────────────────────
console.log('🚀 Starting Solana Trading Bot...');
bot.launch()
  .then(() => {
    console.log('✅ Bot is running! Waiting for messages...');
  })
  .catch((err) => {
    console.error('❌ Failed to start bot:', err);
    closeDb();
    process.exit(1);
  });
