/* Broker tabs shared by Positions, Orders and Account. Defaults to the active broker; remembered in ?broker=. */
window.brokerTabs = function brokerTabs(onChange, { allowAll = false } = {}) {
  const tabs = document.getElementById("brokerTabs");
  const params = new URLSearchParams(location.search);
  let current = params.has("broker") ? params.get("broker") : null;

  function select(broker) {
    current = broker;
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.broker === broker));
    const url = new URL(location.href);
    url.searchParams.set("broker", broker);
    history.replaceState(null, "", url);
    onChange(broker);
  }
  tabs.querySelectorAll("button").forEach((b) => (b.onclick = () => select(b.dataset.broker)));

  (async () => {
    if (current === null && allowAll) current = "";
    if (current === null || (!allowAll && current === "")) {
      try { current = (await TB.get("/api/header")).broker; } catch { current = "paper"; }
    }
    select(current);
  })();
  return () => current;
};
