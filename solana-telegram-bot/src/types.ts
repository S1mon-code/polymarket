import { Context } from 'telegraf';

/** Extended bot context with session data */
export interface BotContext extends Context {
  session?: UserSession;
}

/** Per-user session stored in memory */
export interface UserSession {
  userId: string;
  walletAddress?: string;
  settings: UserSettings;
  dailyVolume: number;
  dailyVolumeResetAt: number;
  lastInteraction: number;
}

/** User-configurable settings */
export interface UserSettings {
  slippageBps: number;       // basis points: 50, 100, 200, 500
  defaultBuyAmount: number;  // SOL
}

/** Safety score returned by anti-rug checks */
export interface SafetyScore {
  score: number;             // 0-100
  warnings: string[];
  isRisky: boolean;
  details: {
    mintAuthorityRevoked: boolean;
    freezeAuthorityRevoked: boolean;
    liquidityUsd: number;
    topHolderConcentration: number; // percentage
  };
}

/** Result of a risk limit check */
export interface RiskLimitResult {
  allowed: boolean;
  reason?: string;
}

/** User risk limits (admin-adjustable) */
export interface UserRiskLimits {
  maxPerTrade: number;  // SOL
  maxPerDay: number;    // SOL
}

/** Token balance info for wallet display */
export interface TokenBalance {
  mint: string;
  symbol: string;
  amount: number;
  usdValue?: number;
}

/** Swap quote info for confirmation */
export interface SwapQuote {
  inputToken: string;
  outputToken: string;
  inputAmount: number;
  expectedOutput: number;
  priceImpact: number;
  fee: number;          // 1% fee in SOL
  route?: string;
}

export const DEFAULT_SETTINGS: UserSettings = {
  slippageBps: 100,
  defaultBuyAmount: 0.1,
};

export const FEE_RATE = 0.01; // 1%

export const ADMIN_USER_IDS = process.env.ADMIN_USER_IDS?.split(',') ?? [];
