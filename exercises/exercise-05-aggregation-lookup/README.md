# Exercise 5 - Aggregation framework and $lookup joins
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 5 uses aggregation pipelines to analyse data and to join collections.

| Folder | What it does | Output |
|---|---|---|
| `Activity_5.1` | a sales-analytics pipeline over 20+ orders: monthly revenue (`$group`, `$sort`), the top 3 customers with the products they bought, and the average order value per status | `orders.json` |
| `Activity_5.1_Try_it_yourself` | the `$bucket` stage: orders grouped as small (under $50), medium ($50 to $200) and large (over $200) | `orders_bucket.json` |
| `Activity_5.2` | `products` and `orders` linked by `product_id`; `$lookup` to show date, product name, quantity and total; products that were never ordered (left join + `$match` on an empty array) | printed result |
| `Activity_5.2_Try_it_yourself` | `$lookup` with `let` and `pipeline` to filter the joined documents before they are attached | `lookup_pipeline_results.json` |

Kavach Assist uses the same tools: the threat board is one `$facet` aggregation.

## Run
```bash
pip install pymongo
python exercises/exercise-05-aggregation-lookup/Activity_5.1/activity_5_1_sales_analytics.py
python exercises/exercise-05-aggregation-lookup/Activity_5.2/activity_5_2_lookup_joins.py
```
