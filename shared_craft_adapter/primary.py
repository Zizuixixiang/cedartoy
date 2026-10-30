"""Instance-local upstream write adaptation and recoverable primary publication.

No global pathlib monkeypatch, no vendor edits, no local full-image staging.
Upstream continues to validate permissions and update its own business metadata.
"""
import ast
import base64
import copy
import inspect
import io
import json
import os
from pathlib import Path
import textwrap
import types

from .object_storage import digest, file_identity, reference_sha, reference
from .storage import atomic_bytes, validate_json

MIMES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}


def decode_data_url(value):
    if not isinstance(value, str) or len(value) > 1_500_000:
        raise ValueError("reference image must be an image under 1 MB")
    header, sep, encoded = value.partition(",")
    mime = header.removeprefix("data:").removesuffix(";base64")
    if not sep or header != f"data:{mime};base64" or mime not in MIMES:
        raise ValueError("invalid reference image format")
    try:
        raw = base64.b64decode(encoded, validate=True)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            actual_mime = next((name for name, fmt in MIMES.items() if fmt == image.format), None)
            if actual_mime is None or image.width * image.height > 16_777_216:
                raise ValueError("invalid reference image")
            image.verify()
    except Exception:
        raise ValueError("invalid reference image") from None
    # Historical browser URLs may mislabel supported images; preserve their bytes.
    return actual_mime, raw


def ref_record(mime, raw):
    return {"craft_r2": 1, "sha256": digest(raw), "mime": mime}


def ref_bytes(value):
    if (not isinstance(value, dict) or set(value) != {"craft_r2", "sha256", "mime"}
            or value["craft_r2"] != 1 or value["mime"] not in MIMES):
        raise ValueError("invalid reference image record")
    return reference(value["sha256"])


def restore_reference(images, room, board):
    value = board.get("reference_image")
    if isinstance(value, dict):
        raw = images.read(room, "references", board["id"], ref_bytes(value))
        board["reference_image"] = f'data:{value["mime"]};base64,' + base64.b64encode(raw).decode("ascii")
    return board


def journal_path(folder):
    return folder / ".r2-commit.json"


def recover(folder, root):
    """Roll forward only fully verified refs + metadata, under the room lock.

    A durable journal makes the two PNG replacements and JSON one recoverable
    publication. No PNG payloads are staged; pre-migration reference URLs may
    still be part of legacy metadata. Re-running after interruption is safe.
    """
    path = journal_path(folder)
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("symlink R2 commit")
    entry = json.loads(path.read_bytes())
    if entry.get("version") != 1 or entry.get("metadata") not in {"gallery.json", "beads.json"}:
        raise ValueError("invalid R2 commit")
    pending = []
    for name, marker in entry["images"].items():
        target = folder / name
        identity = file_identity(root, target)
        if identity[0] != folder.name or not reference_sha(marker.encode("ascii")):
            raise ValueError("invalid R2 commit image")
        pending.append((target, marker.encode("ascii")))
    # Validate the entire journal before writing any target.
    validate_json(entry["data"], entry["metadata"].split(".")[0])
    metadata = json.dumps(entry["data"], ensure_ascii=False).encode()
    for target, marker in pending:
        atomic_bytes(target, marker)
    atomic_bytes(folder / entry["metadata"], metadata)
    path.unlink()
    # atomic_bytes already syncs the directory; sync journal removal too.
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(store, data, images, pending):
    folder = store.path.parent
    entry = {"version": 1, "metadata": store.path.name, "data": data,
             "images": {str(path.relative_to(folder)): marker.decode("ascii")
                        for path, marker in pending.items()}}
    if not pending:
        atomic_bytes(store.path, json.dumps(data, ensure_ascii=False).encode())
        return
    atomic_bytes(journal_path(folder), json.dumps(entry, ensure_ascii=False).encode())
    recover(folder, images.root)


def adapt_writer(store, name, images, pending):
    """Replace exactly the upstream write_bytes/replace pairs with staged R2 IO.

    Checked AST transformation preserves all upstream validation and semantics;
    changed write patterns fail startup instead of silently retaining full PNGs.
    """
    original = getattr(store, name)
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    writes = {}
    count = 0

    class Stage(ast.NodeTransformer):
        def visit_Expr(self, node):
            nonlocal count
            call = node.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                receiver = call.func.value
                if isinstance(receiver, ast.Name) and call.func.attr == "write_bytes":
                    if len(call.args) != 1 or receiver.id in writes:
                        raise ValueError("upstream image writes changed")
                    writes[receiver.id] = call.args[0]
                    return None
                if isinstance(receiver, ast.Name) and call.func.attr == "replace":
                    if receiver.id not in writes or len(call.args) != 1:
                        raise ValueError("upstream image replacement changed")
                    count += 1
                    return ast.copy_location(ast.Expr(ast.Call(
                        func=ast.Name(id="_craft_stage", ctx=ast.Load()),
                        args=[call.args[0], writes.pop(receiver.id)], keywords=[])), node)
            return self.generic_visit(node)

    tree = Stage().visit(tree)
    if count != 2 or writes:
        raise ValueError("upstream image write pairs changed")
    ast.fix_missing_locations(tree)

    def stage(path, raw):
        identity = file_identity(images.root, path)
        pending[Path(path)] = images.store(*identity, raw)

    namespace = dict(original.__func__.__globals__, _craft_stage=stage)
    exec(compile(tree, inspect.getfile(original), "exec"), namespace)
    adapted = types.MethodType(namespace[name], store)

    def call(*args, **kwargs):
        with store.lock:
            pending.clear()
            try:
                return adapted(*args, **kwargs)
            finally:
                pending.clear()
    setattr(store, name, call)


def install(store, kind, images):
    """Called after protect_store; hydrate only after upstream get authorization."""
    room = store.path.parent.name
    if kind == "beads":
        original_get = store.get
        def get(actor, board_id):
            return restore_reference(images, room, original_get(actor, board_id))
        store.get = get
    if not images.primary(room) or kind not in {"gallery", "beads"}:
        return
    pending = {}
    reference_target = []
    def save(data):
        data = copy.deepcopy(data)
        if kind == "beads":
            old = {b["id"]: b.get("reference_image")
                   for b in json.loads(store.path.read_bytes())["boards"]}
            for board in data["boards"]:
                value = board.get("reference_image")
                if isinstance(value, dict):
                    ref_bytes(value)
                elif value and (value != old.get(board["id"]) or board["id"] in reference_target):
                    mime, raw = decode_data_url(value)
                    images.store(room, "references", board["id"], raw)
                    board["reference_image"] = ref_record(mime, raw)
        publish(store, data, images, pending)
    store._save = save
    adapt_writer(store, "add" if kind == "gallery" else "save_decoration", images, pending)

    if kind == "beads":
        def reference_writer(method):
            def call(actor, board_id, *args, **kwargs):
                with store.lock:
                    reference_target.append(board_id)
                    try:
                        return method(actor, board_id, *args, **kwargs)
                    finally:
                        reference_target.pop()
            return call
        store.set_reference = reference_writer(store.set_reference)
        store.set_work_plan = reference_writer(store.set_work_plan)
