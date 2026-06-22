"""
s_converter.py
Core conversion engine: MSTS / Open Rails uncompressed .s shape file -> Wavefront .obj (+ .mtl)

Designed to be imported by app.py (the GUI). Can also be run standalone from the
command line for testing:

    python s_converter.py input.s output.obj

Notes on design choices (so future maintenance is easy):
  * Only the FIRST distance_level of the FIRST lod_control is exported (the
    highest-detail static LOD). Animation/hierarchy is "baked" - each part's
    matrix is applied once to its vertices, giving a static rest-pose mesh.
  * Output is streamed directly to disk per indexed_trilist batch (not
    accumulated in one huge in-memory list) so memory use stays roughly
    proportional to the largest single triangle batch, not the whole model.
    This is what lets it handle very large shapes without running out of RAM.
  * Every parsing stage is wrapped so failures raise a ConversionError with a
    clear, specific message instead of an unhandled traceback.
"""

import re
import os
import numpy as np


class ConversionError(Exception):
    """Raised for any recoverable problem with the input file."""
    pass


class Cancelled(Exception):
    """Raised internally when the user cancels a running conversion."""
    pass


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 16:
        raise ConversionError("File is too small to be a valid .s shape file.")
    if raw[:2] == b"\xff\xfe":
        text = raw.decode("utf-16-le", errors="replace")
    elif raw[:2] == b"\xfe\xff":
        text = raw.decode("utf-16-be", errors="replace")
    else:
        # Plain ASCII/UTF-8 uncompressed .s (older MSTS exporters)
        text = raw.decode("utf-8", errors="replace")

    header = text[:40]
    if "SIMISA" not in header:
        raise ConversionError(
            "This doesn't look like an uncompressed MSTS/Open Rails .s file "
            "(missing the 'SIMISA@@...' header). If this is a *compressed* "
            ".s file, it must first be decompressed (e.g. with Open Rails' "
            "own tools) before it can be converted."
        )
    if "JINX0s1t" not in header and "jinx0s1t" not in header.lower():
        # Not fatal - just a heads up that this may be an unusual variant.
        pass
    return text


def _find(text, name, start=0):
    try:
        idx = text.index(name, start)
    except ValueError:
        raise ConversionError(f"Couldn't find expected block '{name.strip()}' in the file. "
                               f"The file may be incomplete, corrupted, or an unsupported "
                               f"shape variant.")
    return idx + len(name)


def _read_count(text, pos):
    m = re.match(r"\s*(\d+)", text[pos:pos + 64])
    if not m:
        raise ConversionError(f"Expected a number at file position {pos} but didn't find one.")
    return int(m.group(1)), pos + m.end()


