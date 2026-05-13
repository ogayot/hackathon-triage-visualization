# Issue Triage Dashboard — Design Specification

**Date:** 2026-05-13  
**Status:** Approved  
**Approach:** Async Pipeline with Modular Services

---

## 1. Overview

A unified web dashboard + API service that aggregates issues from multiple bug trackers (GitHub, Jira, GitLab, Linear, Bugzilla, etc.), automatically classifies them by feature/area using LLM semantic analysis, and discovers cross-tracker related bugs via embedding-based similarity search. Supports triage, monitoring, and root-cause investigation workflows.

---

## 2. Architecture

### Components

| Component | Purpose | Technology |
|-----------|---------|------------|
| **Ingestion Workers** | Poll trackers, normalize issues, emit jobs | Async workers (Celery/ARQ/Bull) |
| **Normalized Store** | Canonical issue storage + embeddings | PostgreSQL + pgvector + Redis |
| **Classification & Correlation Engine** | LLM enrichment + similarity clustering | Python service + OpenAI/Anthropic/local LLM |
| **API Service** | RESTful API for dashboard and external tools | FastAPI (Python) |
| **Web Dashboard** | React SPA for triage, clusters, trends | React + Vite + Tailwind |
| **Job Queue** | Decouples ingestion → classification → correlation | Redis / RabbitMQ |

### Data Flow

1. **Ingestion** → Workers poll trackers → normalize → upsert to DB → emit `issue.ingested`
2. **Classification** → LLM engine picks up event → extracts feature category, severity, summary → updates DB
3. **Correlation** → Periodic scan generates embeddings → nearest-neighbor search via pgvector → forms/updates clusters
4. **Dashboard/API** → Reads enriched, clustered data → serves to frontend → user actions write back to DB and optionally to source trackers

---

## 3. Database Schema

### Core Tables

#### `trackers` — Bug Tracker Sources
- `id` (UUID, PK)
- `name` (string, e.g., "GitHub:my-org/my-repo", "Jira:PROJ")
- `type` (enum: github, jira, gitlab, linear, bugzilla, custom)
- `base_url` (API endpoint)
- `config` (JSONB: credentials, project keys, repo slugs, etc.)
- `last_sync_at` (timestamp)
- `sync_interval_minutes` (integer, default 15)
- `is_active` (boolean)
- `created_at`, `updated_at`

#### `issues` — Normalized Canonical Issues
- `id` (UUID, PK)
- `tracker_id` (FK → trackers)
- `external_id` (string, tracker-native issue ID)
- `external_url` (string, direct link to source)
- `title` (text)
- `body` (text)
- `state` (enum: open, closed, in_progress, resolved, duplicate, etc.)
- `severity` (enum: critical, high, medium, low, info)
- `labels` (text[])
- `feature_category` (string, e.g., "Authentication", "Payment")
- `feature_subcategory` (string, optional)
- `assigned_to` (string)
- `reporter` (string)
- `created_at`, `updated_at`, `resolved_at` (timestamps)
- `llm_embedding` (vector, for similarity search)
- `metadata` (JSONB: tracker-specific extras)
- `last_classified_at` (timestamp)
- `cluster_id` (UUID, FK → issue_clusters, nullable)

#### `issue_clusters` — Groups of Related Bugs
- `id` (UUID, PK)
- `name` (string, auto-generated or human-edited)
- `description` (text, LLM-generated summary)
- `primary_issue_id` (UUID, FK → issues)
- `status` (enum: active, resolved, investigating)
- `severity` (enum)
- `created_at`, `updated_at`
- `issue_count` (integer, denormalized)

#### `issue_cluster_memberships` — Many-to-Many
- `cluster_id` (UUID, FK)
- `issue_id` (UUID, FK)
- `similarity_score` (float, 0.0–1.0)
- `relationship_type` (enum: duplicate, related, root_cause, affects)
- `created_at`

#### `feature_categories` — Master Taxonomy
- `id` (UUID, PK)
- `name` (string, unique)
- `description` (text)
- `keywords` (text[], for LLM hinting)
- `created_at`

#### `triage_actions` — Audit Log
- `id` (UUID, PK)
- `issue_id` (UUID, FK)
- `user_id` (string)
- `action` (enum: assigned, reclassified, linked_to_cluster, marked_duplicate, updated_state)
- `previous_value`, `new_value` (JSONB)
- `synced_to_tracker` (boolean)
- `tracker_sync_error` (text, nullable)
- `created_at`

#### `sync_jobs` — Background Job Tracking
- `id` (UUID, PK)
- `tracker_id` (UUID, FK)
- `job_type` (enum: ingestion, classification, correlation, writeback)
- `status` (enum: pending, running, succeeded, failed)
- `started_at`, `completed_at`, `error_message`
- `issues_processed`, `issues_failed` (integer)

