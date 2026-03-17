import crypto from "crypto";

const ALGORITHM = "aes-256-gcm";
const IV_LENGTH = 12;
const KEY_LENGTH = 32;

export interface EncryptedData {
  iv: string;
  encrypted: string;
  authTag: string;
}

function deriveKey(masterKey: string): Buffer {
  // Use SHA-256 to derive a consistent 32-byte key from the master key
  return crypto.createHash("sha256").update(masterKey).digest();
}

export function encrypt(plaintext: string, masterKey: string): EncryptedData {
  const key = deriveKey(masterKey);
  if (key.length !== KEY_LENGTH) {
    throw new Error(`Derived key must be ${KEY_LENGTH} bytes`);
  }

  const iv = crypto.randomBytes(IV_LENGTH);
  const cipher = crypto.createCipheriv(ALGORITHM, key, iv);

  let encrypted = cipher.update(plaintext, "utf8", "hex");
  encrypted += cipher.final("hex");

  const authTag = cipher.getAuthTag();

  return {
    iv: iv.toString("hex"),
    encrypted,
    authTag: authTag.toString("hex"),
  };
}

export function decrypt(
  encrypted: string,
  iv: string,
  authTag: string,
  masterKey: string
): string {
  const key = deriveKey(masterKey);
  if (key.length !== KEY_LENGTH) {
    throw new Error(`Derived key must be ${KEY_LENGTH} bytes`);
  }

  const decipher = crypto.createDecipheriv(
    ALGORITHM,
    key,
    Buffer.from(iv, "hex")
  );
  decipher.setAuthTag(Buffer.from(authTag, "hex"));

  let decrypted = decipher.update(encrypted, "hex", "utf8");
  decrypted += decipher.final("utf8");

  return decrypted;
}
