#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const TOKEN = process.env.PANTRYOS_API_TOKEN || "browser-smoke-token";

function resolvePythonCandidate(candidate) {
  if (!candidate) return null;
  const isPath = path.isAbsolute(candidate) || candidate.includes("/") || candidate.includes("\\");
  if (!isPath) return candidate;
  if (!fs.existsSync(candidate)) return null;
  const stat = fs.statSync(candidate);
  if (stat.isFile()) return candidate;
  if (stat.isDirectory()) {
    const executable = path.join(candidate, process.platform === "win32" ? "python.exe" : "python");
    return fs.existsSync(executable) && fs.statSync(executable).isFile() ? executable : null;
  }
  return null;
}

function resolvePython() {
  const candidates = [
    path.join(ROOT, ".venv", "Scripts", "python.exe"),
    path.join(ROOT, ".venv", "bin", "python"),
    "C:\\Users\\Kronus\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe",
    process.env.PYTHON,
    process.env.PYTHON_EXE,
    "python",
    "py",
  ];
  for (const candidate of candidates) {
    const resolved = resolvePythonCandidate(candidate);
    if (resolved && supportsPython312(resolved)) return resolved;
  }
  throw new Error("Could not locate a Python 3.12+ interpreter for browser smoke tests");
}

function supportsPython312(candidate) {
  const result = spawnSync(candidate, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  if (result.status !== 0) return false;
  const [major, minor] = result.stdout.trim().split(".").map((value) => Number.parseInt(value, 10));
  return major > 3 || (major === 3 && minor >= 12);
}

const PYTHON = resolvePython();

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    const bundled = path.resolve(path.dirname(process.execPath), "..", "node_modules", "playwright");
    if (fs.existsSync(bundled)) {
      return require(bundled);
    }
    throw new Error(
      `Playwright is required for browser smoke tests. Install it or run with the bundled Codex Node runtime. Original error: ${error.message}`,
    );
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : null;
      server.close(() => (port ? resolve(port) : reject(new Error("Could not allocate a local port"))));
    });
  });
}

async function waitForReady(baseUrl, server) {
  const deadline = Date.now() + 15000;
  let lastError = null;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`PantryOS server exited early with code ${server.exitCode}`);
    }
    try {
      const response = await fetch(`${baseUrl}/api/v1/health/ready`);
      if (response.ok) {
        const payload = await response.json();
        if (payload.status === "ready") return;
      }
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`PantryOS server did not become ready: ${lastError ? lastError.message : "timeout"}`);
}

function startServer(dbPath, port) {
  const env = {
    ...process.env,
    PANTRYOS_API_TOKEN: TOKEN,
    PANTRYOS_BROWSER_SESSION_STORE: "memory",
  };
  const server = spawn(PYTHON, ["app/server.py", "--host", "127.0.0.1", "--port", String(port), "--data", dbPath], {
    cwd: ROOT,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  server.stdout.on("data", (chunk) => process.stdout.write(`[server] ${chunk}`));
  server.stderr.on("data", (chunk) => process.stderr.write(`[server] ${chunk}`));
  return server;
}

async function stopServer(server) {
  if (server.exitCode !== null) return;
  server.kill("SIGTERM");
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (server.exitCode === null) server.kill("SIGKILL");
      resolve();
    }, 3000);
    server.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function clickButton(page, name) {
  await page.getByRole("button", { name }).click();
}

async function fillForm(scope, values) {
  for (const [name, value] of Object.entries(values)) {
    await scope.locator(`[name="${name}"]`).fill(String(value));
  }
}

async function expectVisibleText(page, text) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: "visible", timeout: 5000 });
}

