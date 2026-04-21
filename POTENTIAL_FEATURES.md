# Hermod Potential Features (The "Intelligent" Roadmap)

These features leverage Hermod's local AI integration to provide a superior, privacy-first email experience.

### 1. Privacy-First Thread Summarization
- **Goal:** Concisely summarize long email threads.
- **Implementation:** Send thread content to a local LLM (via LM Studio) to generate a TL;DR, pending actions, and status updates.
- **Privacy:** Data never leaves the local machine.

### 2. Semantic Intent & Action Detection
- **Task Extraction:** Detect phrases like "I'll send the report by Tuesday" and suggest calendar events or TODO items.
- **Priority Ranking:** Use local ML to bubble up emails requiring "Deep Work" or urgent replies while deprioritizing newsletters and notifications.

### 3. Context-Aware Drafting (Smart Reply)
- **Drafting:** Generate reply options (e.g., "Accept," "Decline," "Schedule") based on the current thread and historical interaction style with the sender.

### 4. Semantic Neural Search
- **Natural Language Queries:** Search for "the server invoice from last month" using vector embeddings rather than just keyword matching.
- **Contextual Retrieval:** Find related documents and previous conversations semantically linked to the current email.

### 5. Intelligent Data Extraction
- **Automatic Pinning:** Extract tracking numbers, flight details, and meeting links, pinning them to the top of the message view for instant access.

### 6. Zero-Cloud Privacy Architecture
- **Local Processing:** All "Intelligence" runs on the user's hardware, ensuring sensitive communications are never processed by third-party AI providers.

---

## Polish backlog (return-to items)

### Reader: Clean / Original view heuristic
- **Shipped:** default-clean reader with per-view toggle, per-sender
  "always original" pin, and a shape heuristic that routes
  design-heavy newsletters and structured receipts to original
  automatically (`window_reader.py::_heuristic_prefers_original`).
- **To polish later:**
  - Improve the clean extractor so `(https://…)` inline URL clutter
    gets trimmed — receipts could then stay in clean mode instead of
    bouncing to original.
  - Learn-from-feedback: record when the user manually flips
    clean→original for a given domain and silently promote that
    domain toward original next time.
  - Settings "Reading" panel listing every sender pinned to original
    view, with a one-click reset.
  - Consider an "always prefer clean from <sender>" option (overrides
    the heuristic for senders where clean reads fine but the shape
    heuristic keeps picking original).
  - Tune heuristic thresholds (`img_count`, extraction-ratio,
    URL-density) against a small labelled corpus from a real inbox.
  - Thread view already renders clean by design; if we ever want an
    "original HTML" peek for a single bubble inside the thread, that
    would be a per-bubble toggle on the bubble header.
