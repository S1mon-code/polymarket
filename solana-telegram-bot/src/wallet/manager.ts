import { Keypair, Connection, PublicKey } from "@solana/web3.js";
import bs58 from "bs58";
import { encrypt, decrypt } from "./encryption";
import { getSolBalance, getTokenBalances, type TokenBalance } from "./balance";
import { insertWallet, getWalletByUserId } from "../db/sqlite";

export interface WalletInfo {
  publicKey: string;
  telegramUserId: string;
  createdAt: string;
}

function getEncryptionKey(): string {
  const key = process.env["WALLET_ENCRYPTION_KEY"];
  if (!key) {
    throw new Error(
      "WALLET_ENCRYPTION_KEY environment variable is not set"
    );
  }
  return key;
}

export function createWallet(telegramUserId: string): string {
  const existing = getWalletByUserId(telegramUserId);
  if (existing) {
    throw new Error(
      `Wallet already exists for user ${telegramUserId}. Public key: ${existing.public_key}`
    );
  }

  const keypair = Keypair.generate();
  const publicKey = keypair.publicKey.toBase58();
  const privateKeyBase58 = bs58.encode(keypair.secretKey);

  const masterKey = getEncryptionKey();
  const { iv, encrypted, authTag } = encrypt(privateKeyBase58, masterKey);

  insertWallet({
    telegram_user_id: telegramUserId,
    public_key: publicKey,
    encrypted_private_key: encrypted,
    iv,
    auth_tag: authTag,
  });

  return publicKey;
}

export function getWallet(telegramUserId: string): WalletInfo {
  const wallet = getWalletByUserId(telegramUserId);
  if (!wallet) {
    throw new Error(`No wallet found for user ${telegramUserId}`);
  }

  return {
    publicKey: wallet.public_key,
    telegramUserId: wallet.telegram_user_id,
    createdAt: wallet.created_at,
  };
}

export function exportPrivateKey(telegramUserId: string): string {
  const wallet = getWalletByUserId(telegramUserId);
  if (!wallet) {
    throw new Error(`No wallet found for user ${telegramUserId}`);
  }

  const masterKey = getEncryptionKey();
  return decrypt(
    wallet.encrypted_private_key,
    wallet.iv,
    wallet.auth_tag,
    masterKey
  );
}

export function getKeypair(telegramUserId: string): Keypair {
  const privateKeyBase58 = exportPrivateKey(telegramUserId);
  const secretKey = bs58.decode(privateKeyBase58);
  return Keypair.fromSecretKey(secretKey);
}

export async function getBalance(
  connection: Connection,
  publicKey: string
): Promise<string> {
  const pubkey = new PublicKey(publicKey);
  return getSolBalance(connection, pubkey);
}

export async function getTokenBalance(
  connection: Connection,
  publicKey: string,
  mintAddress: string
): Promise<TokenBalance | null> {
  const pubkey = new PublicKey(publicKey);
  const balances = await getTokenBalances(connection, pubkey);
  return balances.find((b) => b.mint === mintAddress) ?? null;
}
