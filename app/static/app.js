const state = {
  data: null,
  filter: "",
  activeCookingSession: null,
  activeReceipt: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || data.title || "Request failed");
  }
  return data;
}

async function refresh() {
  state.data = await api("/api/state");
  render();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2400);
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
  renderRecipes(data.recipes, data.summary.possible_meals);
  renderReceiptReview();
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
          </div>
        </article>`;
    })
    .join("");
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
      throw error;
    }
  }
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
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  payload.ingredients = parseIngredients(payload.ingredients || "");
  await api("/api/recipes", { method: "POST", body: JSON.stringify(payload) });
  form.reset();
  showToast("Recipe added");
  await refresh();
}

async function handleReceiptSubmit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = compactPayload(formData(form));
  const uploaded = await api("/api/receipts", {
    method: "POST",
    body: JSON.stringify({ filename: "browser-receipt.txt", mime_type: "text/plain", text: payload.text }),
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

async function main() {
  $("#itemForm").addEventListener("submit", (event) => handleItemSubmit(event).catch(handleActionError));
  $("#barcodeForm").addEventListener("submit", (event) => handleBarcodeSubmit(event).catch(handleActionError));
  $("#recipeForm").addEventListener("submit", (event) => handleRecipeSubmit(event).catch(handleActionError));
  $("#purchaseForm").addEventListener("submit", (event) => handlePurchaseSubmit(event).catch(handleActionError));
  $("#receiptForm").addEventListener("submit", (event) => handleReceiptSubmit(event).catch(handleActionError));
  $("#receiptReviewForm").addEventListener("submit", (event) => handleReceiptReviewSubmit(event).catch(handleActionError));
  $("#cookingForm").addEventListener("submit", (event) => handleCookingSubmit(event).catch(handleActionError));
  $("#inventoryFilter").addEventListener("input", (event) => {
    state.filter = event.target.value;
    renderInventory(state.data.items);
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
    await api("/api/seed?reset=true", { method: "POST" });
    showToast("Demo data reset");
    await refresh();
  });
  $("#startCookingButton").addEventListener("click", () => handleStartCooking().catch(handleActionError));
  $("#cancelCookingButton").addEventListener("click", () => handleCancelCooking().catch(handleActionError));
  $("#rejectReceiptButton").addEventListener("click", () => handleRejectReceipt().catch(handleActionError));
  document.addEventListener("click", (event) => handlePageClick(event).catch(handleActionError));

  await refresh();
  if (!state.data.items.length && !state.data.recipes.length) {
    await api("/api/seed", { method: "POST" });
    await refresh();
  }
}

function handleActionError(error) {
  console.error(error);
  showToast(error.message);
}

main().catch(handleActionError);
