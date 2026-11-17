# Exercise 17 - AI-Native MongoDB: Vector Search and RAG Foundations
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528

Vector search runs on **MongoDB Atlas Local** (`mongodb/mongodb-atlas-local:8.0` = mongod + mongot, the Atlas Search engine) - `$vectorSearch` on your laptop, no cloud account.
Embeddings: `sentence-transformers/all-MiniLM-L6-v2` (384 d, ONNX via fastembed, baked into the image); `VOYAGE_API_KEY` switches to Voyage (`voyage-3`, the course sample).
Answers: `ANTHROPIC_API_KEY` (Claude) or `OLLAMA_URL` (a local model); without them a built-in extractive generator answers from the retrieved chunks and cites them.

| Part | What runs |
|---|---|
| **17.1 RAG pipeline** | 5 threat-intelligence documents (`docs/`) -> 100-word chunks (20-word overlap) -> embeddings -> `rag_demo.documents {text, source, embedding}` + vectorSearch index -> `$vectorSearch` numCandidates 20, limit 3 -> prompt -> generator; retrieval evaluated on 8 questions |
| **Activity: memory** | `rag_demo.conversations {user_id, timestamp, role, content}`; the last 5 exchanges go into the prompt, follow-up questions are rewritten with the previous one; chat on the website |
| **17.2 HNSW vs IVF** | 5,000 x 384-d vectors (sentences of the Kavach reports + generated CTI sentences), inserted in batches of 500; HNSW (Atlas, hnswOptions maxEdges / numEdgeCandidates, numCandidates sweep), IVF built inside MongoDB (k-means nlist 50/100, nprobe 1-20), exact search as ground truth; 100 queries per point: build time, latency, recall@20, latency-recall curve, recommendation |
| **17 on 7** | *Ask Kavach*: the Lab 7 threat reports embedded and indexed; questions answered with citations |

```bash
docker compose up -d lab17          # Lab 7 -> ... -> 16 -> 17 (+ atlas-local)
open http://localhost:8000/#/lab17
docker compose exec lab17 lab17 all # everything + results/Exercise-17_Report.pdf
```
