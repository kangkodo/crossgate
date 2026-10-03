#!/usr/bin/env python3
"""tests/test_*.py를 찾아 임시 저장소에서 크로스게이트 전체 검사를 실행한다."""
import sys
sys.dont_write_bytecode = True

import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))


def main():
    here = Path(__file__).resolve()
    for path in sorted(here.parent.glob("test_*.py")):
        if path == here:
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in sorted(vars(module)):
            test = getattr(module, name)
            if name.startswith("test_") and callable(test):
                test()
    print("ok: crossgate checks passed")


if __name__ == "__main__":
    main()