def convert(s_path, obj_path, progress=None, cancel_event=None):
    """
    progress(stage:str, current:int, total:int) -> called periodically.
    cancel_event: threading.Event - if set(), raises Cancelled and aborts cleanly.
    Returns a stats dict on success.
    """
    def report(stage, cur=0, total=0):
        if progress:
            progress(stage, cur, total)
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()

    report("Reading file")
    text = _read_text(s_path)

    # ---------------- points ----------------
    report("Parsing points")
    p_start = _find(text, "points (")
    n_points, p_start = _read_count(text, p_start)
    pt_re = re.compile(r'point \(\s*([^)]+?)\s*\)')
    pts = []
    it = pt_re.finditer(text, p_start)
    for i, m in zip(range(n_points), it):
        pts.append(m.group(1).split())
        if i % 20000 == 0:
            report("Parsing points", i, n_points)
    if len(pts) != n_points:
        raise ConversionError(f"Expected {n_points} points, only found {len(pts)}.")
    points = np.array(pts, dtype=np.float64)

    # ---------------- uv_points ----------------
    report("Parsing UV points")
    uv_start = _find(text, "uv_points (", p_start)
    n_uv, uv_start = _read_count(text, uv_start)
    uvp_re = re.compile(r'uv_point \(\s*([^)]+?)\s*\)')
    uvs_ = []
    it = uvp_re.finditer(text, uv_start)
    for i, m in zip(range(n_uv), it):
        uvs_.append(m.group(1).split())
        if i % 20000 == 0:
            report("Parsing UV points", i, n_uv)
    if len(uvs_) != n_uv:
        raise ConversionError(f"Expected {n_uv} uv_points, only found {len(uvs_)}.")
    uvs = np.array(uvs_, dtype=np.float64) if n_uv > 0 else np.zeros((0, 2))

    # ---------------- normals ----------------
    report("Parsing normals")
    n_start = _find(text, "normals (", uv_start)
    n_normals, n_start = _read_count(text, n_start)
    vec_re = re.compile(r'vector \(\s*([^)]+?)\s*\)')
    nrm = []
    it = vec_re.finditer(text, n_start)
    for i, m in zip(range(n_normals), it):
        nrm.append(m.group(1).split())
        if i % 20000 == 0:
            report("Parsing normals", i, n_normals)
    if len(nrm) != n_normals:
        raise ConversionError(f"Expected {n_normals} normals, only found {len(nrm)}.")
    normals = np.array(nrm, dtype=np.float64)

    # ---------------- matrices ----------------
    report("Parsing matrices")
    mat_start = _find(text, "matrices (", n_start)
    n_mat, mat_start = _read_count(text, mat_start)
    mat_re = re.compile(r'matrix\s+(\S+)\s*\(\s*([^)]+?)\s*\)')
    mat_names, mat_local = [], []
    it = mat_re.finditer(text, mat_start)
    for i, m in zip(range(n_mat), it):
        mat_names.append(m.group(1))
        vals = [float(x) for x in m.group(2).split()]
        if len(vals) < 12:
            raise ConversionError(f"Matrix '{m.group(1)}' has fewer than 12 values.")
        a = np.eye(4, dtype=np.float64)
        a[0, 0], a[0, 1], a[0, 2] = vals[0], vals[1], vals[2]
        a[1, 0], a[1, 1], a[1, 2] = vals[3], vals[4], vals[5]
        a[2, 0], a[2, 1], a[2, 2] = vals[6], vals[7], vals[8]
        a[0, 3], a[1, 3], a[2, 3] = vals[9], vals[10], vals[11]
        mat_local.append(a)
    if len(mat_local) != n_mat:
        raise ConversionError(f"Expected {n_mat} matrices, only found {len(mat_local)}.")

    # ---------------- images ----------------
    report("Parsing images")
    img_start = _find(text, "images (", mat_start)
    n_img, img_start = _read_count(text, img_start)
    img_re = re.compile(r'image \(\s*([^)]+?)\s*\)')
    images = []
    it = img_re.finditer(text, img_start)
    for i, m in zip(range(n_img), it):
        images.append(m.group(1).strip())

    # ---------------- textures ----------------
    report("Parsing textures")
    tex_start = _find(text, "textures (", img_start)
    n_tex, tex_start = _read_count(text, tex_start)
    tex_re = re.compile(r'texture \(\s*(\d+)[^)]*\)')
    textures = []
    it = tex_re.finditer(text, tex_start)
    for i, m in zip(range(n_tex), it):
        textures.append(int(m.group(1)))

    # ---------------- prim_states ----------------
    report("Parsing materials")
    ps_start = _find(text, "prim_states (", tex_start)
    n_ps, ps_start = _read_count(text, ps_start)
    ps_re = re.compile(
        r'prim_state\s+(\S+)\s*\(\s*\S+\s+\S+\s+tex_idxs\s*\(\s*(\d+)((?:\s+\d+)*)\s*\)'
        r'\s*[-\d.eE]+\s+(\d+)\s+(\d+)\s+(\d+)'
    )
    prim_states = []
    it = ps_re.finditer(text, ps_start)
    for i, m in zip(range(n_ps), it):
        tex_list = m.group(3).split()
        tex_idx = int(tex_list[0]) if tex_list else None
        prim_states.append({
            "name": m.group(1),
            "tex_idx": tex_idx,
            "matrix_idx": int(m.group(4)),
        })
    if len(prim_states) != n_ps:
        raise ConversionError(
            f"Expected {n_ps} prim_states, only matched {len(prim_states)}. "
            f"This shape may use a material format this tool doesn't recognise."
        )

    def image_for_prim_state(ps_idx):
        ps = prim_states[ps_idx]
        if ps["tex_idx"] is None or ps["tex_idx"] >= len(textures):
            return None
        img_idx = textures[ps["tex_idx"]]
        if img_idx >= len(images):
            return None
        return images[img_idx]

    # ---------------- first distance_level ----------------
    report("Locating geometry (LOD0)")
    dl_start = _find(text, "distance_level (", ps_start)
    dlh_start = _find(text, "distance_level_header (", dl_start)
    hier_start = _find(text, "hierarchy (", dlh_start)
    n_hier, hier_start = _read_count(text, hier_start)
    m = re.match(r'([\s\d-]+?)\)', text[hier_start:hier_start + 4000])
    if not m:
        raise ConversionError("Couldn't parse the matrix hierarchy block.")
    hier_vals = [int(x) for x in m.group(1).split()]
    if len(hier_vals) != n_hier:
        raise ConversionError(f"Hierarchy expected {n_hier} entries, found {len(hier_vals)}.")
    if n_hier != n_mat:
        # Not strictly fatal - pad/trim defensively rather than crash.
        if n_hier < n_mat:
            hier_vals += [-1] * (n_mat - n_hier)
        else:
            hier_vals = hier_vals[:n_mat]

    world_cache = {}

    def get_world(i, _depth=0):
        if i in world_cache:
            return world_cache[i]
        if _depth > 64:
            raise ConversionError(f"Matrix hierarchy appears to contain a cycle at index {i}.")
        parent = hier_vals[i]
        if parent == -1 or parent == i or parent < 0 or parent >= n_mat:
            w = mat_local[i]
        else:
            w = get_world(parent, _depth + 1) @ mat_local[i]
        world_cache[i] = w
        return w

    world_mats = [get_world(i) for i in range(n_mat)]

    so_start = _find(text, "sub_objects (", hier_start)
    n_subobj, so_start = _read_count(text, so_start)

    vert_re = re.compile(
        r'vertex \(\s*[0-9A-Fa-f]+\s+(\d+)\s+(\d+)\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s*'
        r'vertex_uvs \(\s*(\d+)\s*((?:\d+\s*)*)\)\s*\)'
    )
    trilist_token_re = re.compile(
        r'prim_state_idx \(\s*(\d+)\s*\)'
        r'|indexed_trilist \(\s*vertex_idxs \(\s*(\d+)\s+((?:\d+\s*)+?)\)'
    )

    def remap(arr_xyz):
        # MSTS (x,y,z) -> common Blender-friendly (x,-z,y), Z-up
        out = np.empty_like(arr_xyz)
        out[:, 0] = arr_xyz[:, 0]
        out[:, 1] = -arr_xyz[:, 2]
        out[:, 2] = arr_xyz[:, 1]
        return out

    obj_dir = os.path.dirname(os.path.abspath(obj_path))
    obj_base = os.path.splitext(os.path.basename(obj_path))[0]
    mtl_path = os.path.join(obj_dir, obj_base + ".mtl")

    v_count = 0
    f_count = 0
    mtl_materials = {}   # image filename (or None) -> material name
    material_order = []

    pos = so_start
    with open(obj_path, "w", encoding="utf-8", newline="\n") as fobj:
        fobj.write(f"# exported by s_converter.py\nmtllib {obj_base}.mtl\n")
        current_material = None

        for so_i in range(n_subobj):
            report("Exporting geometry", so_i, n_subobj)

            vstart = _find(text, "vertices (", pos)
            v_cnt, vpos = _read_count(text, vstart)
            pos = vpos

            pidx = np.empty(v_cnt, dtype=np.int64)
            nidx = np.empty(v_cnt, dtype=np.int64)
            uvidx = np.empty(v_cnt, dtype=np.int64)

            it = vert_re.finditer(text, pos)
            last_end = pos
            k = -1
            for k, m in zip(range(v_cnt), it):
                pidx[k] = int(m.group(1))
                nidx[k] = int(m.group(2))
                uv_list = m.group(4).split()
                uvidx[k] = int(uv_list[0]) if uv_list else 0
                last_end = m.end()
            if k != v_cnt - 1:
                raise ConversionError(
                    f"sub_object {so_i}: expected {v_cnt} vertices, only parsed {k + 1}."
                )
            pos = last_end

            primstart = _find(text, "primitives (", pos)
            prim_cnt, primpos = _read_count(text, primstart)
            pos = primpos

            cur_ps_idx = None
            for _tok in range(prim_cnt):
                m = trilist_token_re.search(text, pos)
                if m is None:
                    raise ConversionError(
                        f"sub_object {so_i}: ran out of data while reading primitives "
                        f"(token {_tok + 1}/{prim_cnt})."
                    )
                if m.group(1) is not None:
                    cur_ps_idx = int(m.group(1))
                    pos = m.end()
                    continue

                if cur_ps_idx is None:
                    raise ConversionError(
                        f"sub_object {so_i}: found triangle data before any material was selected."
                    )

                vidx_count = int(m.group(2))
                vidx_str = m.group(3)
                pos = m.end()
                tri_local = np.fromstring(vidx_str, dtype=np.int64, sep=' ')
                if tri_local.shape[0] != vidx_count:
                    raise ConversionError(
                        f"sub_object {so_i}: triangle list length mismatch "
                        f"({tri_local.shape[0]} vs expected {vidx_count})."
                    )
                if tri_local.shape[0] == 0:
                    continue

                if np.any(tri_local >= v_cnt):
                    raise ConversionError(
                        f"sub_object {so_i}: a triangle references a vertex index out of range."
                    )

                img_file = image_for_prim_state(cur_ps_idx)
                mat_idx = prim_states[cur_ps_idx]["matrix_idx"]
                if mat_idx < 0 or mat_idx >= n_mat:
                    mat_idx = 0
                Wm = world_mats[mat_idx]
                R = Wm[:3, :3]

                p_g = pidx[tri_local]
                n_g = nidx[tri_local]
                uv_g = uvidx[tri_local]

                P = points[p_g]
                Pw = (R @ P.T).T + Wm[:3, 3]
                N = normals[n_g]
                Nw = (R @ N.T).T
                norms = np.linalg.norm(Nw, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                Nw = Nw / norms
                UV = uvs[uv_g] if n_uv > 0 else np.zeros((len(uv_g), 2))

                Pw = remap(Pw)
                Nw = remap(Nw)

                ntri_verts = Pw.shape[0]
                base = v_count

                if img_file not in mtl_materials:
                    if img_file:
                        safe = re.sub(r'[^A-Za-z0-9_]', '_', os.path.splitext(img_file)[0])
                        matname = "mat_" + safe
                    else:
                        matname = f"mat_untextured_{len(mtl_materials)}"
                    mtl_materials[img_file] = matname
                    material_order.append((img_file, matname))
                matname = mtl_materials[img_file]
                if matname != current_material:
                    fobj.write(f"usemtl {matname}\n")
                    current_material = matname

                vbuf = '\n'.join(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in Pw)
                vtbuf = '\n'.join(f"vt {1.0 - u:.6f} {1.0 - v:.6f}" for u, v in UV)
                vnbuf = '\n'.join(f"vn {x:.6f} {y:.6f} {z:.6f}" for x, y, z in Nw)
                fobj.write(vbuf)
                fobj.write('\n')
                fobj.write(vtbuf)
                fobj.write('\n')
                fobj.write(vnbuf)
                fobj.write('\n')

                idxs = np.arange(base + 1, base + ntri_verts + 1)
                faces = idxs.reshape(-1, 3)
                fbuf = '\n'.join(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}" for a, b, c in faces)
                fobj.write(fbuf)
                fobj.write('\n')

                v_count += ntri_verts
                f_count += ntri_verts // 3

    report("Writing material file")
    with open(mtl_path, "w", encoding="utf-8", newline="\n") as fmtl:
        for img_file, matname in material_order:
            fmtl.write(f"newmtl {matname}\n")
            fmtl.write("Ka 1.000 1.000 1.000\n")
            fmtl.write("Kd 1.000 1.000 1.000\n")
            fmtl.write("Ks 0.000 0.000 0.000\n")
            if img_file:
                base = os.path.splitext(img_file)[0]
                fmtl.write(f"map_Kd {base}.dds\n")
            fmtl.write("\n")

    report("Done", n_subobj, n_subobj)
    return {
        "points": n_points,
        "normals": n_normals,
        "uv_points": n_uv,
        "matrices": n_mat,
        "sub_objects": n_subobj,
        "vertices_written": v_count,
        "faces_written": f_count,
        "materials": len(material_order),
        "obj_path": obj_path,
        "mtl_path": mtl_path,
    }


if __name__ == "__main__":
    import sys, time
    if len(sys.argv) != 3:
        print("Usage: python s_converter.py input.s output.obj")
        sys.exit(1)

    t0 = time.time()

    def cb(stage, cur, total):
        if total:
            print(f"\r[{time.time()-t0:6.1f}s] {stage}: {cur}/{total}        ", end="", flush=True)
        else:
            print(f"\r[{time.time()-t0:6.1f}s] {stage}                      ", end="", flush=True)

    try:
        stats = convert(sys.argv[1], sys.argv[2], progress=cb)
        print()
        for k, v in stats.items():
            print(f"  {k}: {v}")
    except ConversionError as e:
        print(f"\nERROR: {e}")
        sys.exit(2)
