# -*- coding: utf-8 -*-
"""PermitAgent - level marks (spot elevations) on the residential floors.

Office rules (Alon, 28.09.26):
  * floor lobby: one spot elevation in the corridor in front of the elevator doors; the "לובי קומתי"
    label sits just above it
  * every balcony (every floor): a spot elevation; the "מרפסת" label sits just above it
  * typical floor (a plan that stands for several floors): a separate block "מפלסים:" with the levels
    of all the floors of the group, next to the floor title / north arrow (a generic annotation in the view)
  * spot type: "** PL_Base_Point - MOST USE NEW" (since 29.09.26; before: "** PL_Base_Point - MOST USE")
  * ground floor (00, added 28.09.26 evening): the surrounding site grading (the pavement / street
    outside the building) is done by Alon by hand - not automated. Only the specific points where the
    paving ("אבן משתלבת" / "משתלבות") reaches a room that serves the building are automated: every such
    room gets ONE combined spot (meant to show both the absolute and the level-relative value - see the
    open question in project_permit_memory.md about which spot type actually displays both) on its own
    floor, right by the transition, with the room name 20cm above it (NOT the 10cm used on floors 1-8 -
    see GROUND_LABEL_GAP_M); and a second, unlabelled spot on the paving itself, right outside the same
    door. Apartment rooms are skipped (name-based heuristic - see APARTMENT_ROOM_KW).
Needs the agent's rooms on the level (run "עדכון חדרים ושמות" first).
Safety: only on a working copy (_AGENT); one TransactionGroup (a single Ctrl+Z); only the agent's own
spots / block (marked with the PermitAgentLabel entity) are deleted and rebuilt; the only other element
changed is the agent's own "לובי קומתי" / "מרפסת" label, which is moved above its spot.
"""
import math
import System
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, Transaction, TransactionGroup, XYZ,
    SpotDimension, SpotDimensionType, TextNote, TextNoteOptions, HorizontalTextAlignment,
    VerticalTextAlignment, Options, Solid, GeometryInstance, PlanarFace, FamilyInstance, Level,
    SpatialElementBoundaryOptions, Wall, CurveElement, TextNoteLeaderTypes, ElementId,
)
from Autodesk.Revit.DB.ExtensibleStorage import Entity
from Autodesk.Revit.DB.Architecture import Room, Railing

import permit_rooms
import permit_write
import permit_apts

FLOOR_LOBBY = u'לובי קומתי'
BALCONY = u'מרפסת'
# office rule (Alon, 29.09.26): the relative spots use "** PL_Base_Point - MOST USE NEW" (was "...MOST USE").
# Matched by the exact type name - the old type's name is a prefix of the new one.
SPOT_TYPE_NAME = u'** PL_Base_Point - MOST USE NEW'
SPOT_TYPE_KEY = SPOT_TYPE_NAME
BLOCK_TITLE = u'מפלסים:'
TITLE_KW = [u'כותרת', u'title', u'צפון', u'north']
LOBBY_MAX_DEPTH = 5.0          # ft: the spot is half way across the corridor in front of the elevators, at most 1.5m
BLOCK_GAP_PAPER = 0.01              # m on paper between the levels block and the floor title

# ground floor (00) service-room / paving transitions - office rule (Alon, 28.09.26)
GROUND_LABEL_GAP_M = 0.20      # 20cm between the value text and the room label above it (10cm on floors 1-8)
PAVING_KW = [u'משתלבות']       # the paving floor type ("Landscape - משתלבות צבעוני")
DOOR_SIDE_PROBE = 1.0           # ft (~30cm) from the door centre, to tell inside from outside
# rooms considered part of an apartment (skipped - only rooms "serving the building" are marked)
APARTMENT_ROOM_KW = [
    u'ח.דיור', u'ח.מגורים', u'מטבח', u'פ.אוכל', u'ח.שינה', u'ח.רחצה', u'ממ"ד', u'ממד', u'מרפסת', u'ש.אורחים',
]


def _is_apartment_room_name(name):
    n = name or u''
    return any(k in n for k in APARTMENT_ROOM_KW)


def _mark(el, schema, kind):
    ent = Entity(schema)
    ent.Set[System.String]('Kind', kind)
    el.SetEntity(ent)


def _kind(el, schema):
    try:
        ent = el.GetEntity(schema)
        return ent.Get[System.String]('Kind') if ent.IsValid() else None
    except Exception:
        return None


def _spot_type(doc, key=None):
    """the spot type by its exact name (trimmed); without an exact match, a name containing it"""
    key = (key or SPOT_TYPE_KEY).strip()
    near = None
    for st in FilteredElementCollector(doc).OfClass(SpotDimensionType):
        nm = (permit_rooms.elem_name(st) or u'').strip()
        if nm == key:
            return st
        if near is None and key in nm:
            near = st
    return near


# the absolute (survey) value without a symbol - on the ground floor it sits under the relative spot in
# the rooms bordering the site, and alone on the Landscape floor outside (office rule, Alon 29.09.26)
SURVEY_TYPE_KEY = u'PL_Survay ללא סימבול הוריזונטלי'
SURVEY_BELOW_MM = 3.3       # paper mm: the survey value's anchor under the relative value's anchor
SURVEY_TEXT_H_MM = 2.5      # paper mm: height of a value text
# the surfaces themselves are not "drawn lines" for the readability test (v28: their sloped faces drew a
# mesh over the whole site, so no place was ever clear); their outlines are checked by `fits` anyway
SITE_SURFACE_CATS = ['OST_Floors', 'OST_Toposolid', 'OST_Topography', 'OST_BuildingPad',
                     # v31 trace: one Section Box (scope box) framed the whole site and "covered" every label
                     'OST_SectionBox', 'OST_VolumeOfInterest']
SITE_MIN_OTHER = 1.5 / 0.3048    # ft: a site spot keeps at least 1.5m from any other spot outside
YARD_KW = u'חצר'            # a private yard gets at least 3 absolute spots, spread (office rule)
YARD_SPOTS = 3
YARD_CORNER_MIN_EDGE = 1.0 / 0.3048     # ft: a corner counts when both its sides are at least 1m long
YARD_CORNER_MIN = 2.5 / 0.3048          # ft: two corner spots of one yard at least 2.5m apart
YARD_CORNER_INSET = (0.5, 0.7, 0.9, 1.2, 1.5)   # m along the corner's bisector into the yard


YARD_PAVING_KW = [u'מרפסת', u'ריצוף', u'משתלב', u'deck', u'paving', u'pavers']


def _yard_floor_ok(type_name):
    """a yard's own surface: a Landscape floor (grass / pavers) or a paving floor - not the apartment's
    interior slab that reaches under the yard edge (v30: a corner landed on "C25_T15")"""
    n = (type_name or u'').lower()
    return _is_landscape(type_name) or any(k in n for k in YARD_PAVING_KW)


def yard_corner_points(room, floors, z, log):
    """office rule (Alon, 29.09.26): the spots of a private yard sit in its corners. The corners are the
    convex corners of the yard outline (both sides >= 1m); the point is a little inside the yard along the
    corner's bisector, on a floor. -> [(point, floor)]; the text-fit search refines each point."""
    try:
        loops = room.GetBoundarySegments(SpatialElementBoundaryOptions())
    except Exception:
        loops = None
    if not loops:
        return []
    polys = []
    for loop in loops:
        pts = []
        for seg in loop:
            p = seg.GetCurve().GetEndPoint(0)
            pts.append((p.X, p.Y))
        if len(pts) >= 3:
            polys.append(pts)
    outer = max(polys, key=lambda ps: abs(sum(ps[i][0] * ps[(i + 1) % len(ps)][1] - ps[(i + 1) % len(ps)][0] * ps[i][1]
                                              for i in range(len(ps)))))
    n = len(outer)
    area2 = sum(outer[i][0] * outer[(i + 1) % n][1] - outer[(i + 1) % n][0] * outer[i][1] for i in range(n))
    sgn = 1.0 if area2 > 0 else -1.0
    out = []
    for i in range(n):
        a, v, b = outer[i - 1], outer[i], outer[(i + 1) % n]
        e1 = (v[0] - a[0], v[1] - a[1])
        e2 = (b[0] - v[0], b[1] - v[1])
        l1 = (e1[0] ** 2 + e1[1] ** 2) ** 0.5
        l2 = (e2[0] ** 2 + e2[1] ** 2) ** 0.5
        if l1 < YARD_CORNER_MIN_EDGE or l2 < YARD_CORNER_MIN_EDGE:
            continue
        cross = e1[0] * e2[1] - e1[1] * e2[0]
        if cross * sgn <= 1e-6 * l1 * l2:
            continue                                   # a straight run or a concave (inner) corner
        bx, by = -e1[0] / l1 + e2[0] / l2, -e1[1] / l1 + e2[1] / l2
        bl = (bx * bx + by * by) ** 0.5
        if bl < 1e-6:
            continue
        bx, by = bx / bl, by / bl                      # points into the yard at a convex corner
        for d in YARD_CORNER_INSET:
            q = XYZ(v[0] + bx * d / 0.3048, v[1] + by * d / 0.3048, z)
            if not room.IsPointInRoom(q):
                continue
            f = _top_floor_at(floors, q.X, q.Y)
            if f is not None and _yard_floor_ok(f[2]):
                out.append((q, f))
                break
    log(u'yard corners: %d convex corner(s) with a floor, of %d outline vertices' % (len(out), n))
    return out


def _fmt(v):
    return (u'+%.2f' if v >= 0 else u'%.2f') % v


def _parse(s):
    try:
        return float((s or u'').replace(u'+', u'').replace(u',', u'.').strip())
    except Exception:
        return None


def _faces(geo):
    out = []
    if geo is None:
        return out
    for obj in geo:
        if isinstance(obj, GeometryInstance):
            out += _faces(obj.GetInstanceGeometry())
        elif isinstance(obj, Solid):
            out += list(obj.Faces)
    return out


def _site_elements(doc):
    """floors, plus toposolids where the API has them - site paving is often hosted as a toposolid,
    not a floor (same reasoning as the `surfaces` list in permit_rooms.py's analyse())"""
    cats = [BuiltInCategory.OST_Floors]
    if hasattr(BuiltInCategory, 'OST_Toposolid'):
        cats.append(getattr(BuiltInCategory, 'OST_Toposolid'))
    for bic in cats:
        for el in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
            yield el


def floor_top_face(doc, level, x, y, only=None):
    """(reference, z, floor/toposolid type name) of the highest upward face of a floor or toposolid
    on/near the level above point (x, y). `only(type name)` limits the search (e.g. Landscape floors).
    Sloped faces (paving laid to falls) are accepted: the projected point may drift a few cm."""
    opts = Options()
    opts.ComputeReferences = True
    lz = level.ProjectElevation
    best = None
    for fl in _site_elements(doc):
        bb = fl.get_BoundingBox(None)
        if bb is None or not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y):
            continue
        if not (lz - 3.0 <= bb.Max.Z <= lz + 3.0):
            continue
        tname = permit_rooms.elem_name(doc.GetElement(fl.GetTypeId()))
        if only is not None and not only(tname):
            continue
        for f in _faces(fl.get_Geometry(opts)):
            if f.Reference is None:
                continue
            zz = _face_z(f, x, y, bb.Max.Z)
            if zz is None:
                continue
            if best is None or zz > best[1]:
                best = (f.Reference, zz, tname)
    return best


def _face_z(f, x, y, top):
    """height of an upward face at (x, y), or None. Planar faces: exactly, from the plane (sloped paving
    too). Other faces (shape-edited / variable-thickness paving): project from above and accept when the
    projected point is right under (x, y) and the face looks up there."""
    try:
        if isinstance(f, PlanarFace):
            n, o = f.FaceNormal, f.Origin
            if n.Z < 0.3:
                return None
            zz = o.Z - (n.X * (x - o.X) + n.Y * (y - o.Y)) / n.Z
            res = f.Project(XYZ(x, y, zz))
            if res is None or res.Distance > 0.05 or not f.IsInside(res.UVPoint):
                return None
            return zz
        res = f.Project(XYZ(x, y, top + 1.0))
        if res is None:
            return None
        p = res.XYZPoint
        if abs(p.X - x) > 0.05 or abs(p.Y - y) > 0.05 or not f.IsInside(res.UVPoint):
            return None
        if f.ComputeNormal(res.UVPoint).Z < 0.3:
            return None
        return p.Z
    except Exception:
        return None


