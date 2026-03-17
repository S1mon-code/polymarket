/**
 * Fee Collector — Handles 1% fee deduction and transfer to treasury.
 * Fee is deducted BEFORE the swap: user sends X SOL, 1% goes to treasury, 99% gets swapped.
 */

import {
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  LAMPORTS_PER_SOL,
} from '@solana/web3.js';
import {
  createTransferInstruction,
  getAssociatedTokenAddress,
  getOrCreateAssociatedTokenAccount,
} from '@solana/spl-token';
import dotenv from 'dotenv';
import { getConnection, getRecentBlockhash } from '../data/rpc';
import { FeeRecord, KNOWN_TOKENS } from '../trading/types';
import { config } from '../config';

dotenv.config();

const TREASURY_WALLET = process.env.TREASURY_WALLET_ADDRESS;
const DEFAULT_FEE_PERCENT = 1.0; // 1%

/**
 * Calculate the fee amount in lamports (or smallest token unit).
 *
 * @param amount     - Total amount in lamports
 * @param feePercent - Fee percentage (default 1.0 = 1%)
 * @returns Fee amount in lamports
 */
export function calculateFee(amount: number, feePercent: number = DEFAULT_FEE_PERCENT): number {
  if (amount <= 0) return 0;
  if (feePercent < 0 || feePercent > 100) {
    throw new Error(`Invalid fee percent: ${feePercent}`);
  }
  return Math.floor(amount * (feePercent / 100));
}

/**
 * Calculate the swap amount after fee deduction.
 *
 * @param totalAmount - Total input amount in lamports
 * @param feePercent  - Fee percentage (default 1.0)
 * @returns { feeAmount, swapAmount } both in lamports
 */
export function splitFeeAndSwap(
  totalAmount: number,
  feePercent: number = DEFAULT_FEE_PERCENT,
): { feeAmount: number; swapAmount: number } {
  const feeAmount = calculateFee(totalAmount, feePercent);
  const swapAmount = totalAmount - feeAmount;
  return { feeAmount, swapAmount };
}

/**
 * Transfer fee to treasury wallet.
 * Supports both SOL (native) and SPL token transfers.
 *
 * @param wallet    - User's Keypair (payer)
 * @param feeAmount - Amount to transfer in lamports / smallest unit
 * @param feeMint   - Mint address of the fee token (SOL native mint for SOL)
 * @returns Transaction hash
 */
export async function collectFee(
  wallet: Keypair,
  feeAmount: number,
  feeMint: string,
): Promise<string> {
  if (config.dryRun || config.isDevnet) {
    console.log(`[FeeCollector] ${config.isDevnet ? 'DEVNET' : 'DRY_RUN'} — skipping fee transfer of ${feeAmount} lamports (${feeMint})`);
    return 'dry_run_no_fee_tx';
  }

  if (!TREASURY_WALLET) {
    throw new Error('TREASURY_WALLET_ADDRESS not set in environment');
  }

  if (feeAmount <= 0) {
    throw new Error(`Invalid fee amount: ${feeAmount}`);
  }

  const connection = getConnection();
  const treasuryPubkey = new PublicKey(TREASURY_WALLET);
  const { blockhash, lastValidBlockHeight } = await getRecentBlockhash();

  const transaction = new Transaction();
  transaction.recentBlockhash = blockhash;
  transaction.feePayer = wallet.publicKey;

  const isNativeSOL = feeMint === KNOWN_TOKENS.SOL;

  if (isNativeSOL) {
    // Native SOL transfer
    transaction.add(
      SystemProgram.transfer({
        fromPubkey: wallet.publicKey,
        toPubkey: treasuryPubkey,
        lamports: feeAmount,
      }),
    );
  } else {
    // SPL token transfer
    const mintPubkey = new PublicKey(feeMint);

    const senderATA = await getAssociatedTokenAddress(mintPubkey, wallet.publicKey);
    const treasuryATA = await getOrCreateAssociatedTokenAccount(
      connection,
      wallet,
      mintPubkey,
      treasuryPubkey,
    );

    transaction.add(
      createTransferInstruction(
        senderATA,
        treasuryATA.address,
        wallet.publicKey,
        feeAmount,
      ),
    );
  }

  transaction.sign(wallet);

  const txHash = await connection.sendRawTransaction(transaction.serialize(), {
    skipPreflight: false,
    maxRetries: 2,
    preflightCommitment: 'confirmed',
  });

  await connection.confirmTransaction(
    { signature: txHash, blockhash, lastValidBlockHeight },
    'confirmed',
  );

  console.log(
    `[FeeCollector] Collected ${feeAmount} lamports (${feeMint}) → treasury. TX: ${txHash}`,
  );

  return txHash;
}

/**
 * Build a FeeRecord for database insertion.
 */
export function buildFeeRecord(
  telegramUserId: string,
  txHash: string,
  feeMint: string,
  feeAmount: number,
  feeUsd?: number,
): FeeRecord {
  return {
    telegramUserId,
    txHash,
    feeMint,
    feeAmount,
    feeUsd,
    timestamp: Date.now(),
  };
}
