CREATE TABLE IF NOT EXISTS purchases (
  id TEXT PRIMARY KEY,
  store TEXT,
  purchased_at TEXT NOT NULL,
  total TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  source TEXT NOT NULL DEFAULT 'api',
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS purchase_lines (
  id TEXT PRIMARY KEY,
  purchase_id TEXT NOT NULL,
  shopping_demand_id TEXT,
  product_id TEXT,
  display_name TEXT NOT NULL,
  quantity TEXT NOT NULL,
  unit TEXT NOT NULL,
  total_cost TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
  FOREIGN KEY (shopping_demand_id) REFERENCES shopping_demands(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_purchase_lines_purchase ON purchase_lines(purchase_id);
CREATE INDEX IF NOT EXISTS idx_purchase_lines_shopping ON purchase_lines(shopping_demand_id);