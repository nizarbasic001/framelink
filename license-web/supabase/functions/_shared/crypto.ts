const publicKeyHex = "ea187c6b65e9bff5a5ea285f47fa0aceb4dd3fbd71d678378582a70248afd524178960b4290dbf82d9fc78518d8944787981ad26379a4c3c7986a101fff2556c";
const hexBytes = (hex: string) => Uint8Array.from(hex.match(/.{1,2}/g) ?? [], (v) => parseInt(v, 16));
const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
const unb64url = (value: string) => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), (c) => c.charCodeAt(0));
const canonical = (value: Record<string, unknown>) => JSON.stringify(Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]])));
const sha256 = async (value: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
export const hashLicense = async (value: string) => Array.from(await sha256(value)).map((v) => v.toString(16).padStart(2, "0")).join("");

async function verifyKey() {
  const raw = hexBytes(publicKeyHex);
  const point = new Uint8Array(65); point[0] = 4; point.set(raw, 1);
  return await crypto.subtle.importKey("raw", point, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
}

export async function verifyFrameLinkCode(code: string) {
  const env = Deno.env.get("LICENSE_SIGNING_KEY") ?? "";
  const payloadBytes = unb64url(code.trim());
  if (payloadBytes.length < 66) throw new Error("License key format is invalid.");
  const body = payloadBytes.slice(0, -64), signature = payloadBytes.slice(-64);
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, await verifyKey(), signature, body);
  if (!ok) throw new Error("This license key is invalid.");
  const payload = JSON.parse(new TextDecoder().decode(body));
  if (payload.product !== "FrameLink" || !payload.id || !["monthly", "yearly", "lifetime"].includes(payload.license_type)) throw new Error("This key is not a supported FrameLink license.");
  if (payload.license_type === "monthly" && payload.days !== 30) throw new Error("Invalid monthly license term.");
  if (payload.license_type === "yearly" && payload.days !== 365) throw new Error("Invalid yearly license term.");
  if (payload.license_type === "lifetime" && payload.days !== 0) throw new Error("Invalid lifetime license term.");
  // Keep the value read to ensure the required private signing key is configured
  // before any issuance or token signing operation is attempted.
  if (!/^[0-9a-f]{64}$/i.test(env)) throw new Error("License signing key is not configured.");
  return payload as { product: string; id: string; days: number; license_type: string };
}

export async function signPayload(payload: Record<string, unknown>) {
  const d = Deno.env.get("LICENSE_SIGNING_KEY") ?? "";
  if (!/^[0-9a-f]{64}$/i.test(d)) throw new Error("License signing key is not configured.");
  const pub = hexBytes(publicKeyHex);
  const jwk = {
    kty: "EC", crv: "P-256", d: b64url(hexBytes(d)), x: b64url(pub.slice(0, 32)), y: b64url(pub.slice(32)),
    ext: true, key_ops: ["sign"],
  };
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const body = new TextEncoder().encode(canonical(payload));
  const signature = new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, body));
  if (signature.length !== 64) throw new Error("Unexpected ECDSA signature format.");
  const envelope = new Uint8Array(body.length + signature.length); envelope.set(body); envelope.set(signature, body.length);
  return b64url(envelope);
}

export async function issueKey(payload: Record<string, unknown>) { return await signPayload(payload); }
