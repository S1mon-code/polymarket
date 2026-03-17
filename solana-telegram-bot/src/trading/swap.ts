/**
 * Swap Manager — High-level buy/sell with fee deduction.
 * Flow: calculate fee → collect fee → get quote → execute swap → return result.
 */

import { Keypair } from '@solana/web3.js';
import dotenv from 'dotenv';
import { getQuote, executeSwap, simulateSwap } from './jupiter';
import { calculateFee, splitFeeAndSwap, collectFee } from '../fees/collector';
import { SwapResult, KNOWN_TOKENS } from './types';
import { config } from '../config';

dotenv.config();

const DRY_RUN = config.dryRun;
const IS_DEVNET = config.isDevnet;

/**
 * Buy a token with SOL.
 * Flow: SOL → deduct 1% fee → swap remaining SOL for token.
 *
 * @param wallet      - User's Keypair
 * @param tokenMint   - Target token mint address
 * @param solAmount   - Total SOL amount in lamports
 * @param slippageBps - Slippage tolerance in basis points
 * @returns SwapResult
 */
export async function buyToken(
  wallet: Keypair,
  tokenMint: string,
  solAmount: number,
  slippageBps: number,
): Promise<SwapResult> {
  try {
    // 1. Split fee and swap amounts
    const { feeAmount, swapAmount } = splitFeeAndSwap(solAmount);

    console.log(`[Swap] BUY: ${solAmount} lamports SOL → ${tokenMint}`);
    console.log(`[Swap]   Fee: ${feeAmount} lamports | Swap: ${swapAmount} lamports`);

    if (DRY_RUN || IS_DEVNET) {
      console.log(`[Swap] ${IS_DEVNET ? 'DEVNET' : 'DRY_RUN'} mode — simulating only`);
      return await dryRunSwap(
        KNOWN_TOKENS.SOL,
        tokenMint,
        swapAmount,
        slippageBps,
        feeAmount,
      );
    }

    // 2. Collect fee (transfer to treasury)
    await collectFee(wallet, feeAmount, KNOWN_TOKENS.SOL);

    // 3. Get quote for the net amount
    const quote = await getQuote(KNOWN_TOKENS.SOL, tokenMint, swapAmount, slippageBps);

    // 4. Execute the swap
    const txHash = await executeSwap(wallet, quote);

    return {
      success: true,
      txHash,
      amountIn: swapAmount,
      amountOut: parseInt(quote.outAmount, 10),
      feeAmount,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[Swap] BUY failed: ${message}`);
    return {
      success: false,
      txHash: null,
      amountIn: solAmount,
      amountOut: 0,
      feeAmount: 0,
      error: message,
    };
  }
}

/**
 * Sell a token for SOL.
 * Flow: swap token → receive SOL → deduct 1% fee from received SOL.
 *
 * Note: For sells, the fee is on the input token amount (deducted before swap).
 * User sends 100 tokens, 1 token fee → 99 tokens get swapped.
 *
 * @param wallet      - User's Keypair
 * @param tokenMint   - Token to sell (mint address)
 * @param tokenAmount - Amount of tokens in smallest unit
 * @param slippageBps - Slippage tolerance in basis points
 * @returns SwapResult
 */
export async function sellToken(
  wallet: Keypair,
  tokenMint: string,
  tokenAmount: number,
  slippageBps: number,
): Promise<SwapResult> {
  try {
    // 1. Split fee and swap amounts (fee deducted from token input)
    const { feeAmount, swapAmount } = splitFeeAndSwap(tokenAmount);

    console.log(`[Swap] SELL: ${tokenAmount} ${tokenMint} → SOL`);
    console.log(`[Swap]   Fee: ${feeAmount} tokens | Swap: ${swapAmount} tokens`);

    if (DRY_RUN || IS_DEVNET) {
      console.log(`[Swap] ${IS_DEVNET ? 'DEVNET' : 'DRY_RUN'} mode — simulating only`);
      return await dryRunSwap(
        tokenMint,
        KNOWN_TOKENS.SOL,
        swapAmount,
        slippageBps,
        feeAmount,
      );
    }

    // 2. Collect fee (transfer token fee to treasury)
    await collectFee(wallet, feeAmount, tokenMint);

    // 3. Get quote for the net amount
    const quote = await getQuote(tokenMint, KNOWN_TOKENS.SOL, swapAmount, slippageBps);

    // 4. Execute the swap
    const txHash = await executeSwap(wallet, quote);

    return {
      success: true,
      txHash,
      amountIn: swapAmount,
      amountOut: parseInt(quote.outAmount, 10),
      feeAmount,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[Swap] SELL failed: ${message}`);
    return {
      success: false,
      txHash: null,
      amountIn: tokenAmount,
      amountOut: 0,
      feeAmount: 0,
      error: message,
    };
  }
}

/**
 * Dry-run swap: get a quote but don't execute.
 */
async function dryRunSwap(
  inputMint: string,
  outputMint: string,
  swapAmount: number,
  slippageBps: number,
  feeAmount: number,
): Promise<SwapResult> {
  const label = IS_DEVNET ? 'DEVNET' : 'DRY_RUN';

  const quote = await simulateSwap(inputMint, outputMint, swapAmount, slippageBps);

  return {
    success: true,
    txHash: null,
    amountIn: swapAmount,
    amountOut: parseInt(quote.outAmount, 10),
    feeAmount,
    error: `${label} — no transaction sent`,
  };
}
