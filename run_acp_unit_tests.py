import sys, tempfile, pathlib, importlib.util
n = 0
for f in ("tests/test_cdb_tools.py", "tests/test_wb_scripts.py"):
    spec = importlib.util.spec_from_file_location("t", f); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    for name in dir(m):
        if name.startswith("test_"):
            fn = getattr(m, name)
            with tempfile.TemporaryDirectory() as d:
                try:
                    fn(pathlib.Path(d)) if fn.__code__.co_argcount else fn(); print("PASS", name); n += 1
                except Exception as e:
                    print("FAIL", name, type(e).__name__, e)
print(n, "passed")
