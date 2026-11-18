# Exercise 4 - Query operators, upserts and bulk writes
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 4 builds a product query API and then updates data safely and in bulk.

| Folder | What it does | Output |
|---|---|---|
| `Activity_4.1` | a `products` collection with schema validation (10+ products); comparison, logical and array operators; pagination with `get_page(page, per_page)` | `products.json` |
| `Activity_4.1_Try_it_yourself` | case-insensitive name search with `$regex`; the user's text is escaped with `re.escape()` so characters such as `.` and `+` match literally | `regex_search_results.json` |
| `Activity_4.2` | `upsert_price(sku, price)` using `update_one(..., upsert=True)`, and one `bulk_write` of 5 `UpdateOne` operations with the `BulkWriteResult` counts checked | `inventory.json` |
| `Activity_4.2_Try_it_yourself` | an idempotent import: upsert on the natural key (`sku`), `$set` for the latest values, `$setOnInsert` for `created_at`, and a unique index, so running it twice creates no duplicates | `catalog.json` |

## Run
```bash
pip install pymongo
python exercises/exercise-04-queries-upserts-bulk/Activity_4.1/activity_4_1_product_query_api.py
python exercises/exercise-04-queries-upserts-bulk/Activity_4.2/activity_4_2_upsert_bulk.py
```
