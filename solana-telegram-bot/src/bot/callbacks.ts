import { Context, Markup } from 'telegraf';
import { Connection, PublicKey, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { mainMenu, settingsMenu } from './menus';
import { userWallets, userSettings, feeCollected } from './commands';
import { recordTrade } from '../safety/risk_limits';
import { DEFAULT_SETTINGS, FEE_RATE, UserSettings } from '../types';

const RPC_URL = process.env.SOLANA_RPC_URL ?? 'https://api.mainnet-beta.solana.com';

function getSettings(userId: string): UserSettings {
  return userSettings.get(userId) ?? { ...DEFAULT_SETTINGS };
}

/**
 * Register all callback query handlers on the bot.
 */
export function registerCallbacks(bot: {
  action: (trigger: string | RegExp, handler: (ctx: Context) => Promise<void>) => void;
}) {
  // ── Confirm buy ───────────────────────────────────────────────────
  bot.action(/^confirm_buy_(.+)_([0-9.]+)$/, async (ctx: Context) => {
    const cbQuery = ctx.callbackQuery;
    if (!cbQuery || !('data' in cbQuery)) return;

    const match = cbQuery.data.match(/^confirm_buy_(.+)_([0-9.]+)$/);
    if (!match) return;

    const token = match[1];
    const amount = parseFloat(match[2]);
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    await ctx.answerCbQuery('Processing buy order...');

    try {
      await ctx.editMessageText(
        [
          '⏳ <b>Executing Buy Order</b>',
          '',
          `Token: <code>${token}</code>`,
          `Amount: <b>${amount} SOL</b>`,
          `Fee: <b>${(amount * FEE_RATE).toFixed(6)} SOL</b>`,
          '',
          'Submitting transaction to Solana...',
        ].join('\n'),
        { parse_mode: 'HTML' }
      );

      // In production, this calls the trading module's executeBuy()
      // For now, record the trade and report success placeholder
      recordTrade(userId, amount);
      feeCollected.totalSol += amount * FEE_RATE;

      await ctx.editMessageText(
        [
          '✅ <b>Buy Order Submitted</b>',
          '',
          `Token: <code>${token}</code>`,
          `Amount: <b>${amount} SOL</b>`,
          `Fee: <b>${(amount * FEE_RATE).toFixed(6)} SOL</b>`,
          '',
          '⏳ Waiting for confirmation...',
          '(The trading module will handle the actual swap)',
        ].join('\n'),
        {
          parse_mode: 'HTML',
          ...Markup.inlineKeyboard([
            [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
          ]),
        }
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      await ctx.editMessageText(
        `❌ <b>Buy failed:</b> <code>${msg}</code>`,
        { parse_mode: 'HTML' }
      );
    }
  });

  // ── Confirm sell ──────────────────────────────────────────────────
  bot.action(/^confirm_sell_(.+)_([0-9.]+)$/, async (ctx: Context) => {
    const cbQuery = ctx.callbackQuery;
    if (!cbQuery || !('data' in cbQuery)) return;

    const match = cbQuery.data.match(/^confirm_sell_(.+)_([0-9.]+)$/);
    if (!match) return;

    const token = match[1];
    const amount = parseFloat(match[2]);
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    await ctx.answerCbQuery('Processing sell order...');

    try {
      await ctx.editMessageText(
        [
          '⏳ <b>Executing Sell Order</b>',
          '',
          `Token: <code>${token}</code>`,
          `Amount: <b>${amount}</b>`,
          '',
          'Submitting transaction to Solana...',
        ].join('\n'),
        { parse_mode: 'HTML' }
      );

      // Placeholder for trading module integration
      feeCollected.totalSol += amount * FEE_RATE;

      await ctx.editMessageText(
        [
          '✅ <b>Sell Order Submitted</b>',
          '',
          `Token: <code>${token}</code>`,
          `Amount: <b>${amount}</b>`,
          '',
          '⏳ Waiting for confirmation...',
        ].join('\n'),
        {
          parse_mode: 'HTML',
          ...Markup.inlineKeyboard([
            [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
          ]),
        }
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      await ctx.editMessageText(
        `❌ <b>Sell failed:</b> <code>${msg}</code>`,
        { parse_mode: 'HTML' }
      );
    }
  });

  // ── Cancel ────────────────────────────────────────────────────────
  bot.action('cancel', async (ctx: Context) => {
    await ctx.answerCbQuery('Cancelled');
    await ctx.editMessageText('❌ Action cancelled.', {
      parse_mode: 'HTML',
      ...Markup.inlineKeyboard([
        [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
      ]),
    });
  });

  // ── Refresh balance ───────────────────────────────────────────────
  bot.action('refresh_balance', async (ctx: Context) => {
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    await ctx.answerCbQuery('Refreshing...');

    const address = userWallets.get(userId);
    if (!address) {
      await ctx.editMessageText(
        '👛 No wallet found. Use /start to create one.',
        { parse_mode: 'HTML' }
      );
      return;
    }

    try {
      const connection = new Connection(RPC_URL, 'confirmed');
      const pubkey = new PublicKey(address);
      const balanceLamports = await connection.getBalance(pubkey);
      const balanceSol = balanceLamports / LAMPORTS_PER_SOL;

      await ctx.editMessageText(
        [
          '👛 <b>Your Wallet</b>',
          '',
          `Address: <code>${address}</code>`,
          `SOL Balance: <b>${balanceSol.toFixed(6)} SOL</b>`,
          '',
          `🔄 Updated: ${new Date().toLocaleTimeString()}`,
        ].join('\n'),
        {
          parse_mode: 'HTML',
          ...Markup.inlineKeyboard([
            [Markup.button.callback('🔄 Refresh', 'refresh_balance')],
            [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
          ]),
        }
      );
    } catch {
      await ctx.answerCbQuery('Failed to refresh balance');
    }
  });

  // ── Set slippage ──────────────────────────────────────────────────
  bot.action(/^set_slippage_(\d+)$/, async (ctx: Context) => {
    const cbQuery = ctx.callbackQuery;
    if (!cbQuery || !('data' in cbQuery)) return;

    const match = cbQuery.data.match(/^set_slippage_(\d+)$/);
    if (!match) return;

    const bps = parseInt(match[1], 10);
    const validOptions = [50, 100, 200, 500];
    if (!validOptions.includes(bps)) {
      await ctx.answerCbQuery('Invalid slippage option');
      return;
    }

    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    const settings = getSettings(userId);
    settings.slippageBps = bps;
    userSettings.set(userId, settings);

    await ctx.answerCbQuery(`Slippage set to ${bps / 100}%`);

    const menu = settingsMenu(settings);
    await ctx.editMessageText(menu.text, {
      parse_mode: 'HTML',
      ...menu.keyboard,
    });
  });

  // ── Main menu navigation ──────────────────────────────────────────
  bot.action('menu_main', async (ctx: Context) => {
    await ctx.answerCbQuery();
    await ctx.editMessageText(
      '🏠 <b>Main Menu</b>\n\nWhat would you like to do?',
      { parse_mode: 'HTML', ...mainMenu() }
    );
  });

  bot.action('menu_buy', async (ctx: Context) => {
    await ctx.answerCbQuery();
    await ctx.editMessageText(
      [
        '💰 <b>Buy Tokens</b>',
        '',
        'Send: /buy &lt;token_address&gt; &lt;amount_in_sol&gt;',
        '',
        'Example:',
        '<code>/buy EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.5</code>',
      ].join('\n'),
      {
        parse_mode: 'HTML',
        ...Markup.inlineKeyboard([
          [Markup.button.callback('⬅️ Back', 'menu_main')],
        ]),
      }
    );
  });

  bot.action('menu_sell', async (ctx: Context) => {
    await ctx.answerCbQuery();
    await ctx.editMessageText(
      [
        '💸 <b>Sell Tokens</b>',
        '',
        'Send: /sell &lt;token_address&gt; &lt;amount&gt;',
        '',
        'Example:',
        '<code>/sell EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 1000</code>',
      ].join('\n'),
      {
        parse_mode: 'HTML',
        ...Markup.inlineKeyboard([
          [Markup.button.callback('⬅️ Back', 'menu_main')],
        ]),
      }
    );
  });

  bot.action('menu_wallet', async (ctx: Context) => {
    await ctx.answerCbQuery();
    // Redirect to the /wallet command behavior
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    const address = userWallets.get(userId);
    if (!address) {
      await ctx.editMessageText(
        '👛 No wallet found. Use /start to create one.',
        { parse_mode: 'HTML' }
      );
      return;
    }

    try {
      const connection = new Connection(RPC_URL, 'confirmed');
      const pubkey = new PublicKey(address);
      const balanceLamports = await connection.getBalance(pubkey);
      const balanceSol = balanceLamports / LAMPORTS_PER_SOL;

      await ctx.editMessageText(
        [
          '👛 <b>Your Wallet</b>',
          '',
          `Address: <code>${address}</code>`,
          `SOL Balance: <b>${balanceSol.toFixed(6)} SOL</b>`,
        ].join('\n'),
        {
          parse_mode: 'HTML',
          ...Markup.inlineKeyboard([
            [Markup.button.callback('🔄 Refresh', 'refresh_balance')],
            [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
          ]),
        }
      );
    } catch {
      await ctx.editMessageText(
        '❌ Failed to load wallet. Try again.',
        { parse_mode: 'HTML' }
      );
    }
  });

  bot.action('menu_settings', async (ctx: Context) => {
    await ctx.answerCbQuery();
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    const settings = getSettings(userId);
    const menu = settingsMenu(settings);
    await ctx.editMessageText(menu.text, {
      parse_mode: 'HTML',
      ...menu.keyboard,
    });
  });

  // ── Noop (used for page indicators etc.) ──────────────────────────
  bot.action('noop', async (ctx: Context) => {
    await ctx.answerCbQuery();
  });

  // ── Confirm export (private key) ─────────────────────────────────
  bot.action('confirm_export', async (ctx: Context) => {
    const userId = ctx.from?.id?.toString();
    if (!userId) return;

    await ctx.answerCbQuery();

    // In production, retrieve the actual private key from the wallet module
    await ctx.editMessageText(
      [
        '🔐 <b>Private Key Export</b>',
        '',
        '⚠️ The wallet module will provide the actual private key.',
        '⚠️ <b>Delete this message immediately after saving your key!</b>',
        '',
        '<i>(Wallet module integration pending)</i>',
      ].join('\n'),
      {
        parse_mode: 'HTML',
        ...Markup.inlineKeyboard([
          [Markup.button.callback('⬅️ Main Menu', 'menu_main')],
        ]),
      }
    );
  });
}