def _is_landscape(type_name):
    """office rule (Alon, 29.09.26): the exterior floors that border the building are the floor types
    whose name starts with "Landscape" (e.g. "Landscape - משתלבות צבעוני")"""
    return (type_name or u'').strip().lower().startswith(u'landscape')


def level_floors(doc, level, landscape=None):
    """[(bbox, sketch polygons, type name, floor)] of the floors near the level; landscape=True/False
    keeps only the Landscape / only the other floors"""
    lz = level.ProjectElevation
    out = []
    for fl in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType():
        tname = permit_rooms.elem_name(doc.GetElement(fl.GetTypeId()))
        if landscape is not None and _is_landscape(tname) != landscape:
            continue
        bb = fl.get_BoundingBox(None)
        if bb is None or not (lz - 3.0 <= bb.Max.Z <= lz + 3.0):
            continue
        polys = permit_rooms.floor_polygons(doc, fl)
        if polys:
            out.append((bb, polys, tname, fl))
    return out


def landscape_floors(doc, level):
    return level_floors(doc, level, landscape=True)


def _landscape_floor_at(lands, x, y):
    """the floor (bbox, polygons, type name, element) of `lands` under (x, y), or None"""
    for f in lands:
        bb, polys = f[0], f[1]
        if bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y and permit_rooms.point_in_polygons(x, y, polys):
            return f
    return None


_TOPZ_CACHE = {}


def _top_floor_at(floors, x, y):
    """the floor seen from above at (x, y): of all the floors whose outline holds the point, the one
    whose top surface is highest there (grass laid under the paving must not hide the paving)"""
    best, best_z = None, None
    for f in floors:
        bb, polys = f[0], f[1]
        if not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y and permit_rooms.point_in_polygons(x, y, polys)):
            continue
        k = permit_rooms._id(f[3].Id)
        if k not in _TOPZ_CACHE:
            _TOPZ_CACHE[k] = _TopZ(f[3])
        h = _TOPZ_CACHE[k].at(x, y)
        if h is None:
            h = bb.Max.Z
        if best is None or h > best_z:
            best, best_z = f, h
    return best


def _landscape_at(lands, x, y):
    f = _landscape_floor_at(lands, x, y)
    return f[2] if f is not None else None


# office rule (Alon, 29.09.26): these "rooms" are openings between the ground floor and the basements -
# no floor, not in use - they get no level marks
NO_SPOT_NAMES = [u'פיר', u'כ.א.צ מרתפים', u'ש.ע מרתפים'] + list(permit_write.SHAFT_NAMES)


def _no_spot_room(name):
    """shafts (every name of the shaft list: פיר, פיר אוורור, אוורור טרפו...) and the basement openings"""
    n = (name or u'').strip()
    return n.startswith(u'פיר') or u'אוורור' in n or \
        any(permit_rooms.norm(n) == permit_rooms.norm(k) for k in NO_SPOT_NAMES)


def _room_samples(room, z, step=1.5, limit=400):
    """points inside the room on a grid"""
    bb = room.get_BoundingBox(None)
    if bb is None:
        return []
    while ((bb.Max.X - bb.Min.X) / step) * ((bb.Max.Y - bb.Min.Y) / step) > limit:
        step *= 1.3
    pts = []
    x = bb.Min.X + step / 2.0
    while x < bb.Max.X:
        y = bb.Min.Y + step / 2.0
        while y < bb.Max.Y:
            p = XYZ(x, y, z)
            if room.IsPointInRoom(p):
                pts.append(p)
            y += step
        x += step
    return pts


def _landscape_share(room, lands, z):
    pts = _room_samples(room, z, step=2.0, limit=120)
    if not pts:
        return 0.0
    return sum(1 for p in pts if _landscape_at(lands, p.X, p.Y)) / float(len(pts))


# ft: how far out to look for the paving if the first, close try finds nothing - widened after v23 kept
# finding the room's own floor (interior slab, e.g. a covered entrance porch) up to 91cm out and never
# reached the paving (roughly 0.3 / 0.6 / 1 / 1.5 / 2.5 / 4m)
GROUND_PROBE_DISTS = tuple(m / 0.3048 for m in (0.3, 0.6, 1.0, 1.5, 2.5, 4.0))


# --- automatic absolute spots on the site (office rule, Alon 29.09.26) ---------------------------------
# where a Landscape floor meets a floor of another type, and where the Landscape surface steps by more
# than 2cm: a stretch longer than 30m gets a spot every 8m, a shorter one every 5m; spots only on the
# Landscape side; no spot closer than that spacing to another one. DWG-only areas get nothing.
SCATTER_LONG_M = 30.0
SCATTER_EVERY_LONG_M = 8.0
SCATTER_EVERY_SHORT_M = 5.0
STEP_CM = 2.0
EDGE_SAMPLE = 0.5 / 0.3048      # ft between samples along a floor edge
GRID_STEP = 0.6 / 0.3048        # ft grid for the Landscape heights
SPOT_INSET = 0.4 / 0.3048       # ft: a scatter spot sits this far into its floor from the edge / step


def _spacing(length_ft):
    m = length_ft * 0.3048
    return (SCATTER_EVERY_LONG_M if m > SCATTER_LONG_M else SCATTER_EVERY_SHORT_M) / 0.3048


def _along(run_pts, length_ft):
    """points spread along a run (ordered samples): one every `spacing`, centred; at least one"""
    s = _spacing(length_ft)
    n = max(1, int(length_ft / s + 0.5))
    idx = [min(len(run_pts) - 1, int((i + 0.5) * len(run_pts) / float(n))) for i in range(n)]
    return [run_pts[i] for i in idx], s


class _TopZ(object):
    """height of a floor's top surface at (x, y), from its upward planar faces (sloped ones included)"""

    def __init__(self, floor):
        self.faces = []
        bb = floor.get_BoundingBox(None)
        self.top = bb.Max.Z if bb is not None else 0.0
        for f in _faces(floor.get_Geometry(Options())):
            if isinstance(f, PlanarFace) and f.FaceNormal.Z <= 0.3:
                continue                       # walls / the underside of a planar slab
            self.faces.append(f)

    def at(self, x, y):
        best = None
        for f in self.faces:
            zz = _face_z(f, x, y, self.top)
            if zz is not None and (best is None or zz > best):
                best = zz
        return best


