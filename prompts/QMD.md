Set up QMD as the memory backend for my OpenClaw agent.
Follow the official docs here:
https://docs.openclaw.ai/concepts/memory#qmd-backend-experimental

Make sure to:
1. Install the QMD CLI
2. Install SQLite with extension support if needed
   (macOS: brew install sqlite)
3. Configure memory.backend = "qmd" in my openclaw.json
4. Add my workspace memory files as a QMD collection
5. Run the initial embed so models are downloaded and
   the index is built
6. Verify it works by running a test query