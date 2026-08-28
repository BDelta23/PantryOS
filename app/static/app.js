const state = {
  data: null,
  filter: "",
  activeCookingSession: null,
  activeReceipt: null,
  purchases: [],
  activePurchase: null,
  activePriceAnalysis: null,
  priceError: "",
  editingRecipeId: null,
  authenticated: false,
  csrfToken: "",
  sessionInfo: null,
  barcodeScanner: {
    active: false,
    detector: null,
    stream: null,
    scanFrame: 0,
  },
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) {
    headers["X-CSRF-Token"] = state.csrfToken;
  }
  let response;
  try {
    response = await fetch(path, {
      ...options,
      method,
      credentials: "same-origin",
      headers,
    });
  } catch (_error) {
    throw new Error("PantryOS Core is offline; the request was not committed.");
  }
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.detail || data.error || data.title || "Request failed");
    error.status = response.status;
    error.code = data.code || "request_failed";
    error.problem = data;
    throw error;
  }
  return data;
}

async function refresh() {
  state.data = await api("/api/state");
  await loadPurchases();
  render();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2400);
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const value = String(reader.result || "");
      const marker = ";base64,";
      const markerIndex = value.indexOf(marker);
      resolve(markerIndex >= 0 ? value.slice(markerIndex + marker.length) : value);
    });
    reader.addEventListener("error", () => reject(new Error("Receipt image could not be read.")));
    reader.readAsDataURL(file);
  });
}

function formatQty(value, unit) {
  return `${value} ${unit || "count"}`;
}

