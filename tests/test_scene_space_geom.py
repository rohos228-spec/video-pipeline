"""CANON §7 property tests 1–8: geometry (random.Random, 200 runs, no hypothesis)."""

from __future__ import annotations

import math
import random

import pytest

from app.services.scene_space.validate import (
    compute_screen_pos_map,
    subject_moved_reestablish,
)

PROP_N = 200


def _load_geom():
    try:
        from app.services.scene_space.errors import DegenerateAxisError
        from app.services.scene_space.geom import (
            apply_deltas,
            axis_side,
            facing_vector,
            normalize_pair,
        )
    except ImportError as exc:
        pytest.fail(
            "geom missing until stream 1 lands: "
            f"{exc}. CONTRACTS require normalize_pair, axis_side, apply_deltas, "
            "DegenerateAxisError; geom.facing_vector for CANON 7.8."
        )
    return apply_deltas, axis_side, facing_vector, normalize_pair, DegenerateAxisError


def _sample_nondeg(rng: random.Random):
    for _ in range(20_000):
        a = (rng.uniform(-6.0, 6.0), rng.uniform(-6.0, 6.0))
        b = (rng.uniform(-6.0, 6.0), rng.uniform(-6.0, 6.0))
        cam = (rng.uniform(-6.0, 6.0), rng.uniform(-6.0, 6.0))
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.8:
            continue
        cross = (b[0] - a[0]) * (cam[1] - a[1]) - (b[1] - a[1]) * (cam[0] - a[0])
        if abs(cross) > 0.25:
            return a, b, cam
    raise RuntimeError("failed to sample a non-degenerate triangle")


def _rigid(x: float, y: float, theta: float, tx: float, ty: float) -> tuple[float, float]:
    c, s = math.cos(theta), math.sin(theta)
    return x * c - y * s + tx, x * s + y * c + ty


def _plan(a, b, cam, lo="c01", hi="c02"):
    return [
        {"id": lo, "x": a[0], "y": a[1], "facing": 0},
        {"id": hi, "x": b[0], "y": b[1], "facing": 180},
        {"id": "cam", "x": cam[0], "y": cam[1], "facing": 0},
    ]


def _by_id(plan):
    return {str(p["id"]): p for p in plan}


def _plans_close(left, right, *, tol: float = 1e-9) -> None:
    a = _by_id(left)
    b = _by_id(right)
    assert set(a) == set(b)
    for kid in a:
        for key in set(a[kid]) | set(b[kid]):
            va, vb = a[kid].get(key), b[kid].get(key)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                assert math.isclose(float(va), float(vb), abs_tol=tol, rel_tol=1e-9), (
                    kid,
                    key,
                    va,
                    vb,
                )
            else:
                assert va == vb


def test_rigid_transform_preserves_side():
    apply_deltas, axis_side, _fv, normalize_pair, _err = _load_geom()
    _ = apply_deltas
    rng = random.Random(1)
    for _i in range(PROP_N):
        a, b, cam = _sample_nondeg(rng)
        lo, hi = normalize_pair("c01", "c02")
        side0 = axis_side(a, b, cam)
        theta = rng.uniform(-math.pi, math.pi)
        tx, ty = rng.uniform(-8.0, 8.0), rng.uniform(-8.0, 8.0)
        a2 = _rigid(*a, theta, tx, ty)
        b2 = _rigid(*b, theta, tx, ty)
        cam2 = _rigid(*cam, theta, tx, ty)
        side1 = axis_side(a2, b2, cam2)
        assert side1 == side0, (side0, side1, a, b, cam, theta)
        _ = lo, hi


def test_mirror_flips_side():
    _ad, axis_side, _fv, _np, _err = _load_geom()
    rng = random.Random(2)
    for _i in range(PROP_N):
        a, b, cam = _sample_nondeg(rng)
        side0 = axis_side(a, b, cam)
        am, bm, cm = (-a[0], a[1]), (-b[0], b[1]), (-cam[0], cam[1])
        side1 = axis_side(am, bm, cm)
        assert side1 != side0
        assert {side0, side1} == {"A", "B"}


def test_scale_preserves_side_and_screen_pos():
    _ad, axis_side, _fv, normalize_pair, _err = _load_geom()
    rng = random.Random(3)
    for _i in range(PROP_N):
        a, b, cam = _sample_nondeg(rng)
        lo, hi = normalize_pair("c01", "c02")
        scale = rng.uniform(0.25, 4.0)
        side0 = axis_side(a, b, cam)
        pos0 = compute_screen_pos_map(_plan(a, b, cam), lo, hi)
        a2 = (a[0] * scale, a[1] * scale)
        b2 = (b[0] * scale, b[1] * scale)
        cam2 = (cam[0] * scale, cam[1] * scale)
        side1 = axis_side(a2, b2, cam2)
        pos1 = compute_screen_pos_map(_plan(a2, b2, cam2), lo, hi)
        assert side1 == side0
        assert pos1 == pos0


