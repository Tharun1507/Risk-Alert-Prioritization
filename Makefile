PY=PYTHONPATH=src python3
starter:  ; $(PY) -m alertgen.build --preset starter --out data/starter
full:     ; $(PY) -m alertgen.build --scale 1.0 --out data/full --formats csv,json,sql
test:     ; python3 -m pytest -q tests
repro:    ; $(PY) scripts/verify_reproducibility.py