function dayBadge(daysLeft) {
  const className = daysLeft <= 1 ? "alert" : daysLeft <= 3 ? "warn" : "info";
  const label = daysLeft === 0 ? "today" : `${daysLeft}d`;
  return `<span class="badge ${className}">${label}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function decimalNumber(value, fallback = 0) {
  const parsed = Number.parseFloat(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatMoney(value, currency = "USD") {
  if (value === null || value === undefined || value === "") return "total pending";
  return `${currency || "USD"} ${Number.parseFloat(String(value)).toFixed(2)}`;
}

function formatPurchaseDate(value) {
  if (!value) return "date pending";
  return String(value).slice(0, 10);
}

async function loadPurchases() {
  try {
    const result = await api("/api/purchases");
    state.purchases = result.items || [];
    state.priceError = "";
  } catch (error) {
    state.purchases = [];
    state.priceError = userFacingErrorMessage(error);
  }
}

function render() {
  const data = state.data;
  const summary = data.summary;
  const tonight = data.meal_plan.Tonight || Object.values(data.meal_plan)[0];
  const tonightRecipe = data.recipes.find((recipe) => recipe.name === tonight);

  $("#tonightTime").textContent = tonightRecipe?.prep_minutes ? `${tonightRecipe.prep_minutes}m` : "";
  $("#tonightMeal").textContent = tonight || "No meal planned";
  $("#tonightDetails").textContent = tonightRecipe
    ? `${tonightRecipe.ingredients.length} ingredients tracked`
    : "Plan a meal from a recipe below.";
  $("#startCookingButton").disabled = !tonightRecipe || Boolean(state.activeCookingSession);
  $("#expiringCount").textContent = summary.expiring_soon_count;
  $("#shoppingCount").textContent = summary.shopping_list_count;
  $("#mealCount").textContent = data.meals_with_two_or_fewer_missing.length;

  renderCooking(tonightRecipe);
  renderUseSoon(summary.expiring_soon);
  renderShopping(data.shopping_list, summary.suggested_purchases);
  renderMeals(data.meals_with_two_or_fewer_missing);
  renderInventory(data.items);
  renderKnownLocations(data.core.locations || data.summary.locations || []);
  renderLocationSettings(data.core.locations || []);
  renderProductSettings(data.core.products || []);
  renderRecipes(data.recipes, data.summary.possible_meals);
  renderReceiptReview();
  renderPurchases();
  renderPurchaseDetail();
  renderPriceAnalysis();
}

function renderReceiptReview() {
  const form = $("#receiptReviewForm");
  const textarea = form.elements.review_json;
  if (!state.activeReceipt) {
    form.hidden = true;
    textarea.value = "";
    return;
  }
  form.hidden = false;
  textarea.value = JSON.stringify(state.activeReceipt.review, null, 2);
}

function renderPurchases() {
  const list = $("#purchaseHistoryList");
  if (state.priceError && !state.purchases.length) {
    list.innerHTML = `<li class="empty">${escapeHtml(state.priceError)}</li>`;
    return;
  }
  if (!state.purchases.length) {
    list.innerHTML = `<li class="empty">Complete a shopping list or commit a receipt to see purchase history.</li>`;
    return;
  }
  list.innerHTML = state.purchases
    .slice(0, 8)
    .map(
      (purchase) => `
        <li class="list-row">
          <div class="row-title">
            <span>${escapeHtml(purchase.store || "Purchase")}</span>
            <span class="badge">${escapeHtml(formatMoney(purchase.total_cost, purchase.currency))}</span>
          </div>
          <div class="row-subtitle">${escapeHtml(formatPurchaseDate(purchase.purchased_at))}</div>
          <div class="row-actions">
            <button class="secondary" type="button" data-purchase-detail="${escapeHtml(purchase.id)}">Inspect</button>
          </div>
        </li>`
    )
    .join("");
}

function renderPurchaseDetail() {
  const panel = $("#purchaseDetail");
  if (!state.activePurchase) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const purchase = state.activePurchase.purchase || {};
  const lines = state.activePurchase.lines || [];
  panel.hidden = false;
  panel.innerHTML = `
    <div class="row-title">
      <span>${escapeHtml(purchase.store || "Purchase detail")}</span>
      <span class="badge">${escapeHtml(formatMoney(purchase.total_cost, purchase.currency))}</span>
    </div>
    <div class="row-subtitle">${escapeHtml(formatPurchaseDate(purchase.purchased_at))}</div>
    <div class="detail-stack">
      ${lines.length ? lines.map(renderPurchaseLine).join("") : `<div class="empty">No purchase lines recorded.</div>`}
    </div>`;
}

function renderPurchaseLine(line) {
  const productName = line.product_name || line.name || line.display_name || line.product_id || "Product";
  const quantity = formatQty(line.quantity || "1", line.unit || "count");
  const lineTotal = line.line_total || line.total_cost;
  return `
    <div class="detail-row">
      <div>
        <div class="row-title"><span>${escapeHtml(productName)}</span></div>
        <div class="row-subtitle">${escapeHtml(quantity)}${lineTotal ? ` | ${escapeHtml(formatMoney(lineTotal, line.currency))}` : ""}</div>
      </div>
      ${line.product_id ? `<button class="secondary" type="button" data-price-analysis="${escapeHtml(line.product_id)}">Price Trend</button>` : ""}
    </div>`;
}

function renderPriceAnalysis() {
  const panel = $("#priceAnalysis");
  if (state.priceError && state.activePurchase) {
    panel.hidden = false;
    panel.innerHTML = `<div class="empty">${escapeHtml(state.priceError)}</div>`;
    return;
  }
  if (!state.activePriceAnalysis) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const product = state.activePriceAnalysis.product || {};
  const analysis = state.activePriceAnalysis.analysis || {};
  const latest = analysis.latest;
  const prices = state.activePriceAnalysis.prices || [];
  panel.hidden = false;
  panel.innerHTML = `
    <div class="price-summary">
      <div class="row-title">
        <span>${escapeHtml(product.name || "Price analysis")}</span>
        <span class="badge ${escapeHtml(priceStatusClass(latest?.status))}">${escapeHtml(latest?.status || "no history")}</span>
      </div>
      <div class="row-subtitle">${escapeHtml(latest?.explanation || analysis.evidence_window || "No comparable purchase history yet.")}</div>
      <div class="row-subtitle">Baseline: ${escapeHtml(formatMoney(latest?.baseline_unit_price, latest?.currency))} | Latest: ${escapeHtml(formatMoney(latest?.unit_price, latest?.currency))} | Ratio: ${escapeHtml(latest?.anomaly_ratio ? `${Number.parseFloat(String(latest.anomaly_ratio)).toFixed(2)}x` : "n/a")}</div>
    </div>
    <div class="detail-stack">
      ${prices.length ? prices.slice(0, 6).map(renderPriceRow).join("") : `<div class="empty">No price rows recorded.</div>`}
    </div>`;
}

function renderPriceRow(row) {
  return `
    <div class="price-row">
      <span>${escapeHtml(formatPurchaseDate(row.purchased_at))}${row.store ? ` | ${escapeHtml(row.store)}` : ""}</span>
      <span>${escapeHtml(formatMoney(row.unit_price, row.currency))}/${escapeHtml(row.comparable_unit || row.unit || "unit")}</span>
    </div>`;
}

function priceStatusClass(status) {
  if (status === "high") return "alert";
  if (status === "low") return "info";
  if (status === "normal") return "";
  return "warn";
}

function renderCooking(recipe) {
  const form = $("#cookingForm");
  const allocations = $("#cookingAllocations");
  if (!state.activeCookingSession) {
    form.hidden = true;
    allocations.innerHTML = "";
    return;
  }
  form.hidden = false;
  const session = state.activeCookingSession;
  $("#cookingStatus").textContent = `${recipe?.name || session.recipe_name} is in progress`;
  const proposed = proposeAllocations(recipe);
  if (!proposed.length) {
    allocations.innerHTML = `<div class="empty">No matched inventory lots are available for this recipe.</div>`;
    return;
  }
  allocations.innerHTML = proposed
    .map(
      (row) => `
        <label class="allocation-row">
          <span>${escapeHtml(row.ingredient.name)}</span>
          <span class="row-subtitle">${escapeHtml(row.lot.name)} in ${escapeHtml(row.lot.location)}</span>
          <input data-cook-lot="${escapeHtml(row.lot.id)}" data-cook-unit="${escapeHtml(row.lot.unit)}" value="${escapeHtml(row.quantity)}" inputmode="decimal" />
        </label>`
    )
    .join("");
}

function proposeAllocations(recipe) {
  if (!recipe) return [];
  const usedLots = new Set();
  return recipe.ingredients
    .map((ingredient) => {
      const lot = state.data.items.find((item) => {
        if (usedLots.has(item.id)) return false;
        if (ingredient.product_id && item.product_id === ingredient.product_id) return true;
        return item.name.toLowerCase() === ingredient.name.toLowerCase();
      });
      if (!lot) return null;
      usedLots.add(lot.id);
      const wanted = decimalNumber(ingredient.quantity, 1);
      const available = decimalNumber(lot.quantity, wanted);
      return {
        ingredient,
        lot,
        quantity: String(Math.min(wanted, available)),
      };
    })
    .filter(Boolean);
}

function renderUseSoon(items) {
  const list = $("#useSoonList");
  if (!items.length) {
    list.innerHTML = `<li class="empty">Nothing expires in the next four days.</li>`;
    return;
  }
  list.innerHTML = items
    .map(
      (item) => `
        <li class="list-row">
          <div class="row-title">
            <span>${escapeHtml(item.name)}</span>
            ${dayBadge(item.days_left)}
          </div>
          <div class="row-subtitle">${escapeHtml(formatQty(item.quantity, item.unit))} in ${escapeHtml(item.location)}</div>
        </li>`
    )
    .join("");
}

function renderShopping(items, suggestions) {
  const list = $("#shoppingList");
  const visibleItems = items.filter((item) => item.status === "active");
  if (!visibleItems.length && !suggestions.length) {
    list.innerHTML = `<li class="empty">No shopping items or restock suggestions.</li>`;
    return;
  }
  const shoppingRows = visibleItems.map(
    (item) => `
      <li class="list-row ${item.checked ? "is-checked" : ""}">
        <div class="row-title">
          <span>${escapeHtml(item.name)}</span>
          <span class="badge">${escapeHtml(formatQty(item.quantity, item.unit))}</span>
        </div>
        <div class="row-subtitle">${escapeHtml(item.source)}${item.store ? ` | ${escapeHtml(item.store)}` : ""}</div>
        <div class="row-actions">
          <button class="secondary" type="button" data-shopping-${item.checked ? "uncheck" : "check"}="${escapeHtml(item.id)}">${item.checked ? "Uncheck" : "Check"}</button>
          <button class="danger" type="button" data-shopping-remove="${escapeHtml(item.id)}">Remove</button>
        </div>
      </li>`
  );
  const suggestionRows = suggestions.map(
    (item) => `
      <li class="list-row">
        <div class="row-title">
          <span>${escapeHtml(item.name)}</span>
          <span class="badge warn">Need ${escapeHtml(formatQty(item.quantity, item.unit))}</span>
        </div>
        <div class="row-subtitle">Suggested purchase</div>
      </li>`
  );
  list.innerHTML = [...shoppingRows, ...suggestionRows].join("");
}

function renderMeals(meals) {
  const list = $("#mealList");
  if (!meals.length) {
    list.innerHTML = `<li class="empty">Add recipes to calculate quick meals.</li>`;
    return;
  }
  list.innerHTML = meals
    .map(
      (meal) => `
        <li class="list-row">
          <div class="row-title">
            <span>${escapeHtml(meal.name)}</span>
            <span class="badge ${meal.missing_count ? "warn" : ""}">${meal.missing_count} missing</span>
          </div>
          <div class="row-subtitle">${meal.prep_minutes || "--"} min</div>
        </li>`
    )
    .join("");
}

function renderKnownLocations(locations) {
  const datalist = $("#knownLocations");
  if (!datalist) return;
  datalist.innerHTML = locations
    .map((location) => `<option value="${escapeHtml(location.path || location.name || location)}"></option>`)
    .join("");
}
const LOCATION_TYPES = ["house", "room", "refrigerator", "freezer", "pantry", "shelf", "bin", "cabinet", "other"];

function renderLocationSettings(locations) {
  const list = $("#locationSettingsList");
  if (!list) return;
  const activeLocations = locations.filter((location) => location.active !== 0).slice(0, 16);
  if (!activeLocations.length) {
    list.innerHTML = `<li class="empty">Add food to create locations.</li>`;
    return;
  }
  list.innerHTML = activeLocations
    .map((location) => {
      const locationId = escapeHtml(location.id);
      return `
        <li class="list-row location-settings-row">
          <div class="row-title">
            <span>${escapeHtml(location.path || location.name)}</span>
            <span class="badge">${escapeHtml(location.active_lot_count || 0)} lots</span>
          </div>
          <div class="location-settings-grid">
            <label>Name<input value="${escapeHtml(location.name || "")}" data-location-name="${locationId}" /></label>
            <label>Parent<input list="knownLocations" value="${escapeHtml(location.parent_path || "")}" data-location-parent="${locationId}" /></label>
            <label>Type<select data-location-type="${locationId}">${locationTypeOptions(location.type)}</select></label>
            <label>Temperature entity<input value="${escapeHtml(location.temperature_entity_id || "")}" placeholder="sensor.fridge_temp" data-location-temperature="${locationId}" /></label>
          </div>
          <div class="product-settings-actions">
            <button class="secondary" type="button" data-location-save="${locationId}">Save</button>
          </div>
        </li>`;
    })
    .join("");
}

function locationTypeOptions(selected) {
  return LOCATION_TYPES.map((type) => `<option value="${type}"${type === selected ? " selected" : ""}>${type}</option>`).join("");
}

function locationSettingValue(locationId, key) {
  return document.querySelector(`[data-location-${key}="${locationId}"]`)?.value?.trim() || "";
}

function renderProductSettings(products) {
  const list = $("#productSettingsList");
  if (!list) return;
  const activeProducts = products.filter((product) => product.active !== 0).slice(0, 12);
  if (!activeProducts.length) {
    list.innerHTML = `<li class="empty">Add food to configure product defaults.</li>`;
    return;
  }
  list.innerHTML = activeProducts
    .map((product) => {
      const productId = escapeHtml(product.id);
      return `
        <li class="list-row product-settings-row">
          <div class="row-title">
            <span>${escapeHtml(product.name)}</span>
            <span class="badge">${escapeHtml(product.default_unit || "count")}</span>
          </div>
          <div class="product-settings-grid">
            <label>Category<input value="${escapeHtml(product.category || "")}" data-product-category="${productId}" /></label>
            <label>Default unit<input value="${escapeHtml(product.default_unit || "count")}" data-product-unit="${productId}" /></label>
            <label>Min<input inputmode="decimal" value="${escapeHtml(product.minimum_stock_quantity || "")}" data-product-minimum="${productId}" /></label>
            <label>Min unit<input value="${escapeHtml(product.minimum_stock_unit || product.default_unit || "count")}" data-product-minimum-unit="${productId}" /></label>
            <label>Location<input list="knownLocations" value="${escapeHtml(productPreferredLocation(product))}" data-product-location="${productId}" /></label>
            <label>Shelf days<input inputmode="numeric" value="${escapeHtml(product.default_shelf_life_days ?? "")}" data-product-shelf="${productId}" /></label>
            <label>Opened days<input inputmode="numeric" value="${escapeHtml(product.opened_shelf_life_days ?? "")}" data-product-opened-shelf="${productId}" /></label>
          </div>
          <div class="product-settings-actions">
            <button class="secondary" type="button" data-product-save="${productId}">Save</button>
          </div>
        </li>`;
    })
    .join("");
}

function productPreferredLocation(product) {
  const locations = state.data?.core?.locations || state.data?.summary?.locations || [];
  const match = locations.find((location) => (location.id || location.location_id) === product.preferred_location_id);
  return match?.path || match?.name || "";
}

function productSettingValue(productId, key) {
  return document.querySelector(`[data-product-${key}="${productId}"]`)?.value?.trim() || "";
}

function renderInventory(items) {
  const list = $("#inventoryList");
  const filter = state.filter.trim().toLowerCase();
  const visible = filter
    ? items.filter((item) => `${item.name} ${item.location}`.toLowerCase().includes(filter))
    : items;
  if (!visible.length) {
    list.innerHTML = `<div class="empty">No inventory matches this view.</div>`;
    return;
  }
  list.innerHTML = visible
    .map(
      (item) => `
        <article class="inventory-row">
          <div>
            <div class="row-title">
              <span>${escapeHtml(item.name)}</span>
              <span class="badge">${escapeHtml(formatQty(item.quantity, item.unit))}</span>
            </div>
            <div class="row-subtitle">${escapeHtml(item.location)}${item.expires ? ` | expires ${escapeHtml(item.expires)}` : ""}${item.estimated_value ? ` | ${escapeHtml(item.estimated_value)} value` : ""}</div>
          </div>
          <div class="item-actions">
            <input aria-label="Consume quantity for ${escapeHtml(item.name)}" value="1" inputmode="decimal" data-consume-qty="${escapeHtml(item.id)}" />
            <button class="secondary" type="button" data-consume="${escapeHtml(item.id)}">Consume</button>
            <input class="location-input" aria-label="Move ${escapeHtml(item.name)} to location" list="knownLocations" placeholder="Move to" value="${escapeHtml(item.location)}" data-move-location="${escapeHtml(item.id)}" />
            <button class="secondary" type="button" data-move="${escapeHtml(item.id)}">Move</button>
            ${item.opened ? "" : `<button class="secondary" type="button" data-open="${escapeHtml(item.id)}">Open</button>`}
            <button class="danger" type="button" data-delete="${escapeHtml(item.id)}">Delete</button>
          </div>
        </article>`
    )
    .join("");
}

function renderRecipes(recipes, possibleMeals) {
  const possibleNames = new Set(possibleMeals.map((meal) => meal.name));
  const list = $("#recipeList");
  if (!recipes.length) {
    list.innerHTML = `<div class="empty">No recipes yet.</div>`;
    return;
  }
  list.innerHTML = recipes
    .map((recipe) => {
      const possible = possibleNames.has(recipe.name);
      const ingredients = recipe.ingredients
        .map((item) => `${item.name} ${item.quantity} ${item.unit}`)
        .join(", ");
      return `
        <article class="recipe-row">
          <div class="row-title">
            <span>${escapeHtml(recipe.name)}</span>
            <span class="badge ${possible ? "" : "warn"}">${possible ? "ready" : "needs list"}</span>
          </div>
          <div class="row-subtitle">${recipe.prep_minutes || "--"} min | ${escapeHtml(ingredients)}</div>
          <div class="recipe-actions">
            <button class="secondary" type="button" data-plan="${escapeHtml(recipe.name)}">Plan Tonight</button>
            <button type="button" data-missing="${escapeHtml(recipe.name)}">Add Missing</button>
            <button class="secondary" type="button" data-recipe-edit="${escapeHtml(recipe.id)}">Edit</button>
            <button class="danger" type="button" data-recipe-delete="${escapeHtml(recipe.id)}">Delete</button>
          </div>
        </article>`;
    })
    .join("");
}

function recipeIngredientsText(recipe) {
  return (recipe.ingredients || [])
    .map((item) => [item.name, item.quantity || "1", item.unit || "count"].join(","))
    .join("\n");
}

function startRecipeEdit(recipeId) {
  const recipe = state.data.recipes.find((row) => row.id === recipeId);
  if (!recipe) return;
  const form = $("#recipeForm");
  state.editingRecipeId = recipe.id;
  form.elements.name.value = recipe.name || "";
  form.elements.prep_minutes.value = recipe.prep_minutes || "";
  form.elements.ingredients.value = recipeIngredientsText(recipe);
  form.elements.instructions.value = recipe.instructions || "";
  $("#recipeFormTitle").textContent = "Edit Recipe";
  $("#recipeSubmitButton").textContent = "Save Recipe";
  $("#cancelRecipeEditButton").hidden = false;
  form.elements.name.focus();
}

function resetRecipeEditor() {
  state.editingRecipeId = null;
  const form = $("#recipeForm");
  form.reset();
  $("#recipeFormTitle").textContent = "Add Recipe";
  $("#recipeSubmitButton").textContent = "Add Recipe";
  $("#cancelRecipeEditButton").hidden = true;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function compactPayload(payload) {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== "" && value !== null)
  );
}

function parseIngredients(value) {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, quantity = "1", unit = "count"] = line.split(",").map((part) => part.trim());
      return { name, quantity, unit };
    });
}

function tonightRecipe() {
  const tonight = state.data.meal_plan.Tonight || Object.values(state.data.meal_plan)[0];
  return state.data.recipes.find((recipe) => recipe.name === tonight);
}

async function handleItemSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  if (payload.leftover) {
    payload.tags = ["leftover"];
    delete payload.leftover;
  }
  await api("/api/items", { method: "POST", body: JSON.stringify(payload) });
  form.reset();
  showToast("Food added");
  await refresh();
}


const DEFAULT_BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"];

const nativeBarcodeScannerAdapter = {
  supported() {
    return "BarcodeDetector" in window && Boolean(navigator.mediaDevices?.getUserMedia);
  },
  async start() {
    const detector = new BarcodeDetector({ formats: DEFAULT_BARCODE_FORMATS });
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
    return { detector, stream };
  },
  async attach(video, stream) {
    video.srcObject = stream;
    await video.play();
  },
  async detect(detector, video) {
    return detector.detect(video);
  },
  stop(stream) {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  },
};

function barcodeScannerAdapter() {
  return window.PantryOSBarcodeScannerAdapter || nativeBarcodeScannerAdapter;
}

function supportsBarcodeCamera() {
  return Boolean(barcodeScannerAdapter().supported());
}

function setupBarcodeScanner() {
  const panel = $("#barcodeScannerPanel");
  const status = $("#barcodeScannerStatus");
  if (!panel || !status) return;
  panel.hidden = false;
  if (!supportsBarcodeCamera()) {
    $("#barcodeCameraButton").disabled = true;
    status.textContent = "Manual barcode entry is available on this browser.";
    return;
  }
  $("#barcodeCameraButton").disabled = false;
  status.textContent = "Camera scanner ready.";
}

async function startBarcodeScanner() {
  const adapter = barcodeScannerAdapter();
  if (!adapter.supported()) {
    showToast("Camera scanning is unavailable on this browser");
    return;
  }
  const video = $("#barcodeVideo");
  const status = $("#barcodeScannerStatus");
  stopBarcodeScanner();
  try {
    const { detector, stream } = await adapter.start();
    state.barcodeScanner = { active: true, adapter, detector, stream, scanFrame: 0 };
    await adapter.attach(video, stream);
    $("#barcodeCameraButton").hidden = true;
    $("#barcodeStopButton").hidden = false;
    status.textContent = "Scanning for a barcode.";
    scanBarcodeFrame();
  } catch (error) {
    stopBarcodeScanner();
    status.textContent = "Manual barcode entry is available.";
    showToast(error.message || "Camera scanner could not start");
  }
}

async function scanBarcodeFrame() {
  if (!state.barcodeScanner.active) return;
  const video = $("#barcodeVideo");
  try {
    const codes = await state.barcodeScanner.adapter.detect(state.barcodeScanner.detector, video);
    const rawValue = codes.find((code) => code.rawValue)?.rawValue;
    if (rawValue) {
      $("#barcodeForm").elements.barcode.value = rawValue;
      stopBarcodeScanner();
      showToast("Barcode captured");
      return;
    }
  } catch (_error) {
    stopBarcodeScanner();
    showToast("Camera scanner stopped");
    return;
  }
  state.barcodeScanner.scanFrame = window.requestAnimationFrame(() => scanBarcodeFrame());
}

function stopBarcodeScanner() {
  if (state.barcodeScanner.scanFrame) {
    window.cancelAnimationFrame(state.barcodeScanner.scanFrame);
  }
  if (state.barcodeScanner.stream) {
    state.barcodeScanner.adapter.stop(state.barcodeScanner.stream);
  }
  const video = $("#barcodeVideo");
  if (video) {
    video.pause();
    video.srcObject = null;
  }
  state.barcodeScanner = { active: false, adapter: nativeBarcodeScannerAdapter, detector: null, stream: null, scanFrame: 0 };
  const startButton = $("#barcodeCameraButton");
  const stopButton = $("#barcodeStopButton");
  if (startButton) startButton.hidden = !supportsBarcodeCamera();
  if (stopButton) stopButton.hidden = true;
}

async function handleBarcodeSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  const barcode = payload.barcode;
  delete payload.barcode;
  const mappingName = payload.name;
  try {
    const result = await api(`/api/barcodes/${encodeURIComponent(barcode)}/add-lot`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    showToast(`${result.item.name} added from barcode`);
    await refresh();
    return;
  } catch (error) {
    if (!mappingName) {
      const nameInput = form.elements.name;
      if (nameInput) {
        nameInput.setAttribute("aria-invalid", "true");
        nameInput.focus();
      }
      const status = $("#barcodeScannerStatus");
      if (status) status.textContent = "Unknown barcode. Enter product details to map it manually.";
      showToast("Unknown barcode. Enter product details to map it.");
      return;
    }
  }
  if (form.elements.name) form.elements.name.removeAttribute("aria-invalid");
  await api("/api/barcodes/mappings", {
    method: "POST",
    body: JSON.stringify({
      barcode,
      name: mappingName,
      package_quantity: payload.quantity || "1",
      package_unit: payload.unit || "count",
    }),
  });
  const result = await api(`/api/barcodes/${encodeURIComponent(barcode)}/add-lot`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  form.reset();
  showToast(`${result.item.name} mapped and added`);
  await refresh();
}

async function handleRecipeSubmit(event) {
  event.preventDefault();
  const payload = compactPayload(formData(event.currentTarget));
  payload.ingredients = parseIngredients(payload.ingredients || "");
  const editingId = state.editingRecipeId;
  await api(editingId ? `/api/recipes/${encodeURIComponent(editingId)}` : "/api/recipes", {
    method: editingId ? "PATCH" : "POST",
    body: JSON.stringify(payload),
  });
  resetRecipeEditor();
  showToast(editingId ? "Recipe updated" : "Recipe added");
  await refresh();
}

async function handleReceiptSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  const imageFile = form.elements.image?.files?.[0];
  let upload;
  if (imageFile) {
    upload = {
      filename: imageFile.name || "browser-receipt.jpg",
      mime_type: imageFile.type || "image/jpeg",
      content_base64: await readFileBase64(imageFile),
    };
  } else {
    if (!payload.text) throw new Error("Enter receipt text or choose a receipt image.");
    upload = { filename: "browser-receipt.txt", mime_type: "text/plain", text: payload.text };
  }
  const uploaded = await api("/api/receipts", {
    method: "POST",
    body: JSON.stringify(upload),
  });
  const extracted = await api(`/api/receipts/${encodeURIComponent(uploaded.receipt.id)}/extract`, { method: "POST" });
  state.activeReceipt = { id: uploaded.receipt.id, review: extracted.review };
  showToast(`${extracted.review.items.length} receipt item${extracted.review.items.length === 1 ? "" : "s"} extracted`);
  renderReceiptReview();
}

async function handleReceiptReviewSubmit(event) {
  event.preventDefault();
  if (!state.activeReceipt) return;
  const review = JSON.parse(event.currentTarget.elements.review_json.value);
  await api(`/api/receipts/${encodeURIComponent(state.activeReceipt.id)}/review`, {
    method: "PATCH",
    body: JSON.stringify(review),
  });
  const committed = await api(`/api/receipts/${encodeURIComponent(state.activeReceipt.id)}/commit`, { method: "POST" });
  state.activeReceipt = null;
  state.activePurchase = null;
  state.activePriceAnalysis = null;
  state.priceError = "";
  $("#receiptForm").reset();
  showToast(`${committed.lots.length} receipt item${committed.lots.length === 1 ? "" : "s"} added`);
  await refresh();
}

async function handleRejectReceipt() {
  if (!state.activeReceipt) return;
  await api(`/api/receipts/${encodeURIComponent(state.activeReceipt.id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: "browser reject" }),
  });
  state.activeReceipt = null;
  showToast("Receipt rejected");
  renderReceiptReview();
}

