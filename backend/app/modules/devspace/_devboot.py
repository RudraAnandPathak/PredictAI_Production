"""Entry point of a development job (standard library only). Applies kernel resource limits, then runs ONE target.
Usage: python _devboot.py <mode> <cpu_s> <memory_mb> <kernel_as 1|0> <file_mb> <target...>
  mode script    target = relative path of a .py file, then its arguments
  mode notebook  target = input.ipynb output.ipynb   (code cells run in order in one namespace; outputs are written in nbformat 4 shape)
  mode command   target = -c CODE | -m MODULE [args] | path [args]
The target is never taken from a request without validation by the API; this file only executes what the engine hands it."""
import ast
import io
import json
import os
import runpy
import sys
import traceback


def limit(name, value):
    try:
        import resource
        resource.setrlimit(getattr(resource, name), (value, value))
    except Exception:  # noqa: BLE001 - a platform that refuses a limit is reported by the parent, never assumed here
        pass


def run_notebook(src, dst):
    with open(src, encoding="utf-8") as f:
        nb = json.load(f)
    ns = {"__name__": "__main__"}
    count, failed = 0, False
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        code = cell.get("source", "")
        code = "".join(code) if isinstance(code, list) else code
        cell["outputs"], cell["execution_count"] = [], None
        if failed:
            continue
        count += 1
        cell["execution_count"] = count
        out, err = io.StringIO(), io.StringIO()
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        result = None
        try:
            if any(line.lstrip().startswith(("%", "!")) for line in code.splitlines()):
                raise SyntaxError("IPython magics (% and !) are not supported by the headless runner")
            tree = ast.parse(code, "<cell %d>" % count)
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            exec(compile(tree, "<cell %d>" % count, "exec"), ns)
            if last is not None:
                value = eval(compile(ast.Expression(last.value), "<cell %d>" % count, "eval"), ns)
                result = None if value is None else repr(value)
        except BaseException as exc:  # noqa: BLE001
            failed = True
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            cell["outputs"].append({"output_type": "error", "ename": type(exc).__name__, "evalue": str(exc), "traceback": [l.rstrip("\n") for l in tb]})
        finally:
            sys.stdout, sys.stderr = real_out, real_err
        outs = []
        if out.getvalue():
            outs.append({"output_type": "stream", "name": "stdout", "text": out.getvalue().splitlines(True)})
        if err.getvalue():
            outs.append({"output_type": "stream", "name": "stderr", "text": err.getvalue().splitlines(True)})
        if result is not None:
            outs.append({"output_type": "execute_result", "execution_count": count, "data": {"text/plain": result}, "metadata": {}})
        cell["outputs"] = outs + cell["outputs"]
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    return 1 if failed else 0


def main(argv):
    mode, cpu_s, mem_mb, kernel_as, file_mb = argv[1], int(argv[2]), int(argv[3]), argv[4] == "1", int(argv[5])
    target = argv[6:]
    limit("RLIMIT_CPU", cpu_s)
    if kernel_as:
        limit("RLIMIT_AS", mem_mb * 1024 * 1024)
    limit("RLIMIT_FSIZE", file_mb * 1024 * 1024)
    limit("RLIMIT_NOFILE", 256)
    limit("RLIMIT_CORE", 0)
    os.umask(0o077)
    sys.path.insert(0, os.getcwd())
    if mode == "notebook":
        sys.exit(run_notebook(target[0], target[1]))
    if mode == "script":
        sys.argv = target
        runpy.run_path(target[0], run_name="__main__")
    elif target and target[0] == "-c":
        sys.argv = ["-c"] + target[2:]
        exec(compile(target[1], "<command>", "exec"), {"__name__": "__main__"})
    elif target and target[0] == "-m":
        sys.argv = target[1:]
        runpy.run_module(target[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = target
        runpy.run_path(target[0], run_name="__main__")


if __name__ == "__main__":
    main(sys.argv)
