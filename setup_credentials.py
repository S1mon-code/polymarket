"""Generate Polymarket API credentials from your wallet private key."""

import os
from dotenv import load_dotenv, set_key
from py_clob_client.client import ClobClient

load_dotenv()

def main():
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print("ERROR: Set PRIVATE_KEY in .env first")
        return

    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=private_key,
    )

    print("Generating API credentials...")
    creds = client.create_or_derive_api_creds()
    print(f"API Key:    {creds.api_key}")
    print(f"Secret:     {creds.api_secret}")
    print(f"Passphrase: {creds.api_passphrase}")

    # Save to .env
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    set_key(env_path, "POLY_API_KEY", creds.api_key)
    set_key(env_path, "POLY_API_SECRET", creds.api_secret)
    set_key(env_path, "POLY_PASSPHRASE", creds.api_passphrase)
    print("\nCredentials saved to .env")


if __name__ == "__main__":
    main()
