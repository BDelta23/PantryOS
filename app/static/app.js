const state = {
  data: null,
  filter: "",
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
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
  $("#expiringCount").textContent = summary.expiring_soon_count;
  $("#shoppingCount").textContent = summary.shopping_list_count;
  $("#mealCount").textContent = data.meals_with_two_or_fewer_missing.length;

  renderUseSoon(summary.expiring_soon);
  renderShopping(data.shopping_list, summary.suggested_purchases);
  renderMeals(data.meals_with_two_or_fewer_missing);
  renderInventory(data.items);
  renderRecipes(data.recipes, data.summary.possible_meals);
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
  const active = items.filter((item) => !item.checked);
  if (!active.length && !suggestions.length) {
    list.innerHTML = `<li class="empty">No shopping items or restock suggestions.</li>`;
    return;
  }
  const shoppingRows = active.map(
    (item) => `
      <li class="list-row">
        <div class="row-title">
          <span>${escapeHtml(item.name)}</span>
          <span class="badge">${escapeHtml(formatQty(item.quantity, item.unit))}</span>
        </div>
        <div class="row-subtitle">${escapeHtml(item.source)}</div>
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
            <div class="row-subtitle">${escapeHtml(item.location)}${item.expires ? ` | expires ${escapeHtml(item.expires)}` : ""}</div>
          </div>
          <div class="item-actions">
            <input aria-label="Consume quantity for ${escapeHtml(item.name)}" value="1" inputmode="decimal" data-consume-qty="${item.id}" />
            <button class="secondary" type="button" data-consume="${item.id}">Consume</button>
            <button class="danger" type="button" data-delete="${item.id}">Delete</button>
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
    await api(`/api/items/${deleteId}`, { method: "DELETE" });
    showToast("Food removed");
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
    showToast("Tonight planned");
    await refresh();
  }
}

async function main() {
  $("#itemForm").addEventListener("submit", handleItemSubmit);
  $("#recipeForm").addEventListener("submit", handleRecipeSubmit);
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
    await api("/api/seed?reset=true", { method: "POST" });
    showToast("Demo data reset");
    await refresh();
  });
  $("#startCookingButton").addEventListener("click", () => showToast("Cooking mode queued"));
  document.addEventListener("click", handlePageClick);

  await refresh();
  if (!state.data.items.length && !state.data.recipes.length) {
    await api("/api/seed", { method: "POST" });
    await refresh();
  }
}

main().catch((error) => {
  console.error(error);
  showToast(error.message);
});
