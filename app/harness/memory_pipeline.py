"""Memory pipeline (TDD §12.1-§12.4).

After commit, derives semantic memories from consequential events (templates,
optional summarizer), embeds them (float32 BinData), writes `memories`, and
updates events.memory_status. Background sweep retries PENDING/FAILED.
Never writes world state.
Scaffold only.
"""