async function handlePurchaseDetail(purchaseId) {
  state.activePurchase = await api(`/api/purchases/${encodeURIComponent(purchaseId)}`);
  state.activePriceAnalysis = null;
  state.priceError = "";
  renderPurchaseDetail();
  renderPriceAnalysis();
}

async function handlePriceAnalysis(productId) {
  state.priceError = "";
  try {
    state.activePriceAnalysis = await api(`/api/products/${encodeURIComponent(productId)}/prices`);
  } catch (error) {
    state.activePriceAnalysis = null;
    state.priceError = userFacingErrorMessage(error);
  }
  renderPriceAnalysis();
}

async function handlePurchaseSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  const checked = state.data.shopping_list.filter((item) => item.status === "active" && item.checked);
  const fallback = state.data.shopping_list.filter((item) => item.status === "active" && !item.checked);
  const selected = checked.length ? checked : fallback;
  if (!selected.length) {
    showToast("No shopping items to purchase");
    return;
  }
  payload.items = selected.map((item) => ({
    shopping_id: item.id,
    quantity: item.quantity,
    unit: item.unit,
  }));
  const result = await api("/api/shopping/complete-purchase", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.activePurchase = null;
  state.activePriceAnalysis = null;
  state.priceError = "";
  form.reset();
  showToast(`${result.lots.length} purchased item${result.lots.length === 1 ? "" : "s"} added`);
  await refresh();
}

