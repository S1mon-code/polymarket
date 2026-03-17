/**
 * Jupiter DEX Integration — Core revenue engine.
 * Handles quote fetching and swap execution via Jupiter v6 API.
 */

import {
  Keypair,
  VersionedTransaction,
  TransactionMessage,
  AddressLookupTableAccount,
} from '@solana/web3.js';
import fetch from 'cross-fetch';
import { getConnection, getRecentBlockhash } from '../data/rpc';
import {
  QuoteResponse,
  SwapResponse,
  JUPITER_API_BASE,
} from './types';

const MAX_RETRIES = 3;
const BASE_DELAY_MS = 500;
const REQUEST_TIMEOUT_MS = 15_000;

/**
 * Fetch with timeout wrapper.
 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal as AbortSignal,
    });
    return response;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Retry wrapper with exponential backoff.
 */
async function withRetry<T>(
  fn: () => Promise<T>,
  retries: number = MAX_RETRIES,
): Promise<T> {
  let lastError: Error | undefined;

  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err as Error;
      if (attempt < retries - 1) {
        const delay = BASE_DELAY_MS * Math.pow(2, attempt);
        console.warn(
          `[Jupiter] Attempt ${attempt + 1} failed: ${lastError.message}. Retrying in ${delay}ms...`,
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  throw new Error(`[Jupiter] All ${retries} attempts failed: ${lastError?.message}`);
}

/**
 * Get a swap quote from Jupiter v6.
 *
 * @param inputMint  - Input token mint address
 * @param outputMint - Output token mint address
 * @param amount     - Amount in smallest unit (lamports for SOL)
 * @param slippageBps - Slippage tolerance in basis points
 * @returns QuoteResponse from Jupiter
 */
export async function getQuote(
  inputMint: string,
  outputMint: string,
  amount: number,
  slippageBps: number,
): Promise<QuoteResponse> {
  return withRetry(async () => {
    const params = new URLSearchParams({
      inputMint,
      outputMint,
      amount: Math.floor(amount).toString(),
      slippageBps: slippageBps.toString(),
      onlyDirectRoutes: 'false',
      asLegacyTransaction: 'false',
    });

    const url = `${JUPITER_API_BASE}/quote?${params.toString()}`;
    const response = await fetchWithTimeout(url);

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Jupiter quote failed (${response.status}): ${body}`);
    }

    const data: QuoteResponse = await response.json();

    if (!data.outAmount || data.outAmount === '0') {
      throw new Error('Jupiter returned zero output amount — token may be illiquid');
    }

    return data;
  });
}

/**
 * Get a serialized swap transaction from Jupiter v6.
 *
 * @param quoteResponse - The quote to execute
 * @param userPublicKey - The user's wallet public key (base58)
 * @returns SwapResponse containing the serialized transaction
 */
export async function getSwapTransaction(
  quoteResponse: QuoteResponse,
  userPublicKey: string,
): Promise<SwapResponse> {
  return withRetry(async () => {
    const response = await fetchWithTimeout(
      `${JUPITER_API_BASE}/swap`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quoteResponse,
          userPublicKey,
          wrapAndUnwrapSol: true,
          dynamicComputeUnitLimit: true,
          prioritizationFeeLamports: 'auto',
        }),
      },
    );

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Jupiter swap transaction failed (${response.status}): ${body}`);
    }

    const data: SwapResponse = await response.json();

    if (!data.swapTransaction) {
      throw new Error('Jupiter returned empty swap transaction');
    }

    return data;
  });
}

/**
 * Execute a swap: deserialize the Jupiter transaction, sign it, and send it.
 *
 * @param wallet - The user's Keypair (signer)
 * @param quote  - The QuoteResponse to execute
 * @returns Transaction signature (hash)
 */
export async function executeSwap(
  wallet: Keypair,
  quote: QuoteResponse,
): Promise<string> {
  const swapResponse = await getSwapTransaction(
    quote,
    wallet.publicKey.toBase58(),
  );

  const connection = getConnection();

  // Deserialize the versioned transaction
  const swapTransactionBuf = Buffer.from(swapResponse.swapTransaction, 'base64');
  const transaction = VersionedTransaction.deserialize(swapTransactionBuf);

  // Sign with user's wallet
  transaction.sign([wallet]);

  // Send with preflight checks
  const txHash = await connection.sendRawTransaction(transaction.serialize(), {
    skipPreflight: false,
    maxRetries: 2,
    preflightCommitment: 'confirmed',
  });

  // Confirm the transaction
  const { blockhash, lastValidBlockHeight } = await getRecentBlockhash();
  await connection.confirmTransaction(
    {
      signature: txHash,
      blockhash,
      lastValidBlockHeight: swapResponse.lastValidBlockHeight || lastValidBlockHeight,
    },
    'confirmed',
  );

  console.log(`[Jupiter] Swap confirmed: ${txHash}`);
  return txHash;
}

/**
 * Simulate a swap (DRY_RUN). Returns the quote without executing.
 */
export async function simulateSwap(
  inputMint: string,
  outputMint: string,
  amount: number,
  slippageBps: number,
): Promise<QuoteResponse> {
  const quote = await getQuote(inputMint, outputMint, amount, slippageBps);
  console.log('[Jupiter] DRY_RUN simulation:', {
    inputMint,
    outputMint,
    amountIn: amount,
    expectedOut: quote.outAmount,
    priceImpact: quote.priceImpactPct,
    slippageBps,
  });
  return quote;
}
