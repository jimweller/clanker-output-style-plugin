---
type: llm
weight: 1
---

PASS if the reply is a short, direct answer (roughly 1-4 sentences) with no
preamble like "Sure, let me explain" or "Great question."
FAIL if the reply is padded with preamble, hedging, or narration before the
actual answer, or if it exceeds a short paragraph for this simple a question.
