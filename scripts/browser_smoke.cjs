#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const TOKEN = process.env.PANTRYOS_API_TOKEN || "browser-smoke-token";
const PYTHON = process.env.PYTHON || process.env.PYTHON_EXE || "python";

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

async function assertNoCriticalA11yIssues(page) {
  const issues = await page.evaluate(() => {
    const failures = [];
    const visible = (element) => {
      if (element.hidden || element.getAttribute("aria-hidden") === "true") return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const text = (element) => (element.innerText || element.textContent || "").trim();
    const labelText = (control) => {
      if (control.getAttribute("aria-label")) return control.getAttribute("aria-label").trim();
      if (control.id) {
        const explicit = document.querySelector(`label[for="${CSS.escape(control.id)}"]`);
        if (explicit && text(explicit)) return text(explicit);
      }
      const parent = control.closest("label");
      return parent ? text(parent) : "";
    };

    const ids = new Map();
    for (const element of document.querySelectorAll("[id]")) {
      ids.set(element.id, (ids.get(element.id) || 0) + 1);
    }
    for (const [id, count] of ids.entries()) {
      if (count > 1) failures.push(`Duplicate id ${id}`);
    }

    for (const control of document.querySelectorAll("input, textarea, select")) {
      if (!visible(control)) continue;
      if (control.type === "hidden") continue;
      if (!labelText(control)) failures.push(`Visible form control missing label: ${control.outerHTML.slice(0, 120)}`);
    }

    for (const button of document.querySelectorAll("button")) {
      if (!visible(button)) continue;
      if (!text(button) && !button.getAttribute("aria-label")) failures.push("Visible button missing accessible name");
    }

    for (const form of document.querySelectorAll("form")) {
      if (!visible(form)) continue;
      const submit = form.querySelector('button[type="submit"], input[type="submit"]');
      if (!submit) failures.push(`Visible form missing submit control: ${form.id || "unnamed"}`);
    }

    return failures;
  });
  if (issues.length) {
    throw new Error(`Critical accessibility checks failed:\n- ${issues.join("\n- ")}`);
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
  const page = await browser.newPage({ viewport: viewport.size });
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
  await page.getByLabel("Setup token").fill(TOKEN);
  await clickButton(page, "Sign In");
  await page.locator("#appShell").waitFor({ state: "visible" });
  await expectVisibleText(page, "Chicken Breast");

  await assertNoHorizontalOverflow(page, viewport.name);
  await assertNoCriticalA11yIssues(page);

  const suffix = `${viewport.name}-${Date.now()}`;
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
  await assertNoCriticalA11yIssues(page);

  if (httpErrors.length) {
    throw new Error(`${viewport.name} unexpected HTTP errors:\n- ${httpErrors.join("\n- ")}`);
  }
  if (consoleErrors.length) {
    throw new Error(`${viewport.name} console errors:\n- ${consoleErrors.join("\n- ")}`);
  }
  await page.close();
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