async function handleStartCooking() {
  const recipe = tonightRecipe();
  if (!recipe) {
    showToast("Plan a recipe first");
    return;
  }
  const result = await api("/api/cooking/sessions", {
    method: "POST",
    body: JSON.stringify({ recipe_name: recipe.name, planned_servings: "1" }),
  });
  state.activeCookingSession = { ...result.session, recipe_name: recipe.name };
  showToast("Cooking started");
  render();
}

async function handleCookingSubmit(event) {
  event.preventDefault();
  if (!state.activeCookingSession) return;
  const form = event.currentTarget;
  const payload = { allocations: cookingAllocations() };
  if (!payload.allocations.length) {
    showToast("Choose at least one allocation");
    return;
  }
  const formValues = compactPayload(formData(form));
  if (decimalNumber(formValues.leftover_quantity) > 0 && formValues.leftover_name) {
    payload.leftovers = [
      {
        name: formValues.leftover_name,
        quantity: formValues.leftover_quantity,
        unit: "serving",
        location: formValues.leftover_location || "Kitchen/Refrigerator",
        use_by: formValues.leftover_use_by,
      },
    ];
  }
  const sessionId = state.activeCookingSession.id;
  const result = await api(`/api/cooking/sessions/${encodeURIComponent(sessionId)}/complete`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.activeCookingSession = null;
  form.reset();
  showToast(`Cooking completed${result.leftovers.length ? " with leftovers" : ""}`);
  await refresh();
}

async function handleCancelCooking() {
  if (!state.activeCookingSession) return;
  await api(`/api/cooking/sessions/${encodeURIComponent(state.activeCookingSession.id)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: "browser cancel" }),
  });
  state.activeCookingSession = null;
  $("#cookingForm").reset();
  showToast("Cooking cancelled");
  await refresh();
}

function cookingAllocations() {
  return [...document.querySelectorAll("[data-cook-lot]")]
    .map((input) => ({
      lot_id: input.dataset.cookLot,
      quantity: input.value,
      unit: input.dataset.cookUnit,
    }))
    .filter((row) => decimalNumber(row.quantity) > 0);
}

async function handlePageClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const purchaseDetail = target.dataset.purchaseDetail;
  if (purchaseDetail) {
    await handlePurchaseDetail(purchaseDetail);
    return;
  }

  const priceProduct = target.dataset.priceAnalysis;
  if (priceProduct) {
    await handlePriceAnalysis(priceProduct);
    return;
  }

  const saveLocation = target.dataset.locationSave;
  if (saveLocation) {
    await api(`/api/locations/${encodeURIComponent(saveLocation)}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: locationSettingValue(saveLocation, "name"),
        parent_path: locationSettingValue(saveLocation, "parent") || null,
        type: locationSettingValue(saveLocation, "type") || "other",
        temperature_entity_id: locationSettingValue(saveLocation, "temperature") || null,
      }),
    });
    showToast("Location saved");
    await refresh();
    return;
  }
  const saveProduct = target.dataset.productSave;
  if (saveProduct) {
    const minimumStock = productSettingValue(saveProduct, "minimum");
    const defaultUnit = productSettingValue(saveProduct, "unit");
    await api(`/api/products/${encodeURIComponent(saveProduct)}`, {
      method: "PATCH",
      body: JSON.stringify({
        category: productSettingValue(saveProduct, "category") || null,
        default_unit: defaultUnit,
        minimum_stock_quantity: minimumStock === "" ? null : minimumStock,
        minimum_stock_unit: productSettingValue(saveProduct, "minimum-unit") || defaultUnit,
        preferred_location: productSettingValue(saveProduct, "location") || null,
        default_shelf_life_days: productSettingValue(saveProduct, "shelf") || null,
        opened_shelf_life_days: productSettingValue(saveProduct, "opened-shelf") || null,
      }),
    });
    showToast("Product settings saved");
    await refresh();
    return;
  }

  const consumeId = target.dataset.consume;
  if (consumeId) {
    const qty = document.querySelector(`[data-consume-qty="${consumeId}"]`)?.value || "1";
    await api(`/api/items/${consumeId}/consume`, {
      method: "POST",
      body: JSON.stringify({ quantity: qty }),
    });
    showToast("Inventory updated");
    await refresh();
    return;
  }

  const moveId = target.dataset.move;
  if (moveId) {
    const location = document.querySelector(`[data-move-location="${moveId}"]`)?.value?.trim();
    if (!location) {
      showToast("Choose a destination location");
      return;
    }
    await api(`/api/items/${encodeURIComponent(moveId)}/move`, {
      method: "POST",
      body: JSON.stringify({ location }),
    });
    showToast("Food moved");
    await refresh();
    return;
  }

  const openId = target.dataset.open;
  if (openId) {
    if (!window.confirm("Open this food and apply its opened shelf-life policy?")) return;
    await api(`/api/items/${encodeURIComponent(openId)}/open`, { method: "POST", body: JSON.stringify({}) });
    showToast("Food opened");
    await refresh();
    return;
  }

  const deleteId = target.dataset.delete;
  if (deleteId) {
    if (!window.confirm("Remove this food from usable inventory?")) return;
    await api(`/api/items/${deleteId}`, { method: "DELETE" });
    showToast("Food removed");
    await refresh();
    return;
  }

  const shoppingCheck = target.dataset.shoppingCheck;
  if (shoppingCheck) {
    await api(`/api/shopping/${encodeURIComponent(shoppingCheck)}/check`, { method: "POST" });
    showToast("Shopping item checked");
    await refresh();
    return;
  }

  const shoppingUncheck = target.dataset.shoppingUncheck;
  if (shoppingUncheck) {
    await api(`/api/shopping/${encodeURIComponent(shoppingUncheck)}/uncheck`, { method: "POST" });
    showToast("Shopping item unchecked");
    await refresh();
    return;
  }

  const shoppingRemove = target.dataset.shoppingRemove;
  if (shoppingRemove) {
    if (!window.confirm("Remove this shopping item?")) return;
    await api(`/api/shopping/${encodeURIComponent(shoppingRemove)}`, { method: "DELETE" });
    showToast("Shopping item removed");
    await refresh();
    return;
  }

  const recipeEdit = target.dataset.recipeEdit;
  if (recipeEdit) {
    startRecipeEdit(recipeEdit);
    return;
  }

  const recipeDelete = target.dataset.recipeDelete;
  if (recipeDelete) {
    if (!window.confirm("Delete this recipe?")) return;
    await api(`/api/recipes/${encodeURIComponent(recipeDelete)}`, { method: "DELETE" });
    if (state.editingRecipeId === recipeDelete) {
      resetRecipeEditor();
    }
    showToast("Recipe deleted");
    await refresh();
    return;
  }

  const missingRecipe = target.dataset.missing;
  if (missingRecipe) {
    await api(`/api/recipes/${encodeURIComponent(missingRecipe)}/shopping`, { method: "POST" });
    showToast("Missing ingredients added");
    await refresh();
    return;
  }

  const planRecipe = target.dataset.plan;
  if (planRecipe) {
    await api("/api/meal-plan", {
      method: "POST",
      body: JSON.stringify({ day: "Tonight", recipe_name: planRecipe }),
    });
    state.activeCookingSession = null;
    showToast("Tonight planned");
    await refresh();
  }
}

