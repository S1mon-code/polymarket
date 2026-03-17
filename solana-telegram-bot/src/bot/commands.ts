import { Context, Markup } from 'telegraf';
import { PublicKey, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { mainMenu, confirmSwapMenu, settingsMenu } from './menus';
import { checkToken, formatSafetyReport } from '../safety/anti_rug';
import { checkTradeLimit } from '../safety/risk_limits';
import { getConnection } from '../data/rpc';
import { getQuote } from '../trading/jupiter';
import { KNOWN_TOKENS } from '../trading/types';
import { getTotalFees, getDailyRevenue } from '../fees/stats';
import {
  createWallet,
  getWallet,
  getBalance,
} from '../wallet/manager';
import { getTokenBalances } from '../wallet/balance';
import {
  DEFAULT_SETTINGS,
  UserSettings,
  SwapQuote,
  FEE_RATE,
  ADMIN_USER_IDS,
} from '../types';

// ── In-memory settings store ─────────────────────────────────────────
const userSettings = new Map<string, UserSettings>();

function getSettings(userId: string): UserSettings {
  return userSettings.get(userId) ?? { ...DEFAULT_SETTINGS };
}

// ── /start ────────────────────────────────────────────────────────────
export async function startCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  let walletMsg: string;
  try {
    const wallet = getWallet(userId);
    walletMsg = `Your wallet: <code>${wallet.publicKey}</code>`;
  } catch {
    // No wallet yet — create one
    try {
      const publicKey = createWallet(userId);
      walletMsg = `🔑 New wallet created!\n\nYour address: <code>${publicKey}</code>`;
    } catch (err) {
      // Wallet already exists (race condition) — retrieve it
      try {
        const wallet = getWallet(userId);
        walletMsg = `Your wallet: <code>${wallet.publicKey}</code>`;
      } catch {
        walletMsg = '❌ Failed to create wallet. Please try again.';
      }
    }
  }

  await ctx.reply(
    [
      '🚀 <b>Welcome to Solana Trading Bot!</b>',
      '',
      walletMsg,
      '',
      'Trade any Solana token directly from Telegram.',
      'We charge a flat <b>1% fee</b> on every swap.',
      '',
      '💡 <b>Quick start:</b>',
      '• Send SOL to your wallet address',
      '• Use /buy &lt;token&gt; &lt;amount&gt; to buy',
      '• Use /sell &lt;token&gt; &lt;amount&gt; to sell',
      '',
      'Type /help for all commands.',
    ].join('\n'),
    { parse_mode: 'HTML', ...mainMenu() }
  );
}

// ── /help ─────────────────────────────────────────────────────────────
export async function helpCommand(ctx: Context) {
  await ctx.reply(
    [
      '📖 <b>Available Commands</b>',
      '',
      '/start — Welcome & wallet setup',
      '/wallet — View wallet address & balances',
      '/buy &lt;token&gt; &lt;amount&gt; — Buy a token',
      '/sell &lt;token&gt; &lt;amount&gt; — Sell a token',
      '/export — Export your private key',
      '/settings — Adjust slippage & defaults',
      '/help — Show this help message',
      '',
      '<b>Admin Commands</b>',
      '/revenue — Total fees collected',
    ].join('\n'),
    { parse_mode: 'HTML' }
  );
}

