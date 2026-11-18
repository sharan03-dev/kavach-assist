# Lab 7.1 - Analyze a Production Schema (embedding vs referencing)
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Lab 7.1 studies the schema of the **Kavach cyber threat intelligence platform** (collections `reports`, `indicators`, `sightings`,
`vulnerabilities`, `threat_actors`, `sources` in the database `cti_platform`) and decides, relationship by relationship, whether
the data should be **embedded** (read together, bounded size) or **referenced** (grows without limit, shared by many documents).

| Part | What it does | File |
|---|---|---|
| **Lab 7.1 tables 1-4** | lists which relationships are embedded and which are referenced, explains why each referenced one is not embedded and what the embedded alternative would look like, measures the average document size with `$bsonSize` and checks that nothing comes near the 16 MB limit. | `lab_01_production_schema.py` |
| **Activity 7.1** | redesigns one referencing relationship (`sightings -> indicators`, `sightings -> sources`) as an embedded `threat_verdicts` collection, builds it with one aggregation (`$lookup`, `$group`, `$reduce`, `$merge`) and benchmarks the two designs on 300 random indicators. | `activity_7_1_embedded_redesign.py`, `ACTIVITY_7_1.md` |
| **Written answers** | the answers to the lab questions. | `ANSWERS.md` |

## Key ideas
- **Embed** when the data is read together and stays small: the CISA KEV entry is embedded 1:1 inside its vulnerability document.
- **Reference** when the data grows without bound or is shared: sightings are a separate collection (1:many), so a document never approaches 16 MB.
- The embedded redesign makes "is this IP an actual threat?" a single-document read, but updates become harder and the data can go stale,
  so the decision is to keep sightings as the source of truth and use `threat_verdicts` as a read-optimised copy rebuilt after each import.

## Run
Start the platform first (it also starts MongoDB replica set rs0):
```bash
docker compose up -d --build lab7
docker compose exec lab7 python labs/lab7_1/lab_01_production_schema.py              # analyse the live database
docker compose exec lab7 python labs/lab7_1/lab_01_production_schema.py --dump       # also create the dump/ folder
docker compose exec lab7 python labs/lab7_1/lab_01_production_schema.py --from-dump  # restore the dump and analyse that copy
docker compose exec lab7 python labs/lab7_1/activity_7_1_embedded_redesign.py        # build + benchmark (use --n 1000 for more)
open http://localhost:8000/#/lab71                                                   # the website's Schema lab 7.1 page
```
The scripts also run directly against any MongoDB with `MONGO_URI` set.
