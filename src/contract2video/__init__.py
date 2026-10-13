"""Contract2Video: verifiable planning and local repair for video workflows."""
__version__ = "0.1.0"

from .calibration import calibrate_thresholds
from .pipeline import compile_contract, run_pipeline
from .repair import affected_shots
from .schema import contract_errors, normalize_contract, validate_contract
from .verification import aggregate_evidence, validate_evidence

__all__ = [
    "aggregate_evidence", "affected_shots", "calibrate_thresholds", "compile_contract",
    "contract_errors", "normalize_contract", "run_pipeline", "validate_contract", "validate_evidence",
]
