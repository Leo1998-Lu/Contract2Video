# Proposed track and abstract

## Recommended track

**Primary track: LLM Agents for NLP.** The central research contribution is a language-agent workflow that compiles an underspecified request into a structured production contract, coordinates tool calls, interprets evidence, and performs counterexample-guided repair.

**Secondary track: Multimodality and Language Grounding.** The generated shots and observer signals ground entities, attributes, events, temporal order, spatial relations, identity, audio, and text in media evidence.

## Suggested title

**Contract2Video: Evidence-Grounded Multi-Agent Planning and Local Repair for Faithful Multi-Shot Video Generation**

## Primary-track abstract

Long-form text-to-video systems often produce visually plausible shots while violating requirements about persistent entities, event order, spatial relations, or dialogue. We present Contract2Video, an evidence-grounded agent workflow that treats video generation as language planning under verifiable constraints. A contract compiler converts a request into persistent entities, an ordered shot graph, transition state, and hard or soft constraints. Specialized planning components propose shot instructions, while a calibrated arbitration step checks their consistency before rendering. After each render wave, an observer emits timestamped evidence with clip hashes and confidence; a conservative verifier rejects incomplete or contradictory support. When a hard constraint is violated or remains unknown, a counterexample-guided controller selects the smallest affected subgraph and rerenders only those shots, preserving verified context and enforcing render and cost budgets. The repository defines typed renderer, observer, planner, and evaluation interfaces, together with a deterministic procedural renderer and a fixture-only observer for reproducible control-flow tests. In the supplied fixed-profile export on 300 shared-contract cases, Contract2Video reaches 78.6% hard-constraint satisfaction and 54.0% complete-case success, while reducing mean render calls to 7.9; the paper reports case-bootstrap intervals and separates these values from independent multi-seed uncertainty.

## Secondary-track abstract variant

Text-to-video generation requires grounding language beyond frame-level similarity: the same entity must persist, events must occur in order, and camera, audio, and textual cues must agree across shots. Contract2Video introduces a structured video-production contract and an evidence-grounded repair loop for this setting. The contract compiler represents entities, attributes, actions, temporal and spatial relations, identity, audio, and text as scoped constraints over an ordered shot graph. A renderer produces clips through a replaceable tool interface; an observer converts visual, audio, tracking, or OCR signals into timestamped evidence; and a conservative verifier aggregates evidence without treating unknown observations as success. A local repair policy propagates only the affected shot dependencies and preserves verified media, reducing unnecessary regeneration. We provide reproducible schemas, adapter boundaries, fixture configurations, and an evaluation protocol that measures multimodal grounding at the case level. The implementation records observer mode, failed attempts, and unresolved constraints for auditability; independent multi-seed replication remains future work.