function renderLoginSessionInfo(session = state.sessionInfo || {}) {
  const configured = session.setup_token_configured !== false;
  const cookie = session.cookie || {};
  const cookieName = cookie.name || "pantryos_session";
  const sameSite = cookie.same_site || "Lax";
  const secureLabel = cookie.secure ? "Secure" : "Local HTTP";
  const input = $("#setupTokenInput");
  const submit = $("#loginSubmitButton");
  const toggle = $("#toggleTokenButton");

  $("#loginStatus").textContent = configured
    ? "Local setup token required."
    : "Setup token is not configured on this PantryOS server.";
  $("#loginCookiePolicy").textContent = `${cookieName} | HttpOnly | SameSite=${sameSite} | ${secureLabel}`;
  $("#loginConfigStatus").textContent = configured ? "Ready" : "PANTRYOS_API_TOKEN missing";
  input.type = "password";
  toggle.textContent = "Show";
  toggle.setAttribute("aria-pressed", "false");
  input.disabled = !configured;
  submit.disabled = !configured;
  toggle.disabled = !configured;
}

function setLoginBusy(busy) {
  const configured = state.sessionInfo?.setup_token_configured !== false;
  const input = $("#setupTokenInput");
  const submit = $("#loginSubmitButton");
  const toggle = $("#toggleTokenButton");
  input.disabled = busy || !configured;
  submit.disabled = busy || !configured;
  toggle.disabled = busy || !configured;
  submit.textContent = busy ? "Signing In" : "Sign In";
  $("#loginForm").setAttribute("aria-busy", busy ? "true" : "false");
}

