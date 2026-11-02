const bootstrapConfigKey = "mf-dl-bootstrap-config";
const statusMessage = document.querySelector("#startup-message");
const retryButton = document.querySelector("#startup-retry");
let retryDelay = 700;
let retryTimer = null;
let isPreparing = false;

async function prepareApp() {
  if (isPreparing) return;
  isPreparing = true;
  retryButton.hidden = true;
  statusMessage.textContent = "Preparing your workspace…";

  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    const config = await response.json();
    if (!response.ok) {
      throw new Error(config.error || "The local service is not ready yet.");
    }
    if (!config.default_destination) {
      throw new Error("The app returned incomplete startup settings.");
    }

    sessionStorage.setItem(bootstrapConfigKey, JSON.stringify(config));
    statusMessage.textContent = "Ready — opening your downloader";
    document.body.classList.add("startup-exit");
    setTimeout(() => location.replace("/app?prepared=1"), 500);
  } catch (error) {
    console.error("Could not prepare the app:", error);
    statusMessage.textContent = `Waiting for the local service. ${error.message}`;
    retryButton.hidden = false;
    retryTimer = setTimeout(prepareApp, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 5000);
  } finally {
    isPreparing = false;
  }
}

retryButton.addEventListener("click", () => {
  clearTimeout(retryTimer);
  retryDelay = 700;
  prepareApp();
});

prepareApp();
