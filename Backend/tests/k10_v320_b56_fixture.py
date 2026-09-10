"""Export actual B56 API responses from isolated CLI and worker regressions."""
from pathlib import Path
import shutil
import pytest
from tests.test_b56_review_regressions import (
    test_paid_analysis_error_contract, test_paid_morning_review_error_contract,
    test_material_stage_continuation_keeps_its_related_window,
)


def export_b56(root: Path, output: Path):
    output.mkdir(parents=True,exist_ok=True)
    cases=[('analysis_'+role+'_'+str(status),test_paid_analysis_error_contract,
            {'status':status,'retry_succeeds':True,'failed_role':role}) for role in ('pro','con') for status in (402,429)]
    cases += [('morning_'+str(status),test_paid_morning_review_error_contract,{'status':status}) for status in (402,429)]
    cases += [('stage',test_material_stage_continuation_keeps_its_related_window,{})]
    for name,scenario,kwargs in cases:
        path=root/name;path.mkdir(parents=True)
        with pytest.MonkeyPatch.context() as mp:
            scenario(path,mp,**kwargs)
        for file in path.glob('b56_*.json'):
            shutil.copy2(file,output/file.name)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    export_b56(args.root,args.output)
