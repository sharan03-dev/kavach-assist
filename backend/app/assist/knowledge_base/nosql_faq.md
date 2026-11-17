# NoSQL and MongoDB questions and answers
## What is NoSQL and when should I use it instead of SQL
NoSQL databases store data in models other than fixed tables: documents (MongoDB), wide columns (Cassandra), graphs (Neo4j) and key-value (Redis). Choose NoSQL when the data shape changes often, when you must scale horizontally over many servers, or when the access pattern fits the model (a whole document read at once, a time series, a relationship graph). Choose SQL when you need many ad-hoc joins over strictly structured data.
## What is the CAP theorem
In a network partition a distributed database must choose between Consistency (every read sees the latest write) and Availability (every request gets an answer). MongoDB with majority writes is CP: the minority side stops accepting writes. Cassandra is AP by default with tunable consistency: it keeps answering and reconciles later.
## What is the difference between embedding and referencing in MongoDB
Embedding puts related data inside one document: one read, atomic update, good for 1:1 and small 1:few data that is read together. Referencing stores the related data in another collection with an id: needed for 1:many data that grows without bound or is shared, because a document cannot exceed 16 MB. Rule of thumb: data that is accessed together should be stored together.
## What is an index and what do explain and covered query mean
An index is a sorted structure (B-tree) that lets MongoDB find documents without scanning the whole collection. explain("executionStats") shows IXSCAN (index used) or COLLSCAN (full scan) and totalDocsExamined; ideally docs examined equals docs returned. A compound index follows the ESR rule: Equality fields first, then Sort, then Range. A covered query is answered from the index alone. A TTL index deletes documents automatically after a time.
## What is the aggregation pipeline
A sequence of stages that transform documents: $match filters, $group aggregates, $project reshapes, $sort orders, $lookup joins another collection, $unwind flattens arrays, $facet runs several sub-pipelines at once. Put $match first so indexes can be used.
## What is a replica set, an election and the oplog
A replica set is a group of mongod processes with the same data. The primary records every write in the oplog (a capped collection); secondaries replay it. If the primary is unreachable for the election timeout (10 seconds by default) a secondary with the newest data wins an election by majority vote. An odd number of voting members avoids ties.
## What are write concern and read concern
Write concern says how many members must acknowledge a write (w:1, w:majority) and whether it must be journaled. Read concern says how durable the data you read must be (local, majority, snapshot). w:majority with read concern majority never shows data that can be rolled back.
## What is sharding and how do I pick a shard key
Sharding splits a collection across shards by a shard key; mongos routes queries. A good key has high cardinality, low frequency, is not monotonic and appears in most queries so they are targeted to one shard. A hashed key gives even writes; a ranged key gives efficient range queries. A bad key creates a hot shard and jumbo chunks.
## What is a transaction in MongoDB and what does ACID mean
A multi-document transaction groups several writes so they are Atomic (all or nothing), Consistent, Isolated (snapshot isolation) and Durable. They need a replica set. Single-document updates are already atomic, so a good schema often avoids the need for transactions.
## What are embeddings, vector search, HNSW and RAG
An embedding is a list of numbers that represents the meaning of a text; similar meanings give nearby vectors. Vector search finds the nearest vectors by cosine similarity. HNSW is a layered graph index that finds approximate nearest neighbours quickly; numCandidates trades speed for recall. RAG (retrieval-augmented generation) retrieves relevant chunks and gives them to a language model as context so the answer is grounded in your data and can cite sources, which reduces hallucination.
## What is the difference between an LLM and an SLM
A large language model (LLM) has many billions of parameters and usually runs in the cloud behind an API. A small language model (SLM) such as Llama 3.2 3B or Phi-3 has a few billion parameters and runs on a laptop with Ollama: private, offline and free per query, but less capable. With good retrieved context (RAG) a small model answers domain questions well.
## What is polyglot persistence
Using several databases in one system, each for what it does best: MongoDB for documents and the application, Cassandra for huge write-heavy time series, Neo4j for relationships, a vector index for semantic search. The cost is operating and keeping several systems consistent.
## What is a circuit breaker
A wrapper around calls to a dependency. After a number of failures it opens and fails fast instead of waiting on timeouts; after a cool-down it lets one trial call through (half-open) and closes again on success. It stops one slow database from making the whole application slow.
