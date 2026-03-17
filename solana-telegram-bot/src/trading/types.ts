/**
 * Trading Types — Shared interfaces for trading, fee, and data modules.
 */

/** Jupiter v6 quote response */
export interface QuoteResponse {
  inputMint: string;
  inAmount: string;
  outputMint: string;
  outAmount: string;
  otherAmountThreshold: string;
  swapMode: string;
  slippageBps: number;
  priceImpactPct: string;
  routePlan: RoutePlanStep[];
  contextSlot?: number;
  timeTaken?: number;
}

export interface RoutePlanStep {
  swapInfo: {
    ammKey: string;
    label: string;
    inputMint: string;
    outputMint: string;
    inAmount: string;
    outAmount: string;
    feeAmount: string;
    feeMint: string;
  };
  percent: number;
}

/** Jupiter v6 swap response */
export interface SwapResponse {
  swapTransaction: string;
  lastValidBlockHeight: number;
  prioritizationFeeLamports?: number;
}

/** Parameters for a swap request */
export interface SwapParams {
  wallet: import('@solana/web3.js').Keypair;
  inputMint: string;
  outputMint: string;
  amount: number; // in lamports (or smallest unit)
  slippageBps: number;
  dryRun?: boolean;
}

/** Result returned after a swap attempt */
export interface SwapResult {
  success: boolean;
  txHash: string | null;
  amountIn: number;
  amountOut: number;
  feeAmount: number;
  error?: string;
}

/** Fee record stored in the database */
export interface FeeRecord {
  id?: number;
  telegramUserId: string;
  txHash: string;
  feeMint: string;
  feeAmount: number; // lamports
  feeUsd?: number;
  timestamp: number;
}

/** Token metadata */
export interface TokenInfo {
  address: string;
  name: string;
  symbol: string;
  decimals: number;
  logoURI?: string;
  tags?: string[];
}

/** Token price response */
export interface TokenPrice {
  id: string;
  mintSymbol?: string;
  vsToken?: string;
  vsTokenSymbol?: string;
  price: number;
  timestamp: number;
}

/** Daily revenue entry */
export interface DailyRevenue {
  date: string; // YYYY-MM-DD
  totalFeeLamports: number;
  totalFeeUsd: number;
  txCount: number;
}

/** Well-known token addresses on Solana mainnet */
export const KNOWN_TOKENS = {
  SOL: 'So11111111111111111111111111111111111111112',
  USDC: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
  USDT: 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
  BONK: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',
  JUP: 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN',
  RAY: '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R',
} as const;

/** Jupiter API base URL */
export const JUPITER_API_BASE = 'https://quote-api.jup.ag/v6';

/** Jupiter price API base URL */
export const JUPITER_PRICE_API = 'https://price.jup.ag/v6';
