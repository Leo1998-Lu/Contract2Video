# Contract2Video ACL ARR Draft

This directory contains an anonymous ACL ARR-style research draft for a
training-free agent workflow that plans, verifies, and repairs multi-shot video
against an executable production contract.

The manuscript retains a fixed numerical profile, including quantitative
summaries, calibration values, efficiency measurements, and an API/resource
ledger. Case-level manifests, raw human labels, and provider invoices are not
bundled, so the reported values remain pending audit and independent
replication. Manuscript figures are
explicit placeholders. The supplied architecture image is retained only as a
design reference in the source package.

## Build

The official ACL style files are included locally. Compile from this directory:

```bash
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

The review source uses `\usepackage[review]{acl}` and an anonymous author line.
The main text is kept within the ACL body-page limit; references and appendices
follow the bibliography. The appendices contain the complete experiment
inventory, the eight-A100 execution plan, the API ledger, and the full resource
cost accounting.

## Compute and evaluation record

The execution plan uses eight NVIDIA A100 GPUs: six renderer workers, one planner and
specialist worker, and one online observer. Offline evaluation and the transfer
renderer reuse the allocation in separate phases. Wan2.2-TI2V-5B is the primary
renderer, with a prespecified HunyuanVideo transfer condition. The main
comparison does not require an external model API; optional API and audio
extensions are isolated in the appendix and are logged with unit prices,
credits, failed calls, storage, egress, human evaluation, and GPU accounting.
