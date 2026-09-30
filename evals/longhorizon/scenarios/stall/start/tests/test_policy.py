"""The service's engineering policy, checked in CI. Don't weaken these: fix the code instead."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "invoicing"


def trees():
    return [(str(f.relative_to(ROOT)), ast.parse(f.read_text())) for f in sorted(PACKAGE.rglob("*.py"))]


def public_functions():
    return [(name, node) for name, tree in trees() for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")]


def test_every_public_function_is_in_the_changelog():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    missing = sorted({node.name for _, node in public_functions() if f"`{node.name}`" not in changelog})
    assert not missing, f"add these to CHANGELOG.md: {missing}"


def test_public_functions_have_type_hints():
    missing = []
    for name, node in public_functions():
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        args += [a for a in (node.args.vararg, node.args.kwarg) if a]
        if node.returns is None or any(a.annotation is None for a in args if a.arg not in ("self", "cls")):
            missing.append(f"{name}: {node.name}")
    assert not missing, f"annotate every parameter and the return type: {missing}"


def test_library_code_never_prints():
    bad = [f"{name}:{n.lineno}" for name, tree in trees() for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
    assert not bad, bad


def test_library_is_silent(capsys):
    """The package runs inside other services: nothing it does may write to stdout or stderr, by any means."""
    import invoicing

    customer = invoicing.add_customer("Asha Rao", "asha@example.com")["id"]
    product = invoicing.add_product("Plan", 1000)["id"]
    invoice = invoicing.create_invoice(customer, [(product, 2)])["id"]
    invoicing.get_invoice(invoice)
    invoicing.list_invoices()
    if hasattr(invoicing, "issue_invoice"):
        invoicing.issue_invoice(invoice)
    if hasattr(invoicing, "record_payment"):
        invoicing.record_payment(invoice, 1, "card")
    out, err = capsys.readouterr()
    assert (out, err) == ("", ""), "the library wrote output"


def test_no_local_time():
    """Use invoicing.now(): now() without a timezone, utcnow(), today() and fromtimestamp() use local or naive time."""
    bad = []
    for name, tree in trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if n.func.attr in ("utcnow", "today", "fromtimestamp", "utcfromtimestamp") or (
                        n.func.attr == "now" and not n.args and not n.keywords
                        and not (isinstance(n.func.value, ast.Name) and n.func.value.id == "clock")):
                    bad.append(f"{name}:{n.lineno}: {n.func.attr}()")
    assert not bad, bad


def error_classes():
    tree = ast.parse((PACKAGE / "errors.py").read_text())
    codes = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            codes[node.name] = next((s.value.value for s in node.body if isinstance(s, ast.Assign)
                                     and any(isinstance(t, ast.Name) and t.id == "code" for t in s.targets)
                                     and isinstance(s.value, ast.Constant)), None)
    return codes


def test_every_error_carries_an_error_code():
    """Raise only the classes in invoicing/errors.py, and give each its own `code`: clients match on it."""
    codes = error_classes()
    missing = [name for name, code in codes.items() if not code]
    taken = [code for code in codes.values() if code]
    duplicated = sorted({code for code in taken if taken.count(code) > 1})
    bad = []
    for name, tree in trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Raise) and n.exc is not None:
                exc = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
                ident = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", None)
                if ident not in codes:
                    bad.append(f"{name}:{n.lineno}: raise {ident}")
    assert not (missing or duplicated or bad), {"no code": missing, "shared codes": duplicated, "not ours": bad}