def test_cross_axis_flips_screen_pos_and_reestablish():
    apply_deltas, axis_side, _fv, normalize_pair, _err = _load_geom()
    rng = random.Random(4)
    hits = 0
    for _i in range(PROP_N * 3):
        if hits >= PROP_N:
            break
        a, b, cam = _sample_nondeg(rng)
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
            continue
        lo, hi = normalize_pair("c01", "c02")
        plan0 = _plan(a, b, cam)
        pos0 = compute_screen_pos_map(plan0, lo, hi)
        if pos0.get("c01") == pos0.get("c02"):
            continue
        plan1 = [
            {"id": "c01", "x": b[0], "y": b[1], "facing": 0},
            {"id": "c02", "x": a[0], "y": a[1], "facing": 180},
            {"id": "cam", "x": cam[0], "y": cam[1], "facing": 0},
        ]
        pos1 = compute_screen_pos_map(plan1, lo, hi)
        if pos1.get("c01") == pos0.get("c01"):
            continue
        assert pos1.get("c01") == pos0.get("c02")
        assert pos1.get("c02") == pos0.get("c01")
        assert subject_moved_reestablish(plan0, plan1) is True
        axis_side(
            (plan1[0]["x"], plan1[0]["y"]),
            (plan1[1]["x"], plan1[1]["y"]),
            cam,
        )
        hits += 1
        _ = apply_deltas
    assert hits == PROP_N


def test_delta_accumulation_matches_full_replay():
    apply_deltas, _side, _fv, _np, _err = _load_geom()
    rng = random.Random(5)
    for _i in range(PROP_N):
        a, b, cam = _sample_nondeg(rng)
        plan = _plan(a, b, cam)
        n = rng.randint(1, 8)
        deltas = []
        for _j in range(n):
            kid = rng.choice(["c01", "c02", "cam"])
            deltas.append(
                {
                    kid: {
                        "x": rng.uniform(-6.0, 6.0),
                        "y": rng.uniform(-6.0, 6.0),
                        "facing": rng.uniform(0.0, 360.0),
                    }
                }
            )
        if rng.random() < 0.2:
            deltas.append({"turn": {"subject": "c02"}})
        full = apply_deltas(plan, deltas)
        step = [dict(p) for p in plan]
        for delta in deltas:
            step = apply_deltas(step, [delta])
        _plans_close(full, step)


def test_pair_order_normalizes_side():
    _ad, axis_side, _fv, normalize_pair, _err = _load_geom()
    rng = random.Random(6)
    for _i in range(PROP_N):
        a, b, cam = _sample_nondeg(rng)
        p1 = normalize_pair("c02", "c01")
        p2 = normalize_pair("c01", "c02")
        assert p1 == p2 == ("c01", "c02")
        pos = {"c01": a, "c02": b}
        s1 = axis_side(pos[p1[0]], pos[p1[1]], cam)
        s2 = axis_side(pos[p2[0]], pos[p2[1]], cam)
        assert s1 == s2
        flipped = axis_side(b, a, cam)
        assert flipped != axis_side(a, b, cam)


def test_camera_on_axis_raises():
    _ad, axis_side, _fv, _np, DegenerateAxisError = _load_geom()
    rng = random.Random(7)
    with pytest.raises(DegenerateAxisError, match="cross==0"):
        axis_side((0.0, 0.0), (2.0, 0.0), (1.0, 0.0))
    for _i in range(PROP_N):
        ax, ay = rng.uniform(-5.0, 5.0), rng.uniform(-5.0, 5.0)
        span = rng.uniform(1.0, 5.0)
        a = (ax, ay)
        b = (ax + span, ay)
        t = rng.uniform(0.05, 0.95)
        cam = (ax + t * span, ay)
        with pytest.raises(DegenerateAxisError):
            axis_side(a, b, cam)


def test_facing_0_360_90_270():
    _ad, _side, facing_vector, _np, _err = _load_geom()
    rng = random.Random(8)
    x0, y0 = facing_vector(0)
    x360, y360 = facing_vector(360)
    assert math.isclose(x0, 0.0, abs_tol=1e-12)
    assert math.isclose(y0, 1.0, abs_tol=1e-12)
    assert math.isclose(x0, x360, abs_tol=1e-12)
    assert math.isclose(y0, y360, abs_tol=1e-12)
    x90, y90 = facing_vector(90)
    assert math.isclose(x90, 1.0, abs_tol=1e-12)
    assert math.isclose(y90, 0.0, abs_tol=1e-12)
    x270, y270 = facing_vector(270)
    assert math.isclose(x270, -1.0, abs_tol=1e-12)
    assert math.isclose(y270, 0.0, abs_tol=1e-12)
    for _i in range(PROP_N):
        deg = rng.uniform(-720.0, 720.0)
        x, y = facing_vector(deg)
        assert math.isclose(math.hypot(x, y), 1.0, abs_tol=1e-9)
        x2, y2 = facing_vector(deg + 360.0)
        assert math.isclose(x, x2, abs_tol=1e-9)
        assert math.isclose(y, y2, abs_tol=1e-9)
