# -*- coding: utf-8 -*-
"""PermitAgent - read-only diagnosis: why does Revit not create a Room around a picked point?

Looks at every room-bounding candidate (walls, room separation lines) around the point and reports:
  * free ends       - a wall/line end that touches no other bounding element (a gap)
  * bounding off    - walls with "Room Bounding" unchecked
  * height          - walls that do not cross the level's room computation height
  * phase / option  - walls not existing in the view's phase, or in a secondary design option
  * escaping rays   - directions in which a 2D ray from the point meets no bounding element
Nothing is modified; the suspects are only selected so they can be seen.
"""
import datetime
import io
import json
import math
import os

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, ElementId, XYZ,
    Wall, CurveElement, ElementOnPhaseStatus, Outline, BoundingBoxIntersectsFilter,
)

FT_TO_CM = 30.48
SEARCH_RADIUS = 50.0      # ft (~15m) around the picked point
JOIN_TOL = 0.1            # ft (~3cm) extra tolerance when testing if an end touches another element
RAY_COUNT = 72
RAY_LENGTH = 50.0         # ft


def _id(eid):
    v = getattr(eid, 'Value', None)
    return int(v if v is not None else eid.IntegerValue)


def _cm(v):
    return round(v * FT_TO_CM)


def _name(doc, el):
    try:
        t = doc.GetElement(el.GetTypeId())
        p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM) if t else None
        nm = p.AsString() if p else None
        return nm or (t.Name if t else u'')
    except Exception:
        return u''


def _segments(curve):
    pts = list(curve.Tessellate())
    return [((pts[i].X, pts[i].Y), (pts[i + 1].X, pts[i + 1].Y)) for i in range(len(pts) - 1)]