### Indexing Strategy
- `issues(tracker_id, external_id)` — unique, prevents duplicates
- `issues(tracker_id, updated_at)` — incremental sync queries
- `issues(llm_embedding)` — pgvector HNSW/IVFFlat for similarity search
- `issues(cluster_id)` — cluster lookups
- `issues(feature_category)` — dashboard filtering
- `issue_cluster_memberships(issue_id)` — reverse cluster lookup
- `triage_actions(issue_id, created_at)` — audit history

---

## 4. API Design

### Endpoints

#### Issues
- `GET /api/v1/issues` — List issues (paginated, filterable by tracker_id, feature_category, state, severity, cluster_id, date_range, assigned_to, text_search)
- `GET /api/v1/issues/{id}` — Issue detail with enrichment
- `PATCH /api/v1/issues/{id}` — Update triage fields (triggers optional write-back)
- `POST /api/v1/issues/{id}/link` — Link to another issue

#### Clusters
- `GET /api/v1/clusters` — List clusters (filterable by status, severity, feature_category, date_range)
- `GET /api/v1/clusters/{id}` — Cluster detail with members and similarity scores
- `POST /api/v1/clusters` — Create manual cluster
- `PATCH /api/v1/clusters/{id}` — Update cluster
- `DELETE /api/v1/clusters/{id}` — Dissolve cluster

#### Trackers
- `GET /api/v1/trackers` — List configured sources
- `POST /api/v1/trackers` — Add new tracker
- `PATCH /api/v1/trackers/{id}` — Update config / pause / resume
- `DELETE /api/v1/trackers/{id}` — Remove tracker
- `POST /api/v1/trackers/{id}/sync` — Trigger manual sync

#### Dashboard / Analytics
- `GET /api/v1/dashboard/summary` — KPIs (total issues, open vs closed, top categories, active clusters, tracker breakdown)
- `GET /api/v1/dashboard/trends` — Time-series data
- `GET /api/v1/dashboard/activity` — Recent triage actions, sync statuses

#### Jobs / Admin
- `GET /api/v1/jobs` — List background jobs
- `GET /api/v1/jobs/{id}` — Job status and logs
- `POST /api/v1/admin/classify-all` — Bulk re-classification
- `POST /api/v1/admin/correlate-all` — Bulk re-correlation

### Response Format
Standard JSON envelope:
```json
{
  "data": { ... },
  "meta": {
    "page": 1,
    "per_page": 50,
    "total": 1240,
    "total_pages": 25
  }
}
```

---

## 5. Component Design

### 5.1 Ingestion Worker
- **Adapter pattern** per tracker type (`GitHubAdapter`, `JiraAdapter`, `GitLabAdapter`, `LinearAdapter`, `BugzillaAdapter`, `CustomAdapter`)
- Common interface: `fetch_issues(since)`, `normalize(raw_issue)`, `write_back(issue_id, action)`
- **Incremental sync:** Fetch only issues updated since `trackers.last_sync_at`
- **Deduplication:** Upsert on `(tracker_id, external_id)`
- **Emits:** `issue.ingested` event to job queue

**Error Handling:**
- Rate limits (HTTP 429): exponential backoff, max 5 retries
- Auth failures: pause tracker after 3 consecutive failures
- Schema drift: log warning, store raw payload in `metadata`, continue

### 5.2 Classification & Correlation Engine

**Classification Pipeline:**
1. Consume `issue.ingested` event
2. Send title + body + labels to LLM
3. LLM prompt requests: `feature_category`, `feature_subcategory`, `severity`, `summary`, `keywords`
4. Write results to `issues` table

**Correlation Pipeline:**
1. Triggered periodically (every 30 min) or on-demand
2. Generate embedding vector for title + body
3. Query nearest neighbors via pgvector (cosine similarity ≥ 0.85)
4. Insert/update `issue_cluster_memberships` with `relationship_type`
5. Auto-create clusters for orphans; flag large-cluster merges for human review

**Cost Management:**
- Batch classification in groups of 10–20
- Cache embeddings for unchanged issues
- Use local embedding model (`all-MiniLM-L6-v2`) for similarity; reserve large LLM for semantic classification only

### 5.3 API Service
- Stateless, horizontally scalable
- Auth: Dashboard users via JWT session cookies (with optional OAuth2 SSO); external integrations via API keys
- Rate limiting: per-user and per-API-key tiers
- Write-back integration: user actions enqueue `write_back` jobs to push changes to source trackers

### 5.4 Web Dashboard

**Key Views:**
- **Issue Stream:** Table/list, filterable by tracker, category, severity, state, cluster. Sortable. Bulk actions.
- **Cluster Explorer:** Visual graph or grouped list of related issues across trackers with similarity scores.
- **Issue Detail:** Full view with source link, LLM classification, cluster membership, audit log, action buttons.
- **Dashboard / Overview:** KPI cards, trend charts (line/bar), recent activity feed.
- **Tracker Management:** Add/remove/edit configs, view sync status, trigger manual sync.
- **Admin / Jobs:** View job queue, re-run failed jobs, trigger bulk classification/correlation.