// ── /wallet ───────────────────────────────────────────────────────────
export async function walletCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  let wallet;
  try {
    wallet = getWallet(userId);
  } catch {
    await ctx.reply(
      '👛 You don\'t have a wallet yet. Use /start to create one.',
      { parse_mode: 'HTML' }
    );
    return;
  }

  try {
    const connection = getConnection();
    const balanceSol = await getBalance(connection, wallet.publicKey);

    // Fetch token balances
    const pubkey = new PublicKey(wallet.publicKey);
    const tokenBalances = await getTokenBalances(connection, pubkey);

    const tokenLines = tokenBalances.length > 0
      ? tokenBalances.slice(0, 10).map(
          (t) => `  • <code>${t.mint.slice(0, 8)}...</code> — ${t.uiAmount.toFixed(4)}`
        )
      : ['  No tokens found'];

    await ctx.reply(
      [
        '👛 <b>Your Wallet</b>',
        '',
        `Address: <code>${wallet.publicKey}</code>`,
        `SOL Balance: <b>${balanceSol} SOL</b>`,
        '',
        '<b>Token Balances:</b>',
        ...tokenLines,
        '',
        '💡 Send SOL to this address to start trading.',
      ].join('\n'),
      {
        parse_mode: 'HTML',
        ...Markup.inlineKeyboard([
          [Markup.button.callback('🔄 Refresh', 'refresh_balance')],
          [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
        ]),
      }
    );
  } catch (err) {
    await ctx.reply(
      '❌ Failed to fetch wallet balance. Please try again.',
      { parse_mode: 'HTML' }
    );
  }
}

// ── /buy <token> <amount> ─────────────────────────────────────────────
export async function buyCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  // Ensure user has a wallet
  try {
    getWallet(userId);
  } catch {
    await ctx.reply('👛 You don\'t have a wallet yet. Use /start to create one.', {
      parse_mode: 'HTML',
    });
    return;
  }

  const text =
    ctx.message && 'text' in ctx.message ? ctx.message.text : '';
  const parts = text.trim().split(/\s+/);

  if (parts.length < 3) {
    await ctx.reply(
      '💡 <b>Usage:</b> /buy &lt;token_address&gt; &lt;amount_in_sol&gt;\n\nExample: /buy So11111111111111111111111111111112 0.5',
      { parse_mode: 'HTML' }
    );
    return;
  }

  const token = parts[1];
  const amount = parseFloat(parts[2]);

  if (isNaN(amount) || amount <= 0) {
    await ctx.reply('❌ Invalid amount. Please enter a positive number.', {
      parse_mode: 'HTML',
    });
    return;
  }

  // Validate token address
  try {
    new PublicKey(token);
  } catch {
    await ctx.reply('❌ Invalid token address. Please check and try again.', {
      parse_mode: 'HTML',
    });
    return;
  }

  // Check risk limits
  const limitCheck = checkTradeLimit(userId, amount);
  if (!limitCheck.allowed) {
    await ctx.reply(`🚫 <b>Trade blocked:</b> ${limitCheck.reason}`, {
      parse_mode: 'HTML',
    });
    return;
  }

  // Run safety check
  await ctx.reply('🔍 Running safety checks...');
  const safety = await checkToken(token);
  const safetyReport = formatSafetyReport(safety);

  // Get a real quote from Jupiter for display
  const fee = amount * FEE_RATE;
  const netLamports = Math.floor((amount - fee) * LAMPORTS_PER_SOL);

  let expectedOutput = 0;
  let priceImpact = 0;
  const settings = getSettings(userId);

  try {
    const jupQuote = await getQuote(KNOWN_TOKENS.SOL, token, netLamports, settings.slippageBps);
    expectedOutput = parseInt(jupQuote.outAmount, 10);
    priceImpact = parseFloat(jupQuote.priceImpactPct);
  } catch {
    // Quote failed — show zero and let user decide
  }

  const quote: SwapQuote = {
    inputToken: 'SOL',
    outputToken: token,
    inputAmount: amount,
    expectedOutput,
    priceImpact,
    fee,
  };

  const confirmation = confirmSwapMenu('buy', token, amount, quote);

  await ctx.reply(
    [safetyReport, '', '─'.repeat(30), '', confirmation.text].join('\n'),
    { parse_mode: 'HTML', ...confirmation.keyboard }
  );
}

