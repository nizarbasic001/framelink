import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { hashLicense, signPayload, verifyFrameLinkCode } from "../_shared/crypto.ts";

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
const now = () => new Date();
const addDays = (date: Date, n: number) => new Date(date.getTime() + n * 86400000).toISOString();
const audit = async (action: string, licenseId?: string, customerId?: string | null, details: Record<string, unknown> = {}) => {
  await db.from("activity_logs").insert({ action, license_id: licenseId ?? null, user_id: customerId ?? null, details });
};
const publicSettings = async () => {
  const { data } = await db.from("app_settings").select("setting_key,setting_value").in("setting_key", ["software_name", "buy_license_url", "support_email", "trial_duration", "default_device_limit"]);
  return Object.fromEntries((data ?? []).map((row) => [row.setting_key, row.setting_value]));
};
const lease = async (license: Record<string, any>, fingerprint: string) => {
  const issuedAt = now();
  return await signPayload({
    product: "FrameLink", id: license.id,
    days: license.license_type === "monthly" ? 30 : license.license_type === "yearly" ? 365 : 0,
    license_type: license.license_type, device_hash: fingerprint, expires_at: license.expires_at,
    activated_at: license.activated_at, lease_expires_at: addDays(issuedAt, 3), issued: issuedAt.toISOString(),
  });
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "Use POST." }, 405);
  let licenseId: string | undefined;
  try {
    const data = await req.json();
    const code = String(data.code ?? "").trim();
    const fingerprint = String(data.device_hash ?? "").toLowerCase();
    const computerName = String(data.computer_name ?? "Computer").trim().slice(0, 120) || "Computer";
    if (!code || !/^[0-9a-f]{64}$/.test(fingerprint)) throw Object.assign(new Error("License key or device identity is invalid."), { status: 400 });
    const payload = await verifyFrameLinkCode(code);
    licenseId = String(payload.id);
    const { data: license, error } = await db.from("licenses").select("*,customers(id,account_status)").eq("id", licenseId).maybeSingle();
    if (error) throw error;
    if (!license || license.key_hash !== await hashLicense(code)) throw Object.assign(new Error("This license key was not issued by FrameLink."), { status: 403 });
    if (license.status !== "active" || license.customers?.account_status !== "active") throw Object.assign(new Error("This license or customer account is not active."), { status: 403 });
    if (license.expires_at && new Date(license.expires_at) <= now()) throw Object.assign(new Error("This license has expired."), { status: 403 });
    const { data: registration, error: registrationError } = await db.rpc("register_framelink_device", {
      p_license_id: licenseId, p_fingerprint_hash: fingerprint, p_computer_name: computerName,
    });
    if (registrationError) {
      const detail = registrationError.message ?? "Device activation failed.";
      const status = detail.includes("DEVICE_LIMIT") ? 409 : 403;
      throw Object.assign(new Error(detail.includes("DEVICE_LIMIT") ? "This license has reached its allowed device limit." : detail.includes("EXPIRED") ? "This license has expired." : "This license is inactive or unavailable."), { status });
    }
    const { data: savedLicense, error: reloadError } = await db.from("licenses").select("*").eq("id", licenseId).single();
    if (reloadError) throw reloadError;
    await audit("activation_succeeded", licenseId, license.customer_id, { computer_name: computerName });
    return json({ activation_token: await lease(savedLicense, fingerprint), license_type: savedLicense.license_type, expires_at: savedLicense.expires_at, activated_at: savedLicense.activated_at, settings: await publicSettings() });
  } catch (error) {
    const e = error as Error & { status?: number };
    if (licenseId) await audit("activation_failed", licenseId, null, { reason: e.message });
    console.error("activate error", e.message);
    return json({ error: e.message || "Activation failed." }, e.status || 400);
  }
});
