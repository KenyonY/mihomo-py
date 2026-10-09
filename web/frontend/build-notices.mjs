import { readFileSync, writeFileSync } from "node:fs";

const notices = ["react", "react-dom", "scheduler", "tailwindcss", "vite"].map(name => {
  const root = new URL(`node_modules/${name}/`, import.meta.url);
  const { version } = JSON.parse(readFileSync(new URL("package.json", root), "utf8"));
  const license = name === "vite" ? "LICENSE.md" : "LICENSE";
  const text = readFileSync(new URL(license, root), "utf8")
    .split("\n").map(line => line.trimEnd()).join("\n").trimEnd();
  return `${name} ${version}\n${text}`;
}).join("\n\n");
writeFileSync(new URL("../src/mihomo_py_web/portal/THIRD_PARTY_NOTICES.txt", import.meta.url), `${notices}\n`);
