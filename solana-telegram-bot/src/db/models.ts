export interface Wallet {
  telegram_user_id: string;
  public_key: string;
  encrypted_private_key: string;
  iv: string;
  auth_tag: string;
  created_at: string;
}

export interface Transaction {
  id: string;
  telegram_user_id: string;
  type: "buy" | "sell";
  token_mint: string;
  amount_in: number;
  amount_out: number;
  fee_amount: number;
  tx_hash: string;
  status: "pending" | "confirmed" | "failed";
  created_at: string;
}

export interface FeeRecord {
  id: string;
  tx_hash: string;
  fee_amount_lamports: number;
  fee_token_mint: string;
  collected_at: string;
}
