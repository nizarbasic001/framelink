import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { hashLicense, issueKey } from "../_shared/crypto.ts";

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "POST, OPTIONS" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const sbUrl = Deno.env.get("SUPABASE_URL")!;
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const db = createClient(sbUrl, serviceKey, { auth: { persistSession: false } });

async function audit(action: string, details: Record<string, unknown> = {}, customerId?: string | null, licenseId?: string | null) {
  await db.from("activity_logs").insert({ action, details, user_id: customerId || null, license_id: licenseId || null });
}
const fail = (message: string, status = 400) => { throw Object.assign(new Error(message), { status }); };

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return json({ error: "Use POST." }, 405);
  try {
    const bearer = req.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") ?? "";
    if (!bearer) return json({ error: "Sign in with an administrator account." }, 401);
    const { data: auth, error: authError } = await db.auth.getUser(bearer);
    if (authError || !auth.user?.email) return json({ error: "Your admin session is invalid or expired." }, 401);
    const { data: admin } = await db.from("admin_users").select("user_id").eq("user_id", auth.user.id).eq("email", auth.user.email).maybeSingle();
    if (!admin) return json({ error: "This account is not authorized to manage FrameLink licenses." }, 403);
    const { action, payload = {} } = await req.json();

    if (action === "dashboard") {
      const [users, licenses, devices] = await Promise.all([
        db.from("customers").select("id", { count: "exact", head: true }),
        db.from("licenses").select("id,status,expires_at"),
        db.from("devices").select("id", { count: "exact", head: true }).eq("active", true),
      ]);
      if (users.error || licenses.error || devices.error) throw new Error("Could not load dashboard data.");
      const rows = licenses.data ?? [], now = Date.now();
      return json({ total_users: users.count ?? 0, active_licenses: rows.filter((r) => r.status === "active" && (!r.expires_at || Date.parse(r.expires_at) > now)).length,
        expired_licenses: rows.filter((r) => r.status === "active" && r.expires_at && Date.parse(r.expires_at) <= now).length,
        revoked_licenses: rows.filter((r) => r.status === "revoked").length, activated_devices: devices.count ?? 0 });
    }
    if (action === "users") {
      const { data, error } = await db.from("customers").select("id,name,email,account_status,created_at,licenses(id,license_type,status,expires_at)").order("created_at", { ascending: false });
      if (error) throw error;
      return json({ users: data ?? [] });
    }
    if (action === "licenses") {
      const { data, error } = await db.from("licenses").select("id,key_hash,customer_id,license_type,status,expires_at,max_devices,created_at,activated_at,customers(name,email),devices(id,active)").order("created_at", { ascending: false });
      if (error) throw error;
      return json({ licenses: data ?? [] });
    }
    if (action === "devices") {
      const { data, error } = await db.from("devices").select("id,license_id,fingerprint_hash,computer_name,activated_at,last_verification,active,licenses(id,license_type,status,customers(name,email))").order("activated_at", { ascending: false });
      if (error) throw error;
      return json({ devices: data ?? [] });
    }
    if (action === "logs") {
      const { data, error } = await db.from("activity_logs").select("id,action,details,occurred_at,customers(name,email),licenses(id,license_type)").order("occurred_at", { ascending: false }).limit(300);
      if (error) throw error;
      return json({ logs: data ?? [] });
    }
    if (action === "settings") {
      const { data, error } = await db.from("app_settings").select("setting_key,setting_value");
      if (error) throw error;
      return json({ settings: Object.fromEntries((data ?? []).map((r) => [r.setting_key, r.setting_value])) });
    }
    if (action === "save_settings") {
      const { software_name, buy_license_url, support_email, trial_duration, default_device_limit } = payload;
      if (!String(software_name ?? "").trim()) fail("Software name is required.");
      if (buy_license_url && !/^https?:\/\//i.test(buy_license_url)) fail("Buy License URL must start with http:// or https://.");
      if (support_email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(support_email)) fail("Enter a valid support email.");
      const trial = Number(trial_duration), limit = Number(default_device_limit);
      if (!Number.isInteger(trial) || trial < 0 || trial > 3650) fail("Trial duration must be from 0 to 3650 days.");
      if (!Number.isInteger(limit) || limit < 1 || limit > 100) fail("Default device limit must be from 1 to 100.");
      const items = Object.entries({ software_name: String(software_name).trim(), buy_license_url: String(buy_license_url ?? "").trim(), support_email: String(support_email ?? "").trim(), trial_duration: String(trial), default_device_limit: String(limit) }).map(([setting_key, setting_value]) => ({ setting_key, setting_value, updated_at: new Date().toISOString() }));
      const { error } = await db.from("app_settings").upsert(items, { onConflict: "setting_key" });
      if (error) throw error;
      await audit("settings_updated", { keys: items.map((i) => i.setting_key) });
      return json({ ok: true });
    }
    if (action === "create_license") {
      const name = String(payload.name ?? "").trim(), email = String(payload.email ?? "").trim().toLowerCase();
      const kind = String(payload.license_type ?? "");
      const days = ({ monthly: 30, yearly: 365, lifetime: 0 } as Record<string, number>)[kind];
      const maxDevices = Number(payload.max_devices);
      if (!name) fail("Customer name is required.");
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) fail("Enter a valid customer email.");
      if (days === undefined) fail("Choose monthly, yearly, or lifetime.");
      if (!Number.isInteger(maxDevices) || maxDevices < 1 || maxDevices > 100) fail("Device limit must be from 1 to 100.");
      let { data: customer } = await db.from("customers").select("id").eq("email", email).maybeSingle();
      if (customer) {
        const update = await db.from("customers").update({ name, account_status: "active" }).eq("id", customer.id);
        if (update.error) throw update.error;
      } else {
        const insert = await db.from("customers").insert({ name, email }).select("id").single();
        if (insert.error) throw insert.error;
        customer = insert.data;
      }
      const id = crypto.randomUUID();
      const issued = new Date().toISOString();
      const key = await issueKey({ product: "FrameLink", id, days, issued, license_type: kind });
      const { error } = await db.from("licenses").insert({ id, key_hash: await hashLicense(key), customer_id: customer.id, license_type: kind, status: "active", max_devices: maxDevices });
      if (error) throw error;
      await audit("license_created", { license_type: kind, max_devices: maxDevices }, customer.id, id);
      return json({ id, key, license_type: kind, max_devices: maxDevices });
    }
    if (action === "update_license") {
      const id = String(payload.id ?? ""), patch: Record<string, unknown> = {};
      if (!id) fail("License ID is required.");
      if (payload.status !== undefined) {
        if (!["active", "deactivated", "revoked"].includes(payload.status)) fail("Invalid license status.");
        patch.status = payload.status;
      }
      if (payload.max_devices !== undefined) {
        const limit = Number(payload.max_devices);
        if (!Number.isInteger(limit) || limit < 1 || limit > 100) fail("Device limit must be from 1 to 100.");
        patch.max_devices = limit;
      }
      if (payload.expires_at !== undefined) {
        const parsed = new Date(payload.expires_at);
        if (Number.isNaN(parsed.getTime())) fail("Expiration date is invalid.");
        patch.expires_at = parsed.toISOString();
      }
      if (!Object.keys(patch).length) fail("No license changes were provided.");
      const { data: existing } = await db.from("licenses").select("customer_id,license_type").eq("id", id).maybeSingle();
      if (!existing) fail("License not found.", 404);
      if (existing.license_type === "lifetime" && patch.expires_at) fail("Lifetime licenses do not expire.");
      const { error } = await db.from("licenses").update(patch).eq("id", id);
      if (error) throw error;
      await audit(patch.status === "revoked" ? "license_revoked" : "license_updated", patch, existing.customer_id, id);
      return json({ ok: true });
    }
    if (action === "remove_device") {
      const id = String(payload.id ?? "");
      const { data: device } = await db.from("devices").select("license_id,licenses(customer_id)").eq("id", id).maybeSingle();
      if (!device) fail("Device not found.", 404);
      const { error } = await db.from("devices").update({ active: false }).eq("id", id);
      if (error) throw error;
      const linked = device.licenses as { customer_id?: string } | null;
      await audit("device_removed", { device_id: id }, linked?.customer_id, device.license_id);
      return json({ ok: true });
    }
    if (action === "update_customer") {
      const id = String(payload.id ?? ""), status = String(payload.account_status ?? "");
      if (!["active", "deactivated"].includes(status)) fail("Invalid account status.");
      const { error } = await db.from("customers").update({ account_status: status }).eq("id", id);
      if (error) throw error;
      if (status === "deactivated") {
        const { data: rows } = await db.from("licenses").select("id").eq("customer_id", id);
        if (rows?.length) await db.from("licenses").update({ status: "deactivated" }).eq("customer_id", id);
      }
      await audit("customer_status_changed", { status }, id);
      return json({ ok: true });
    }
    return json({ error: "Unknown admin action." }, 404);
  } catch (error) {
    const e = error as Error & { status?: number };
    console.error("admin-api error", e.message);
    return json({ error: e.message || "Admin request failed." }, e.status || 500);
  }
});