def _dist_point_seg(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = float(dx * dx + dy * dy)  # float: avoid Python 2 integer division
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _ray_hit(o, d, a, b):
    """distance along ray o+t*d to segment ab, or None"""
    ox, oy = o
    dx, dy = d
    ax, ay = a
    bx, by = b
    ex, ey = bx - ax, by - ay
    den = float(dx * ey - dy * ex)
    if abs(den) < 1e-12:
        return None
    t = ((ax - ox) * ey - (ay - oy) * ex) / den
    u = ((ax - ox) * dy - (ay - oy) * dx) / den
    if t > 0 and 0 <= u <= 1:
        return t
    return None


def diagnose(doc, view, pick, progress=None):
    def step(pct, msg):
        if progress:
            progress(pct, msg)

    level = view.GenLevel
    comp_p = level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)
    comp_h = comp_p.AsDouble() if comp_p else 0.0
    z_comp = level.ProjectElevation + comp_h
    phase_p = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
    phase_id = phase_p.AsElementId() if phase_p else ElementId.InvalidElementId
    px, py = pick.X, pick.Y

    def near(bb):
        return (bb is not None and bb.Min.X - SEARCH_RADIUS <= px <= bb.Max.X + SEARCH_RADIUS and
                bb.Min.Y - SEARCH_RADIUS <= py <= bb.Max.Y + SEARCH_RADIUS)

    suspects = []   # (element id, reason, distance ft)
    bounding = []   # (el, segments, half width)

    # only elements whose box touches the search area of this storey (native, fast filter)
    zone = BoundingBoxIntersectsFilter(Outline(
        XYZ(px - SEARCH_RADIUS, py - SEARCH_RADIUS, level.ProjectElevation - 1.0),
        XYZ(px + SEARCH_RADIUS, py + SEARCH_RADIUS, level.ProjectElevation + 10.0)))

    step(5, u'אוסף קירות סביב הנקודה...')
    for w in FilteredElementCollector(doc).OfClass(Wall).WherePasses(zone):
        bb = w.get_BoundingBox(None)
        if not near(bb):
            continue
        if not (bb.Min.Z - 0.5 <= level.ProjectElevation + 10.0 and bb.Max.Z >= level.ProjectElevation - 0.5):
            continue  # a wall of another storey
        lc = getattr(w.Location, 'Curve', None)
        if lc is None:
            continue
        segs = _segments(lc)
        dist = min(_dist_point_seg((px, py), a, b) for a, b in segs)
        label = u'%s (%s)' % (_name(doc, w), u'קיר מסך' if w.CurtainGrid is not None else u'קיר')
        ok = True
        rb = w.get_Parameter(BuiltInParameter.WALL_ATTR_ROOM_BOUNDING)
        if rb is not None and rb.AsInteger() == 0:
            suspects.append((w.Id, u'%s: Room Bounding כבוי' % label, dist))
            ok = False
        if not (bb.Min.Z <= z_comp <= bb.Max.Z):
            suspects.append((w.Id, u'%s: לא חוצה את גובה חישוב החדר (%d ס"מ מעל המפלס). הקיר בין %d ל-%d ס"מ' % (
                label, _cm(comp_h), _cm(bb.Min.Z - level.ProjectElevation), _cm(bb.Max.Z - level.ProjectElevation)), dist))
            ok = False
        if phase_id != ElementId.InvalidElementId:
            try:
                st = w.GetPhaseStatus(phase_id)
                if st in (ElementOnPhaseStatus.Future, ElementOnPhaseStatus.Demolished, ElementOnPhaseStatus.Past):
                    suspects.append((w.Id, u'%s: לא קיים בשלב של התצוגה (%s)' % (label, st), dist))
                    ok = False
            except Exception:
                pass
        if w.DesignOption is not None and not w.DesignOption.IsPrimary:
            suspects.append((w.Id, u'%s: באופציית תכנון משנית (%s)' % (label, w.DesignOption.Name), dist))
            ok = False
        if ok:
            bounding.append((w, segs, w.Width / 2.0, label))

    step(25, u'קווי הפרדה ועמודים...')
    for ln in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_RoomSeparationLines) \
            .WhereElementIsNotElementType().WherePasses(zone):
        if not isinstance(ln, CurveElement):
            continue
        try:
            if ln.LevelId != level.Id:
                continue
        except Exception:
            pass
        crv = ln.GeometryCurve
        segs = _segments(crv)
        if not segs:
            continue
        mid = segs[0][0]
        if abs(mid[0] - px) > SEARCH_RADIUS * 2 or abs(mid[1] - py) > SEARCH_RADIUS * 2:
            continue
        bounding.append((ln, segs, 0.0, u'קו הפרדת חדרים'))

    # columns bound rooms too - use their plan box so a wall ending at a column is not "free"
    for bic in (BuiltInCategory.OST_Columns, BuiltInCategory.OST_StructuralColumns):
        for col in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().WherePasses(zone):
            bb = col.get_BoundingBox(None)
            if not near(bb) or not (bb.Min.Z <= z_comp <= bb.Max.Z):
                continue
            c = [(bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Min.Y), (bb.Max.X, bb.Max.Y), (bb.Min.X, bb.Max.Y)]
            segs = [(c[i], c[(i + 1) % 4]) for i in range(4)]
            bounding.append((col, segs, 0.0, u'עמוד'))

    # free ends: an end that does not touch any other bounding element
    free_ends = []
    n_b = max(len(bounding), 1)
    for i, (el, segs, hw, label) in enumerate(bounding):
        if i % 10 == 0:
            step(30 + int(50.0 * i / n_b), u'מחפש קצוות חופשיים (%d/%d)...' % (i, n_b))
        if label == u'עמוד':
            continue
        ends = [segs[0][0], segs[-1][1]]
        for e in ends:
            touched = False
            for j, (el2, segs2, hw2, _) in enumerate(bounding):
                if j == i:
                    continue
                if any(_dist_point_seg(e, a, b) <= hw2 + hw + JOIN_TOL for a, b in segs2):
                    touched = True
                    break
            if not touched:
                d = math.hypot(e[0] - px, e[1] - py)
                if d <= SEARCH_RADIUS:
                    free_ends.append((el, e, d, label))
    for el, e, d, label in free_ends:
        suspects.append((el.Id, u'%s: קצה חופשי שלא נוגע באף אלמנט תוחם, בנקודה (%d, %d) ס"מ' % (
            label, _cm(e[0]), _cm(e[1])), d))

    gaps = []
    for a in range(len(free_ends)):
        for b in range(a + 1, len(free_ends)):
            ea, eb = free_ends[a][1], free_ends[b][1]
            g = math.hypot(ea[0] - eb[0], ea[1] - eb[1])
            if g <= 3.3:  # ~1m
                gaps.append((_id(free_ends[a][0].Id), _id(free_ends[b][0].Id), _cm(g), (_cm(ea[0]), _cm(ea[1]))))

    # escaping rays
    step(85, u'בודק כיוונים פתוחים...')
    all_segs = [s for (_, segs, _, _) in bounding for s in segs]
    escapes = []
    for k in range(RAY_COUNT):
        ang = 2 * math.pi * k / RAY_COUNT
        d = (math.cos(ang), math.sin(ang))
        best = None
        for a, b in all_segs:
            t = _ray_hit((px, py), d, a, b)
            if t is not None and (best is None or t < best):
                best = t
        if best is None or best > RAY_LENGTH:
            escapes.append(int(round(math.degrees(ang))))

    # locate each leak: group neighbouring escaping angles; for the middle ray of each group find the
    # closest element end on its left and on its right - the opening lies between those two ends
    leaks = []
    if escapes:
        step_deg = 360.0 / RAY_COUNT
        groups = []
        for a in sorted(escapes):
            if groups and a - groups[-1][-1] <= step_deg + 0.5:
                groups[-1].append(a)
            else:
                groups.append([a])
        if len(groups) > 1 and groups[0][0] - step_deg <= 0.5 and groups[-1][-1] + step_deg >= 359.5:
            groups[0] = groups[-1] + groups[0]
            groups.pop()
        for g in groups:
            mid = math.radians(g[len(g) // 2])
            d = (math.cos(mid), math.sin(mid))
            left = right = None
            for el, segs, hw, label in bounding:
                for pt in (segs[0][0], segs[-1][1]):
                    vx, vy = pt[0] - px, pt[1] - py
                    t = vx * d[0] + vy * d[1]
                    if t <= 0 or t > RAY_LENGTH:
                        continue
                    s = d[0] * vy - d[1] * vx          # signed distance from the ray
                    if s > 0 and (left is None or s < left[0]):
                        left = (s, t, el, label, pt)
                    if s < 0 and (right is None or -s < -right[0]):
                        right = (s, t, el, label, pt)
            leak = {'angles': [int(x) for x in g]}
            if left and right:
                leak['width_cm'] = _cm(left[0] - right[0])
                leak['at_cm'] = (_cm((left[4][0] + right[4][0]) / 2), _cm((left[4][1] + right[4][1]) / 2))
                leak['element_a'] = (_id(left[2].Id), left[3])
                leak['element_b'] = (_id(right[2].Id), right[3])
                leak['distance_cm'] = _cm(min(left[1], right[1]))
            leaks.append(leak)

    suspects.sort(key=lambda s: s[2])
    return {
        'level': level.Name,
        'computation_height_cm': _cm(comp_h),
        'pick_cm': (_cm(px), _cm(py)),
        'bounding_count': len(bounding),
        'suspects': [(_id(i), r, _cm(d)) for i, r, d in suspects],
        'suspect_ids': [_id(i) for i, _, _ in suspects],
        'gaps': gaps,
        'escaping_ray_angles': escapes,
        'leaks': leaks,
    }


def save_result(doc, result):
    base = os.path.dirname(doc.PathName) if doc.PathName else os.path.join(os.path.expanduser('~'), 'Desktop')
    out_dir = os.path.join(base, u'PermitAgent_exports')
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(out_dir, u'diag_%s.json' % stamp)
    txt = json.dumps(result, ensure_ascii=False, indent=1)
    if not isinstance(txt, type(u'')):
        txt = txt.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(txt)
    return path