function userFacingErrorMessage(error) {
  if (error.code === "auth_not_configured") {
    return "Set PANTRYOS_API_TOKEN on the PantryOS server, then restart PantryOS.";
  }
  if (error.code === "unauthorized") {
    return "Sign in again with the PantryOS setup token.";
  }
  if (error.code === "rate_limited" || error.status === 429) {
    return "PantryOS is limiting repeated requests. Wait a moment and try again.";
  }
  if (error.code === "validation_error") {
    return error.message || "Check the highlighted fields and try again.";
  }
  if (error.status === 409 || error.code === "conflict") {
    return "PantryOS has newer data for this record. Refresh and try again.";
  }
  if (error.status >= 500) {
    return "PantryOS hit a server error. The request was not committed.";
  }
  return error.message || "Request failed.";
}

function loginFailureMessage(error) {
  if (error.code === "unauthorized") {
    return "Setup token did not match this PantryOS server.";
  }
  return userFacingErrorMessage(error);
}

function toggleSetupTokenVisibility() {
  const input = $("#setupTokenInput");
  const toggle = $("#toggleTokenButton");
  const visible = input.type === "password";
  input.type = visible ? "text" : "password";
  toggle.textContent = visible ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", visible ? "true" : "false");
  input.focus();
}

function setAuthenticated(authenticated) {
  state.authenticated = authenticated;
  $("#loginPanel").hidden = authenticated;
  $("#appShell").hidden = !authenticated;
  $("#seedButton").hidden = !authenticated;
  $("#resetButton").hidden = !authenticated;
  $("#logoutButton").hidden = !authenticated;
  if (!authenticated && state.sessionInfo) {
    renderLoginSessionInfo();
  }
}

