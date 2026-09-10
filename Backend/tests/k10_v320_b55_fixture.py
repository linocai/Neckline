"""Export B55 real CLI/worker/API scenarios for cross-language and native QA."""
from pathlib import Path
import shutil
import pytest
from tests.test_b55_consistency import (
    test_material_morning_without_recommendation,
    test_frozen_price_and_consecutive_result,
    test_structured_debate_survives_real_worker_and_api,
)


def export_b55(root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    for name, scenario in [('morning', test_material_morning_without_recommendation),
                           ('price', test_frozen_price_and_consecutive_result),
                           ('debate', test_structured_debate_survives_real_worker_and_api)]:
        path = root / name
        path.mkdir(parents=True)
        with pytest.MonkeyPatch.context() as mp:
            scenario(path, mp)
        for file in path.glob('b55_*.json'):
            shutil.copy2(file, output/file.name)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    export_b55(args.root,args.output)
