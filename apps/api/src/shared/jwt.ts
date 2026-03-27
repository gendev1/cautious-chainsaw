import * as jose from 'jose';
import { getConfig } from '../config.js';

let _privateKey: Uint8Array | CryptoKey | null = null;
let _publicKey: Uint8Array | CryptoKey | null = null;

async function getKeys() {
  if (_privateKey && _publicKey) return { privateKey: _privateKey, publicKey: _publicKey };

  const config = getConfig();
  if (config.JWT_PRIVATE_KEY) {
    _privateKey = await jose.importPKCS8(config.JWT_PRIVATE_KEY, 'ES256');
    _publicKey = await jose.importSPKI(config.JWT_PUBLIC_KEY!, 'ES256');
  } else {
    // Dev fallback: symmetric
    const secret = new TextEncoder().encode(process.env.JWT_SECRET ?? 'dev-secret-change-me');
    _privateKey = secret;
    _publicKey = secret;
  }
  return { privateKey: _privateKey, publicKey: _publicKey };
}

export interface TokenPayload {
  sub: string;
  tid: string;
  act: 'user' | 'service' | 'impersonator';
  sid: string;
  roles: string[];
  mfa: boolean;
  imp?: string; // impersonated user id
}

export async function signAccessToken(payload: TokenPayload): Promise<string> {
  const config = getConfig();
  const { privateKey } = await getKeys();
  const alg = config.JWT_PRIVATE_KEY ? 'ES256' : 'HS256';

  return new jose.SignJWT({ ...payload })
    .setProtectedHeader({ alg })
    .setIssuedAt()
    .setExpirationTime(`${config.JWT_ACCESS_TOKEN_TTL}s`)
    .sign(privateKey);
}

export async function verifyAccessToken(token: string): Promise<TokenPayload & jose.JWTPayload> {
  const { publicKey } = await getKeys();
  const { payload } = await jose.jwtVerify(token, publicKey);
  return payload as TokenPayload & jose.JWTPayload;
}
