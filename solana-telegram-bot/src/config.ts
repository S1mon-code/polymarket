import dotenv from "dotenv";

dotenv.config();

export interface Config {
  solanaRpcUrl: string;
  solanaPrivateKey: string;
  walletEncryptionKey: string;
  heliusApiKey: string;
  telegramBotToken: string;
  dryRun: boolean;
  network: "mainnet" | "devnet";
  isDevnet: boolean;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export function loadConfig(): Config {
  const network = process.env["SOLANA_NETWORK"] === "devnet" ? "devnet" : "mainnet";
  const isDevnet = network === "devnet";

  return {
    solanaRpcUrl: requireEnv("SOLANA_RPC_URL"),
    solanaPrivateKey: requireEnv("SOLANA_PRIVATE_KEY"),
    walletEncryptionKey: requireEnv("WALLET_ENCRYPTION_KEY"),
    heliusApiKey: requireEnv("HELIUS_API_KEY"),
    telegramBotToken: requireEnv("TELEGRAM_BOT_TOKEN"),
    dryRun: process.env["DRY_RUN"] === "true",
    network,
    isDevnet,
  };
}

export const config = loadConfig();
