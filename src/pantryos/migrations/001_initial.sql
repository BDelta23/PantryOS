-- PantryOS Core schema v1

CREATE TABLE IF NOT EXISTS app_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  category TEXT,
  default_unit TEXT NOT NULL,
  minimum_stock_quantity TEXT,
  minimum_stock_unit TEXT,
  preferred_location_id TEXT,
  default_shelf_life_days INTEGER,
  opened_shelf_life_days INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  version INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (preferred_location_id) REFERENCES locations(id)
);

CREATE TABLE IF NOT EXISTS product_aliases (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'manual',
  confidence TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_barcodes (
  id TEXT PRIMARY KEY,
  barcode TEXT NOT NULL UNIQUE,
  format TEXT NOT NULL DEFAULT 'unknown',
  product_id TEXT NOT NULL,
  package_quantity TEXT,
  package_unit TEXT,
  brand TEXT,
  size_text TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  parent_id TEXT,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'other',
  temperature_entity_id TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(parent_id, normalized_name),
  FOREIGN KEY (parent_id) REFERENCES locations(id)
);

CREATE TABLE IF NOT EXISTS inventory_lots (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  quantity TEXT NOT NULL,
  unit TEXT NOT NULL,
  location_id TEXT NOT NULL,
  acquired_at TEXT,
  expires_at TEXT,
  opened_at TEXT,
  lot_type TEXT NOT NULL DEFAULT 'grocery',
  purchase_line_id TEXT,
  cooking_session_id TEXT,
  total_cost TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT,
  source_legacy_id TEXT UNIQUE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  version INTEGER NOT NULL DEFAULT 1,
  CHECK (CAST(quantity AS REAL) >= 0),
  FOREIGN KEY (product_id) REFERENCES products(id),
  FOREIGN KEY (location_id) REFERENCES locations(id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_lots_product_fefo
  ON inventory_lots(product_id, status, expires_at, created_at);

CREATE TABLE IF NOT EXISTS inventory_events (
  id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  product_id TEXT,
  lot_id TEXT,
  quantity TEXT,
  unit TEXT,
  from_location_id TEXT,
  to_location_id TEXT,
  reason TEXT,
  source TEXT,
  occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (product_id) REFERENCES products(id),
  FOREIGN KEY (lot_id) REFERENCES inventory_lots(id),
  FOREIGN KEY (from_location_id) REFERENCES locations(id),
  FOREIGN KEY (to_location_id) REFERENCES locations(id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_events_revision ON inventory_events(revision);

CREATE TABLE IF NOT EXISTS recipes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  yield_servings TEXT NOT NULL DEFAULT '1',
  prep_minutes INTEGER,
  cook_minutes INTEGER,
  instructions TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL,
  product_id TEXT,
  display_text TEXT NOT NULL,
  quantity TEXT NOT NULL,
  unit TEXT NOT NULL,
  optional INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS meal_plan_entries (
  id TEXT PRIMARY KEY,
  plan_date TEXT NOT NULL,
  meal_type TEXT NOT NULL,
  recipe_id TEXT NOT NULL,
  servings TEXT NOT NULL DEFAULT '1',
  status TEXT NOT NULL DEFAULT 'planned',
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(plan_date, meal_type),
  FOREIGN KEY (recipe_id) REFERENCES recipes(id)
);

CREATE TABLE IF NOT EXISTS shopping_demands (
  id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL UNIQUE,
  product_id TEXT,
  display_name TEXT NOT NULL,
  quantity TEXT NOT NULL,
  unit TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  accepted INTEGER NOT NULL DEFAULT 0,
  checked INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  store TEXT,
  recalculated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS legacy_imports (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL UNIQUE,
  source_path TEXT NOT NULL,
  imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  backup_path TEXT NOT NULL,
  item_count INTEGER NOT NULL,
  recipe_count INTEGER NOT NULL,
  shopping_count INTEGER NOT NULL,
  meal_plan_count INTEGER NOT NULL
);