async function bootstrapApp() {
  await refresh();
  if (!state.data.items.length && !state.data.recipes.length) {
    await api("/api/seed", { method: "POST" });
    await refresh();
  }
}

async function loadSession() {
  const session = await api("/api/session");
  state.sessionInfo = session;
  state.csrfToken = session.csrf_token || "";
  setAuthenticated(Boolean(session.authenticated));
  if (state.authenticated) {
    await bootstrapApp();
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = $("#loginError");
  error.textContent = "";
  setLoginBusy(true);
  try {
    const session = await api("/api/session/login", {
      method: "POST",
      body: JSON.stringify({ token: form.elements.token.value }),
    });
    state.sessionInfo = session;
    state.csrfToken = session.csrf_token || "";
    form.reset();
    setAuthenticated(true);
    await bootstrapApp();
  } catch (loginError) {
    error.textContent = loginFailureMessage(loginError);
    error.focus();
  } finally {
    if (!state.authenticated) {
      setLoginBusy(false);
    }
  }
}

async function handleLogout() {
  await api("/api/session/logout", { method: "POST" });
  state.data = null;
  state.csrfToken = "";
  state.activeCookingSession = null;
  state.activeReceipt = null;
  resetRecipeEditor();
  state.purchases = [];
  state.activePurchase = null;
  state.activePriceAnalysis = null;
  state.priceError = "";
  stopBarcodeScanner();
  setAuthenticated(false);
  showToast("Signed out");
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    return;
  }
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch((error) => {
      console.info("PantryOS offline shell unavailable", error);
    });
  });
}

