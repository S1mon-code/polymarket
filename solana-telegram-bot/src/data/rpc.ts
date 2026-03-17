/**
 * RPC Connection Manager — Manages Solana RPC connections with caching and fallback.
 */

import { Connection, Commitment } from '@solana/web3.js';
import dotenv from 'dotenv';
import { config } from '../config';

dotenv.config();

const DEVNET_RPC_URL = 'https://api.devnet.solana.com';

const HELIUS_RPC_URL = config.isDevnet
  ? DEVNET_RPC_URL
  : (process.env.HELIUS_RPC_URL || 'https://api.mainnet-beta.solana.com');
const FALLBACK_RPC_URL = config.isDevnet
  ? DEVNET_RPC_URL
  : (process.env.FALLBACK_RPC_URL || 'https://api.mainnet-beta.solana.com');
const BLOCKHASH_CACHE_TTL_MS = 30_000; // 30 seconds

interface BlockhashCache {
  blockhash: string;
  lastValidBlockHeight: number;
  fetchedAt: number;
}

let primaryConnection: Connection | null = null;
let fallbackConnection: Connection | null = null;
let blockhashCache: BlockhashCache | null = null;
let usingFallback = false;

/**
 * Returns the primary Solana Connection (Helius RPC preferred).
 * Falls back to public RPC if primary is unhealthy.
 */
export function getConnection(): Connection {
  if (usingFallback) {
    if (!fallbackConnection) {
      fallbackConnection = new Connection(FALLBACK_RPC_URL, {
        commitment: 'confirmed' as Commitment,
        confirmTransactionInitialTimeout: 60_000,
      });
    }
    return fallbackConnection;
  }

  if (!primaryConnection) {
    primaryConnection = new Connection(HELIUS_RPC_URL, {
      commitment: 'confirmed' as Commitment,
      confirmTransactionInitialTimeout: 60_000,
    });
  }
  return primaryConnection;
}

/**
 * Returns a cached recent blockhash, refreshing every 30 seconds.
 */
export async function getRecentBlockhash(): Promise<{
  blockhash: string;
  lastValidBlockHeight: number;
}> {
  const now = Date.now();

  if (blockhashCache && now - blockhashCache.fetchedAt < BLOCKHASH_CACHE_TTL_MS) {
    return {
      blockhash: blockhashCache.blockhash,
      lastValidBlockHeight: blockhashCache.lastValidBlockHeight,
    };
  }

  const conn = getConnection();
  const { blockhash, lastValidBlockHeight } = await conn.getLatestBlockhash('confirmed');

  blockhashCache = {
    blockhash,
    lastValidBlockHeight,
    fetchedAt: now,
  };

  return { blockhash, lastValidBlockHeight };
}

/**
 * Health check: pings the current RPC and switches to fallback if it fails.
 * Returns true if healthy, false if switched to fallback.
 */
export async function healthCheck(): Promise<boolean> {
  const conn = getConnection();
  try {
    const slot = await conn.getSlot();
    if (slot > 0) {
      // If we were on fallback and primary recovers, switch back
      if (usingFallback) {
        try {
          if (!primaryConnection) {
            primaryConnection = new Connection(HELIUS_RPC_URL, {
              commitment: 'confirmed' as Commitment,
            });
          }
          const primarySlot = await primaryConnection.getSlot();
          if (primarySlot > 0) {
            usingFallback = false;
            console.log('[RPC] Primary RPC recovered, switching back');
          }
        } catch {
          // Primary still down, stay on fallback
        }
      }
      return true;
    }
    throw new Error('Invalid slot');
  } catch (err) {
    console.error('[RPC] Health check failed:', (err as Error).message);
    if (!usingFallback) {
      console.warn('[RPC] Switching to fallback RPC');
      usingFallback = true;
      blockhashCache = null; // Invalidate cache on switch
    }
    return false;
  }
}

/**
 * Force reset all connections and caches — useful for testing.
 */
export function resetConnections(): void {
  primaryConnection = null;
  fallbackConnection = null;
  blockhashCache = null;
  usingFallback = false;
}

/**
 * Returns current RPC URL for diagnostics.
 */
export function getCurrentRpcUrl(): string {
  return usingFallback ? FALLBACK_RPC_URL : HELIUS_RPC_URL;
}
