/**
 * Token Data — Fetch token metadata, prices, and validation via Jupiter APIs.
 * Includes 5-minute caching for token info.
 */

import fetch from 'cross-fetch';
import { PublicKey } from '@solana/web3.js';
import { getConnection } from './rpc';
import {
  TokenInfo,
  TokenPrice,
  KNOWN_TOKENS,
  JUPITER_API_BASE,
  JUPITER_PRICE_API,
} from '../trading/types';

const TOKEN_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const REQUEST_TIMEOUT_MS = 10_000;

interface CacheEntry<T> {
  data: T;
  fetchedAt: number;
}

const tokenInfoCache = new Map<string, CacheEntry<TokenInfo>>();
const tokenPriceCache = new Map<string, CacheEntry<TokenPrice>>();

/**
 * Fetch with timeout.
 */
async function fetchWithTimeout(url: string, timeoutMs: number = REQUEST_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { signal: controller.signal as AbortSignal });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Get token metadata (name, symbol, decimals, logo).
 * Results are cached for 5 minutes.
 *
 * @param mintAddress - Token mint address
 * @returns TokenInfo or null if not found
 */
export async function getTokenInfo(mintAddress: string): Promise<TokenInfo | null> {
  // Check cache
  const cached = tokenInfoCache.get(mintAddress);
  if (cached && Date.now() - cached.fetchedAt < TOKEN_CACHE_TTL_MS) {
    return cached.data;
  }

  try {
    // Use Jupiter token list API
    const response = await fetchWithTimeout(
      `https://tokens.jup.ag/token/${mintAddress}`,
    );

    if (!response.ok) {
      console.warn(`[Tokens] Token info not found for ${mintAddress}: ${response.status}`);
      return null;
    }

    const raw = await response.json();

    const info: TokenInfo = {
      address: raw.address || mintAddress,
      name: raw.name || 'Unknown',
      symbol: raw.symbol || 'UNKNOWN',
      decimals: raw.decimals ?? 9,
      logoURI: raw.logoURI,
      tags: raw.tags,
    };

    tokenInfoCache.set(mintAddress, { data: info, fetchedAt: Date.now() });
    return info;
  } catch (err) {
    console.error(`[Tokens] Failed to fetch token info for ${mintAddress}:`, (err as Error).message);
    return null;
  }
}

/**
 * Get token price in USD via Jupiter price API.
 *
 * @param mintAddress - Token mint address
 * @returns TokenPrice or null if unavailable
 */
export async function getTokenPrice(mintAddress: string): Promise<TokenPrice | null> {
  // Check cache (short TTL for prices - 30 seconds)
  const cached = tokenPriceCache.get(mintAddress);
  if (cached && Date.now() - cached.fetchedAt < 30_000) {
    return cached.data;
  }

  try {
    const response = await fetchWithTimeout(
      `${JUPITER_PRICE_API}/price?ids=${mintAddress}`,
    );

    if (!response.ok) {
      console.warn(`[Tokens] Price fetch failed for ${mintAddress}: ${response.status}`);
      return null;
    }

    const data = await response.json();
    const priceData = data.data?.[mintAddress];

    if (!priceData) {
      return null;
    }

    const price: TokenPrice = {
      id: mintAddress,
      mintSymbol: priceData.mintSymbol,
      vsToken: priceData.vsToken,
      vsTokenSymbol: priceData.vsTokenSymbol,
      price: priceData.price,
      timestamp: Date.now(),
    };

    tokenPriceCache.set(mintAddress, { data: price, fetchedAt: Date.now() });
    return price;
  } catch (err) {
    console.error(`[Tokens] Failed to fetch price for ${mintAddress}:`, (err as Error).message);
    return null;
  }
}

/**
 * Validate that a token mint address is valid and tradeable.
 *
 * @param mintAddress - Token mint address to validate
 * @returns true if valid and tradeable
 */
export async function validateToken(mintAddress: string): Promise<{
  valid: boolean;
  reason?: string;
}> {
  // 1. Check if it's a valid public key
  try {
    new PublicKey(mintAddress);
  } catch {
    return { valid: false, reason: 'Invalid Solana address format' };
  }

  // 2. Check if the account exists on-chain
  try {
    const connection = getConnection();
    const accountInfo = await connection.getAccountInfo(new PublicKey(mintAddress));

    if (!accountInfo) {
      return { valid: false, reason: 'Token account does not exist on-chain' };
    }
  } catch (err) {
    return { valid: false, reason: `RPC error: ${(err as Error).message}` };
  }

  // 3. Check if Jupiter can trade it (try to get a tiny quote)
  try {
    const response = await fetchWithTimeout(
      `${JUPITER_API_BASE}/quote?inputMint=${KNOWN_TOKENS.SOL}&outputMint=${mintAddress}&amount=100000&slippageBps=500`,
    );

    if (!response.ok) {
      return { valid: false, reason: 'Token not tradeable on Jupiter (no routes)' };
    }

    const quote = await response.json();
    if (!quote.outAmount || quote.outAmount === '0') {
      return { valid: false, reason: 'Token has no liquidity on Jupiter' };
    }
  } catch {
    return { valid: false, reason: 'Could not verify tradeability on Jupiter' };
  }

  return { valid: true };
}

/**
 * Get multiple token prices at once.
 */
export async function getTokenPrices(
  mintAddresses: string[],
): Promise<Map<string, number>> {
  const prices = new Map<string, number>();

  try {
    const ids = mintAddresses.join(',');
    const response = await fetchWithTimeout(
      `${JUPITER_PRICE_API}/price?ids=${ids}`,
    );

    if (!response.ok) {
      return prices;
    }

    const data = await response.json();

    for (const mint of mintAddresses) {
      const priceData = data.data?.[mint];
      if (priceData?.price) {
        prices.set(mint, priceData.price);
      }
    }
  } catch (err) {
    console.error('[Tokens] Batch price fetch failed:', (err as Error).message);
  }

  return prices;
}

/**
 * Clear all caches — useful for testing.
 */
export function clearTokenCache(): void {
  tokenInfoCache.clear();
  tokenPriceCache.clear();
}

/**
 * Get display-friendly token symbol, falling back to truncated address.
 */
export async function getTokenSymbol(mintAddress: string): Promise<string> {
  // Check known tokens first
  for (const [symbol, address] of Object.entries(KNOWN_TOKENS)) {
    if (address === mintAddress) return symbol;
  }

  const info = await getTokenInfo(mintAddress);
  if (info?.symbol) return info.symbol;

  // Fallback: truncated address
  return `${mintAddress.slice(0, 4)}...${mintAddress.slice(-4)}`;
}