async function main() {
  registerServiceWorker();
  setupBarcodeScanner();
  setAuthenticated(false);
  $("#loginForm").addEventListener("submit", (event) => handleLogin(event).catch(handleActionError));
  $("#toggleTokenButton").addEventListener("click", toggleSetupTokenVisibility);
  $("#logoutButton").addEventListener("click", () => handleLogout().catch(handleActionError));
  $("#itemForm").addEventListener("submit", (event) => handleItemSubmit(event).catch(handleActionError));
  $("#barcodeForm").addEventListener("submit", (event) => handleBarcodeSubmit(event).catch(handleActionError));
  $("#barcodeCameraButton").addEventListener("click", () => startBarcodeScanner().catch(handleActionError));
  $("#barcodeStopButton").addEventListener("click", stopBarcodeScanner);
  $("#recipeForm").addEventListener("submit", (event) => handleRecipeSubmit(event).catch(handleActionError));
  $("#cancelRecipeEditButton").addEventListener("click", resetRecipeEditor);
  $("#purchaseForm").addEventListener("submit", (event) => handlePurchaseSubmit(event).catch(handleActionError));
  $("#receiptForm").addEventListener("submit", (event) => handleReceiptSubmit(event).catch(handleActionError));
  $("#receiptReviewForm").addEventListener("submit", (event) => handleReceiptReviewSubmit(event).catch(handleActionError));
  $("#cookingForm").addEventListener("submit", (event) => handleCookingSubmit(event).catch(handleActionError));
  $("#inventoryFilter").addEventListener("input", (event) => {
    state.filter = event.target.value;
    if (state.data) {
      renderInventory(state.data.items);
    }
  });
  $("#promoteButton").addEventListener("click", async () => {
    await api("/api/shopping/promote-suggestions", { method: "POST" });
    showToast("Suggestions added");
    await refresh();
  });
  $("#seedButton").addEventListener("click", async () => {
    await api("/api/seed", { method: "POST" });
    showToast("Demo data loaded");
    await refresh();
  });
  $("#resetButton").addEventListener("click", async () => {
    state.activeCookingSession = null;
    state.activeReceipt = null;
    resetRecipeEditor();
    state.activePurchase = null;
    state.activePriceAnalysis = null;
    state.priceError = "";
    await api("/api/seed?reset=true", { method: "POST" });
    showToast("Demo data reset");
    await refresh();
  });
  $("#startCookingButton").addEventListener("click", () => handleStartCooking().catch(handleActionError));
  $("#cancelCookingButton").addEventListener("click", () => handleCancelCooking().catch(handleActionError));
  $("#rejectReceiptButton").addEventListener("click", () => handleRejectReceipt().catch(handleActionError));
  document.addEventListener("click", (event) => handlePageClick(event).catch(handleActionError));

  await loadSession();
}

function handleActionError(error) {
  console.error(error);
  showToast(userFacingErrorMessage(error));
}

main().catch(handleActionError);
