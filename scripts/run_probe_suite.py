"""Run the synchronous probe suite from the command line."""

from __future__ import annotations

import argparse

from app.api.routes_evals import run_evaluation
from app.api.schemas import EvaluationRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-version", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    print(run_evaluation(EvaluationRequest(policy_version=args.policy_version, runs=args.runs)))


if __name__ == "__main__":
    main()
