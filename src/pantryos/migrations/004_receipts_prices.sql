CREATE TABLE IF NOT EXISTS receipt_uploads (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded',
  store TEXT,
  purchased_at TEXT,
  total TEXT,
  currency TEXT NOT NULL DEFAULT 'USD',
  extracted_json TEXT NOT NULL DEFAULT '{}',
  review_json TEXT NOT NULL DEFAULT '{}',
  committed_purchase_id TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (committed_purchase_id) REFERENCES purchases(id)
);

CREATE INDEX IF NOT EXISTS idx_receipt_uploads_status ON receipt_uploads(status);

CREATE TABLE IF NOT EXISTS price_history (
  id TEXT PRIMARY KEY,
  purchase_line_id TEXT NOT NULL UNIQUE,
  product_id TEXT NOT NULL,
  store TEXT,
  purchased_at TEXT NOT NULL,
  quantity TEXT NOT NULL,
  unit TEXT NOT NULL,
  total_cost TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  comparable_quantity TEXT NOT NULL,
  comparable_unit TEXT NOT NULL,
  unit_price TEXT NOT NULL,
  baseline_unit_price TEXT,
  anomaly_ratio TEXT,
  explanation TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (purchase_line_id) REFERENCES purchase_lines(id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, purchased_at);