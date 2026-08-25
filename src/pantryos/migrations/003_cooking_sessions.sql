CREATE TABLE IF NOT EXISTS cooking_sessions (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL,
  meal_plan_entry_id TEXT,
  planned_servings TEXT NOT NULL DEFAULT '1',
  actual_servings TEXT,
  status TEXT NOT NULL DEFAULT 'cooking',
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  completed_at TEXT,
  cancelled_at TEXT,
  allocations_json TEXT NOT NULL DEFAULT '[]',
  ha_correlation_id TEXT,
  notes TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (recipe_id) REFERENCES recipes(id),
  FOREIGN KEY (meal_plan_entry_id) REFERENCES meal_plan_entries(id)
);

CREATE INDEX IF NOT EXISTS idx_cooking_sessions_recipe ON cooking_sessions(recipe_id);
CREATE INDEX IF NOT EXISTS idx_cooking_sessions_status ON cooking_sessions(status);