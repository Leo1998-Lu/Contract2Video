# Contract2Video

**Contract2Video** is an anonymous research implementation for evidence-grounded, multi-agent video planning and local repair. It compiles a natural-language request into a structured video-production contract, renders individual shots through a replaceable renderer, observes the rendered assets, and repairs only the shots implicated by unresolved constraints.


## Repository layout

```text
contract2video/
├── src/contract2video/       # package implementation
├── configs/                  # JSON policies and adapter selections
├── examples/                 # small, human-readable contract fixtures
├── prompts/                  # planner, verifier, and repair prompt templates
├── docs/                     # architecture, reproducibility
├── paper/                    # submission-facing abstract and positioning
├── scripts/                  # repository checks and evaluation utilities
├── tests/                    # unit tests (added by the implementation modules)
├── pyproject.toml
└── README.md
```

## Installation

The core package uses Python's standard library and Pillow for the reference procedural renderer. Install the project in an isolated environment:

```bash
python -m pip install -e .
```

Optional integrations are deliberately isolated. A model-backed planner or renderer may require its own provider package and credentials; those dependencies are not imported at package import time and are not committed to this repository.

## Quick start with the reference components

Validate an example contract:

```bash
python -m contract2video validate --contract examples/two_shot_demo.json
```

Compile a contract from a prompt file:

```bash
python -m contract2video compile \
  --prompt-file examples/demo_prompt.txt \
  --config configs/reference.json \
  --output /tmp/contract.json
```

Run the pipeline with the fixture-only observer:

```bash
python -m contract2video run \
  --contract examples/two_shot_demo.json \
  --config configs/reference.json \
  --output /tmp/contract2video-run
```

The run writes a contract copy, redacted configuration, a manifest, JSONL traces, evidence records, versioned shot assets, and a composed `final.mp4` when every shot is available. Fixture observations are for interface and control-flow validation only; they must not be presented as perceptual measurements or research results.

## Contract and evidence model

A contract has a stable `contract_id`, a prompt, persistent entities, ordered shots, and scoped hard or soft constraints. A shot's `state_in` and `state_out` describe planning state only; they are never evidence that a rendered state was achieved. Every non-unknown observation must include timestamp spans and a SHA-256 hash for each referenced clip. The verifier treats missing, malformed, stale, or conflicting evidence conservatively.

The controller supports `local`, `full`, and `single` repair strategies. Local repair selects violated or unknown constraints and their transitive dependent shots in original order. Full repair rerenders the complete timeline, while single-shot mode limits the repair scope for controlled ablations. Render-call, cost, and round budgets are enforced before each call.

## Validation and tests

The repository uses the Python standard-library `unittest` runner rather than requiring a test framework:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py" -v
```

The reference commands validate the included contract, compile the example prompt, and run the procedural renderer. These checks verify interfaces and control flow. They do not establish perceptual quality or reproduce the paper's pending summary tables. See `docs/REPRODUCTION_STATUS.md` for the external assets required for a full empirical rerun.

## Connecting a real renderer

Implement the `Renderer` protocol and register a factory entry in `src/contract2video/adapters.py`:

```python
class Renderer(Protocol):
    def render(
        self, contract, shot, target, *, seed, attempt, feedback, references
    ) -> RenderedShot: ...
```

The adapter must write the requested clip to `target`, return its measured duration, and preserve failed attempts and metadata. A real renderer can invoke a local diffusion pipeline, a managed service, or another executable through an adapter. Keep credentials in environment variables or a local secret manager; never place them in JSON configuration or logs. Estimate cost before each call and return the actual cost when available.

For a real observer, implement `Observer.observe(contract, assets)` and emit timestamped `Evidence` records. An observer should use independent video, audio, OCR, or tracking signals and declare its source in each record. The included scripted observer is explicitly fixture-only.

## Evaluation

Evaluation consumes independent per-case manifests rather than frame-level rows. Aggregate by prompt or case, report paired bootstrap confidence intervals and fixed-seed randomization tests, and keep human-evaluation pairing keys separate from anonymized labels. Export clip manifests for external video benchmarks instead of fabricating perceptual scores. The paper's supplied fixed-profile values are kept in the submission source under `paper/latex/`; no raw benchmark media or weights are bundled in this repository.

See:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the control loop and interfaces.
- [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) for environment, data, renderer, and reporting requirements.
- [`docs/RESPONSIBLE_NLP.md`](docs/RESPONSIBLE_NLP.md) for limitations and responsible use.