async function assertNoCriticalA11yIssues(page, label) {
  const issues = await page.evaluate(() => {
    const failures = [];
    const visible = (element) => {
      if (element.hidden || element.getAttribute("aria-hidden") === "true") return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const text = (element) => (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
    const describe = (element) => {
      const id = element.id ? `#${element.id}` : "";
      const classes = [...element.classList].slice(0, 3).map((value) => `.${value}`).join("");
      return `${element.tagName.toLowerCase()}${id}${classes}`;
    };
    const labelText = (control) => {
      if (control.getAttribute("aria-label")) return control.getAttribute("aria-label").trim();
      if (control.getAttribute("aria-labelledby")) {
        const labelled = control.getAttribute("aria-labelledby").split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(text).join(" ").trim();
        if (labelled) return labelled;
      }
      if (control.id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
        if (explicit && text(explicit)) return text(explicit);
      }
      const parent = control.closest("label");
      return parent ? text(parent) : "";
    };
    const accessibleName = (element) => {
      if (element.getAttribute("aria-label")) return element.getAttribute("aria-label").trim();
      if (element.getAttribute("aria-labelledby")) {
        const labelled = element.getAttribute("aria-labelledby").split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(text).join(" ").trim();
        if (labelled) return labelled;
      }
      return text(element);
    };
    const parseColor = (value) => {
      const match = value.match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(",").map((part) => Number.parseFloat(part.trim()));
      if (parts.length < 3 || parts.some((part) => Number.isNaN(part))) return null;
      const alpha = parts.length > 3 ? parts[3] : 1;
      return { r: parts[0], g: parts[1], b: parts[2], a: Number.isNaN(alpha) ? 1 : alpha };
    };
    const blend = (foreground, background) => ({
      r: foreground.r * foreground.a + background.r * (1 - foreground.a),
      g: foreground.g * foreground.a + background.g * (1 - foreground.a),
      b: foreground.b * foreground.a + background.b * (1 - foreground.a),
      a: 1,
    });
    const luminance = (color) => {
      const channel = (value) => {
        const normalized = value / 255;
        return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
    };
    const contrastRatio = (foreground, background) => {
      const front = foreground.a < 1 ? blend(foreground, background) : foreground;
      const lighter = Math.max(luminance(front), luminance(background));
      const darker = Math.min(luminance(front), luminance(background));
      return (lighter + 0.05) / (darker + 0.05);
    };
    const effectiveBackground = (element) => {
      for (let current = element; current; current = current.parentElement) {
        const color = parseColor(window.getComputedStyle(current).backgroundColor);
        if (color && color.a > 0) return color;
      }
      return { r: 255, g: 255, b: 255, a: 1 };
    };

    if (document.documentElement.lang !== "en") failures.push("Document language must be set to en");
    if (!document.title.trim()) failures.push("Document title is empty");
    if (document.querySelectorAll("main:not([hidden])").length !== 1) failures.push("Exactly one visible main landmark is required");
    if (!document.querySelector("header")) failures.push("Header landmark is missing");
    if (document.querySelectorAll("h1").length !== 1) failures.push("Exactly one h1 is required");

    const ids = new Map();
    for (const element of document.querySelectorAll("[id]")) {
      ids.set(element.id, (ids.get(element.id) || 0) + 1);
    }
    for (const [id, count] of ids.entries()) {
      if (count > 1) failures.push(`Duplicate id ${id}`);
    }

    for (const element of document.querySelectorAll("[aria-controls], [aria-describedby], [aria-labelledby]")) {
      for (const attr of ["aria-controls", "aria-describedby", "aria-labelledby"]) {
        const value = element.getAttribute(attr);
        if (!value) continue;
        for (const id of value.split(/\s+/).filter(Boolean)) {
          if (!document.getElementById(id)) failures.push(`${describe(element)} references missing ${attr} target ${id}`);
        }
      }
    }

    for (const element of document.querySelectorAll("[role]")) {
      const role = element.getAttribute("role");
      if (!["alert", "status", "button", "list", "listitem", "dialog", "switch", "checkbox", "group"].includes(role)) {
        failures.push(`${describe(element)} uses unexpected role ${role}`);
      }
    }

    for (const control of document.querySelectorAll("input, textarea, select")) {
      if (!visible(control)) continue;
      if (control.type === "hidden") continue;
      if (!labelText(control)) failures.push(`Visible form control missing label: ${describe(control)}`);
      if (control.hasAttribute("required") && control.getAttribute("aria-invalid") === "true" && !control.getAttribute("aria-describedby")) {
        failures.push(`Invalid required control lacks described error: ${describe(control)}`);
      }
    }

    for (const button of document.querySelectorAll("button, [role='button']")) {
      if (!visible(button)) continue;
      if (!accessibleName(button)) failures.push(`Visible button missing accessible name: ${describe(button)}`);
    }

    for (const form of document.querySelectorAll("form")) {
      if (!visible(form)) continue;
      const submit = form.querySelector('button[type="submit"], input[type="submit"]');
      if (!submit) failures.push(`Visible form missing submit control: ${form.id || "unnamed"}`);
    }

    for (const image of document.querySelectorAll("img")) {
      if (visible(image) && !image.hasAttribute("alt")) failures.push(`Visible image missing alt text: ${describe(image)}`);
    }

    for (const media of document.querySelectorAll("video, audio")) {
      if (visible(media) && !media.hasAttribute("aria-label") && !media.hasAttribute("aria-labelledby") && !media.closest("figure")) {
        failures.push(`Visible media lacks an accessible label: ${describe(media)}`);
      }
    }

    const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    for (const element of document.querySelectorAll(focusableSelector)) {
      if (!visible(element)) continue;
      const rect = element.getBoundingClientRect();
      if ((rect.width < 24 || rect.height < 24) && element.type !== "checkbox") {
        failures.push(`Focusable target is smaller than 24px: ${describe(element)} ${Math.round(rect.width)}x${Math.round(rect.height)}`);
      }
      const outline = window.getComputedStyle(element, ":focus").outlineStyle;
      if (outline === "none" && !window.getComputedStyle(element, ":focus").boxShadow) {
        failures.push(`Focusable target lacks visible focus styling: ${describe(element)}`);
      }
    }

    const checkedContrast = new Set();
    for (const element of document.querySelectorAll("body *")) {
      if (!visible(element) || !text(element)) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width < 2 || rect.height < 2) continue;
      const key = `${window.getComputedStyle(element).color}|${window.getComputedStyle(element).backgroundColor}|${window.getComputedStyle(element).fontSize}|${window.getComputedStyle(element).fontWeight}`;
      if (checkedContrast.has(key)) continue;
      checkedContrast.add(key);
      const color = parseColor(window.getComputedStyle(element).color);
      const background = effectiveBackground(element);
      if (!color || !background) continue;
      const ratio = contrastRatio(color, background);
      const fontSize = Number.parseFloat(window.getComputedStyle(element).fontSize);
      const fontWeight = Number.parseInt(window.getComputedStyle(element).fontWeight, 10) || 400;
      const largeText = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
      const minimum = largeText ? 3 : 4.5;
      if (ratio < minimum) {
        failures.push(`Insufficient contrast ${ratio.toFixed(2)}:1 on ${describe(element)}; required ${minimum}:1`);
      }
      if (checkedContrast.size > 60) break;
    }

    const liveStatus = [...document.querySelectorAll('[role="status"], [aria-live]')].filter(visible);
    if (!liveStatus.length) failures.push("At least one visible live status region is required");

    return failures;
  });
  if (issues.length) {
    throw new Error(`${label} accessibility checks failed:\n- ${issues.join("\n- ")}`);
  }
}

async function assertNoHorizontalOverflow(page, viewportName) {
  const overflow = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    offenders: [...document.body.querySelectorAll("*")]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.right > document.documentElement.clientWidth + 2 || rect.left < -2;
      })
      .slice(0, 5)
      .map((element) => `${element.tagName.toLowerCase()}#${element.id || ""}.${[...element.classList].join(".")}`),
  }));
  if (overflow.scrollWidth > overflow.clientWidth + 2) {
    throw new Error(
      `${viewportName} has horizontal overflow: scrollWidth=${overflow.scrollWidth}, clientWidth=${overflow.clientWidth}, offenders=${overflow.offenders.join(", ")}`,
    );
  }
}