**Technology:** React + Vite + Tailwind CSS. Charts via Recharts. Tables via TanStack Table.

### 5.5 End-to-End Data Flow
1. Admin adds GitHub repo tracker via Dashboard → API writes `trackers` row
2. Ingestion Worker polls, normalizes, upserts → emits `issue.ingested`
3. Classification Worker consumes → calls LLM → updates `feature_category`, `severity`
4. Correlation Worker scans → generates embeddings → finds similar issues → updates clusters
5. User opens Dashboard → API queries enriched data → frontend renders
6. User marks issues as duplicates → API updates memberships → enqueues write-back → adapters push to source trackers
7. Dashboard Overview refreshes with updated counts and trends

---

## 6. Error Handling & Reliability

### Tracker Failures
- **Rate limiting:** Exponential jitter backoff. `trackers.rate_limited_until` timestamp skips polling.
- **Auth expiry:** Auto-pause after 3 consecutive auth failures. Alert surfaced in Dashboard.
- **Schema drift:** Adapter logs warning, stores raw payload in `issues.metadata`, continues.

### LLM Pipeline Resilience
- **Timeouts:** 60s timeout, 3 retries.
- **Malformed responses:** Fallback to keyword matching against `feature_categories.keywords`. Flag for human review.
- **Quota limits:** Defer jobs, send admin notification. Issues remain visible unclassified.

### Database & Queue
- **Queue durability:** ACK-based job consumption. Worker crashes don’t lose jobs.
- **Connection pooling:** API and workers use pooled connections with reconnect on transient failures.
- **Dead letter queue:** Failed jobs move to DLQ after max retries for inspection and manual replay.

### Dashboard UX
- **Optimistic UI:** Immediate feedback, background sync. "Sync failed" badge with retry option.
- **Real-time updates:** Optional WebSocket or Server-Sent Events for new issues, cluster changes, job completions.

---

## 7. Testing Strategy

### Unit Tests
- **Adapters:** Mock tracker APIs. Test normalization edge cases (empty bodies, Unicode, missing fields).
- **LLM Pipeline:** Mock LLM client. Test prompt formatting, JSON parsing, fallback behavior.
- **API Service:** Test each endpoint with in-memory test DB. Verify filtering, pagination, auth.

### Integration Tests
- **End-to-end:** Docker-based flow (PostgreSQL + Redis). Fake tracker adapter → ingestion → classification → correlation → API → dashboard. Verify cluster formation.
- **Write-back:** Mock tracker API. Trigger triage action via API, assert correct write-back payload.

### Load & Performance
- **Ingestion:** 10,000 issues across 5 trackers. Measure time to cluster formation.
- **Dashboard API:** Load-test issue list with heavy filters and pagination.
- **LLM cost:** Measure token usage per issue, optimize batching.

### Observability
- **Structured logging:** JSON logs with correlation IDs tracing issues through the pipeline.
- **Metrics:** Prometheus/Grafana for queue depth, LLM latency, tracker sync rates, API response times.
- **Health checks:** `/health` and `/ready` endpoints for load balancers.

---

## 8. Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend API | **FastAPI (Python)** | Excellent async support, type safety, auto-generated OpenAPI docs |
| Workers | **Celery + Redis** | Mature Python ecosystem, Redis as broker and result backend (ARQ acceptable alternative) |
| Database | **PostgreSQL 15+ with pgvector** | Robust relational DB with vector extension for embeddings |
| LLM | **OpenAI/Anthropic API** for classification, **local embedding model** for similarity | Cost optimization: small model for embeddings, large model for semantic classification |
| Frontend | **React + Vite + Tailwind CSS** | Modern, fast build, utility-first CSS |
| Charts | **Recharts** | React-native charting |
| Tables | **TanStack Table** | Virtualization + filtering + sorting for large datasets |

---

## 9. Success Criteria

- [ ] Can ingest issues from at least 3 different tracker types (GitHub, Jira, GitLab)
- [ ] LLM classification assigns `feature_category` to >90% of ingested issues
- [ ] Cross-tracker correlation discovers related issues with measurable similarity scores
- [ ] Dashboard displays issue stream, cluster explorer, and trend analytics
- [ ] Triage actions (assign, reclassify, mark duplicate) persist and optionally sync to source trackers
- [ ] Background jobs are observable and retryable on failure

---

## 10. Open Questions / Future Work

- **Custom tracker adapter SDK:** Provide a plugin interface for internal/proprietary bug trackers.
- **Release correlation:** Link issues to software releases/versions to track bug resolution over release cycles.
- **Team-based routing:** Route issues to teams based on `feature_category` ownership.
- **Notification system:** Alerts when new high-severity issues enter a cluster or when a cluster grows significantly.
