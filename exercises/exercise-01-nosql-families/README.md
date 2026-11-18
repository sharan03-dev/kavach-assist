# Exercise 1 - NoSQL database families
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 1 introduces the four NoSQL families (document, key-value, wide-column, graph) and how to choose one from the way an application reads and writes data.

| Folder | What it does | Output |
|---|---|---|
| `Activity_1.1` | classifies Amazon S3, Couchbase, Apache HBase, Amazon Neptune and Memcached into a NoSQL family with a one-sentence reason, a best use case and a limitation each | `classification.json` |
| `Activity_1.1_Try_it_yourself` | researches ScyllaDB, InfluxDB and ArangoDB and asks whether each fits one family or spans several | `research_classification.json` |
| `Activity_1.2` | maps two applications (ride-sharing and social-media analytics) to four data domains each and picks a family per domain (polyglot persistence) | `application_mapping.json` |
| `Activity_1.2_Try_it_yourself` | applies the same method to a food-delivery app and checks each choice with a simple scoring rule | `food_delivery_mapping.json` |

Results are stored in MongoDB and exported to the JSON files.

## Run
```bash
pip install pymongo
python exercises/exercise-01-nosql-families/Activity_1.1/activity_1_1_classify_databases.py
python exercises/exercise-01-nosql-families/Activity_1.2/activity_1_2_map_application.py
```
A local MongoDB is expected at `mongodb://localhost:27017` (set `MONGO_URI` to use another one).
