# Exercise 6 - Indexing and query performance
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 6 measures what indexes do to read speed and write speed, using `explain()` and timing.

| Folder | What it does | Output |
|---|---|---|
| `Activity_6.1` | 50,000 generated products and five representative queries: `explain()` before any index (stage and `totalDocsExamined`), then indexes designed for all five queries and `explain()` again | `index_design.json` |
| `Activity_6.1_Try_it_yourself` | the `$indexStats` stage after running the five queries a different number of times, to see which indexes are used most (run Lab 6.1 first) | `index_stats.json` |
| `Activity_6.2` | times the insert of 10,000 documents with no indexes, again with 5 indexes (percentage slowdown), then drops all but the most critical index and compares the queries; a short analysis of the read vs write trade-off | `index_strategies.json` |
| `Activity_6.2_Try_it_yourself` | a covered query: the filter and the returned fields are all inside the index `{category: 1, price: 1}` with `_id` excluded, so there is no FETCH stage and `totalDocsExamined` is 0 | `covered_query.json` |

Every index speeds up reads but slows writes and uses memory, so index only what the queries need.

## Run
```bash
pip install pymongo
python exercises/exercise-06-indexing/Activity_6.1/activity_6_1_index_design.py
python exercises/exercise-06-indexing/Activity_6.2/activity_6_2_index_strategies.py
```
