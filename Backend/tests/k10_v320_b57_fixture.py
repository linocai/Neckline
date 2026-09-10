"""Export B57 actual API envelopes from isolated regression producers."""
from pathlib import Path
import shutil
import pytest
from tests.test_b57_review_regressions import (
    test_parent_recovery_preserves_child_terminal_and_retry_budget,
    test_early_scan_failure_is_current_daily_report,
    test_actual_paginated_morning_api_preserves_selection_and_window,
)


def export_b57(root, output):
    output.mkdir(parents=True, exist_ok=True)
    cases = [(f'parent_{status}', test_parent_recovery_preserves_child_terminal_and_retry_budget, {'status': status}) for status in (402, 429)]
    cases += [(kind, test_early_scan_failure_is_current_daily_report, {'kind': kind, 'failure': 'provider'}) for kind in ('morning', 'evening')]
    cases += [('pagination', test_actual_paginated_morning_api_preserves_selection_and_window, {})]
    for name, run, args in cases:
        path = root / name
        path.mkdir(parents=True)
        with pytest.MonkeyPatch.context() as mp:
            run(path, mp, **args)
        for source in path.glob('b57_*.json'):
            shutil.copy2(source, output/source.name)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export_b57(args.root, args.output)
