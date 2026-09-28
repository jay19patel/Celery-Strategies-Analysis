/* Broker tabs shared by Positions, Orders and Account: one per active broker. Remembered in ?broker=. */
window.brokerTabs = function brokerTabs(onChange, { allowAll = false } = {}) {
  const tabs = document.getElementById("brokerTabs");
  const buttons = [...tabs.querySelectorAll("button")];
  const known = buttons.map((b) => b.dataset.broker);
  const asked = new URLSearchParams(location.search).get("broker");
  const fallback = allowAll && known.includes("") ? "" : known.find((b) => b !== "") ?? "paper";

  function select(broker) {
    buttons.forEach((b) => b.classList.toggle("on", b.dataset.broker === broker));
    const url = new URL(location.href);
    url.searchParams.set("broker", broker);
    history.replaceState(null, "", url);
    onChange(broker);
  }
  buttons.forEach((b) => (b.onclick = () => select(b.dataset.broker)));
  // A single broker needs no tabs.
  tabs.classList.toggle("hidden", known.filter((b) => b !== "").length < 2);
  select(asked !== null && known.includes(asked) ? asked : fallback);
};
