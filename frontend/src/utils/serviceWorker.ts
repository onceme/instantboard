export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) {
    return null;
  }

  const base = import.meta.env.BASE_URL;
  const swUrl = `${base.endsWith("/") ? base : `${base}/`}sw.js`;

  try {
    return await navigator.serviceWorker.register(swUrl);
  } catch (error) {
    console.error("[serviceWorker] registration failed:", error);
    return null;
  }
}
