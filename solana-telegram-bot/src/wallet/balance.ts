import {
  Connection,
  PublicKey,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import { TOKEN_PROGRAM_ID } from "@solana/spl-token";

export interface TokenBalance {
  mint: string;
  amount: string;
  decimals: number;
  uiAmount: number;
}

export function formatBalance(lamports: number): string {
  return (lamports / LAMPORTS_PER_SOL).toFixed(9);
}

export async function getSolBalance(
  connection: Connection,
  publicKey: PublicKey
): Promise<string> {
  const lamports = await connection.getBalance(publicKey);
  return formatBalance(lamports);
}

export async function getTokenBalances(
  connection: Connection,
  publicKey: PublicKey
): Promise<TokenBalance[]> {
  const tokenAccounts = await connection.getParsedTokenAccountsByOwner(
    publicKey,
    { programId: TOKEN_PROGRAM_ID }
  );

  return tokenAccounts.value.map((account) => {
    const parsed = account.account.data.parsed as {
      info: {
        mint: string;
        tokenAmount: {
          amount: string;
          decimals: number;
          uiAmount: number;
        };
      };
    };
    const info = parsed.info;
    return {
      mint: info.mint,
      amount: info.tokenAmount.amount,
      decimals: info.tokenAmount.decimals,
      uiAmount: info.tokenAmount.uiAmount,
    };
  });
}