async function runViewport(browser, baseUrl, viewport) {
  const context = await browser.newContext({ viewport: viewport.size, serviceWorkers: "block" });
  const page = await context.newPage();
  const cameraBarcode = `990${Math.floor(100000000 + Math.random() * 899999999)}`;
  await page.addInitScript((barcode) => {
    window.__pantryosBarcodeScannerSmoke = { attached: false, detected: false, stopped: false };
    window.PantryOSBarcodeScannerAdapter = {
      supported: () => true,
      start: async () => ({
        detector: { barcode },
        stream: { getTracks: () => [{ stop: () => { window.__pantryosBarcodeScannerSmoke.stopped = true; } }] },
      }),
      attach: async () => { window.__pantryosBarcodeScannerSmoke.attached = true; },
      detect: async (detector) => {
        window.__pantryosBarcodeScannerSmoke.detected = true;
        return [{ rawValue: detector.barcode }];
      },
      stop: (stream) => { for (const track of stream.getTracks()) track.stop(); },
    };
  }, cameraBarcode);
  const consoleErrors = [];
  const httpErrors = [];
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !text.includes("the server responded with a status of 404")) {
      consoleErrors.push(text);
    }
  });
  page.on("response", (response) => {
    const status = response.status();
    const url = response.url();
    const expectedUnknownBarcode = status === 404 && /\/api\/barcodes\/[^/]+\/add-lot$/.test(new URL(url).pathname);
    if (status >= 400 && !expectedUnknownBarcode) {
      httpErrors.push(`${status} ${url}`);
    }
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  page.on("dialog", async (dialog) => {
    if (/Open this food/.test(dialog.message())) {
      await dialog.accept();
      return;
    }
    if (!/Remove this food|Remove this shopping item|Delete this recipe/.test(dialog.message())) {
      throw new Error(`Unexpected dialog: ${dialog.message()}`);
    }
    await dialog.dismiss();
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await expectVisibleText(page, "Local setup token required.");
  await assertNoCriticalA11yIssues(page, `${viewport.name} login`);

  const tokenInput = page.locator("#setupTokenInput");
  await page.getByRole("button", { name: "Show" }).click();
  if ((await tokenInput.getAttribute("type")) !== "text") {
    throw new Error("Setup token visibility toggle did not reveal the input");
  }
  await page.getByRole("button", { name: "Hide" }).click();
  if ((await tokenInput.getAttribute("type")) !== "password") {
    throw new Error("Setup token visibility toggle did not restore password masking");
  }
  await page.getByLabel("Setup token").fill(TOKEN);
  await clickButton(page, "Sign In");
  await page.locator("#appShell").waitFor({ state: "visible" });
  await expectVisibleText(page, "Chicken Breast");

  await assertNoHorizontalOverflow(page, viewport.name);
  await assertNoCriticalA11yIssues(page, `${viewport.name} initial app`);

  const suffix = `${viewport.name}-${Date.now()}`;
  await page.locator('#barcodeForm [name="barcode"]').fill("");
  await clickButton(page, "Use Camera");
  await page.waitForFunction((barcode) => document.querySelector('#barcodeForm [name="barcode"]')?.value === barcode, cameraBarcode);
  const cameraState = await page.evaluate(() => window.__pantryosBarcodeScannerSmoke);
  if (!cameraState.attached || !cameraState.detected || !cameraState.stopped) {
    throw new Error(`Barcode camera adapter did not complete capture and cleanup: ${JSON.stringify(cameraState)}`);
  }

  const itemName = `Smoke Flour ${suffix}`;
  const recipeName = `Smoke Pancakes ${suffix}`;
  const barcodeItem = `Smoke Barcode Beans ${suffix}`;
  const receiptItem = `Smoke Receipt Rice ${suffix}`;

  await fillForm(page.locator("#itemForm"), {
    name: itemName,
    quantity: "3",
    unit: "count",
    location: "Kitchen/Pantry",
    estimated_cost: "3.00",
  });
  await page.locator('#itemForm button[type="submit"]').click();
  await expectVisibleText(page, itemName);
  await page.locator(".inventory-row", { hasText: itemName }).first().getByRole("button", { name: "Open" }).click();
  await page.waitForFunction((name) => {
    const rows = [...document.querySelectorAll(".inventory-row")];
    const row = rows.find((candidate) => candidate.textContent.includes(name));
    return row && ![...row.querySelectorAll("button")].some((button) => button.textContent.trim() === "Open");
  }, itemName);

  await fillForm(page.locator("#barcodeForm"), {
    barcode: String(Math.floor(100000000000 + Math.random() * 899999999999)),
    name: barcodeItem,
    quantity: "2",
    unit: "can",
    location: "Kitchen/Pantry",
    estimated_cost: "4.50",
  });
  await page.locator('#barcodeForm button[type="submit"]').click();
  await expectVisibleText(page, barcodeItem);


  const pantryLocationId = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".location-settings-row")];
    const row = rows.find((candidate) => candidate.querySelector(".row-title span")?.textContent.trim() === "Kitchen/Pantry");
    return row?.querySelector("[data-location-name]")?.getAttribute("data-location-name") || "";
  });
  if (!pantryLocationId) throw new Error("Kitchen/Pantry location row was not rendered");
  await page.locator(`[data-location-name="${pantryLocationId}"]`).fill(`Smoke Pantry ${suffix}`);
  await page.locator(`[data-location-save="${pantryLocationId}"]`).click();
  await page.waitForFunction(
    async ({ id, expectedPath }) => {
      const response = await fetch("/api/locations", { credentials: "same-origin" });
      if (!response.ok) return false;
      const payload = await response.json();
      return payload.items.some((location) => location.id === id && location.path === expectedPath);
    },
    { id: pantryLocationId, expectedPath: `Kitchen/Smoke Pantry ${suffix}` },
  );
  await fillForm(page.locator("#recipeForm"), {
    name: recipeName,
    prep_minutes: "12",
    ingredients: `${itemName},1,count`,
    instructions: "Mix and cook.",
  });
  await page.locator('#recipeForm button[type="submit"]').click();
  await expectVisibleText(page, recipeName);

  const recipeRow = page.locator(".recipe-row", { hasText: recipeName }).first();
  await recipeRow.getByRole("button", { name: "Edit" }).click();
  await page.locator('#recipeForm [name="name"]').evaluate((element) => {
    if (document.activeElement !== element) throw new Error("Recipe edit did not focus the recipe name field");
  });
  await clickButton(page, "Cancel");

  await page.locator(".recipe-row", { hasText: recipeName }).first().getByRole("button", { name: "Plan Tonight" }).click();
  await expectVisibleText(page, recipeName);
  await clickButton(page, "Start Cooking");
  await page.locator("#cookingForm").waitFor({ state: "visible" });
  await page.locator("#cookingForm [name='leftover_quantity']").fill("1");
  await page.locator("#cookingForm [name='leftover_name']").fill(`Leftover ${recipeName}`);
  await page.locator("#cookingForm [name='leftover_location']").fill("Kitchen/Refrigerator");
  await page.locator('#cookingForm button[type="submit"]').click();
  await expectVisibleText(page, `Leftover ${recipeName}`);

  await page.locator("#receiptForm textarea[name='text']").fill(
    `Store: Browser Smoke Market\nDate: 2026-08-26\n${receiptItem},1,count,2.50\nTotal: 2.50\n`,
  );
  await page.locator('#receiptForm button[type="submit"]').click();
  await page.locator("#receiptReviewForm").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelector("#receiptReviewForm textarea[name='review_json']")?.value.length > 5);
  await page.locator("#receiptReviewForm").evaluate((form) => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await expectVisibleText(page, receiptItem);
  await expectVisibleText(page, "Browser Smoke Market");

  const firstDelete = page.locator("[data-delete]").first();
  await firstDelete.focus();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(100);

  await assertNoHorizontalOverflow(page, viewport.name);
  await assertNoCriticalA11yIssues(page, `${viewport.name} completed workflow`);

  if (httpErrors.length) {
    throw new Error(`${viewport.name} unexpected HTTP errors:\n- ${httpErrors.join("\n- ")}`);
  }
  if (consoleErrors.length) {
    throw new Error(`${viewport.name} console errors:\n- ${consoleErrors.join("\n- ")}`);
  }
  await context.close();
  return { viewport: viewport.name, itemName, recipeName, barcodeItem, receiptItem };
}

(async () => {
  const { chromium } = loadPlaywright();
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "pantryos-browser-smoke-"));
  const dbPath = path.join(tempDir, "pantryos.sqlite3");
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const server = startServer(dbPath, port);
  let browser;
  try {
    await waitForReady(baseUrl, server);
    browser = await chromium.launch({ headless: true });
    const results = [];
    for (const viewport of [
      { name: "phone", size: { width: 390, height: 844 } },
      { name: "kitchen-tablet", size: { width: 1024, height: 768 } },
    ]) {
      results.push(await runViewport(browser, baseUrl, viewport));
    }
    console.log(`browser smoke passed: ${JSON.stringify({ baseUrl, viewports: results.map((result) => result.viewport) })}`);
  } finally {
    if (browser) await browser.close();
    await stopServer(server);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
