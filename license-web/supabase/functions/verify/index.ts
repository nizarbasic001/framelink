import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { signPayload } from "../_shared/crypto.ts";

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "Use POST." }, 405);
  try {
    const { license_id, device_hash } = await req.json();
    if (typeof license_id !== "string" || typeof device_hash !== "string" || !/^[0-9a-f]{64}$/i.test(device_hash)) return json({ error: "License or device identity is invalid." }, 400);
    const { data: license, error } = await db.from("licenses").select("*,customers(account_status)").eq("id", license_id).maybeSingle();
    if (error) throw error;
    if (!license || license.status !== "active" || license.customers?.account_status !== "active") return json({ error: "This license has been revoked or deactivated." }, 403);
    if (license.expires_at && new Date(license.expires_at) <= new Date()) return json({ error: "This license has expired." }, 403);
    const { data: device, error: deviceError } = await db.from("devices").select("id,active").eq("license_id", license_id).eq("fingerprint_hash", device_hash.toLowerCase()).maybeSingle();
    if (deviceError) throw deviceError;
    if (!device?.active) return json({ error: "This computer is not an active device for this license." }, 403);
    const checked = new Date().toISOString();
    const { error: updateError } = await db.from("devices").update({ last_verification: checked }).eq("id", device.id);
    if (updateError) throw updateError;
    const token = await signPayload({ product: "FrameLink", id: license.id,
      days: license.license_type === "monthly" ? 30 : license.license_type === "yearly" ? 365 : 0,
      license_type: license.license_type, device_hash: device_hash.toLowerCase(), expires_at: license.expires_at,
      activated_at: license.activated_at, lease_expires_at: new Date(Date.now() + 3 * 86400000).toISOString(), issued: checked });
    const { data: settingsRows } = await db.from("app_settings").select("setting_key,setting_value").in("setting_key", ["software_name", "buy_license_url", "support_email", "trial_duration", "default_device_limit"]);
    const settings = Object.fromEntries((settingsRows ?? []).map((row) => [row.setting_key, row.setting_value]));
    return json({ status: "active", activation_token: token, license_type: license.license_type, expires_at: license.expires_at, last_verification: checked, settings });
  } catch (error) {
    const e = error as Error;
    console.error("verify error", e.message);
    return json({ error: "License verification is temporarily unavailable." }, 500);
  }
});