def scatter_points(doc, view, level, z, lands, log):
    """-> [(point inside a Landscape floor, floor, spacing ft, why)] - the automatic site spots"""
    phase = permit_rooms.view_phase(doc, view)
    others = level_floors(doc, level, landscape=False)
    out = []

    interior = {}

    def building_room(q):
        r = doc.GetRoomAtPoint(q, phase)
        if r is None:
            return False
        k = permit_rooms._id(r.Id)
        if k not in interior:
            interior[k] = _landscape_share(r, lands, z) < 0.5
        return interior[k]

    # (a) Landscape meets another floor type
    for fl in lands:
        bb, polys, tname, el = fl
        for loop in polys:
            runs, cur = [], []
            n = len(loop)
            for i in range(n):
                a, b = loop[i], loop[(i + 1) % n]
                L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
                if L < 1e-6:
                    continue
                ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
                nx, ny = uy, -ux
                m = max(1, int(L / EDGE_SAMPLE))
                for j in range(m):
                    t = (j + 0.5) / m * L
                    px, py = a[0] + ux * t, a[1] + uy * t
                    sx, sy = (nx, ny) if not permit_rooms.point_in_polygons(px + nx * 0.2, py + ny * 0.2, polys) else (-nx, -ny)
                    qx, qy = px + sx * 0.6, py + sy * 0.6                  # ~18cm outside this floor
                    other = _landscape_floor_at(others, qx, qy)
                    if other is None:
                        o2 = _landscape_floor_at(lands, qx, qy)
                        other = o2 if (o2 is not None and o2[2] != tname) else None
                    # the building is not "another paving": the building's slab reaches under its walls,
                    # so look a little further out too - a room of the building there means a wall
                    ok = other is not None and not any(
                        building_room(XYZ(px + sx * d, py + sy * d, z)) for d in (0.6, 1.5, 3.0))
                    if ok:
                        cur.append(((px - sx * SPOT_INSET, py - sy * SPOT_INSET), other[2]))
                    elif cur:
                        runs.append(cur)
                        cur = []
            if cur:
                if runs and len(loop) > 2:
                    runs[0] = cur + runs[0]          # the loop closes: the last run joins the first
                else:
                    runs.append(cur)
            for run in runs:
                length = len(run) * EDGE_SAMPLE
                pts, s = _along([p for p, _o in run], length)
                log(u'site edge: "%s" meets "%s" along %.1fm -> %d spot(s)' % (
                    tname, run[len(run) // 2][1], length * 0.3048, len(pts)))
                for p in pts:
                    out.append((XYZ(p[0], p[1], z), fl, s, u'edge with "%s"' % run[len(run) // 2][1]))

    # (b) the Landscape surface steps by more than 2cm: plateaus on a grid, spots along each side of
    # every step line
    if lands:
        x0 = min(f[0].Min.X for f in lands)
        y0 = min(f[0].Min.Y for f in lands)
        tops = {}
        cell = {}
        for f in lands:
            bb, polys, tname, el = f
            tz = tops.setdefault(permit_rooms._id(el.Id), _TopZ(el))
            for i in range(int((bb.Min.X - x0) / GRID_STEP), int((bb.Max.X - x0) / GRID_STEP) + 1):
                x = x0 + (i + 0.5) * GRID_STEP
                for j in range(int((bb.Min.Y - y0) / GRID_STEP), int((bb.Max.Y - y0) / GRID_STEP) + 1):
                    y = y0 + (j + 0.5) * GRID_STEP
                    if not permit_rooms.point_in_polygons(x, y, polys):
                        continue
                    h = tz.at(x, y)
                    if h is not None and ((i, j) not in cell or h > cell[(i, j)][0]):
                        cell[(i, j)] = (h, f)
        lim = STEP_CM / 30.48
        edges = {}                    # (plateau cell side) -> list of step cells on that side
        for (i, j), (h, f) in cell.items():
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = cell.get((i + di, j + dj))
                if nb is not None and abs(nb[0] - h) > lim:
                    edges.setdefault((i, j), (h, f))
        # group the step cells into chains (8-connected), each side separately (by height)
        seen = set()
        chains = []
        for c in edges:
            if c in seen:
                continue
            stack, chain = [c], []
            seen.add(c)
            while stack:
                a = stack.pop()
                chain.append(a)
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        b = (a[0] + di, a[1] + dj)
                        if b in edges and b not in seen and abs(edges[b][0] - edges[a][0]) <= lim:
                            seen.add(b)
                            stack.append(b)
            chains.append(chain)
        for chain in chains:
            if len(chain) < 2:
                continue
            # order along the chain's main direction
            xs = [c[0] for c in chain]
            ys = [c[1] for c in chain]
            axis = 0 if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else 1
            chain.sort(key=lambda c: c[axis])
            length = len(chain) * GRID_STEP
            pts, s = _along(chain, length)
            h, f = edges[chain[0]]
            log(u'site step: %d cells at %+.1fcm on "%s" (%.1fm) -> %d spot(s)' % (
                len(chain), (h - level.ProjectElevation) * 30.48, f[2], length * 0.3048, len(pts)))
            for c in pts:
                out.append((XYZ(x0 + (c[0] + 0.5) * GRID_STEP, y0 + (c[1] + 0.5) * GRID_STEP, z), f, s,
                            u'step, %+.1fcm side' % ((edges[c][0] - level.ProjectElevation) * 30.48)))
    return out


BORDER_STEP = 1.5                        # ft (~45cm) between samples along a room boundary
BORDER_PROBES = tuple(m / 0.3048 for m in (0.15, 0.3, 0.45, 0.6, 0.8, 1.0))  # out from the boundary
UNIT_INSET = 0.8 / 0.3048                # the room unit's centre, this far in from the border
PAVING_OUT = 0.35 / 0.3048               # the outside spot, this far past the first Landscape point


def ground_border_points(doc, view, level, z, log):
    """office rule (Alon, 29.09.26): every room that serves the building (not an apartment room) and
    borders - through a wall, a curtain wall or a door - an exterior floor whose type starts with
    "Landscape". Found by walking the room boundary and looking just outside it: the first point outside
    every room must lie on a Landscape floor sketch. One transition per room: at its exterior door if the
    door is on the bordering stretch, else the middle of the longest bordering stretch.
    -> [{'room', 'in_pt', 'out_pt', 'how'}]"""
    phase = permit_rooms.view_phase(doc, view)
    lands = landscape_floors(doc, level)
    log(u'ground border: %d Landscape floor(s): %s' % (len(lands), u', '.join(sorted(set(l[2] for l in lands)))))
    rooms = [r for r in permit_write.agent_rooms(doc, level)
             if not _is_apartment_room_name((r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u''))]
    log(u'ground border: %d non-apartment agent room(s)' % len(rooms))
    # exterior doors (a room on one side only) per room
    ext_doors = {}
    for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not isinstance(d, FamilyInstance) or d.LevelId != level.Id:
            continue
        try:
            fr, to = d.get_FromRoom(phase), d.get_ToRoom(phase)
            loc = d.Location.Point
        except Exception:
            continue
        if (fr is None) != (to is None):
            inside = fr if fr is not None else to
            ext_doors.setdefault(permit_rooms._id(inside.Id), []).append(loc)
    opts = SpatialElementBoundaryOptions()
    out = []
    exterior = []
    for room in rooms:
        name = (room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
        if _no_spot_room(name):
            log(u'ground border: "%s" - openings to the basement, no floor: no spots' % name)
            continue
        share = _landscape_share(room, lands, z)
        if share >= 0.5:
            # a "room" floored with Landscape is outside the building (office rule): absolute spots only
            log(u'ground border: "%s" is %.0f%% Landscape floor -> exterior (absolute spots only)' % (name, share * 100))
            exterior.append(room)
            continue
        hits = []           # (seg index, boundary pt, outward normal, first landscape pt)
        try:
            loops = room.GetBoundarySegments(opts)
        except Exception:
            loops = None
        k = 0
        for loop in (loops or []):
            for seg in loop:
                c = seg.GetCurve()
                a, b = c.GetEndPoint(0), c.GetEndPoint(1)
                L = c.Length
                if L < 0.3:
                    continue
                n = max(1, int(L / BORDER_STEP))
                for i in range(n):
                    p = c.Evaluate((i + 0.5) / n, True)
                    der = c.ComputeDerivatives((i + 0.5) / n, True).BasisX.Normalize()
                    nx, ny = der.Y, -der.X                      # a normal; make it point outwards
                    if room.IsPointInRoom(XYZ(p.X + nx * 0.3, p.Y + ny * 0.3, z)):
                        nx, ny = -nx, -ny
                    for dd in BORDER_PROBES:
                        q = XYZ(p.X + nx * dd, p.Y + ny * dd, z)
                        other = doc.GetRoomAtPoint(q, phase)
                        if other is not None and other.Id == room.Id:
                            continue
                        if _landscape_at(lands, q.X, q.Y):
                            # outside: a Landscape floor (even inside a "room" such as a yard - the
                            # Landscape floor makes it exterior, office rule)
                            hits.append((k, p, (nx, ny), q))
                            break
                        if other is not None:
                            break                              # the neighbour is another room, not outside
                        # else: still inside the wall thickness - look a little further out
                k += 1
        if not hits:
            continue
        # the bordering stretches (runs of consecutive samples on consecutive segments)
        runs, cur = [], [hits[0]]
        for h in hits[1:]:
            prev = cur[-1]
            if h[0] - prev[0] <= 1 and ((h[1].X - prev[1].X) ** 2 + (h[1].Y - prev[1].Y) ** 2) ** 0.5 < 2.5 * BORDER_STEP:
                cur.append(h)
            else:
                runs.append(cur)
                cur = [h]
        runs.append(cur)
        pick, how = None, u''
        for dl in ext_doors.get(permit_rooms._id(room.Id), []):
            near = min(hits, key=lambda h: (h[1].X - dl.X) ** 2 + (h[1].Y - dl.Y) ** 2)
            if ((near[1].X - dl.X) ** 2 + (near[1].Y - dl.Y) ** 2) ** 0.5 < 3.0:
                pick, how = near, u'exterior door'
                break
        if pick is None:
            run = max(runs, key=len)
            pick, how = run[len(run) // 2], u'middle of the bordering stretch (%d samples)' % len(run)
        _k, p, (nx, ny), q = pick
        in_pt = None
        for inset in (UNIT_INSET, UNIT_INSET * 0.75, UNIT_INSET * 0.5):
            cand = XYZ(p.X - nx * inset, p.Y - ny * inset, z)
            if room.IsPointInRoom(cand):
                in_pt = cand
                break
        out_pt = XYZ(q.X + nx * PAVING_OUT, q.Y + ny * PAVING_OUT, z)
        if not _landscape_at(lands, out_pt.X, out_pt.Y):
            out_pt = q
        log(u'ground border: "%s" borders %s along %d sample(s) -> %s, inside %s, outside %s cm' % (
            name, _landscape_at(lands, q.X, q.Y), len(hits), how,
            [round(in_pt.X * 30.48), round(in_pt.Y * 30.48)] if in_pt else None,
            [round(out_pt.X * 30.48), round(out_pt.Y * 30.48)]))
        if in_pt is not None:
            out.append({'room': room, 'in_pt': in_pt, 'out_pt': out_pt, 'how': how})
    return out, exterior, lands


def ground_service_points(doc, view, level, z, log):
    """rooms on the entrance level reached directly by the exterior paving: for each qualifying room,
    the point just inside its door and the point just outside it, on the paving. office rule (Alon,
    28.09.26): identified by the door's FromRoom/ToRoom (one side has a room, the other none - it leads
    outside), the inside room is not an apartment room, and the floor just outside is the paving type
    ("משתלבות"). Verbose logging - this is a first version, not yet verified in Revit."""
    phase = permit_rooms.view_phase(doc, view)
    rooms = [r for r in permit_write.agent_rooms(doc, level)
             if not _is_apartment_room_name((r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u''))]
    room_ids = set(permit_rooms._id(r.Id) for r in rooms)
    log(u'ground service: %d non-apartment agent room(s) on this level' % len(rooms))
    doors = [d for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors)
             .WhereElementIsNotElementType() if isinstance(d, FamilyInstance) and d.LevelId == level.Id]
    log(u'ground service: %d door(s) on this level' % len(doors))
    out = []
    seen_rooms = set()
    for d in doors:
        try:
            fr = d.get_FromRoom(phase)
            to = d.get_ToRoom(phase)
            loc, fo = d.Location.Point, d.FacingOrientation
        except Exception as ex:
            log(u'ground service: door %s - could not read room/location (%s)' % (permit_rooms._id(d.Id), ex))
            continue
        inside = None
        if fr is not None and to is None:
            inside = fr
        elif to is not None and fr is None:
            inside = to
        if inside is None:
            continue                              # interior door: both sides have a room, or neither does
        iname = (inside.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
        rid = permit_rooms._id(inside.Id)
        if rid not in room_ids:
            log(u'ground service: door of "%s" leads outside, but the room is skipped '
                u'(apartment room, or not one of the agent\'s rooms)' % iname)
            continue
        if rid in seen_rooms:
            continue
        found = False
        for dist in GROUND_PROBE_DISTS:
            p_a = XYZ(loc.X + fo.X * dist, loc.Y + fo.Y * dist, z)
            p_b = XYZ(loc.X - fo.X * dist, loc.Y - fo.Y * dist, z)
            if inside.IsPointInRoom(p_a):
                p_in, p_out = p_a, p_b
            elif inside.IsPointInRoom(p_b):
                p_in, p_out = p_b, p_a
            else:
                continue
            hit = floor_top_face(doc, level, p_out.X, p_out.Y)
            out_cm = [round(p_out.X * 30.48), round(p_out.Y * 30.48)]
            if hit is None:
                log(u'ground service: door of "%s" - no floor/toposolid %.0fcm outside, at %s cm' % (
                    iname, dist * 30.48, out_cm))
                continue
            if not any(k in (hit[2] or u'') for k in PAVING_KW):
                log(u'ground service: door of "%s" - floor %.0fcm outside is "%s" (not paving), at %s cm' % (
                    iname, dist * 30.48, hit[2], out_cm))
                continue
            seen_rooms.add(rid)
            out.append({'room': inside, 'in_pt': p_in, 'out_pt': p_out})
            log(u'ground service: door of "%s" -> paving "%s", %.0fcm out, at %s cm' % (
                iname, hit[2], dist * 30.48, [round(p_out.X * 30.48), round(p_out.Y * 30.48)]))
            found = True
            break
        if not found:
            log(u'ground service: door of "%s" - no paving found up to %.0fcm out' % (
                iname, GROUND_PROBE_DISTS[-1] * 30.48))
    return out


def _rooms_named(doc, level, name):
    out = []
    for r in permit_write.agent_rooms(doc, level):
        p = r.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p is not None and (p.AsString() or u'').strip() == name:
            out.append(r)
    return out


def _label_in(doc, view, room, text, schema, z):
    for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
        if _kind(t, schema) != u'label' or permit_rooms.norm(t.Text) != permit_rooms.norm(text):
            continue
        c = t.Coord
        if room.IsPointInRoom(XYZ(c.X, c.Y, z)):
            return t
    return None


def _room_centre(room, z):
    bb = room.get_BoundingBox(None)
    c = XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, z)
    if room.IsPointInRoom(c):
        return c
    return room.Location.Point


def lobby_spot_point(doc, level, lobby, z, log):
    """half way across the corridor in front of the elevator doors (at most 1.5m from them)"""
    doors = []
    for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not isinstance(d, FamilyInstance) or d.LevelId != level.Id:
            continue
        low = permit_rooms.fam_type_label(doc, d).lower()
        if not any(k in low for k in permit_rooms.ELEVATOR_DOOR_KW):
            continue
        loc, fo = d.Location.Point, d.FacingOrientation
        for sgn in (1.0, -1.0):
            if lobby.IsPointInRoom(XYZ(loc.X + sgn * fo.X * 2.0, loc.Y + sgn * fo.Y * 2.0, z)):
                doors.append((loc, XYZ(sgn * fo.X, sgn * fo.Y, 0)))
                break
    if not doors:
        log(u'floor lobby: no elevator door opens into it - spot at the lobby centre')
        return _room_centre(lobby, z), None
    cx = sum(l.X for l, _ in doors) / len(doors)
    cy = sum(l.Y for l, _ in doors) / len(doors)
    dx = sum(v.X for _, v in doors) / len(doors)
    dy = sum(v.Y for _, v in doors) / len(doors)
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    dx, dy = dx / n, dy / n
    # the corridor in front of the doors: from the wall face (first point inside the lobby) to the
    # opposite wall (last point inside); the spot + label unit is centred between them
    t_start = t_end = None
    t = 0.0
    while t < 30.0:
        inside = lobby.IsPointInRoom(XYZ(cx + dx * t, cy + dy * t, z))
        if inside:
            if t_start is None:
                t_start = t
            t_end = t
        elif t_start is not None:
            break
        t += 0.1
    if t_start is None:
        log(u'floor lobby: the corridor in front of the elevators was not found - lobby centre')
        return _room_centre(lobby, z), (dx, dy)
    k = min((t_start + t_end) / 2.0, t_start + LOBBY_MAX_DEPTH)
    log(u'floor lobby: %d elevator doors, corridor %.0f..%.0fcm from them -> unit centre at %.0fcm'
        % (len(doors), t_start * 30.48, t_end * 30.48, k * 30.48))
    return XYZ(cx + dx * k, cy + dy * k, z), (dx, dy)


def _group_levels(doc, view):
    """the levels this plan stands for (typical floor = more than one), lowest first"""
    levels = sorted([l for l in FilteredElementCollector(doc).OfClass(Level) if permit_apts._is_story(l)],
                    key=lambda l: l.ProjectElevation)
    pviews = permit_apts.permit_views(doc, view)
    groups = []
    for l in levels:
        if permit_rooms._id(l.Id) in pviews:
            groups.append([l])
        elif groups:
            groups[-1].append(l)
    me = permit_rooms._id(view.GenLevel.Id)
    for g in groups:
        if permit_rooms._id(g[0].Id) == me:
            return g
    return [view.GenLevel]


def _title_annotation(doc, view):
    anns = [e for e in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_GenericAnnotation)
            .WhereElementIsNotElementType() if e.OwnerViewId == view.Id]
    if not anns:
        return None
    named = [a for a in anns if any(k in permit_rooms.fam_type_label(doc, a).lower() for k in TITLE_KW)]
    pool = named or anns

    def key(a):                       # the lowest one (the title sits under the plan)
        bb = a.get_BoundingBox(view)
        return bb.Min.Y if bb is not None else 1e9
    return sorted(pool, key=key)[0]


TITLE_TEXT_KW = u'קומ'          # "קומה 8", "קומות 2-7"
BLOCK_GAP_M = 0.50               # office rule (Alon): 50cm between the levels block and the floor title


def _texts_of(el):
    """the texts an element shows: a text note's text, or the string parameters of an annotation family"""
    if isinstance(el, TextNote):
        return [el.Text or u'']
    out = []
    for p in el.Parameters:
        try:
            s = p.AsString()
            if s:
                out.append(s)
        except Exception:
            pass
    return out


def floor_title(doc, view, schema, log):
    """the floor title text in the view ("קומות 2-7" + its scale line) -> (bb [x0, y0, x1, y1], description)
    A text note or a generic annotation whose text holds "קומ"; lines of the title that are separate text
    notes (e.g. "קנ"מ 1:100") right next to it are included."""
    cands = []
    for el in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        if el.OwnerViewId != view.Id or not (isinstance(el, TextNote) or (
                el.Category is not None and permit_rooms._id(el.Category.Id) == int(BuiltInCategory.OST_GenericAnnotation))):
            continue
        if _kind(el, schema) is not None:
            continue                               # the agent's own texts
        txt = [s for s in _texts_of(el) if TITLE_TEXT_KW in s and len(s) < 40]
        bb = el.get_BoundingBox(view)
        if txt and bb is not None:
            cands.append((el, txt[0], bb))
    if not cands:
        log(u'levels block: no floor title text ("%s...") found in the view' % TITLE_TEXT_KW)
        return None
    for el, txt, bb in cands:
        log(u'levels block: title candidate "%s" x %d..%d y %d..%d cm' % (
            txt.replace(u'\r', u' '), bb.Min.X * 30.48, bb.Max.X * 30.48, bb.Min.Y * 30.48, bb.Max.Y * 30.48))
    el, txt, bb = sorted(cands, key=lambda c: c[2].Min.Y)[0]        # the title sits under the plan
    box = [bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y]
    # other lines of the same title written as separate text notes (the scale line under it)
    near = 1.0 / 0.3048
    for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
        if t.Id == el.Id or _kind(t, schema) is not None:
            continue
        b = t.get_BoundingBox(view)
        if b is None:
            continue
        if b.Max.X > box[0] and b.Min.X < box[2] and b.Min.Y < box[3] + near and b.Max.Y > box[1] - near:
            box = [min(box[0], b.Min.X), min(box[1], b.Min.Y), max(box[2], b.Max.X), max(box[3], b.Max.Y)]
    return box, txt.replace(u'\r', u' ')


# ---- rulebook (Alon, 29.09.26): apartments, mamad, bathrooms, railings, fences, "קו קומה מעל", פ.ב --------
APT_LABEL_KW = u'דירה'
APT_UNIT_GAP_M = 0.20          # the apartment number sits 20cm above the value of its spot
ROOM_UNIT_GAP_M = 0.10         # mamad / bathroom: the room name 10cm above (as balconies and the lobby)
MAMAD_KW = [u'ממ"ד', u'ממ״ד', u'ממד']
BATH_PREFIX = u'ח.רחצה'
LOWER_EPS = 0.5 / 30.48        # ft: a bathroom floor lower than the apartment's by more than 0.5cm
UNIT_SEARCH_M = 1.5            # how far a label + spot unit may move to be readable
RAIL_TEXT_OUT_M = 1.0          # the railing note sits this far outside the railing, with an arrow to it
BUILT_RAIL_MAX_CM = 130.0      # a low wall on a balcony edge = built railing ("מעקה בנוי H=X")
BUILT_RAIL_MIN_CM = 60.0       # lower walls on a balcony edge are curbs, not railings
FENCE_MAX_CM = 150.0           # a low wall not tied to the floor above = fence (rulebook 6)
FENCE_EVERY_M = 7.5            # fence top spots: at the ends and every 5-10m along
FENCE_MIN_GAP_M = 2.0
HIDDEN_KW = u'hidden'          # the line style of "קו קומה מעל"
HIDDEN_MIN_M = 3.0
HIDDEN_EVERY_M = 10.0
PB_MAX_DEPTH_FT = 10.0         # the concrete under a garden is found within 3m below the grass


def _rects_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _railing_height_cm(doc, rl):
    t = doc.GetElement(rl.GetTypeId())
    for getter in (lambda: t.get_Parameter(getattr(BuiltInParameter, 'RAILING_SYSTEM_HEIGHT_PARAM')),
                   lambda: t.LookupParameter(u'Railing Height'), lambda: t.LookupParameter(u'גובה מעקה')):
        try:
            p = getter()
            if p is not None and p.AsDouble() > 0:
                return int(round(p.AsDouble() * 30.48))
        except Exception:
            pass
    return 105


def _wall_top_face(wall):
    """(reference, z) of the top face of a wall, or None"""
    opts = Options()
    opts.ComputeReferences = True
    best = None
    for f in _faces(wall.get_Geometry(opts)):
        if isinstance(f, PlanarFace) and f.FaceNormal.Z > 0.9 and f.Reference is not None:
            if best is None or f.Origin.Z > best[1]:
                best = (f.Reference, f.Origin.Z)
    return best


def _wall_unattached_top(wall):
    """the wall is not tied to the floor above: top constraint "Unconnected" and not attached"""
    try:
        p = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
        if p is not None and p.AsElementId() != ElementId.InvalidElementId:
            return False
        a = wall.get_Parameter(BuiltInParameter.WALL_TOP_IS_ATTACHED)
        if a is not None and a.AsInteger() == 1:
            return False
    except Exception:
        pass
    return True


def _hidden_lines(doc, view):
    out = []
    for c in FilteredElementCollector(doc, view.Id).OfClass(CurveElement):
        try:
            if c.OwnerViewId != view.Id:
                continue
            ls = c.LineStyle
            nm = (ls.Name if ls is not None else u'') or u''
            if HIDDEN_KW in nm.lower():
                out.append(c)
        except Exception:
            continue
    return out


def _slab_under(doc, x, y, ztop):
    """the top of the concrete under a garden point: the highest non-Landscape floor (any level) whose
    outline holds the point and whose top lies below the grass (office rule: "פ.ב")"""
    best = None
    for fl in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType():
        bb = fl.get_BoundingBox(None)
        if bb is None or not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y):
            continue
        if not (ztop - PB_MAX_DEPTH_FT <= bb.Max.Z <= ztop - 0.1):
            continue
        if _is_landscape(permit_rooms.elem_name(doc.GetElement(fl.GetTypeId()))):
            continue
        polys = permit_rooms.floor_polygons(doc, fl)
        if not polys or not permit_rooms.point_in_polygons(x, y, polys):
            continue
        tz = _TopZ(fl).at(x, y)
        tz = bb.Max.Z if tz is None else tz
        if tz < ztop - 0.1 and (best is None or tz > best):
            best = tz
    return best


def _absolute_m(doc, z_internal):
    """the absolute (survey) height in metres of an internal Z"""
    try:
        pp = doc.ActiveProjectLocation.GetProjectPosition(XYZ(0, 0, 0))
        return (z_internal + pp.Elevation) * 0.3048
    except Exception:
        return None


def run_levels(doc, view, log):
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        raise Exception(u'הכלי רץ רק על עותק עבודה - שם הקובץ חייב להכיל %s' % permit_write.WRITE_MARK)
    level = view.GenLevel
    z = level.ProjectElevation + 1.0
    scale = view.Scale or 100
    schema = permit_write._label_schema(create=True)
    stype = _spot_type(doc)
    survey_type = _spot_type(doc, SURVEY_TYPE_KEY)
    text_type = permit_write._text_type(doc)
    out = {'spots': 0, 'errors': [], 'lobby': None, 'balconies': 0, 'block': None,
           'spot_type': permit_rooms.elem_name(stype) if stype else None}
    log(u'levels: view %s, level %s, spot type %s' % (view.Name, level.Name, out['spot_type']))

    lobbies = _rooms_named(doc, level, FLOOR_LOBBY)
    balconies = _rooms_named(doc, level, BALCONY)
    if not permit_write.agent_rooms(doc, level):
        raise Exception(u'אין בקומה חדרים של העובד - יש להריץ קודם "עדכון חדרים ושמות".')

    tg = TransactionGroup(doc, u'PermitAgent - מפלסים')
    tg.Start()
    try:
        # (a unique name: in IronPython a list-comprehension variable "t" leaks and replaced it - v32 crash)
        levels_tx = Transaction(doc, u'PermitAgent - מפלסים')
        levels_tx.Start()
        # 1. the agent's previous spots and levels block in this view (first its groups and dashed lines -
        # "קו קומה מעל" is one group; its texts go with it)
        import permit_ground as PG
        removed_extra = PG.delete_old(doc, view, schema)
        old = [e for e in FilteredElementCollector(doc, view.Id).OfClass(SpotDimension)
               if _kind(e, schema) == u'spot']
        old += [e for e in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
                if _kind(e, schema) == u'levels']
        for e in old:
            try:
                doc.Delete(e.Id)
            except Exception:
                pass
        out['removed'] = len(old) + removed_extra

        spot_what = {}                   # spot id -> what created it (the end-of-run inventory in the trace)

        def add_spot(pt, what, typ=None, only=None):
            hit = floor_top_face(doc, level, pt.X, pt.Y, only)
            if hit is None:
                # a few cm around (a face seam / a hole in the face right at the point)
                for d in (0.1, 0.2, 0.35):
                    for k in range(8):
                        a = 2 * 3.14159265 * k / 8.0
                        q = XYZ(pt.X + d * math.cos(a), pt.Y + d * math.sin(a), pt.Z)
                        hit = floor_top_face(doc, level, q.X, q.Y, only)
                        if hit is not None:
                            pt = q
                            break
                    if hit is not None:
                        break
            if hit is None:
                out['errors'].append(u'%s: אין רצפה מתחת לנקודה' % what)
                log(u'%s: no floor face under %s' % (what, [round(pt.X * 30.48), round(pt.Y * 30.48)]))
                return None
            ref, fz, _fname = hit
            o = XYZ(pt.X, pt.Y, fz)
            # the value to the RIGHT of the symbol, above its line (as in the office drawings):
            # the (unused) leader points lie to the right of the origin
            right = XYZ(1.0, 0, 0)
            try:
                sp = doc.Create.NewSpotElevation(view, ref, o, o + right, o + right * 2.0, o, False)
            except Exception as ex:
                out['errors'].append(u'%s: %s' % (what, ex))
                log(u'%s: spot failed: %s' % (what, ex))
                return None
            typ = typ or stype
            if typ is not None:
                sp.ChangeTypeId(typ.Id)
            _mark(sp, schema, u'spot')
            spot_what[permit_rooms._id(sp.Id)] = what
            out['spots'] += 1
            doc.Regenerate()
            _text_to_right(sp, what)
            return sp

        def survey_under(sp_rel, what):
            """the absolute value (survey type, no symbol) right under the relative spot's value text,
            centred on it: created, then shifted so its text lands where it should (text position measured)"""
            if survey_type is None:
                return None
            mm = scale / 1000.0 / 0.3048
            try:
                tp_rel = sp_rel.TextPosition
            except Exception:
                o = sp_rel.Origin
                tp_rel = XYZ(permit_write.spot_value_centre_x(o.X, None, scale), o.Y, o.Z)
            want = XYZ(tp_rel.X, tp_rel.Y - SURVEY_BELOW_MM * mm, 0)
            o_rel = sp_rel.Origin
            sp = add_spot(XYZ(o_rel.X, o_rel.Y - SURVEY_BELOW_MM * mm, o_rel.Z), what, survey_type)
            if sp is None:
                return None
            try:
                tp = sp.TextPosition
                dx, dy = want.X - tp.X, want.Y - tp.Y
                if abs(dx) > 0.02 or abs(dy) > 0.02:
                    o = sp.Origin
                    doc.Delete(sp.Id)
                    out['spots'] -= 1
                    sp = add_spot(XYZ(o.X + dx, o.Y + dy, o.Z), what, survey_type)
                    tp = sp.TextPosition if sp is not None else None
                if tp is not None:
                    log(u'%s: survey value text at %s cm (relative value text at %s cm)' % (
                        what, [round(tp.X * 30.48), round(tp.Y * 30.48)], [round(tp_rel.X * 30.48), round(tp_rel.Y * 30.48)]))
            except Exception as ex:
                log(u'%s: survey value position not measured: %s' % (what, ex))
            return sp

        def _text_to_right(sp, what):
            b = sp.get_BoundingBox(view)
            o = sp.Origin
            if b is None or (b.Min.X + b.Max.X) / 2.0 >= o.X:
                return
            try:                              # value still on the left: mirror its position
                tp = sp.TextPosition
                sp.TextPosition = XYZ(o.X + (o.X - tp.X), tp.Y, tp.Z)
                doc.Regenerate()
                b = sp.get_BoundingBox(view)
                log(u'%s: spot value moved to the right of the symbol (%s)' % (
                    what, u'ok' if (b.Min.X + b.Max.X) / 2.0 >= o.X else u'still left'))
            except Exception as ex:
                log(u'%s: spot value is left of the symbol and could not be moved: %s' % (what, ex))

        last_unit = {}

        def spot_unit(centre, room, text, what, gap_m=None, with_survey=False, label_tn=None):
            """spot + the room's label right above its value text, handled as ONE unit whose vertical
            centre is `centre`; the label is centred on the value text. gap_m overrides the default 10cm
            gap (ground floor service rooms use 20cm - office rule, Alon 28.09.26). with_survey: the
            absolute value (survey type) under the relative one - ground floor rooms bordering the site."""
            hw, hh = permit_write._label_box(text, scale)
            mm = scale / 1000.0 / 0.3048                       # 1mm on paper, in feet
            lab = permit_write.label_over_spot([0.0, 0.0], text, scale, gap_m=gap_m)
            y0 = -permit_write.SPOT_SYMBOL_R_MM * mm           # bottom of the symbol circle
            if with_survey:
                y0 = min(y0, -(SURVEY_BELOW_MM + SURVEY_TEXT_H_MM) * mm)   # bottom of the survey value
            y1 = lab[1] + hh                                   # top of the label
            # horizontally: the unit runs from the symbol's left edge to the right end of the value /
            # label (both centred on the value text)
            x0 = -permit_write.SPOT_SYMBOL_R_MM * mm
            x1 = lab[0] + max(hw, 2.5 * permit_write.SPOT_VALUE_CHAR_MM * mm)
            sp = add_spot(XYZ(centre.X - (x0 + x1) / 2.0, centre.Y - (y0 + y1) / 2.0, centre.Z), what)
            if sp is None:
                return None
            o = sp.Origin
            try:
                val = sp.ValueString
            except Exception:
                val = None
            lp = permit_write.label_over_spot([o.X, o.Y], text, scale, val, gap_m=gap_m)
            pt = XYZ(lp[0], lp[1], 0)
            try:
                tp = sp.TextPosition
                log(u'%s: spot origin %s, value text position %s cm' % (
                    what, [round(o.X * 30.48), round(o.Y * 30.48)], [round(tp.X * 30.48), round(tp.Y * 30.48)]))
            except Exception:
                pass
            tn = label_tn if label_tn is not None else _label_in(doc, view, room, text, schema, z)
            if tn is not None:
                tn.Coord = pt
            elif text_type is not None:
                opts = TextNoteOptions(text_type.Id)
                opts.HorizontalAlignment = HorizontalTextAlignment.Center
                opts.VerticalAlignment = VerticalTextAlignment.Middle
                permit_write._mark_label(TextNote.Create(doc, view.Id, pt, text, opts), schema)
            last_unit.clear()
            last_unit['rel'] = sp
            if with_survey:
                last_unit['abs'] = survey_under(sp, what + u' (אבסולוטי)')
            # (v37) the unit's texts are taken: the dashed-line texts placed later must not cover them
            # (floor 00, 30.09.26: "משטח מרוצף" under "קו קומת מרתף")
            taken_units.append((pt.X - hw, pt.Y - hh, pt.X + hw, pt.Y + hh))
            vw = (5 * permit_write.SPOT_VALUE_CHAR_MM / 2.0 + 0.5) * mm
            vh = (SURVEY_TEXT_H_MM / 2.0 + 0.4) * mm
            for s in (sp, last_unit.get('abs')):
                try:
                    tp = s.TextPosition
                    taken_units.append((tp.X - vw, tp.Y - vh, tp.X + vw, tp.Y + vh))
                except Exception:
                    pass
            return (sp.ValueString if hasattr(sp, 'ValueString') else None) or u''

        # what is drawn inside the building (for the apartment / mamad / bathroom units): the lines of
        # everything in the view except the surfaces and scope boxes; units placed in this run are added
        mm = scale / 1000.0 / 0.3048
        agent_rooms_here = permit_write.agent_rooms(doc, level)
        occ_b = None
        try:
            rbbs = [r.get_BoundingBox(None) for r in agent_rooms_here]
            rbbs = [b for b in rbbs if b is not None]
            if rbbs:
                occ_b = permit_write.view_occupancy(
                    doc, view, (min(b.Min.X for b in rbbs) - 3, min(b.Min.Y for b in rbbs) - 3,
                                max(b.Max.X for b in rbbs) + 3, max(b.Max.Y for b in rbbs) + 3),
                    cell=0.2, extra_skip=SITE_SURFACE_CATS)
        except Exception as ex:
            log(u'building drawing scan failed: %s' % ex)
        taken_units = []
        checks = []                      # (rule, found, done, note) - the rulebook check at the end
        checks_notes = []                # units that found no fully clear place (reported in the check)

        def note_at(pt, text, rotation=None, leader_to=None):
            """a 2.0mm text of the levels tool (kind 'levels': rebuilt on every run)"""
            if text_type is None:
                return None
            opts = TextNoteOptions(text_type.Id)
            opts.HorizontalAlignment = HorizontalTextAlignment.Center
            opts.VerticalAlignment = VerticalTextAlignment.Middle
            if rotation is not None:
                opts.Rotation = rotation
            tn = TextNote.Create(doc, view.Id, XYZ(pt.X, pt.Y, 0), text, opts)
            if leader_to is not None:
                try:
                    ld = tn.AddLeader(TextNoteLeaderTypes.TNLT_STRAIGHT_R)
                    ld.End = XYZ(leader_to.X, leader_to.Y, 0)
                except Exception as ex:
                    log(u'leader failed for "%s": %s' % (text, ex))
            _mark(tn, schema, u'levels')
            return tn

        note_rects = []

        def note_clear(anchor, out_dir, text):
            """a text with an arrow to `anchor`, out along `out_dir`, at the first distance where it lies
            on no other text of this run and on no drawn line (office rule: never text on text)"""
            hw, hh = permit_write._label_box(text, scale)
            cand = None
            for dist in (1.0, 1.4, 1.8, 2.2, 2.6):
                for side in (0.0, 0.6, -0.6):
                    cx = anchor.X + out_dir[0] * dist / 0.3048 - out_dir[1] * side / 0.3048
                    cy = anchor.Y + out_dir[1] * dist / 0.3048 + out_dir[0] * side / 0.3048
                    r = (cx - hw, cy - hh, cx + hw, cy + hh)
                    if any(_rects_overlap(r, q) for q in note_rects + taken_units):
                        continue
                    if occ_b is not None and occ_b.count(r) > 0 and dist < 2.6:
                        continue
                    cand = (cx, cy, r)
                    break
                if cand:
                    break
            if cand is None:
                cx = anchor.X + out_dir[0] * 1.0 / 0.3048
                cy = anchor.Y + out_dir[1] * 1.0 / 0.3048
                cand = (cx, cy, (cx - hw, cy - hh, cx + hw, cy + hh))
                checks_notes.append(u'"%s": אין מקום נקי לגמרי' % text)
            note_rects.append(cand[2])
            return note_at(XYZ(cand[0], cand[1], 0), text, leader_to=anchor)

        def add_spot_ref(ref, pt, what, typ=None):
            right = XYZ(1.0, 0, 0)
            try:
                sp = doc.Create.NewSpotElevation(view, ref, pt, pt + right, pt + right * 2.0, pt, False)
            except Exception as ex:
                log(u'%s: spot failed: %s' % (what, ex))
                return None
            typ = typ or stype
            if typ is not None:
                sp.ChangeTypeId(typ.Id)
                if sp.GetTypeId() != typ.Id:
                    log(u'%s: the spot kept its type (%s) - deleted' % (
                        what, permit_rooms.elem_name(doc.GetElement(sp.GetTypeId()))))
                    doc.Delete(sp.Id)
                    return None
            _mark(sp, schema, u'spot')
            spot_what[permit_rooms._id(sp.Id)] = what
            out['spots'] += 1
            doc.Regenerate()
            return sp

        def unit_under_label(tn, room, gap_m, what, fixed=False):
            """office rule: a spot right under an existing label (the label gap_m above the spot's value,
            centred on it). The pair is one unit: it moves together to the nearest place where it is
            readable - inside the room, over nothing drawn (the label's own old place excepted) and
            away from the other units. -> the spot or None"""
            txt = (tn.Text or u'').strip()
            hw, hh = permit_write._label_box(txt, scale)
            val_w = 5 * permit_write.SPOT_VALUE_CHAR_MM * mm
            left = permit_write.SPOT_VALUE_LEFT_MM * mm
            top = permit_write.SPOT_TEXT_TOP_MM * mm
            R = permit_write.SPOT_SYMBOL_R_MM * mm
            gap = gap_m / 0.3048
            c0 = tn.Coord
            old = (c0.X - hw, c0.Y - hh, c0.X + hw, c0.Y + hh)

            def geom(lx, ly, sdx=0.0, sdy=0.0):
                oy = ly - hh - gap - top - sdy
                ox = lx - (left + val_w / 2.0) + sdx
                return [(lx - hw, ly - hh, lx + hw, ly + hh), (ox + left, oy, ox + left + val_w, oy + top),
                        (ox - R, oy - R, ox + R, oy + R)], (ox, oy)

            def drawn(r):
                if occ_b is None:
                    return 0
                n = occ_b.count(r)
                ix = (max(r[0], old[0]), max(r[1], old[1]), min(r[2], old[2]), min(r[3], old[3]))
                if ix[0] < ix[2] and ix[1] < ix[3]:
                    n -= occ_b.count(ix)
                return n

            mode = [fixed]

            def ok(lx, ly, sdx=0.0, sdy=0.0):
                rs, _o = geom(lx, ly, sdx, sdy)
                if mode[0] and lx == c0.X and ly == c0.Y:
                    rs = rs[1:]          # the label stays where it is (approved by Alon) - only the spot is checked
                for r in rs:
                    for cx, cy in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])):
                        if not room.IsPointInRoom(XYZ(cx, cy, z)):
                            return False
                    if drawn(r) > 0 or any(_rects_overlap(r, t) for t in taken_units):
                        return False
                return True

            spot_at = None
            sd = (0.0, 0.0)
            least = [None]

            def search_fixed(allow_least=True):
                # regular floor (office rule): the apartment number stays 20cm past its door - only the
                # spot looks for a clear place under it (sideways, a little lower, or the label nudged)
                # (v37: apartment 5 on 01 - the symbol sat on the door / walls: wider search, up to 1m aside
                # and 50cm lower, before the label itself is nudged)
                mode[0] = True
                cands = []
                for dy in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
                    for dx in (0.0, 0.2, -0.2, 0.4, -0.4, 0.6, -0.6, 0.8, -0.8, 1.0, -1.0):
                        cands.append((0.0, 0.0, dx / 0.3048, dy / 0.3048))
                for ldx, ldy in ((0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15), (0.25, 0), (-0.25, 0)):
                    for dx in (0.0, 0.3, -0.3):
                        cands.append((ldx / 0.3048, ldy / 0.3048, dx / 0.3048, 0.0))
                for ldx, ldy, sdx, sdy in cands:
                    if ok(c0.X + ldx, c0.Y + ldy, sdx, sdy):
                        return (c0.X + ldx, c0.Y + ldy), (sdx, sdy)
                # (v38) no place fully clear: the one with the FEWEST lines under the symbol and value (v37 went
                # back to the start, the worst place - apartments 5 / 11 and a mamad on 01 sat on walls)
                if not allow_least:
                    return None, (0.0, 0.0)
                best = None
                for ldx, ldy, sdx, sdy in cands:
                    rs, _o = geom(c0.X + ldx, c0.Y + ldy, sdx, sdy)
                    if ldx == 0.0 and ldy == 0.0:
                        rs = rs[1:]
                    sc = 0
                    for r in rs:
                        sc += 50 * sum(1 for cx, cy in ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]))
                                       if not room.IsPointInRoom(XYZ(cx, cy, z)))
                        sc += drawn(r) + (100 if any(_rects_overlap(r, t) for t in taken_units) else 0)
                    if best is None or sc < best[0]:
                        best = (sc, (c0.X + ldx, c0.Y + ldy), (sdx, sdy))
                if best is not None:
                    least[0] = best[0]
                    return best[1], best[2]
                return None, (0.0, 0.0)

            if fixed:
                spot_at, sd = search_fixed(allow_least=False)
                if spot_at is None:
                    # (v39, Alon 01.10.26: "the 20cm is only a limit - it must look good and be in the right
                    # place") the number and its spot move TOGETHER, up to 80cm, to the nearest clear place
                    mode[0] = False
                    r, step = 0.1 / 0.3048, 0.1 / 0.3048
                    while r <= 0.8 / 0.3048 and spot_at is None:
                        for k in range(16):
                            a = 2 * 3.14159265 * k / 16.0
                            lx, ly = c0.X + r * math.cos(a), c0.Y + r * math.sin(a)
                            if ok(lx, ly):
                                spot_at = (lx, ly)
                                break
                        r += step
                if spot_at is None:
                    spot_at, sd = search_fixed()
            else:
                r, step = 0.0, 0.1 / 0.3048
                while r <= UNIT_SEARCH_M / 0.3048 and spot_at is None:
                    for k in (range(16) if r > 0 else [0]):
                        a = 2 * 3.14159265 * k / 16.0
                        lx, ly = c0.X + r * math.cos(a), c0.Y + r * math.sin(a)
                        if ok(lx, ly):
                            spot_at = (lx, ly)
                            break
                    r += step
                if spot_at is None:
                    # (v37) the whole unit found no place - keep the label, look for the spot only
                    spot_at, sd = search_fixed()
            how = u'readable'
            if least[0] is not None:
                how = u'least drawn place (score %d)' % least[0]
                checks_notes.append(u'%s: המקום עם הכי מעט קווים' % what)
            if spot_at is None:
                spot_at, how = (c0.X, c0.Y), u'no fully clear place - kept at the label'
                checks_notes.append(u'%s: אין מקום נקי לגמרי' % what)
            rs, (ox, oy) = geom(spot_at[0], spot_at[1], sd[0], sd[1])
            sp = add_spot(XYZ(ox, oy, z), what)
            if sp is None:
                return None
            tn.Coord = XYZ(spot_at[0], spot_at[1], 0)
            taken_units.extend(rs)
            log(u'%s: unit %s, moved %.0fcm' % (what, how, ((spot_at[0] - c0.X) ** 2 + (spot_at[1] - c0.Y) ** 2) ** 0.5 * 30.48))
            return sp

        # what permit_ground (the ground-floor rules and "קו קומה מעל") needs
        gctx = PG.Ctx()
        gctx.doc, gctx.view, gctx.level, gctx.z, gctx.scale = doc, view, level, z, scale
        gctx.schema, gctx.log, gctx.out = schema, log, out
        gctx.stype, gctx.survey_type, gctx.text_type = stype, survey_type, text_type
        gctx.add_spot_ref, gctx.note_at = add_spot_ref, note_at
        gctx.phase = permit_rooms.view_phase(doc, view)
        gctx.agent_rooms = agent_rooms_here
        gctx.interior_cache, gctx.slab_polys = {}, {}
        gctx.occ, gctx.taken = None, []
        gctx.lands = landscape_floors(doc, level)
        gctx.all_floors, gctx.exterior, gctx.slab_floors = [], [], []
        gctx.fence_segs, gctx.skipped, gctx.line_labels = [], [], {}

        # 2. floor lobby: spot in front of the elevator doors, "לובי קומתי" above it
        lobby_value = None
        for lobby in lobbies[:1]:
            p, _d = lobby_spot_point(doc, level, lobby, z, log)
            lobby_value = spot_unit(p, lobby, FLOOR_LOBBY, FLOOR_LOBBY)
            if lobby_value is not None:
                out['lobby'] = lobby_value
                log(u'floor lobby unit %s centred at %s cm' % (lobby_value, [round(p.X * 30.48), round(p.Y * 30.48)]))
        if not lobbies:
            log(u'no "%s" room on this level' % FLOOR_LOBBY)

        # 3. every balcony: the spot + "מרפסת" unit centred in the balcony
        for b in balconies:
            if spot_unit(_room_centre(b, z), b, BALCONY, BALCONY) is not None:
                out['balconies'] += 1
        if lobbies:
            checks.append((u'לובי קומתי - מפלס + שם', len(lobbies[:1]), 1 if out['lobby'] is not None else 0, u''))
        checks.append((u'מרפסות - מפלס + "מרפסת"', len(balconies), out['balconies'], u''))

        # 3b. rulebook: every apartment - its spot, with "דירה מס' X" 20cm above it (one unit, readable)
        labels = [t for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
                  if _kind(t, schema) == u'label' and (t.Text or u'').strip().startswith(APT_LABEL_KW)]
        old_style = [t for t in labels if u"מס'" not in (t.Text or u'')]
        apt_done = 0
        for t in labels:
            c = t.Coord
            room = next((r for r in agent_rooms_here if r.IsPointInRoom(XYZ(c.X, c.Y, z))), None)
            if room is None:
                log(u'apartment label "%s": not inside any room - skipped' % t.Text.strip())
                continue
            # a one-line label (a regular floor) stays at its door; a typical floor's may move (v6 rule)
            regular = u'\r' not in (t.Text or u'').strip()
            if unit_under_label(t, room, APT_UNIT_GAP_M, u'דירה: %s' % t.Text.strip().split(u'\r')[0],
                                fixed=regular) is not None:
                apt_done += 1
        checks.append((u'דירות - מפלס, ו"דירה מס\'" 20 ס"מ מעליו', len(labels), apt_done,
                       (u'%d תוויות בפורמט הישן ("דירה X") - להריץ קודם "עדכון חדרים ושמות"' % len(old_style))
                       if old_style else u''))

        # 3c. every mamad: a spot under its name (10cm)
        def room_name(r):
            return (r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
        mamads = [r for r in agent_rooms_here if any(k in room_name(r) for k in MAMAD_KW)]
        mm_done = 0
        for r in mamads:
            tn = _label_in(doc, view, r, room_name(r), schema, z)
            if tn is None:
                log(u'mamad %s: no agent label in the room' % permit_rooms._id(r.Id))
                continue
            if unit_under_label(tn, r, ROOM_UNIT_GAP_M, u'ממ"ד %s' % permit_rooms._id(r.Id)) is not None:
                mm_done += 1
        checks.append((u'ממ"ד - מפלס מתחת לשם', len(mamads), mm_done, u''))

        # 3d. bathrooms: a spot only where the bathroom floor is modelled lower than the apartment's
        def top_at(p):
            hit = floor_top_face(doc, level, p.X, p.Y)
            return hit[1] if hit is not None else None
        ref_z = []
        for r in agent_rooms_here:
            n = room_name(r)
            if n in (permit_write.LIVING, u'ח.דיור', u'ח.שינה', u'ח.שינה הורים') or any(k in n for k in MAMAD_KW):
                tz = top_at(_room_centre(r, z))
                if tz is not None and not any(k in n for k in MAMAD_KW):
                    ref_z.append(tz)
        ref_z.sort()
        apt_z = ref_z[len(ref_z) // 2] if ref_z else None
        baths = [r for r in agent_rooms_here if room_name(r).startswith(BATH_PREFIX)]
        low, bath_done = 0, 0
        for r in baths:
            tn = _label_in(doc, view, r, room_name(r), schema, z)
            probe = tn.Coord if tn is not None else _room_centre(r, z)
            tz = top_at(XYZ(probe.X, probe.Y, z))
            if apt_z is None or tz is None or tz >= apt_z - LOWER_EPS:
                continue
            low += 1
            log(u'bathroom %s is %.1fcm lower than the apartments' % (permit_rooms._id(r.Id), (apt_z - tz) * 30.48))
            if tn is not None and unit_under_label(tn, r, ROOM_UNIT_GAP_M, u'%s %s' % (room_name(r), permit_rooms._id(r.Id))) is not None:
                bath_done += 1
        checks.append((u'חדרי רחצה נמוכים מהדירה - מפלס', low, bath_done,
                       u'נבדקו %d חדרי רחצה%s' % (len(baths), u'' if apt_z is not None else u' - לא נמצא מפלס רצפת דירה להשוואה')))

        # 3e. railings: "מעקה H=105" with an arrow on every balcony railing; a low wall on a balcony edge
        # = "מעקה בנוי H=X" (X = balcony floor to the top of the wall); other low walls not tied to the
        # floor above = fences: spots on their top ("ג.גדר"), at the ends and every 5-10m
        lz = level.ProjectElevation
        rail_done = 0
        balcony_with_note = set()
        balcony_with_rail = set()
        for rl in FilteredElementCollector(doc).OfClass(Railing):
            bb = rl.get_BoundingBox(None)
            if bb is None or not (lz - 1.0 <= bb.Min.Z <= lz + 4.0):
                continue
            try:
                curves = list(rl.GetPath())
            except Exception:
                continue
            if not curves:
                continue
            cv = max(curves, key=lambda c: c.Length)
            mid = cv.Evaluate(0.5, True)
            d = cv.ComputeDerivatives(0.5, True).BasisX.Normalize()
            nx, ny = d.Y, -d.X
            side = None
            for sgn in (1.0, -1.0):
                q = XYZ(mid.X + sgn * nx * 0.8, mid.Y + sgn * ny * 0.8, z)
                b = next((bb_ for bb_ in balconies if bb_.IsPointInRoom(q)), None)
                if b is not None:
                    side = (b, sgn)
                    break
            if side is None:
                continue
            b, sgn = side
            balcony_with_rail.add(permit_rooms._id(b.Id))
            if permit_rooms._id(b.Id) in balcony_with_note:
                continue
            h = _railing_height_cm(doc, rl)
            if note_clear(mid, (-sgn * nx, -sgn * ny), u'מעקה H=%d' % h) is not None:
                rail_done += 1
                balcony_with_note.add(permit_rooms._id(b.Id))
        checks.append((u'מעקה H=... עם חץ - מרפסות עם מעקה', len(balcony_with_rail), rail_done,
                       u'מרפסת אחת = כיתוב אחד (על המעקה הארוך)'))

        built_found, built_done = 0, 0
        for w in FilteredElementCollector(doc).OfClass(Wall):
            if w.LevelId != level.Id or not _wall_unattached_top(w):
                continue
            bb = w.get_BoundingBox(None)
            loc = getattr(w.Location, 'Curve', None)
            if bb is None or loc is None:
                continue
            mid = loc.Evaluate(0.5, True)
            d = loc.ComputeDerivatives(0.5, True).BasisX.Normalize()
            nx, ny = d.Y, -d.X
            off = w.Width / 2.0 + 0.5
            bal = None
            for sgn in (1.0, -1.0):
                q = XYZ(mid.X + sgn * nx * off, mid.Y + sgn * ny * off, z)
                b = next((bb_ for bb_ in balconies if bb_.IsPointInRoom(q)), None)
                if b is not None:
                    bal = (b, sgn, q)
                    break
            if bal is not None:
                b, sgn, q = bal
                ft = top_at(q)
                if ft is None:
                    continue
                hcm = (bb.Max.Z - ft) * 30.48
                # a built railing is at least 60cm (v35 on 01: 12cm balcony curbs were counted - "H=12")
                if hcm > BUILT_RAIL_MAX_CM or hcm < BUILT_RAIL_MIN_CM:
                    continue
                built_found += 1
                if note_clear(mid, (-sgn * nx, -sgn * ny), u'מעקה בנוי H=%d' % int(round(hcm))) is not None:
                    built_done += 1
        checks.append((u'מעקה בנוי H=X (קיר 60-130 ס"מ בקצה מרפסת)', built_found, built_done, u''))
        # fences: the ground floor only (office rule) - in the ground section below, permit_ground.fences()

        # 4. typical floor: the "מפלסים:" block beside the floor title / north arrow
        group = _group_levels(doc, view)
        if len(group) > 1:
            base_val = _parse(lobby_value)
            if base_val is None:
                base_val = round(level.Elevation * 0.3048, 2)
                log(u'levels block: no lobby spot value - using the level elevation %.2f' % base_val)
            vals = [base_val + (l.ProjectElevation - level.ProjectElevation) * 0.3048 for l in group]
            text = BLOCK_TITLE + u'\r' + u'\r'.join(_fmt(v) for v in vals)
            hw, hh = permit_write._label_box(text, scale)
            title = floor_title(doc, view, schema, log)
            valign = VerticalTextAlignment.Middle
            if title is not None:
                # office rule: 50cm left of the floor title, centred on it (vertically)
                box, ttxt = title
                pt = XYZ(box[0] - BLOCK_GAP_M / 0.3048, (box[1] + box[3]) / 2.0, 0)
                where = u'50cm left of the title "%s", centred on it' % ttxt
            else:
                ann = _title_annotation(doc, view)
                gap = BLOCK_GAP_M / 0.3048
                if ann is not None and ann.get_BoundingBox(view) is not None:
                    ab = ann.get_BoundingBox(view)
                    pt = XYZ(ab.Min.X - gap, (ab.Min.Y + ab.Max.Y) / 2.0, 0)
                    where = u'left of "%s" (no title text found)' % permit_rooms.fam_type_label(doc, ann)
                else:
                    cb = view.CropBox
                    pt = XYZ(cb.Max.X - gap, cb.Min.Y + hh + gap, 0)
                    where = u'bottom-right corner of the crop (no title found)'
            if text_type is not None:
                opts = TextNoteOptions(text_type.Id)
                opts.HorizontalAlignment = HorizontalTextAlignment.Right
                opts.VerticalAlignment = valign
                tn = TextNote.Create(doc, view.Id, pt, text, opts)
                _mark(tn, schema, u'levels')
                out['block'] = text.replace(u'\r', u' ')
            log(u'levels block (%d floors): %s | %s' % (len(group), text.replace(u'\r', u' '), where))

        # 5. ground floor (00): every room reached directly by the exterior paving gets one combined
        # spot on its own floor near the transition ("לובי כניסה" and other service rooms), and a second
        # unlabelled spot on the paving right outside the same door. Site grading itself is manual.
        out['ground_service'] = 0
        out['ground_paving'] = 0
        if abs(level.Elevation) < 0.05:
            log(u'ground: survey spot type %s' % (permit_rooms.elem_name(survey_type) if survey_type else u'NOT FOUND'))
            units, exterior, lands = ground_border_points(doc, view, level, z, log)
            mm = scale / 1000.0 / 0.3048
            survey_off = [None]            # the survey value text position relative to the spot origin

            def measure_survey_offset(pt):
                sp = add_spot(pt, u'קרקע: מדידת טקסט', survey_type, _is_landscape)
                if sp is None:
                    return
                try:
                    tp, o = sp.TextPosition, sp.Origin
                    survey_off[0] = (tp.X - o.X, tp.Y - o.Y)
                    log(u'ground: survey value text offset from its origin %s cm' % (
                        [round(v * 30.48, 1) for v in survey_off[0]],))
                except Exception as ex:
                    log(u'ground: survey text offset not measured (%s) - assuming centred' % ex)
                doc.Delete(sp.Id)
                out['spots'] -= 1

            # what is drawn around the site (lines of elements, texts - the yard labels, trees, furniture):
            # a value text must be readable - on its own floor AND over nothing drawn (office rule)
            occ = None
            occ_rec = []                   # (bbox, "category | type | id") of everything drawn - diagnostics
            pb_count = [0]
            culprits_logged = [0]
            areas = [f[0] for f in lands] + [r.get_BoundingBox(None) for r in exterior if r.get_BoundingBox(None)]
            if areas:
                try:
                    t0 = __import__('time').time()
                    occ = permit_write.view_occupancy(doc, view, (min(b.Min.X for b in areas), min(b.Min.Y for b in areas),
                                                                   max(b.Max.X for b in areas), max(b.Max.Y for b in areas)),
                                                      cell=0.2, extra_skip=SITE_SURFACE_CATS, record=occ_rec)
                    log(u'ground: drawing scan of the site in %.1fs' % (__import__('time').time() - t0))
                except Exception as ex:
                    log(u'ground: drawing scan failed (%s) - texts checked against the floors only' % ex)
            all_floors = lands + level_floors(doc, level, landscape=False)
            outside_spots = []            # origins of the absolute spots outside (spacing / de-duplication)

            def text_rect(p):
                ox, oy = survey_off[0] or (0.0, 0.0)
                tx, ty = p.X + ox, p.Y + oy
                w = (5 * permit_write.SPOT_VALUE_CHAR_MM / 2.0 + 0.6) * mm
                h = (SURVEY_TEXT_H_MM + 0.4) * mm
                return tx, ty, w, h

            def fits(p, polys, clear=False):
                """the survey value text (estimated box around its text position) lies fully on the floor;
                clear=True: and over nothing drawn"""
                tx, ty, w, h = text_rect(p)
                pts = [(tx - w, ty - h), (tx + w, ty - h), (tx + w, ty + h), (tx - w, ty + h),
                       (tx, ty - h), (tx, ty + h), (tx - w, ty), (tx + w, ty), (p.X, p.Y)]
                if not all(permit_rooms.point_in_polygons(a, b, polys) for a, b in pts):
                    return False
                if clear and occ is not None and occ.count((tx - w, ty - h, tx + w, ty + h)) > 0:
                    return False
                return True

            def fitted(p, floor, max_r=2.5 / 0.3048, inside=None):
                """the nearest point to p where the value text sits fully on that floor over nothing drawn;
                if there is none, fully on the floor only. inside(pt) optionally limits the search."""
                polys = floor[1]
                for clear in (True, False):
                    r, step = 0.0, 0.1 / 0.3048
                    while r <= max_r:
                        for k in (range(16) if r > 0 else [0]):
                            a = 2 * 3.14159265 * k / 16.0
                            c = XYZ(p.X + r * math.cos(a), p.Y + r * math.sin(a), p.Z)
                            if (inside is None or inside(c)) and fits(c, polys, clear):
                                return c
                        r += step
                return None

            def survey_on(p, what, floor=None, max_r=2.5 / 0.3048, inside=None):
                floor = floor or _landscape_floor_at(lands, p.X, p.Y)
                if floor is None:
                    log(u'%s: no Landscape floor at %s cm' % (what, [round(p.X * 30.48), round(p.Y * 30.48)]))
                    return None
                q = fitted(p, floor, max_r, inside)
                if q is None:
                    log(u'%s: the value text does not fit on "%s" near %s cm - skipped' % (
                        what, floor[2], [round(p.X * 30.48), round(p.Y * 30.48)]))
                    return None
                sp = add_spot(q, what, survey_type, lambda n, t=floor[2]: n == t)
                # (the grass and its "פ.ב" are the gardens' own units now - permit_ground.gardens)
                if sp is not None:
                    outside_spots.append(sp.Origin)
                    clear = fits(q, floor[1], True)
                    log(u'%s: on "%s" at %s cm%s' % (what, floor[2], [round(q.X * 30.48), round(q.Y * 30.48)],
                                                    u'' if clear else u' (touches drawn lines)'))
                    if not clear and culprits_logged[0] < 12:
                        # which drawn elements lie under the text - to find what blocks every place
                        culprits_logged[0] += 1
                        tx, ty, w, h = text_rect(q)
                        under = [lab for b, lab in occ_rec
                                 if b[0] <= tx + w and b[2] >= tx - w and b[1] <= ty + h and b[3] >= ty - h]
                        log(u'   under the text: %s' % (u' || '.join(under[:6]) or u'(no element box - cell rounding?)'))
                return sp

            survey_on_landscape = survey_on

            probe = [u['out_pt'] for u in units] + [p for r in exterior for p in _room_samples(r, z)[:1]]
            if probe and survey_type is not None:
                measure_survey_offset(probe[0])

            # rooms of the building bordering the site: name / +0.00 / 31.95 inside, 31.95 outside
            for item in units:
                room = item['room']
                name = (room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
                if not name:
                    continue
                if spot_unit(item['in_pt'], room, name, u'קרקע: %s (פנים)' % name,
                             gap_m=GROUND_LABEL_GAP_M, with_survey=True) is not None:
                    out['ground_service'] += 1
                fo = _landscape_floor_at(lands, item['out_pt'].X, item['out_pt'].Y)
                if fo is not None and any(k in fo[2].lower() for k in permit_rooms.LANDSCAPE_KW):
                    # (v38) grass outside: the garden units mark it - a lone "32.42" here was a duplicate
                    log(u'קרקע: %s (חוץ): on grass - the garden units mark it' % name)
                    continue
                if survey_on_landscape(item['out_pt'], u'קרקע: %s (חוץ)' % name) is not None:
                    out['ground_paving'] += 1

            # the ground-floor rules of 30.09.26 (permit_ground): fences, gardens, paved patches, basement
            gctx.occ = occ
            gctx.taken = list(taken_units) + list(note_rects)
            gctx.lands = lands
            gctx.all_floors = all_floors
            gctx.exterior = exterior
            # (v39) every non-Landscape floor, whatever its level: v38 dropped floors of level 00 lying more than
            # ~1m under it (the C25 at 30.65 under the planters - no "30.65 פ.ב")
            gctx.slab_floors = [
                fl for fl in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType()
                if not _is_landscape(permit_rooms.elem_name(doc.GetElement(fl.GetTypeId())))]
            gctx.fence_segs = permit_rooms.fence_segments(doc, level)
            # (v38) the garden units first: in v37 the fence texts took the narrow west strips and their
            # "גינון 32.55 / 31.75 פ.ב" found no place
            g = PG.gardens(gctx)
            f_walls, f_spots = PG.fences(gctx)
            checks.append((u'גדר (קרקע בלבד, מחוץ למבנה, 20-200 ס"מ): ערך + "ג.גדר" בצד החיצוני עם חץ',
                           f_walls, f_spots, u'קירות / סימונים (בקצוות וכל ~6.5 מ\')'))
            checks.append((u'גינון / חצרות: "גינון" / ערך דשא 2 מ"מ / פ.ב מקושר לבטון', g['zones'], g['units'],
                           u'אזורי גינון / יחידות; פ.ב: %d, "גינון": %d%s%s' % (
                               g['pb'], g['gin'], (u', אזורי פ.ב משתנים: %d' % g['pb_zones']) if g['pb_zones'] else u'',
                               (u' | ' + u'; '.join(g['notes'][:3])) if g['notes'] else u'')))
            # a paved patch of a yard: "משטח מרוצף" / relative / absolute (+ פ.ב over the basement)
            pb_type = PG.ensure_spot_type(doc, survey_type, PG.PB_TYPE_NAME, suffix=PG.PB_SUFFIX, log=log)
            paved_found = paved_done = 0
            paved_pts = []
            paved_used = set()
            paved_labels = [t for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
                            if _kind(t, schema) == u'label' and permit_rooms.norm(t.Text) == permit_rooms.norm(u'משטח מרוצף')]
            for room in exterior:
                name = (room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
                if YARD_KW not in name:
                    continue
                patches = {}
                for p in _room_samples(room, z, step=1.0, limit=900):
                    f = _top_floor_at(all_floors, p.X, p.Y)
                    if f is None or any(k in f[2].lower() for k in permit_rooms.LANDSCAPE_KW) or \
                            not _yard_floor_ok(f[2]):
                        continue                              # grass (or nothing)
                    patches.setdefault(permit_rooms._id(f[3].Id), (f, []))[1].append(p)
                # (v38) one paving floor may cover several separate patches - split them into connected pieces
                pieces = []
                # (v39) the real sampling step: in a big yard _room_samples spreads its points - v38 assumed
                # 30cm, every point became a "piece" of its own and 3 of the 4 patios got no unit
                allx = sorted(set(round(p.X, 3) for _f, ps in patches.values() for p in ps))
                gaps = [b - a for a, b in zip(allx, allx[1:]) if b - a > 0.05]
                stp = min(gaps) if gaps else 1.0
                for key, (f, pts) in patches.items():
                    left = list(pts)
                    while left:
                        comp, stack = [], [left.pop()]
                        while stack:
                            a = stack.pop()
                            comp.append(a)
                            near = [b for b in left if abs(b.X - a.X) <= 1.6 * stp and abs(b.Y - a.Y) <= 1.6 * stp]
                            for b in near:
                                left.remove(b)
                            stack.extend(near)
                        pieces.append((key, f, comp))
                for key, f, pts in pieces:
                    if len(pts) * stp * stp * 0.0929 < 5.0:
                        continue                              # < 5 m2: the name only (Alon's file, west yard)
                    paved_found += 1
                    cx = sum(p.X for p in pts) / len(pts)
                    cy = sum(p.Y for p in pts) / len(pts)
                    c = min(pts, key=lambda p: (p.X - cx) ** 2 + (p.Y - cy) ** 2)
                    if any(((c.X - q.X) ** 2 + (c.Y - q.Y) ** 2) ** 0.5 < 2.0 / 0.3048 for q in paved_pts):
                        log(u'paved patch at %s cm: a unit already stands within 2m - skipped' % (
                            [round(c.X * 30.48), round(c.Y * 30.48)],))
                        continue
                    paved_pts.append(c)
                    # (v38) the patch's OWN label - one that stands on this patch (v37 took the nearest one,
                    # which belonged to the next patch: in the west yard the name stayed below on the fence)
                    own = [t for t in paved_labels if permit_rooms._id(t.Id) not in paved_used and
                           any(abs(t.Coord.X - p.X) <= 2.0 and abs(t.Coord.Y - p.Y) <= 2.0 for p in pts)]
                    lab = min(own, key=lambda t: (t.Coord.X - c.X) ** 2 + (t.Coord.Y - c.Y) ** 2) if own else None
                    if lab is not None:
                        paved_used.add(permit_rooms._id(lab.Id))
                    v = spot_unit(c, room, u'משטח מרוצף', u'קרקע: %s (משטח מרוצף)' % name,
                                  gap_m=GROUND_LABEL_GAP_M, with_survey=True, label_tn=lab)
                    if v is None:
                        continue
                    paved_done += 1
                    outside_spots.append(c)
                    sa = last_unit.get('abs')
                    hit = floor_top_face(doc, level, c.X, c.Y)
                    # (v39, Alon 01.10.26) the פ.ב of a paved patch is the top of the concrete INSIDE its own
                    # build-up: 32.45 - 13cm (fill + tiles) = 32.32 on the east patio. Taken from the floor
                    # type's layers above its structural core; pavers on soil (no core) - no פ.ב (Alon's file)
                    cover = PG.cover_above_core_ft(doc, f[3])
                    if hit is not None and sa is not None and cover:
                        v_pb = _absolute_m(doc, hit[1] - cover)
                        try:
                            tp = sa.TextPosition
                            yb = tp.Y - SURVEY_BELOW_MM * mm
                            note_at(XYZ(tp.X, yb + PG.VALUE_ANCHOR_MM * mm, 0), u'%s%s' % (v_pb, PG.PB_SUFFIX))
                            vw = (5 * permit_write.SPOT_VALUE_CHAR_MM / 2.0 + 1.5) * mm
                            taken_units.append((tp.X - vw, yb - 0.5 * mm, tp.X + vw, yb + 3 * mm))
                            log(u'פ.ב of the paved patch at %s cm: "%s" top %s minus %.0fcm above the concrete = %s' % (
                                [round(c.X * 30.48), round(c.Y * 30.48)], f[2], _absolute_m(doc, hit[1]),
                                cover * 30.48, v_pb))
                        except Exception as ex:
                            log(u'פ.ב of the paved patch: %s' % ex)
                    elif hit is not None:
                        log(u'paved patch at %s cm: "%s" has no concrete in its build-up - no פ.ב' % (
                            [round(c.X * 30.48), round(c.Y * 30.48)], f[2]))
            checks.append((u'משטח מרוצף בחצר: שם / יחסי / אבסולוטי (+ פ.ב מעל מרתף)', paved_found, paved_done, u''))
            gctx.taken = list(gctx.taken) + [r for r in taken_units if r not in gctx.taken]
            bl, bt, sl, st = PG.basement_lines(gctx, g['slabs'])
            checks.append((u'"קו קומת מרתף" (HIDDEN THIN + כיתוב)', bl, bt, u'קטעי קו / כיתובים'))
            checks.append((u'"קו שינוי גובה פ.ב" (HIDDEN THIN + כיתוב)', sl, st, u'קטעי קו / כיתובים'))
            # other "rooms" floored with Landscape (not a yard / garden): one absolute spot
            for room in exterior:
                name = (room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
                if YARD_KW in name or u'גינון' in name:
                    continue
                in_room = lambda c, r=room: r.IsPointInRoom(XYZ(c.X, c.Y, z))
                samples = [p for p in _room_samples(room, z, step=1.0, limit=600)
                           if _landscape_floor_at(lands, p.X, p.Y) is not None]
                if samples:
                    cx = sum(p.X for p in samples) / len(samples)
                    cy = sum(p.Y for p in samples) / len(samples)
                    c = min(samples, key=lambda p: (p.X - cx) ** 2 + (p.Y - cy) ** 2)
                    if survey_on(c, u'קרקע: %s' % name, inside=in_room) is not None:
                        out['ground_paving'] += 1
            # the site: Landscape edges against other floor types, and height steps > 2cm
            out['ground_site'] = 0
            import time
            t0 = time.time()
            site = scatter_points(doc, view, level, z, lands, log)
            log(u'site: %d candidate spot(s) in %.1fs' % (len(site), time.time() - t0))
            # the height steps first (the points Alon marked), then the edges against other floor types;
            # the spacing rule applies within each kind; any other spot outside only has to be 1.5m away;
            # a private yard keeps its own rule (corners + paved patches) - no site spots inside it
            yards = [r for r in exterior if YARD_KW in (r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'')]
            site.sort(key=lambda s: 0 if s[3].startswith(u'step') else 1)
            by_kind = {u'step': [], u'edge': []}
            too_close = in_yard = 0
            for p, floor, spacing, why in site:
                kind = u'step' if why.startswith(u'step') else u'edge'
                if any(y.IsPointInRoom(XYZ(p.X, p.Y, z)) for y in yards):
                    in_yard += 1
                    continue
                if any(k in floor[2].lower() for k in permit_rooms.LANDSCAPE_KW):
                    in_yard += 1
                    continue                           # grass: the gardens' own units (גינון / ערך / פ.ב)
                if any(((p.X - q.X) ** 2 + (p.Y - q.Y) ** 2) ** 0.5 < 0.8 * spacing for q in by_kind[kind]) or \
                        any(((p.X - q.X) ** 2 + (p.Y - q.Y) ** 2) ** 0.5 < SITE_MIN_OTHER for q in outside_spots):
                    too_close += 1
                    continue
                sp = survey_on(p, u'קרקע: אתר (%s)' % why, floor, 1.5 / 0.3048)
                if sp is not None:
                    by_kind[kind].append(sp.Origin)
                    out['ground_site'] += 1
            log(u'site: skipped %d inside private yards, %d closer than the spacing' % (in_yard, too_close))
            log(u'ground floor: %d room unit(s), %d absolute spot(s) by rooms / in yards, %d on the site' % (
                out['ground_service'], out['ground_paving'], out['ground_site']))
            checks.append((u'קרקע - חדרים הגובלים בחוץ: שם / ±0.00 / אבסולוטי', len(units), out['ground_service'], u''))
            checks.append((u'קרקע - אתר (קצוות ריצוף ושינויי גובה)', len(site), out['ground_site'],
                           u'מועמדים / סימונים (השאר נפסלו: מרווח, חצר או דשא)'))

        # "קו קומה מעל" (every floor below another): drawn by the agent, texts with arrows, one group
        if gctx.occ is None:
            gctx.occ = occ_b
        gctx.taken = list(gctx.taken or []) + list(taken_units) + list(note_rects)
        fa_lines, fa_texts, fa_group = PG.floor_above_line(gctx)
        checks.append((u'"קו קומה מעל" (קו שהעובד מצייר + כיתוב עם חץ + Group)', fa_lines, fa_texts,
                       (u'Group: "%s"' % fa_group) if fa_group else
                       (u'אין בינוי בקומה מעל שבולט מקירות הקומה' if not fa_lines else u'ה-Group לא נוצר')))
        if checks_notes:
            checks.append((u'יחידות בלי מקום נקי לגמרי', len(checks_notes), 0, u'; '.join(checks_notes[:6])))
        if gctx.skipped:
            # (v37) a text is never forced onto another text - it is left out and reported here
            checks.append((u'כיתובים שלא הוצבו (אין מקום בלי לעלות על טקסט)', len(gctx.skipped), 0,
                           u'; '.join(gctx.skipped[:6])))
        # the rulebook check: what the run did against every rule (shown in the output window)
        # (v38) every spot in the view after the run - who made it, its type and value (v37: a second
        # "+0.50" with a symbol stood by every paved patch and the trace could not tell where it came from)
        try:
            doc.Regenerate()
            for s in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_SpotElevations):
                k = permit_rooms._id(s.Id)
                try:
                    tp = s.TextPosition
                    tpc = [round(tp.X * 30.48), round(tp.Y * 30.48)]
                except Exception:
                    tpc = None
                log(u'spot inventory: %d | %s | %s | %s | %s | text at %s cm' % (
                    k, spot_what.get(k, u'(not made by this run, kind %s)' % _kind(s, schema)),
                    permit_rooms.elem_name(doc.GetElement(s.GetTypeId())),
                    getattr(s, 'ValueString', u'') or u'', u'', tpc))
        except Exception as ex:
            log(u'spot inventory failed: %s' % ex)
        overlaps = PG.text_overlaps(doc, view, schema)
        checks.append((u'אין טקסט על טקסט (טקסטים של העובד)', len(overlaps), 0 if overlaps else 0,
                       (u'חופפים: ' + u'; '.join(overlaps[:6])) if overlaps else u'אין חפיפות'))
        agent_texts = [(t.Text or u'').strip() for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
                       if _kind(t, schema) == u'label']
        stale = [x for x in agent_texts if x in (u'ח.דיור', u'מעלית')]
        if stale:
            checks.append((u'שמות לפי המילון (ח.מגורים / פיר מעלית)', len(stale), 0,
                           u'נמצאו %d שמות ישנים - להריץ "עדכון חדרים ושמות"' % len(stale)))
        used = (permit_rooms.elem_name(stype) or u'').strip() if stype is not None else u''
        checks.insert(0, (u'סוג המפלס היחסי: %s' % SPOT_TYPE_NAME, 1, 1 if used == SPOT_TYPE_NAME else 0,
                          u'' if used == SPOT_TYPE_NAME else
                          (u'הסוג לא נמצא בפרויקט - נעשה שימוש ב"%s"' % used if used else u'הסוג לא נמצא בפרויקט')))
        out['checks'] = checks
        for c in checks:
            log(u'CHECK | %s | found %s | done %s | %s' % c)
        levels_tx.Commit()
        tg.Assimilate()
    except Exception:
        # an open transaction must be rolled back before its group - otherwise Revit terminates (30.09.26)
        try:
            permit_write.abort_open(levels_tx)
        except Exception:
            pass
        permit_write.abort_open(tg)
        raise
    return out
