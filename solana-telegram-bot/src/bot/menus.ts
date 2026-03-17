import { Markup } from 'telegraf';
import { SwapQuote, UserSettings, TokenBalance } from '../types';

/**
 * Main menu — Buy / Sell / Wallet / Settings
 */
export function mainMenu() {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('💰 Buy', 'menu_buy'),
      Markup.button.callback('💸 Sell', 'menu_sell'),
    ],
    [
      Markup.button.callback('👛 Wallet', 'menu_wallet'),
      Markup.button.callback('⚙️ Settings', 'menu_settings'),
    ],
    [Markup.button.callback('🔄 Refresh Balance', 'refresh_balance')],
  ]);
}

/**
 * Confirmation keyboard for buy/sell swaps.
 * Shows price impact, fee, and confirm/cancel buttons.
 */
export function confirmSwapMenu(
  action: 'buy' | 'sell',
  token: string,
  amount: number,
  quote: SwapQuote
) {
  const confirmData = `confirm_${action}_${token}_${amount}`;
  const priceImpactPct = (quote.priceImpact * 100).toFixed(2);
  const feeDisplay = quote.fee.toFixed(6);

  const lines = [
    `<b>${action === 'buy' ? '🟢 Buy' : '🔴 Sell'} Confirmation</b>`,
    '',
    `Token: <code>${token}</code>`,
    `Amount: <b>${amount} SOL</b>`,
    `Expected output: <b>${quote.expectedOutput.toFixed(6)}</b>`,
    `Price impact: ${Number(priceImpactPct) > 2 ? '⚠️' : '✅'} <b>${priceImpactPct}%</b>`,
    `Fee (1%): <b>${feeDisplay} SOL</b>`,
    quote.route ? `Route: ${quote.route}` : '',
  ].filter(Boolean);

  return {
    text: lines.join('\n'),
    keyboard: Markup.inlineKeyboard([
      [
        Markup.button.callback(`✅ Confirm ${action.toUpperCase()}`, confirmData),
        Markup.button.callback('❌ Cancel', 'cancel'),
      ],
    ]),
  };
}

/**
 * Settings menu showing current slippage and adjustment buttons.
 */
export function settingsMenu(currentSettings: UserSettings) {
  const slippageOptions = [50, 100, 200, 500];
  const slippageButtons = slippageOptions.map((bps) => {
    const label =
      bps === currentSettings.slippageBps
        ? `✅ ${bps / 100}%`
        : `${bps / 100}%`;
    return Markup.button.callback(label, `set_slippage_${bps}`);
  });

  return {
    text: [
      '<b>⚙️ Settings</b>',
      '',
      `Slippage: <b>${currentSettings.slippageBps / 100}%</b>`,
      `Default buy amount: <b>${currentSettings.defaultBuyAmount} SOL</b>`,
      '',
      'Tap a button to change slippage:',
    ].join('\n'),
    keyboard: Markup.inlineKeyboard([
      slippageButtons.slice(0, 2),
      slippageButtons.slice(2, 4),
      [Markup.button.callback('⬅️ Back', 'menu_main')],
    ]),
  };
}

/**
 * Paginated token list menu.
 * Shows up to 8 tokens per page with navigation.
 */
export function tokenListMenu(
  tokens: TokenBalance[],
  page = 0,
  pageSize = 8
) {
  const totalPages = Math.max(1, Math.ceil(tokens.length / pageSize));
  const safePage = Math.max(0, Math.min(page, totalPages - 1));
  const slice = tokens.slice(safePage * pageSize, (safePage + 1) * pageSize);

  const tokenButtons = slice.map((t) =>
    [
      Markup.button.callback(
        `${t.symbol} — ${t.amount.toFixed(4)}${t.usdValue ? ` ($${t.usdValue.toFixed(2)})` : ''}`,
        `select_token_${t.mint}`
      ),
    ]
  );

  const navRow: ReturnType<typeof Markup.button.callback>[] = [];
  if (safePage > 0) {
    navRow.push(Markup.button.callback('⬅️ Prev', `token_page_${safePage - 1}`));
  }
  navRow.push(Markup.button.callback(`${safePage + 1}/${totalPages}`, 'noop'));
  if (safePage < totalPages - 1) {
    navRow.push(Markup.button.callback('➡️ Next', `token_page_${safePage + 1}`));
  }

  return {
    text: '<b>📊 Your Tokens</b>\n\nSelect a token to trade:',
    keyboard: Markup.inlineKeyboard([...tokenButtons, navRow]),
  };
}