// ── /sell <token> <amount> ────────────────────────────────────────────
export async function sellCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  // Ensure user has a wallet
  try {
    getWallet(userId);
  } catch {
    await ctx.reply('👛 You don\'t have a wallet yet. Use /start to create one.', {
      parse_mode: 'HTML',
    });
    return;
  }

  const text =
    ctx.message && 'text' in ctx.message ? ctx.message.text : '';
  const parts = text.trim().split(/\s+/);

  if (parts.length < 3) {
    await ctx.reply(
      '💡 <b>Usage:</b> /sell &lt;token_address&gt; &lt;amount_in_tokens&gt;\n\nExample: /sell So11111111111111111111111111111112 1000',
      { parse_mode: 'HTML' }
    );
    return;
  }

  const token = parts[1];
  const amount = parseFloat(parts[2]);

  if (isNaN(amount) || amount <= 0) {
    await ctx.reply('❌ Invalid amount. Please enter a positive number.', {
      parse_mode: 'HTML',
    });
    return;
  }

  try {
    new PublicKey(token);
  } catch {
    await ctx.reply('❌ Invalid token address. Please check and try again.', {
      parse_mode: 'HTML',
    });
    return;
  }

  const fee = amount * FEE_RATE;
  const quote: SwapQuote = {
    inputToken: token,
    outputToken: 'SOL',
    inputAmount: amount,
    expectedOutput: 0,
    priceImpact: 0,
    fee,
  };

  const confirmation = confirmSwapMenu('sell', token, amount, quote);

  await ctx.reply(confirmation.text, {
    parse_mode: 'HTML',
    ...confirmation.keyboard,
  });
}

// ── /export ───────────────────────────────────────────────────────────
export async function exportCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  try {
    getWallet(userId);
  } catch {
    await ctx.reply('❌ No wallet found. Use /start first.', {
      parse_mode: 'HTML',
    });
    return;
  }

  // First send a warning, then ask for explicit confirmation
  await ctx.reply(
    [
      '🔐 <b>Export Private Key</b>',
      '',
      '⚠️ <b>SECURITY WARNING:</b>',
      '• Never share your private key with anyone',
      '• Delete the message containing your key immediately after saving it',
      '• Anyone with your key can steal all your funds',
      '',
      'Are you sure you want to export?',
    ].join('\n'),
    {
      parse_mode: 'HTML',
      ...Markup.inlineKeyboard([
        [
          Markup.button.callback('✅ Yes, export', 'confirm_export'),
          Markup.button.callback('❌ Cancel', 'cancel'),
        ],
      ]),
    }
  );
}

// ── /settings ─────────────────────────────────────────────────────────
export async function settingsCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  const settings = getSettings(userId);
  const menu = settingsMenu(settings);

  await ctx.reply(menu.text, {
    parse_mode: 'HTML',
    ...menu.keyboard,
  });
}

// ── /revenue (admin only) ─────────────────────────────────────────────
export async function revenueCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  if (!ADMIN_USER_IDS.includes(userId)) {
    await ctx.reply('🚫 This command is restricted to admins.', {
      parse_mode: 'HTML',
    });
    return;
  }

  try {
    const totalAll = await getTotalFees();
    const totalDay = await getTotalFees('day');
    const totalWeek = await getTotalFees('week');
    const dailyRev = await getDailyRevenue(7);

    const dailyLines = dailyRev.map(
      (d) => `  ${d.date}: ${(d.totalFeeLamports / LAMPORTS_PER_SOL).toFixed(6)} SOL (${d.txCount} txs)`
    );

    await ctx.reply(
      [
        '💰 <b>Revenue Dashboard</b>',
        '',
        `All-time fees: <b>${(totalAll / LAMPORTS_PER_SOL).toFixed(6)} SOL</b>`,
        `Last 24h: <b>${(totalDay / LAMPORTS_PER_SOL).toFixed(6)} SOL</b>`,
        `Last 7d: <b>${(totalWeek / LAMPORTS_PER_SOL).toFixed(6)} SOL</b>`,
        `Fee rate: <b>${(FEE_RATE * 100).toFixed(0)}%</b>`,
        '',
        '<b>Daily Breakdown (last 7 days):</b>',
        ...(dailyLines.length > 0 ? dailyLines : ['  No revenue yet']),
      ].join('\n'),
      { parse_mode: 'HTML' }
    );
  } catch (err) {
    await ctx.reply('❌ Failed to load revenue data.', { parse_mode: 'HTML' });
  }
}

// ── Exported stores (for use by callbacks) ────────────────────────────
export { userSettings };
