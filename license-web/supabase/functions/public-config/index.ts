import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type", "Access-Control-Allow-Methods": "GET, OPTIONS" };
const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    const { data, error } = await db.from("app_settings").select("setting_key,setting_value");
    if (error) throw error;
    const settings = Object.fromEntries((data ?? []).map((r) => [r.setting_key, r.setting_value]));
    return new Response(JSON.stringify({ settings }), { headers: { ...cors, "Content-Type": "application/json" } });
  } catch (error) {
    console.error("public-config error", (error as Error).message);
    return new Response(JSON.stringify({ error: "Configuration is temporarily unavailable." }), { status: 500, headers: { ...cors, "Content-Type": "application/json" } });
  }
});
