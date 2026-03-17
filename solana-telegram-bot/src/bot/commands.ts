import { Context, Markup } from 'telegraf';
import { PublicKey, Connection, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { mainMenu, confirmSwapMenu, settingsMenu } from './menus';
import { checkToken, formatSafetyReport } from '../safety/anti_rug';
import { checkTradeLimit, formatLimitsInfo } from '../safety/risk_limits';
import {
  BotContext,
  DEFAULT_SETTINGS,
  UserSettings,
  SwapQuote,
  FEE_RATE,
  ADMIN_USER_IDS,
} from '../types';

// ── In-memory stores (to be replaced with DB in production) ───────────
const userWallets = new Map<string, string>();   // tgUserId → walletAddress
const userSettings = new Map<string, UserSettings>();
const feeCollected = { totalSol: 0 };

const RPC_URL = process.env.SOLANA_RPC_URL ?? 'https://api.mainnet-beta.solana.com';

function getSettings(userId: string): UserSettings {
  return userSettings.get(userId) ?? { ...DEFAULT_SETTINGS };
}

// ── /start ────────────────────────────────────────────────────────────
export async function startCommand(ctx: Context) {
  const userId = ctx.from?.id?.toString();
  if (!userId) return;

  let walletMsg: string;
  if (userWallets.has(userId)) {
    walletMsg = `Your wallet: <code>${userWallets.get(userId)}</code>`;
  } else {
    // In production, the wallet module creates a real Keypair.
    // Here we store a placeholder to signal "user registered".
    walletMsg = '🔑 A new wallet will be generated for you on first trade.';
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

  const address = userWallets.get(userId);
  if (!address) {
    await ctx.reply(
      '👛 You don\'t have a wallet yet. Use /start to create one.',
      { parse_mode: 'HTML' }
    );
    return;
  }

  try {
    const connection = new Connection(RPC_URL, 'confirmed');
    const pubkey = new PublicKey(address);
    const balanceLamports = await connection.getBalance(pubkey);
    const balanceSol = balanceLamports / LAMPORTS_PER_SOL;

    await ctx.reply(
      [
        '👛 <b>Your Wallet</b>',
        '',
        `Address: <code>${address}</code>`,
        `SOL Balance: <b>${balanceSol.toFixed(6)} SOL</b>`,
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

  // Build a mock quote (in production, this comes from Jupiter API)
  const fee = amount * FEE_RATE;
  const quote: SwapQuote = {
    inputToken: 'SOL',
    outputToken: token,
    inputAmount: amount,
    expectedOutput: 0, // filled by trading module
    priceImpact: 0,
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

  if (!userWallets.has(userId)) {
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

  await ctx.reply(
    [
      '💰 <b>Revenue Dashboard</b>',
      '',
      `Total fees collected: <b>${feeCollected.totalSol.toFixed(6)} SOL</b>`,
      `Fee rate: <b>${(FEE_RATE * 100).toFixed(0)}%</b>`,
    ].join('\n'),
    { parse_mode: 'HTML' }
  );
}

// ── Exported stores (for use by callbacks) ────────────────────────────
export { userWallets, userSettings, feeCollected };
