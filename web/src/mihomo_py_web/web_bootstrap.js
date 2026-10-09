// Integration for the pinned zashboard v3.29.1 storage contract.
// All package resources and API requests belong to the current mihomo origin.
for (const key of [
  "config/check-upgrade-core", "config/auto-upgrade", "config/auto-upgrade-core",
  "config/auto-ip-check", "config/auto-connection-check",
]) {
  localStorage.setItem(key, "false");
}

const backends = JSON.parse(localStorage.getItem("setup/api-list") || "[]");
const protocol = location.protocol.slice(0, -1);
const port = location.port || (protocol === "https" ? "443" : "80");
let backend = backends.find(item =>
  item.host === location.hostname && item.port === port && item.protocol === protocol
);
if (!backend) {
  backend = {
    uuid: "mihomo-py-local", type: "clash", protocol, host: location.hostname,
    port, secondaryPath: "", password: "", label: "mihomo-py",
    disableUpgradeCore: true, disableTunMode: true,
  };
  backends.push(backend);
  localStorage.setItem("setup/api-list", JSON.stringify(backends));
}
// The subscription portal and the embedded node panel share one authenticated origin.
const portalSecret = localStorage.getItem("mihomo-py/secret");
if (portalSecret) {
  backend.password = portalSecret;
  localStorage.setItem("setup/api-list", JSON.stringify(backends));
}
localStorage.setItem("setup/active-uuid", backend.uuid);
