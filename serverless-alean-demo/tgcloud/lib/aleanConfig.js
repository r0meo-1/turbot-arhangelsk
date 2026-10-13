// Telegram Serverless has no documented process.env API. These deployment-time
// flags are immutable; an unsafe build fails closed instead of enabling suppliers.
export const CONFIG = Object.freeze({
  ALEAN_DEMO_ONLY:true, ALEAN_ENABLED:false, ALEAN_READONLY_LIVE_ENABLED:false,
  supplierCalls:false, bookings:false, payments:false, submissions:false,
  crmLeads:false, managerNotifications:false,
});
export function assertDemo() {
  if (CONFIG.ALEAN_DEMO_ONLY !== true || Object.entries(CONFIG).some(([key,value]) => key !== 'ALEAN_DEMO_ONLY' && value !== false))
    throw new Error('Unsafe demo configuration');
}
