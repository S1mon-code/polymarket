import { Connection, PublicKey } from '@solana/web3.js';
import { SafetyScore } from '../types';

const RPC_URL = process.env.SOLANA_RPC_URL ?? 'https://api.mainnet-beta.solana.com';

/**
 * Run anti-rug safety checks on a token before buying.
 *
 * Checks:
 * 1. Mint authority revoked
 * 2. Freeze authority revoked
 * 3. Liquidity pool size
 * 4. Top-holder concentration
 */
export async function checkToken(mintAddress: string): Promise<SafetyScore> {
  const connection = new Connection(RPC_URL, 'confirmed');
  const warnings: string[] = [];
  let score = 100;

  let mintPubkey: PublicKey;
  try {
    mintPubkey = new PublicKey(mintAddress);
  } catch {
    return {
      score: 0,
      warnings: ['Invalid mint address'],
      isRisky: true,
      details: {
        mintAuthorityRevoked: false,
        freezeAuthorityRevoked: false,
        liquidityUsd: 0,
        topHolderConcentration: 100,
      },
    };
  }

  // ── 1. Check mint & freeze authority ────────────────────────────────
  let mintAuthorityRevoked = false;
  let freezeAuthorityRevoked = false;

  try {
    const accountInfo = await connection.getAccountInfo(mintPubkey);
    if (!accountInfo) {
      return {
        score: 0,
        warnings: ['Token mint account not found on-chain'],
        isRisky: true,
        details: {
          mintAuthorityRevoked: false,
          freezeAuthorityRevoked: false,
          liquidityUsd: 0,
          topHolderConcentration: 100,
        },
      };
    }

    // SPL Token Mint layout: mintAuthority at offset 0 (36 bytes: 4-byte option + 32-byte pubkey)
    // freezeAuthority at offset 46 (36 bytes)
    const data = accountInfo.data;
    if (data.length >= 82) {
      // mintAuthority option flag at byte 0: 0 = None (revoked), 1 = Some
      const mintAuthOption = data[0];
      mintAuthorityRevoked = mintAuthOption === 0;

      // freezeAuthority option flag at byte 46
      const freezeAuthOption = data[46];
      freezeAuthorityRevoked = freezeAuthOption === 0;
    }
  } catch (err) {
    warnings.push('Could not fetch mint account data');
    score -= 15;
  }

  if (!mintAuthorityRevoked) {
    warnings.push('⚠️ Mint authority is NOT revoked — team can mint unlimited tokens');
    score -= 30;
  }
  if (!freezeAuthorityRevoked) {
    warnings.push('⚠️ Freeze authority is NOT revoked — team can freeze your tokens');
    score -= 20;
  }

  // ── 2. Check liquidity (via largest token accounts as proxy) ────────
  let liquidityUsd = 0;
  try {
    // Use token supply and largest accounts as a rough liquidity proxy
    const supply = await connection.getTokenSupply(mintPubkey);
    const decimals = supply.value.decimals;
    const totalSupply = Number(supply.value.amount) / 10 ** decimals;

    // In production you'd query a DEX API for actual liquidity.
    // As a proxy we estimate from holder distribution.
    // For now we mark unknown liquidity as needing caution.
    liquidityUsd = 0; // will be updated below if we can estimate
  } catch {
    warnings.push('Could not fetch token supply');
    score -= 10;
  }

  // ── 3. Top holder concentration ─────────────────────────────────────
  let topHolderConcentration = 0;
  try {
    const largestAccounts = await connection.getTokenLargestAccounts(mintPubkey);
    const accounts = largestAccounts.value;

    if (accounts.length > 0) {
      const supply = await connection.getTokenSupply(mintPubkey);
      const totalSupplyRaw = Number(supply.value.amount);

      if (totalSupplyRaw > 0) {
        // Sum top 10 holders
        const top10Sum = accounts
          .slice(0, 10)
          .reduce((sum, acc) => sum + Number(acc.amount), 0);

        topHolderConcentration = (top10Sum / totalSupplyRaw) * 100;
      }
    }
  } catch {
    warnings.push('Could not fetch holder distribution');
    score -= 10;
  }

  if (topHolderConcentration > 50) {
    warnings.push(
      `⚠️ Top 10 holders own ${topHolderConcentration.toFixed(1)}% of supply — high concentration`
    );
    score -= 25;
  } else if (topHolderConcentration > 30) {
    warnings.push(
      `⚠️ Top 10 holders own ${topHolderConcentration.toFixed(1)}% of supply — moderate concentration`
    );
    score -= 10;
  }

  // ── 4. Liquidity warning ────────────────────────────────────────────
  // In a production bot you'd hit Jupiter or Raydium API for real LP data.
  // For now, flag that liquidity is unknown.
  if (liquidityUsd < 10_000) {
    warnings.push(
      liquidityUsd === 0
        ? '⚠️ Liquidity data unavailable — trade with caution'
        : `⚠️ Low liquidity: ~$${liquidityUsd.toLocaleString()}`
    );
    score -= 10;
  }

  // Clamp score
  score = Math.max(0, Math.min(100, score));

  return {
    score,
    warnings,
    isRisky: score < 50,
    details: {
      mintAuthorityRevoked,
      freezeAuthorityRevoked,
      liquidityUsd,
      topHolderConcentration,
    },
  };
}

/**
 * Format a SafetyScore into a human-readable Telegram message (HTML).
 */
export function formatSafetyReport(s: SafetyScore): string {
  const scoreEmoji =
    s.score >= 80 ? '🟢' : s.score >= 50 ? '🟡' : '🔴';

  const lines = [
    `${scoreEmoji} <b>Safety Score: ${s.score}/100</b>`,
    '',
    `Mint authority revoked: ${s.details.mintAuthorityRevoked ? '✅ Yes' : '❌ No'}`,
    `Freeze authority revoked: ${s.details.freezeAuthorityRevoked ? '✅ Yes' : '❌ No'}`,
    `Top 10 holder concentration: <b>${s.details.topHolderConcentration.toFixed(1)}%</b>`,
    s.details.liquidityUsd > 0
      ? `Liquidity: <b>$${s.details.liquidityUsd.toLocaleString()}</b>`
      : 'Liquidity: <b>Unknown</b>',
  ];

  if (s.warnings.length > 0) {
    lines.push('', '<b>Warnings:</b>');
    s.warnings.forEach((w) => lines.push(`• ${w}`));
  }

  if (s.isRisky) {
    lines.push(
      '',
      '🚨 <b>HIGH RISK TOKEN</b> — Proceed with extreme caution!'
    );
  }

  return lines.join('\n');
}
