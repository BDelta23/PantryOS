# Price anomaly policy

PantryOS records price history only when a purchase line has a product, positive quantity, compatible unit, and total cost. Comparable prices are normalized by unit dimension before comparison: mass uses `oz`, volume uses `fl oz`, count uses `count`, and non-convertible package units keep their own unit code.

For each new price point after the first compatible purchase, PantryOS compares the current normalized unit price against the median of up to the five most recent prior price-history rows for the same product and comparable unit. The median is used instead of an average so one unusual receipt does not move the baseline as aggressively.

The product price endpoint returns the stored price rows plus an `analysis` object. `analysis.latest` reports the current unit price, baseline unit price, anomaly ratio, status, compatible-unit sample count, and a human-readable explanation. Ratios at or above `1.25` are marked `high`, ratios at or below `0.75` are marked `low`, and values between those thresholds are marked `normal`. The first compatible purchase is marked `baseline` because there is no prior evidence window.