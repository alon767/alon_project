# -*- coding: utf-8 -*-
"""PermitAgent - dry-run experiment: how well can room names be guessed automatically?

Rooms are placed temporarily inside a Transaction that is ALWAYS rolled back, so the model
is left exactly as it was. For each temporary room the name is guessed from its contents and
compared with the text notes the architect typed in the same view. Only a JSON report is written.
"""
import io
import json
import os
import datetime

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, ElementId, XYZ,
    Transaction, FamilyInstance, TextNote, Wall,
    SpatialElementBoundaryOptions, ElementMulticategoryFilter,
    Options, GeometryInstance, Solid, Curve, Visibility, UV, ViewDetailLevel, SpotDimension,
)
from Autodesk.Revit.DB.Architecture import Room, Railing
from System.Collections.Generic import List

SQFT_TO_M2 = 0.09290304

CONTENT_CATS = [
    BuiltInCategory.OST_Furniture, BuiltInCategory.OST_FurnitureSystems,
    BuiltInCategory.OST_PlumbingFixtures, BuiltInCategory.OST_Casework,
    BuiltInCategory.OST_SpecialityEquipment, BuiltInCategory.OST_GenericModel,
    BuiltInCategory.OST_MechanicalEquipment, BuiltInCategory.OST_ElectricalEquipment,
]

# keyword groups, matched against lower-case "family : type"
KW = {
    'bed': [u'bed', u'מיטה', u'מיטת'],
    'double_bed': [u'double', u'queen', u'king', u'זוגי', u'180', u'160'],  # "זוגי" also covers "זוגית"
    'toilet': [u'toilet', u'wc', u'w.c', u'אסלה', u'אסלת', u'water closet'],
    'bath': [u'bath', u'tub', u'אמבט'],
    'shower': [u'shower', u'מקלחת', u'מקלחון'],
    'basin': [u'basin', u'lavatory', u'washbasin', u'כיור'],
    'kitchen': [u'kitchen', u'oven', u'stove', u'cooktop', u'hob', u'refrigerator', u'fridge',
                u'מטבח', u'כיריים', u'תנור', u'מקרר', u'dishwasher', u'מדיח'],
    'sofa': [u'sofa', u'couch', u'ספה', u'armchair', u'כורסא', u'כורסה'],
    # no plain "chair" here: it also matches "Armchair" in bedrooms
    'dining': [u'dining', u'table', u'שולחן'],
    'washer': [u'washing', u'washer', u'w.m', u'w_m', u'כביסה', u'dryer', u'מייבש'],
    'elevator': [u'elevator', u'lift', u'מעלית'],
    'boiler': [u'boiler', u'דוד'],
    # used only to place the living-room label (centre of the sofa / armchair / TV group)
    'tv': [u'television', u'טלוויז', u'plasma'],
}
# a real dining table (a coffee table also says "table", so it is not enough for the label position)
DINING_TABLE_KW = [u'dining', u'אוכל']
MAMAD_KW = [u'ממ"ד', u'ממד', u'mmd', u'mamad', u'ממ״ד']
BALCONY_KW = [u'מרפסת', u'balcon', u'deck']
ELEVATOR_DOOR_KW = [u'elevator', u'lift', u'מעלית']
SHAFT_MAX_M2 = 15.0            # an elevator shaft found by position (roof levels, no doors) is smaller than this
LIVING_NAME = u'ח.מגורים'      # office dictionary (Alon, 29.09.26) - was "ח.דיור"
# office rules (Alon, 27.09.26): mamad floor is named mamad and sits +2cm above the apartment level;
# a balcony is identified by its floor type name only.
MAMAD_OFFSET_CM = (1.0, 4.0)   # relative to the apartment floor
STAIRS_MIN_SHARE = 0.25        # stairs footprint / room area
ENTRY_LABEL_OFFSET = 3.0       # ft (~90cm) inside the apartment from its entrance door
# grass only. NOT "landscape": the office's site paving is also a "Landscape - ..." floor type
# (e.g. "Landscape - משתלבות צבעוני" = interlocking pavers)
LANDSCAPE_KW = [u'דשא', u'גינון', u'grass', u'lawn']
SERVICE_ROOM = u'חדר שירות כללי'   # the architect picks the final name from a fixed list
FLOOR_SAMPLE_STEP = 2.5        # ft (~75cm) grid used to find all floor types inside a room
YARD_STEP = 1.0                # ft (~30cm) finer grid for the grass / paving inside yards (labels)
OPEN_GRASS_STEP = 1.25         # ft (~38cm) grid for grass outside any room
OPEN_GARDEN_MIN_M2 = 3.0       # smaller grass patches outside rooms get no label
SURFACE_BELOW = 3.0            # ft: floors/toposolids of other levels count as "seen" if their top is
SURFACE_ABOVE = 5.0            #     between 0.9m below and 1.5m above this level
YARD_MIN_GRASS_M2 = 2.0        # smaller grass pieces of a yard get no "חצר פרטית"
YARD_MIN_PAVED_M2 = 1.0        # smaller paved pieces of a yard get no "משטח מרוצף"


def _id(eid):
    v = getattr(eid, 'Value', None)
    return int(v if v is not None else eid.IntegerValue)


# categories a vertical ray may NOT stop on when asking "what surface is seen here from above"
# (trees, furniture, railings... stand ON the ground, they are not the ground)
_NOT_GROUND = ['OST_Planting', 'OST_Entourage', 'OST_Furniture', 'OST_FurnitureSystems', 'OST_Casework',
               'OST_LightingFixtures', 'OST_Rooms', 'OST_Areas', 'OST_Railings', 'OST_RailingTopRail',
               'OST_RailingHandRail', 'OST_StairsRailing', 'OST_Doors', 'OST_Windows', 'OST_PlumbingFixtures',
               'OST_SpecialityEquipment', 'OST_ElectricalFixtures', 'OST_ElectricalEquipment',
               'OST_MechanicalEquipment', 'OST_Cameras', 'OST_Parking', 'OST_Mass']


class SeenFromAbove(object):
    """What the plan shows outdoors: a vertical ray from 1.5m above the level down to 0.9m below it,
    stopping on the first ground-like element that is visible in the plan view (any level, any
    category: floor, toposolid, generic model...). The result names the element type AND the material of
    the hit face, so grass is recognised by either. Needs a 3D view (not a template, no perspective)."""

    def __init__(self, doc, view, level_z):
        from Autodesk.Revit.DB import View3D, ReferenceIntersector, FindReferenceTarget
        self.doc, self.z = doc, level_z
        v3 = [v for v in FilteredElementCollector(doc).OfClass(View3D)
              if not v.IsTemplate and not v.IsPerspective]
        v3.sort(key=lambda v: (v.IsSectionBoxActive, v.Name != u'{3D}'))
        if not v3:
            raise ValueError('no 3D view')
        skip = set()
        for n in _NOT_GROUND:
            c = getattr(BuiltInCategory, n, None)
            if c is not None:
                skip.add(int(c))
        ids = List[ElementId]()
        for el in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
            cat = el.Category
            if cat is None or _id(cat.Id) in skip or isinstance(el, (Room, TextNote)):
                continue
            bb = el.get_BoundingBox(None)
            if bb is None or bb.Max.Z < level_z - SURFACE_BELOW or bb.Min.Z > level_z + SURFACE_ABOVE:
                continue
            ids.Add(el.Id)
        if ids.Count == 0:
            raise ValueError('no ground elements in the view')
        self.count = ids.Count
        self.view3d = v3[0].Name
        self.ri = ReferenceIntersector(ids, FindReferenceTarget.Face, v3[0])
        self.ri.FindReferencesInRevitLinks = False
        self._names = {}
        self.stats = {}

    def _label(self, ref):
        el = self.doc.GetElement(ref.ElementId)
        mat = u''
        try:
            face = el.GetGeometryObjectFromReference(ref)
            mid = getattr(face, 'MaterialElementId', None)
            if mid is not None and mid != ElementId.InvalidElementId:
                m = self.doc.GetElement(mid)
                mat = m.Name if m is not None else u''
        except Exception:
            pass
        key = (_id(el.Id), mat)
        if key not in self._names:
            t = self.doc.GetElement(el.GetTypeId())
            tn = elem_name(t) if t is not None else (el.Name or u'')
            cat = el.Category.Name if el.Category is not None else u''
            self._names[key] = u'%s | %s | %s' % (tn, mat, cat)
        return self._names[key]

    def at(self, x, y):
        """-> (top offset cm from the level, 'type | material | category') or None"""
        top = self.z + SURFACE_ABOVE
        rwc = self.ri.FindNearest(XYZ(x, y, top), XYZ(0, 0, -1))
        if rwc is None or rwc.Proximity > SURFACE_ABOVE + SURFACE_BELOW:
            return None
        nm = self._label(rwc.GetReference())
        self.stats[nm] = self.stats.get(nm, 0) + 1
        return (round((top - rwc.Proximity - self.z) * 30.48, 1), nm)


def elem_name(el):
    """Element.Name is unreliable for types in IronPython - fall back to parameters."""
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p and p.AsString():
                return p.AsString()
        except Exception:
            pass
    return u''


def _seg_cross(p1, p2, q1, q2):
    """the segments p1-p2 and q1-q2 cross (2D)"""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(q1, q2, p1), orient(q1, q2, p2)
    d3, d4 = orient(p1, p2, q1), orient(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def is_free_top(doc, wall, level):
    """the wall is NOT tied to the floor above `level` (Alon's fence rule): its top is unconnected, or
    constrained to this level (or one below) with an offset - not to a level above, and not attached.
    (v38: v37 counted only "Unconnected" tops - the walls along the ramp, "up to 00 + offset", were taken
    for building walls: no 32.85 fences, and the basement line along the south fence was dropped)"""
    try:
        a = wall.get_Parameter(BuiltInParameter.WALL_TOP_IS_ATTACHED)
        if a is not None and a.AsInteger() == 1:
            return False
        p = wall.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
        if p is None or p.AsElementId() == ElementId.InvalidElementId:
            return True
        top = doc.GetElement(p.AsElementId())
        if top is not None and top.ProjectElevation <= level.ProjectElevation + 0.1:
            return True
        # (v39, Alon 01.10.26) a fence built "up to level 01 with a minus offset" (the new 32.85 wall by the
        # parking) is still a fence: what counts is where its top really is - well below the floor above
        bb = wall.get_BoundingBox(None)
        return bb is not None and bb.Max.Z <= level.ProjectElevation + 2.5 / 0.3048
    except Exception:
        return False


def fence_segments(doc, level, min_cm=20.0, max_cm=200.0):
    """2D centre lines [((x0, y0), (x1, y1))] of the low free-standing walls around the ground floor
    (top not tied to a level, 20-200cm above the level) - they split a yard into the apartments' yards"""
    lz = level.ProjectElevation
    out = []
    for w in FilteredElementCollector(doc).OfClass(Wall):
        try:
            if not is_free_top(doc, w, level):
                continue
            c = getattr(w.Location, 'Curve', None)
            bb = w.get_BoundingBox(None)
            if c is None or bb is None:
                continue
            h = (bb.Max.Z - lz) * 30.48
            if not (min_cm - 60.0 <= h <= max_cm) or bb.Min.Z > lz + 1.0:
                continue
            e0, e1 = c.GetEndPoint(0), c.GetEndPoint(1)
            out.append(((e0.X, e0.Y), (e1.X, e1.Y)))
        except Exception:
            continue
    return out


def split_cells(cells, step, origin, segs):
    """grid cells (i, j) -> connected groups, two neighbours being apart when a fence lies between
    them (the apartments' yards inside one yard room). -> [[cells]] largest first"""
    cells = set(tuple(c) for c in cells)
    ox, oy = origin
    centre = lambda c: (ox + (c[0] + 0.5) * step, oy + (c[1] + 0.5) * step)
    near = []
    if segs and cells:
        xs = [ox + c[0] * step for c in cells]
        ys = [oy + c[1] * step for c in cells]
        x0, x1, y0, y1 = min(xs) - step, max(xs) + 2 * step, min(ys) - step, max(ys) + 2 * step
        near = [s for s in segs if max(s[0][0], s[1][0]) >= x0 and min(s[0][0], s[1][0]) <= x1 and
                max(s[0][1], s[1][1]) >= y0 and min(s[0][1], s[1][1]) <= y1]
    groups, seen = [], set()
    for c in cells:
        if c in seen:
            continue
        comp, stack = [], [c]
        seen.add(c)
        while stack:
            a = stack.pop()
            comp.append(a)
            pa = centre(a)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                b = (a[0] + d[0], a[1] + d[1])
                if b not in cells or b in seen:
                    continue
                pb = centre(b)
                if any(_seg_cross(pa, pb, s[0], s[1]) for s in near):
                    continue
                seen.add(b)
                stack.append(b)
        groups.append(comp)
    groups.sort(key=lambda g: -len(g))
    return groups


def _type_key(s):
    """type names are unique in Revit regardless of case and spaces - compare the same way"""
    return u''.join((s or u'').split()).lower()


def _all_names(el):
    names = set()
    try:
        names.add(el.Name or u'')
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p and p.AsString():
                names.add(p.AsString())
        except Exception:
            pass
    return names


def type_named(doc, cls, name):
    key = _type_key(name)
    for t in FilteredElementCollector(doc).OfClass(cls):
        if any(_type_key(n) == key for n in _all_names(t)):
            return t
    return None


def ensure_type(doc, cls, base, name, log=None):
    """-> (the type named `name`, created). The existing one is taken as is; a failed copy never raises."""
    t = type_named(doc, cls, name)
    if t is not None or base is None:
        return t, False
    try:
        return base.Duplicate(name), True
    except Exception as ex:
        if log:
            log(u'type "%s" not created: %s' % (name, ex))
        return type_named(doc, cls, name), False


def fam_type_label(doc, el):
    try:
        sym = doc.GetElement(el.GetTypeId())
        fam = getattr(sym, 'FamilyName', u'') or u''
        return u'%s : %s' % (fam, elem_name(sym))
    except Exception:
        return u''


def has_kw(text, group):
    t = text.lower()
    for k in KW[group]:
        if k in t:
            return True
    return False


def norm(s):
    """compare names ignoring all whitespace (incl. the \\x0b line break of Revit text notes) and punctuation"""
    out = []
    for ch in (s or u''):
        if ch.isspace() or ch in u'.\'"״׳\x0b\x0c':
            continue
        out.append(ch)
    return u''.join(out)


def view_phase(doc, view):
    p = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
    if p:
        return doc.GetElement(p.AsElementId())
    phases = list(doc.Phases)
    return phases[-1] if phases else None


def room_at(doc, rooms_by_id, x, y, z, phase):
    r = doc.GetRoomAtPoint(XYZ(x, y, z), phase)
    if r is not None and _id(r.Id) in rooms_by_id:
        return r
    return None


RAILING_EDGE_TOL = 1.0  # ft (~30cm): a railing counts if it runs along the room boundary


def floor_polygons(doc, floor):
    """The floor's sketch loops as lists of (x, y) points (arcs tessellated)."""
    polys = []
    try:
        sk = doc.GetElement(floor.SketchId)
        for arr in sk.Profile:
            pts = []
            for c in arr:
                for p in c.Tessellate():
                    pts.append((p.X, p.Y))
            if len(pts) >= 3:
                polys.append(pts)
    except Exception:
        pass
    return polys


def visible_footprint(doc, el, view, segments=None):
    """[minx, miny, maxx, maxy] of the geometry the element actually shows in the plan view.
    Hidden family parts (e.g. the 'usage zone' boxes of sanitary fixtures) are left out.
    None when nothing visible was found.
    If `segments` (a list) is given, the visible lines are appended to it as (x1, y1, x2, y2) - the
    2D drawing itself, used to find empty spots for labels."""
    opts = Options()
    opts.View = view
    xs, ys = [], []

    def add(pts):
        for p in pts:
            xs.append(p.X)
            ys.append(p.Y)
        if segments is not None:
            for k in range(len(pts) - 1):
                segments.append((pts[k].X, pts[k].Y, pts[k + 1].X, pts[k + 1].Y))

    def hidden(obj):
        try:
            if obj.Visibility != Visibility.Visible:
                return True
        except Exception:
            pass
        try:
            gs = doc.GetElement(obj.GraphicsStyleId)
            cat = gs.GraphicsStyleCategory if gs is not None else None
            if cat is not None and view.GetCategoryHidden(cat.Id):
                return True
        except Exception:
            pass
        return False

    def walk(geo):
        if geo is None:
            return
        for obj in geo:
            if isinstance(obj, GeometryInstance):
                walk(obj.GetInstanceGeometry())
                continue
            if hidden(obj):
                continue
            if isinstance(obj, Curve):
                add(list(obj.Tessellate()))
            elif isinstance(obj, Solid) and obj.Volume > 0:
                for e in obj.Edges:
                    add(list(e.Tessellate()))

    try:
        walk(el.get_Geometry(opts))
    except Exception:
        return None
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def top_face_polygons(doc, el, view=None, min_area=0.5):
    """plan polygons of the upward-facing faces of the element's solids (e.g. a kitchen counter),
    each face >= min_area sq ft. [] when none.
    The 3D model geometry is used (no view): in plan views kitchen families usually show only 2D
    symbolic lines, which have no faces."""
    opts = Options()
    if view is not None:
        opts.View = view
    else:
        opts.DetailLevel = ViewDetailLevel.Fine
    polys = []

    def walk(geo):
        if geo is None:
            return
        for obj in geo:
            if isinstance(obj, GeometryInstance):
                walk(obj.GetInstanceGeometry())
                continue
            if not isinstance(obj, Solid) or obj.Volume <= 0:
                continue
            for f in obj.Faces:
                try:
                    n = f.ComputeNormal(UV(0.5, 0.5))
                except Exception:
                    continue
                if n.Z < 0.9 or f.Area < min_area:
                    continue
                for loop in f.EdgeLoops:
                    pts = []
                    for e in loop:
                        for p in e.Tessellate():
                            pts.append((p.X, p.Y))
                    if len(pts) >= 3:
                        polys.append(pts)
                    break  # outer loop only

    try:
        walk(el.get_Geometry(opts))
    except Exception:
        return []
    return polys


def l_kitchen_corner(polys, bb):
    """If the counter footprint is an L inside its box, return (inner corner [x, y], (sx, sy)) where
    (sx, sy) points from the corner into the free quadrant; else None."""
    if not polys:
        return None
    if not bb:
        bb = [min(p[0] for q in polys for p in q), min(p[1] for q in polys for p in q),
              max(p[0] for q in polys for p in q), max(p[1] for q in polys for p in q)]
    x0, y0, x1, y1 = bb
    w, h = x1 - x0, y1 - y0
    if w < 2.0 or h < 2.0:
        return None

    def occ(x, y):
        # each face on its own: overlapping faces (counter + hob) must not cancel each other out
        return any(point_in_polygons(x, y, [p]) for p in polys)

    e = 0.2  # ft inset from the box corners
    corners = {(-1, -1): occ(x0 + e, y0 + e), (1, -1): occ(x1 - e, y0 + e),
               (1, 1): occ(x1 - e, y1 - e), (-1, 1): occ(x0 + e, y1 - e)}
    free = [k for k, v in corners.items() if not v]
    if len(free) != 1:
        return None                      # not an L (a line, an island, or something else)
    fx, fy = free[0]                     # the free corner; the L corner is the opposite one
    lx, ly = -fx, -fy
    cx = x1 if lx > 0 else x0            # outer corner of the L
    cy = y1 if ly > 0 else y0
    step = 0.05
    # depth of the arm that runs along x (measured at the free corner's x), and of the arm along y
    probe_x = x0 + e if fx < 0 else x1 - e
    d = 0.0
    while d < h and occ(probe_x, cy - ly * (d + step / 2.0)):
        d += step
    depth_x_arm = d
    probe_y = y0 + e if fy < 0 else y1 - e
    d = 0.0
    while d < w and occ(cx - lx * (d + step / 2.0), probe_y):
        d += step
    depth_y_arm = d
    if depth_x_arm <= 0 or depth_y_arm <= 0:
        return None
    inner = [cx - lx * depth_y_arm, cy - ly * depth_x_arm]
    return inner, (fx, fy)


def grid_clusters(cells, step, min_cells=3, min_area_m2=None, origin=None):
    """cells {(ix, iy): (x, y)} -> [{'pt': [x, y], 'area_m2': a, 'cells': [...], 'step', 'origin'}] one per
    8-connected patch (at least min_cells cells / min_area_m2). 'pt' is the patch cell closest to the patch
    centroid (always inside the patch). 'cells' + 'origin' let a label be kept fully inside the patch:
    cell (i, j) covers x in origin.x + (i -/+ 0.5) * step."""
    if min_area_m2 is not None:
        min_cells = max(1, int(min_area_m2 / (step * step * SQFT_TO_M2)))
    seen = set()
    out = []
    for start in cells:
        if start in seen:
            continue
        stack, patch = [start], []
        seen.add(start)
        while stack:
            c = stack.pop()
            patch.append(c)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (c[0] + dx, c[1] + dy)
                    if n in cells and n not in seen:
                        seen.add(n)
                        stack.append(n)
        if len(patch) < min_cells:
            continue
        pts = [cells[c] for c in patch]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        best = min(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        out.append({'pt': [best[0], best[1]], 'centroid': [cx, cy],
                    'area_m2': round(len(pts) * step * step * SQFT_TO_M2, 1),
                    'cells': [list(c) for c in patch], 'step': step, 'origin': origin})
    return out


def room_outline(room):
    """outer boundary loop of a room as [(x, y)] (feet), arcs tessellated"""
    try:
        best = None
        for loop in room.GetBoundarySegments(SpatialElementBoundaryOptions()):
            pts = []
            for seg in loop:
                for p in seg.GetCurve().Tessellate():
                    pts.append((p.X, p.Y))
            if best is None or abs(_signed_area(pts)) > abs(_signed_area(best)):
                best = pts
        return best or []
    except Exception:
        return []


def _signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def polygon_centroid(pts):
    if not pts or len(pts) < 3:
        return None
    a = _signed_area(pts)
    if abs(a) < 1e-9:
        return None
    cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return [cx / (6.0 * a), cy / (6.0 * a)]


def point_in_polygons(x, y, polys):
    """even-odd rule over all loops, so openings/holes in the floor are respected"""
    inside = False
    for pts in polys:
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def boundary_curves(room):
    curves = []
    try:
        for loop in room.GetBoundarySegments(SpatialElementBoundaryOptions()):
            for seg in loop:
                curves.append(seg.GetCurve())
    except Exception:
        pass
    return curves


def railing_on_perimeter(rail_paths, curves):
    """True if some railing path runs along the room boundary (its midpoint lies within tolerance)."""
    if not curves:
        return False
    z = curves[0].GetEndPoint(0).Z
    for path in rail_paths:
        for c in path:
            m = c.Evaluate(0.5, True)
            p = XYZ(m.X, m.Y, z)
            for bc in curves:
                try:
                    if bc.Distance(p) <= RAILING_EDGE_TOL:
                        return True
                except Exception:
                    continue
    return False


def boundary_wall_types(doc, room):
    names = set()
    try:
        for loop in room.GetBoundarySegments(SpatialElementBoundaryOptions()):
            for seg in loop:
                w = doc.GetElement(seg.ElementId)
                if isinstance(w, Wall):
                    names.add(elem_name(w.WallType))
    except Exception:
        pass
    return sorted(names)


def guess_name(it):
    """returns (main_name, extra_labels, rule)"""
    f = it['flags']
    empty = not it['contents']
    if f.get('stairs'):
        return u'ח. מדרגות', [], 'stairs'
    if it.get('elevator_inner_side'):
        # office rule (Alon, 29.09.26): "פיר מעלית" for one elevator, "פיר מעליות" for more
        n = it.get('elevator_count', 1)
        return (u'פיר מעליות' if n > 1 else u'פיר מעלית'), [], 'inner side of elevator door (%d elevator(s))' % n
    # office rule: the lobby is the space on the outer side of the elevator door
    if it.get('elevator_outer_side'):
        if it.get('entrance_level'):
            return u'לובי כניסה', [], 'outer side of elevator door, entrance level'
        return u'לובי קומתי', [], 'outer side of elevator door'
    # office rules for outdoor ground: grass floor next to an apartment = private yard (its paved
    # part = "משטח מרוצף"); grass floor not next to an apartment = shared garden
    if it.get('has_landscape'):
        if it.get('touches_apartment'):
            extras = [u'משטח מרוצף'] if it.get('has_paving') else []
            return u'חצר פרטית', extras, 'grass floor + next to apartment %s' % it.get('touches_apartment')
        return u'גינון משותף', [], 'grass floor, not next to an apartment'
    if it.get('touches_private_yard') and not it.get('apartment'):
        return u'משטח מרוצף', [], 'paved space next to a private yard'
    # office rule: the balcony floor type name identifies a balcony; a railing along all or part of
    # the perimeter is only a double check (reported), never a reason on its own
    if it.get('balcony_floor_name'):
        if it.get('railing_on_perimeter'):
            return u'מרפסת', [], 'balcony floor type (double check: railing on perimeter)'
        return u'מרפסת', [], 'balcony floor type (double check failed: no railing on perimeter)'
    # office rule: a bathroom takes its name from the bedroom it opens into
    # (parents bedroom -> "הורים", regular bedroom -> plain "ח.רחצה"), otherwise it is "כללי"
    if f.get('bath') or f.get('shower'):
        if it.get('opens_to_parents_bedroom'):
            return u'ח.רחצה הורים', [], 'bath/shower + door to parents bedroom'
        if it.get('opens_to_bedroom'):
            return u'ח.רחצה', [], 'bath/shower + door to regular bedroom'
        return u'ח.רחצה כללי', [], 'bath/shower, not attached to a bedroom'
    in_apt = bool(it.get('apartment'))
    if f.get('toilet'):
        if in_apt:
            return u'ש.אורחים', [], 'toilet without bath/shower, inside an apartment'
        return u'שרותים', [], 'toilet without bath/shower, common area'
    if f.get('kitchen') and not in_apt:
        return SERVICE_ROOM, [], 'kitchen in a common (non-apartment) space'
    # a room with a bed and no kitchen is a bedroom even if it also has an armchair and a small table
    if f.get('kitchen') or (f.get('sofa') and f.get('dining') and not f.get('bed')):
        extras = []
        if f.get('kitchen'):
            extras.append(u'מטבח')
        if f.get('dining'):
            extras.append(u'פ.אוכל')
        if f.get('sofa'):
            return LIVING_NAME, extras, 'open space: sofa/kitchen/dining'
        return u'מטבח', [x for x in extras if x != u'מטבח'], 'kitchen items'
    if it.get('mamad'):
        return u'ח.שינה/ממ"ד', [], it.get('mamad_reason', 'mamad floor')
    # office rule: at most one parents bedroom per apartment, chosen among the double-bed bedrooms.
    # a bed wins over an armchair/sofa in the same room
    if f.get('bed'):
        if it.get('parents_bedroom'):
            return u'ח.שינה הורים', [], it.get('parents_reason', 'parents bedroom')
        if f.get('double_bed'):
            return u'ח.שינה', [], 'double bed, but apartment already has its parents bedroom'
        return u'ח.שינה', [], 'single bed'
    if f.get('sofa'):
        return LIVING_NAME, [], 'sofa'
    if f.get('washer'):
        return u'ח.שרות', [], 'washing machine'
    # office rule: a boiler room holds more than one boiler ("דוד" / "boiler" families)
    n_boilers = sum(1 for i in it.get('items', []) if 'boiler' in i['groups'])
    if n_boilers >= 2:
        return u'ח. דוודים', [], '%d boilers' % n_boilers
    if it['area_m2'] < 2.0 and empty:
        return u'פיר', [], 'small & empty'
    if not in_apt:
        return SERVICE_ROOM, [], 'common space with no identifying content'
    return u'?', [], 'no rule'


def _analyse(doc, view, level, phase, new_ids, log, detailed=True):
    z_probe = level.ProjectElevation + 1.0  # 1 ft above level
    rooms = [r for r in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms)
             .WhereElementIsNotElementType() if isinstance(r, Room) and r.LevelId == level.Id and r.Area > 0]
    rooms_by_id = dict((_id(r.Id), r) for r in rooms)
    log(u'חדרים זמניים בקומה: %d' % len(rooms))

    info = {}
    for r in rooms:
        c = r.Location.Point if r.Location else None
        # label point: area centroid of the room outline when it lies inside the room,
        # else the centre of its box, else Revit's room point
        poly = room_outline(r)
        lp = None
        cen = polygon_centroid(poly)
        if cen is not None and r.IsPointInRoom(XYZ(cen[0], cen[1], z_probe)):
            lp = cen
        if lp is None:
            rbb = r.get_BoundingBox(None)
            if rbb is not None:
                m = (rbb.Min + rbb.Max) * 0.5
                if r.IsPointInRoom(XYZ(m.X, m.Y, z_probe)):
                    lp = [m.X, m.Y]
        if lp is None and c is not None:
            lp = [c.X, c.Y]
        info[_id(r.Id)] = {'id': _id(r.Id), 'area_m2': round(r.Area * SQFT_TO_M2, 2), 'flags': {},
                           'contents': [], 'entrance_doors': 0, 'truth_texts': [],
                           'doors': [], 'floor_types': [], 'items': [],
                           'label_pt': lp, 'outline': poly,
                           'point_cm': [round(c.X * 30.48), round(c.Y * 30.48)] if c else None}

    # contents
    cat_ids = List[ElementId]([ElementId(c) for c in CONTENT_CATS])
    for el in FilteredElementCollector(doc).WherePasses(ElementMulticategoryFilter(cat_ids)) \
            .WhereElementIsNotElementType():
        bb = el.get_BoundingBox(None)
        if bb is None:
            continue
        if not (el.LevelId == level.Id or (bb.Min.Z - 0.5 <= z_probe <= bb.Max.Z)):
            continue
        c = (bb.Min + bb.Max) * 0.5
        r = room_at(doc, rooms_by_id, c.X, c.Y, z_probe, phase)
        if r is None:
            continue
        label = fam_type_label(doc, el)
        it = info[_id(r.Id)]
        it['contents'].append(u'%s | %s' % (el.Category.Name if el.Category else u'?', label))
        groups = [g for g in KW if has_kw(label, g)]
        for g in groups:
            it['flags'][g] = True
        low = label.lower()
        plan_segs = []
        plan_bb = visible_footprint(doc, el, view, plan_segs)
        l_corner = None
        l_debug = None
        if 'kitchen' in groups:
            # an L-shaped counter: its inner corner (the "מטבח" label sits 30cm off both arms).
            # The footprint comes from the 3D faces; its own bounds are used as the box.
            faces = top_face_polygons(doc, el)
            l_corner = l_kitchen_corner(faces, None)
            l_debug = {'top_faces': len(faces), 'is_L': l_corner is not None}
            if faces:
                # the counter's own bounds (used to put the label in front of a line kitchen)
                l_debug['counter_bb'] = [min(p[0] for q in faces for p in q), min(p[1] for q in faces for p in q),
                                         max(p[0] for q in faces for p in q), max(p[1] for q in faces for p in q)]
        it['items'].append({'label': label, 'groups': groups, 'pt': [c.X, c.Y],  # feet, model coords
                            'bb': [bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y],
                            # what the plan shows (without hidden usage zones) - used to place labels
                            'plan_bb': plan_bb,
                            'plan_segs': plan_segs,   # the lines actually drawn in the plan
                            'l_corner': l_corner, 'l_debug': l_debug,
                            'plumbing': el.Category is not None and
                                        el.Category.Id.IntegerValue == int(BuiltInCategory.OST_PlumbingFixtures),
                            'dining_table': any(k in low for k in DINING_TABLE_KW)})

    for st in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Stairs).WhereElementIsNotElementType():
        bb = st.get_BoundingBox(None)
        if bb is None or not (bb.Min.Z - 0.5 <= z_probe <= bb.Max.Z):
            continue
        c = (bb.Min + bb.Max) * 0.5
        r = room_at(doc, rooms_by_id, c.X, c.Y, z_probe, phase)
        if r is None:
            continue
        # a stair room is one the stairs fill a real share of; a big room that only touches the
        # top of a stair (e.g. basement escape stairs) keeps its own name
        share = (bb.Max.X - bb.Min.X) * (bb.Max.Y - bb.Min.Y) / r.Area if r.Area else 0
        it = info[_id(r.Id)]
        it['stairs_share'] = round(max(share, it.get('stairs_share', 0)), 2)
        if share >= STAIRS_MIN_SHARE:
            it['flags']['stairs'] = True

    # doors
    door_links = []
    elevator_doors = []
    elevator_log = []
    for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not isinstance(d, FamilyInstance) or d.LevelId != level.Id:
            continue
        try:
            fr = d.get_FromRoom(phase)
            to = d.get_ToRoom(phase)
        except Exception:
            continue
        label = fam_type_label(doc, d)
        low = label.lower()
        is_entr = u'entrance' in low or u'כניסה' in low
        is_elev = any(k in low for k in ELEVATOR_DOOR_KW)
        # the door as drawn in the plan (leaf + swing arc) - an obstacle for labels in both rooms
        door_segs = []
        door_bb = visible_footprint(doc, d, view, door_segs)
        for r in (fr, to):
            if r is None or _id(r.Id) not in info:
                continue
            it = info[_id(r.Id)]
            it['doors'].append(label)
            if door_bb:
                it['items'].append({'label': label, 'groups': [], 'bb': door_bb, 'plan_bb': door_bb,
                                    'plan_segs': door_segs,
                                    'pt': [(door_bb[0] + door_bb[2]) / 2.0, (door_bb[1] + door_bb[3]) / 2.0],
                                    'plumbing': False, 'dining_table': False, 'door': True})
            if is_entr:
                it['entrance_doors'] += 1
                # a point just inside this room, in front of the entrance door (apartment number label)
                try:
                    loc = d.Location.Point
                    fo = d.FacingOrientation
                    for sgn in (1.0, -1.0):
                        q = XYZ(loc.X + sgn * fo.X * ENTRY_LABEL_OFFSET, loc.Y + sgn * fo.Y * ENTRY_LABEL_OFFSET, z_probe)
                        if r.IsPointInRoom(q):
                            it.setdefault('entry_label_pts', []).append([q.X, q.Y])
                            # the door itself (regular-floor rule: the number sits 20cm past the door as
                            # drawn - leaf and swing - centred on it): its centre, the direction into the
                            # apartment and how far its drawing reaches into the apartment
                            dx, dy = sgn * fo.X, sgn * fo.Y
                            reach = 0.0
                            for s in door_segs:
                                for px, py in ((s[0], s[1]), (s[2], s[3])):
                                    reach = max(reach, (px - loc.X) * dx + (py - loc.Y) * dy)
                            it.setdefault('entry_doors', []).append(
                                {'loc': [loc.X, loc.Y], 'dir': [dx, dy], 'reach': reach})
                            break
                except Exception:
                    pass
        if fr is not None and to is not None:
            door_links.append((_id(fr.Id), _id(to.Id), is_entr or is_elev))
        elif (fr is None) != (to is None):
            # a door with a room on one side only leads outside (e.g. the building entrance)
            inside = fr if fr is not None else to
            if _id(inside.Id) in info:
                try:
                    loc = d.Location.Point
                    info[_id(inside.Id)].setdefault('exterior_door_pts', []).append([loc.X, loc.Y])
                except Exception:
                    pass
        if is_elev:
            elevator_doors.append(d)

    # spot elevations drawn in the view, per room (the entrance lobby label sits just above one)
    for sp in FilteredElementCollector(doc, view.Id).OfClass(SpotDimension):
        try:
            o = sp.Origin
            vb = sp.get_BoundingBox(view)
        except Exception:
            continue
        if o is None or vb is None:
            continue
        r = room_at(doc, rooms_by_id, o.X, o.Y, z_probe, phase)
        if r is not None:
            try:
                val = sp.ValueString
            except Exception:
                val = None
            info[_id(r.Id)].setdefault('spots', []).append(
                {'origin': [o.X, o.Y], 'bb': [vb.Min.X, vb.Min.Y, vb.Max.X, vb.Max.Y], 'value': val})

    # elevator = the space on the inner side of an elevator door.
    # In the example project ("דלת מעלית") the inner side is the one the FacingOrientation points to;
    # if that side turns out to be the floor lobby (2+ entrance doors) the sides are swapped.
    # Both sides are logged for review.
    for d in elevator_doors:
        loc = d.Location.Point
        fo = d.FacingOrientation
        step = 2.0  # ft (~60cm) from the wall centre line
        back = room_at(doc, rooms_by_id, loc.X - fo.X * step, loc.Y - fo.Y * step, z_probe, phase)
        front = room_at(doc, rooms_by_id, loc.X + fo.X * step, loc.Y + fo.Y * step, z_probe, phase)
        inner, outer = front, back
        if inner is not None and info[_id(inner.Id)]['entrance_doors'] >= 2:
            inner, outer = front, back
        elevator_log.append({'door_id': _id(d.Id), 'back_side_room': _id(back.Id) if back else None,
                             'front_side_room': _id(front.Id) if front else None,
                             'chosen_inner': _id(inner.Id) if inner else None})
        if inner is not None:
            info[_id(inner.Id)]['elevator_inner_side'] = True
            info[_id(inner.Id)].setdefault('elevator_positions', set()).add((round(loc.X), round(loc.Y)))
        if outer is not None:
            info[_id(outer.Id)]['elevator_outer_side'] = True

    # office rule (Alon, 29.09.26): the elevator shaft continues up to the roof and the upper roof, where
    # there are no elevator doors - a small room at the plan position of an elevator door of a level below
    # is the shaft too. One elevator = one door position (the same door repeats on every floor).
    lz = level.ProjectElevation
    for d in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not isinstance(d, FamilyInstance) or d.LevelId == level.Id:
            continue
        lv = doc.GetElement(d.LevelId)
        if lv is None or lv.ProjectElevation >= lz - 0.5:
            continue
        if not any(k in fam_type_label(doc, d).lower() for k in ELEVATOR_DOOR_KW):
            continue
        try:
            loc, fo = d.Location.Point, d.FacingOrientation
        except Exception:
            continue
        # the inner side, as on the door's own level (FacingOrientation points into the car)
        shaft = room_at(doc, rooms_by_id, loc.X + fo.X * 2.0, loc.Y + fo.Y * 2.0, z_probe, phase)
        if shaft is None or shaft.Area * SQFT_TO_M2 >= SHAFT_MAX_M2:
            continue
        it = info[_id(shaft.Id)]
        if it.get('elevator_outer_side'):
            continue                          # the lobby of this level, not the shaft
        it['elevator_inner_side'] = True
        it.setdefault('elevator_positions', set()).add((round(loc.X), round(loc.Y)))
    for it in info.values():
        if it.get('elevator_positions'):
            it['elevator_count'] = len(it.pop('elevator_positions'))

    # railings on this level, checked against each room's boundary
    rail_paths = []
    for rl in FilteredElementCollector(doc).OfClass(Railing):
        bb = rl.get_BoundingBox(None)
        if bb is None or not (bb.Min.Z - 0.5 <= z_probe <= bb.Max.Z + 0.5):
            continue
        try:
            rail_paths.append(list(rl.GetPath()))
        except Exception:
            continue
    for rid, it in info.items():
        it['railing_on_perimeter'] = railing_on_perimeter(rail_paths, boundary_curves(rooms_by_id[rid]))

    # the floor under each room: exact point-in-sketch test on every floor hosted on this level.
    # If several floors contain the point (e.g. slab + finish), the highest one is the finish floor.
    floors = []
    for fl in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType():
        if fl.LevelId != level.Id:
            continue
        bb = fl.get_BoundingBox(None)
        if bb is None:
            continue
        p = fl.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        off = round(p.AsDouble() * 30.48, 1) if p else 0.0
        floors.append((bb, floor_polygons(doc, fl), elem_name(doc.GetElement(fl.GetTypeId())), off))
    for rid, it in info.items():
        p = it['point_cm']
        it['wall_types'] = boundary_wall_types(doc, rooms_by_id[rid])
        if p is None:
            continue
        x, y = p[0] / 30.48, p[1] / 30.48
        hits = []
        for bb, polys, nm, off in floors:
            if not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y):
                continue
            if polys and point_in_polygons(x, y, polys):
                hits.append((off, nm))
        hits.sort(reverse=True)
        it['floor_types'] = [u'%s (%+.1f)' % (nm, off) for off, nm in hits]
        if not hits:
            continue
        off, nm = hits[0]
        it['floor_type'], it['floor_offset_cm'] = nm, off
        low = nm.lower()
        # office rule: mamad = floor named mamad; its +2cm height is only a double check,
        # evaluated after the apartments are known (ground floor apartments may be raised)
        if any(k in low for k in MAMAD_KW):
            it['mamad'] = True
        it['balcony_floor_name'] = any(k in low for k in BALCONY_KW)

    # What is seen from above outdoors: the floors of this level PLUS floors hosted on other levels and
    # toposolids whose top lies near this level (site paving is often hosted elsewhere). Ranked by the
    # real top height, so grass covered by paving does not count as grass.
    surfaces = list(floors)
    lev_z = level.ProjectElevation
    site_cats = [BuiltInCategory.OST_Floors]
    if hasattr(BuiltInCategory, 'OST_Toposolid'):
        site_cats.append(getattr(BuiltInCategory, 'OST_Toposolid'))
    for bic in site_cats:
        for el in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
            if bic == BuiltInCategory.OST_Floors and el.LevelId == level.Id:
                continue      # already in `floors`
            bb = el.get_BoundingBox(None)
            if bb is None or not (lev_z - SURFACE_BELOW <= bb.Max.Z <= lev_z + SURFACE_ABOVE):
                continue
            polys = floor_polygons(doc, el) if hasattr(el, 'SketchId') else []
            if not polys:
                polys = [[(bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Min.Y), (bb.Max.X, bb.Max.Y), (bb.Min.X, bb.Max.Y)]]
            surfaces.append((bb, polys, elem_name(doc.GetElement(el.GetTypeId())),
                             round((bb.Max.Z - lev_z) * 30.48, 1)))

    # all floor types inside each room (grid sample) - a yard room can hold grass and paving together
    def top_floor_at(x, y):
        best = None
        for bb, polys, nm, off in surfaces:
            if bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y and polys \
                    and point_in_polygons(x, y, polys):
                if best is None or off > best[0]:
                    best = (off, nm)
        return best

    # the labels on outdoor surfaces use what is really seen from above (a ray), not the floor sketches:
    # a grass floor running under the paving must not count as grass there
    seen = None
    if detailed:
        try:
            seen = SeenFromAbove(doc, view, lev_z)
            log(u'seen-from-above: %d ground elements, 3D view "%s"' % (seen.count, seen.view3d))
        except Exception as ex:
            log(u'seen-from-above unavailable (%s) - using floor sketches' % ex)

    ray = {'calls': 0, 'none': 0, 'errors': 0, 'first_error': u''}

    def seen_at(x, y):
        """the ray's answer; where the ray finds nothing (or fails) - the floor sketches"""
        if seen is not None:
            ray['calls'] += 1
            try:
                r = seen.at(x, y)
                if r is not None:
                    return r
                ray['none'] += 1
            except Exception as ex:
                ray['errors'] += 1
                if not ray['first_error']:
                    ray['first_error'] = u'%s' % ex
        return top_floor_at(x, y)

    for rid, it in info.items():
        room = rooms_by_id[rid]
        rbb = room.get_BoundingBox(None)
        if rbb is None:
            continue
        step = FLOOR_SAMPLE_STEP
        while ((rbb.Max.X - rbb.Min.X) / step) * ((rbb.Max.Y - rbb.Min.Y) / step) > 600:
            step *= 1.5
        names = set()
        grass, paved = {}, {}   # grid cell -> (x, y): where the room is grass / paved
        ix = 0
        x = rbb.Min.X + step / 2
        while x < rbb.Max.X:
            iy = 0
            y = rbb.Min.Y + step / 2
            while y < rbb.Max.Y:
                if room.IsPointInRoom(XYZ(x, y, z_probe)):
                    tf = top_floor_at(x, y)
                    if tf is not None:
                        names.add(tf[1])
                        if any(k in tf[1].lower() for k in LANDSCAPE_KW):
                            grass[(ix, iy)] = (x, y)
                        else:
                            paved[(ix, iy)] = (x, y)
                y += step
                iy += 1
            x += step
            ix += 1
        it['floor_mix'] = sorted(names)
        land = [n for n in names if any(k in n.lower() for k in LANDSCAPE_KW)]
        it['has_landscape'] = bool(land)
        it['has_paving'] = bool(land) and len(names) > len(land)
        if land and not detailed:
            it['grass_patches'] = grid_clusters(grass, step)
            it['paved_patches'] = grid_clusters(paved, step)
        elif land:
            # a finer map of the yard's grass / paving (labels must sit fully on the right surface);
            # the patches are built after the apartments are known (a yard may touch two apartments)
            fs = YARD_STEP
            while ((rbb.Max.X - rbb.Min.X) / fs) * ((rbb.Max.Y - rbb.Min.Y) / fs) > 8000:
                fs *= 1.25
            fgrass, fpaved = {}, {}
            ox, oy = rbb.Min.X, rbb.Min.Y
            for i in range(int((rbb.Max.X - ox) / fs) + 1):
                x = ox + (i + 0.5) * fs
                for j in range(int((rbb.Max.Y - oy) / fs) + 1):
                    y = oy + (j + 0.5) * fs
                    if not room.IsPointInRoom(XYZ(x, y, z_probe)):
                        continue
                    tf = seen_at(x, y)
                    if tf is None:
                        continue
                    if any(k in tf[1].lower() for k in LANDSCAPE_KW):
                        fgrass[(i, j)] = (x, y)
                    else:
                        fpaved[(i, j)] = (x, y)
            it['_yard_cells'] = (fgrass, fpaved, fs, [ox, oy])

    # grass that lies in no room at all (e.g. an open shared garden) -> "גינון משותף" patches.
    # A fine grid, so grass parts split by a path become separate patches (each gets its own label)
    open_grass = {}
    step = OPEN_GRASS_STEP if detailed else FLOOR_SAMPLE_STEP * 2
    # candidates: every grass-named surface (any level); what is really grass is decided by seen_at
    land_floors = [f for f in surfaces if any(k in f[2].lower() for k in LANDSCAPE_KW)]
    for bb, polys, nm, off in land_floors:
        log(u'grass surface "%s" %+.1fcm, x %d..%d y %d..%d cm, %d loops' % (
            nm, off, bb.Min.X * 30.48, bb.Max.X * 30.48, bb.Min.Y * 30.48, bb.Max.Y * 30.48, len(polys)))
    if seen is not None:
        seen.stats = {}
    gx0 = gy0 = 0.0
    if land_floors:
        gx0 = min(f[0].Min.X for f in land_floors)
        gy0 = min(f[0].Min.Y for f in land_floors)
        for bb, polys, nm, off in land_floors:
            if not polys:
                continue
            for i in range(int((bb.Min.X - gx0) / step), int((bb.Max.X - gx0) / step) + 1):
                x = gx0 + (i + 0.5) * step
                for j in range(int((bb.Min.Y - gy0) / step), int((bb.Max.Y - gy0) / step) + 1):
                    y = gy0 + (j + 0.5) * step
                    if (i, j) in open_grass or not point_in_polygons(x, y, polys):
                        continue
                    if doc.GetRoomAtPoint(XYZ(x, y, z_probe), phase) is not None:
                        continue
                    tf = seen_at(x, y)
                    if tf is not None and any(k in tf[1].lower() for k in LANDSCAPE_KW):
                        open_grass[(i, j)] = (x, y)
    open_grass_patches = grid_clusters(open_grass, step, min_area_m2=OPEN_GARDEN_MIN_M2, origin=[gx0, gy0])
    if seen is not None:
        top = sorted(seen.stats.items(), key=lambda kv: -kv[1])[:8]
        log(u'open grass area, seen from above: ' + u' || '.join(u'%s x%d' % kv for kv in top))
        log(u'rays (yards + open grass): %d calls, %d found nothing, %d errors %s' % (
            ray['calls'], ray['none'], ray['errors'], ray['first_error']))
    for bb, polys, nm, off in surfaces:
        if bb.Max.Y * 30.48 > 3000 and not any(k in nm.lower() for k in LANDSCAPE_KW):
            log(u'other surface (north) "%s" %+.1fcm, x %d..%d y %d..%d cm' % (
                nm, off, bb.Min.X * 30.48, bb.Max.X * 30.48, bb.Min.Y * 30.48, bb.Max.Y * 30.48))
    log(u'open grass patches: %s' % [(round(p['area_m2'], 1), [round(v * 30.48) for v in p['pt']])
                                     for p in open_grass_patches])

    # neighbours = rooms that share a bounding element (wall / separation line)
    owners = {}
    for rid in info:
        try:
            for loop in rooms_by_id[rid].GetBoundarySegments(SpatialElementBoundaryOptions()):
                for seg in loop:
                    owners.setdefault(_id(seg.ElementId), set()).add(rid)
        except Exception:
            pass
    neighbours = dict((rid, set()) for rid in info)
    for eid, rids in owners.items():
        if eid < 0:
            continue
        for a in rids:
            neighbours[a].update(r for r in rids if r != a)

    def is_bed(rid):
        return info[rid]['flags'].get('bed')

    def is_bath(rid):
        f = info[rid]['flags']
        return f.get('bath') or f.get('shower')

    # bathroom <-> bedroom relations through doors
    ensuite_of = {}  # bedroom id -> [bathroom ids]
    for a, b, _ in door_links:
        if a in info and b in info:
            for x, y in ((a, b), (b, a)):
                if is_bath(x) and is_bed(y):
                    info[x]['opens_to_bedroom'] = y
                    ensuite_of.setdefault(y, []).append(x)

    # apartments = rooms connected through interior doors (entrance / elevator doors are cut)
    adj = dict((rid, set()) for rid in info)
    for a, b, cut in door_links:
        if not cut and a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    apt = 0
    for rid in info:
        if 'apartment' in info[rid] or info[rid]['entrance_doors'] == 0:
            continue
        if info[rid]['entrance_doors'] >= 2:
            continue  # the floor lobby is not an apartment
        apt += 1
        stack = [rid]
        while stack:
            cur = stack.pop()
            if 'apartment' in info[cur]:
                continue
            info[cur]['apartment'] = apt
            stack.extend(n for n in adj[cur] if 'apartment' not in info[n])

    # an "apartment" without any bedroom or mamad is a common space (e.g. residents' room)
    members = {}
    for rid, it in info.items():
        if it.get('apartment'):
            members.setdefault(it['apartment'], []).append(rid)
    for a, rids in members.items():
        if not any(is_bed(r) or info[r].get('mamad') for r in rids):
            for r in rids:
                del info[r]['apartment']
                info[r]['common_space'] = True

    # mamad double check: +2cm relative to the apartment's main floor (the most common offset
    # among the apartment's non-mamad rooms), or relative to the level when no apartment is known
    base = {}
    for a, rids in members.items():
        offs = [info[r]['floor_offset_cm'] for r in rids
                if info[r].get('apartment') and not info[r].get('mamad') and 'floor_offset_cm' in info[r]]
        if offs:
            base[a] = max(set(offs), key=offs.count)
    for rid, it in info.items():
        if not it.get('mamad'):
            continue
        off = it.get('floor_offset_cm', 0.0)
        ref = base.get(it.get('apartment'), 0.0)
        rel = off - ref
        ok = MAMAD_OFFSET_CM[0] <= rel <= MAMAD_OFFSET_CM[1]
        it['mamad_reason'] = 'mamad floor type (double check %s: %+.1fcm above apartment floor)' % (
            'ok' if ok else 'FAILED', rel)

    # office rule: at most one parents bedroom per apartment - among the double-bed bedrooms
    # the one with an attached bathroom wins; if several have one, the larger attached bathroom wins
    # (matches the example: 10.7m2 room + 4.1m2 bath is parents, 11.8m2 room + 3.3m2 bath is not)
    def ensuite_area(r):
        return max([info[b]['area_m2'] for b in ensuite_of.get(r, [])] or [0])

    by_apt = {}
    for rid, it in info.items():
        if it.get('apartment') and it['flags'].get('double_bed') and is_bed(rid) and not it.get('mamad'):
            by_apt.setdefault(it['apartment'], []).append(rid)
    for a, cands in by_apt.items():
        cands.sort(key=lambda r: (1 if r in ensuite_of else 0, ensuite_area(r), info[r]['area_m2']),
                   reverse=True)
        win = cands[0]
        info[win]['parents_bedroom'] = True
        info[win]['parents_reason'] = 'double bed; parents bedroom of apartment %d (of %d double-bed rooms%s)' % (
            a, len(cands), ', has attached bathroom' if win in ensuite_of else '')
        for bath in ensuite_of.get(win, []):
            info[bath]['opens_to_parents_bedroom'] = True
    # a double-bed bedroom that could not be assigned to an apartment keeps the simple rule
    for rid, it in info.items():
        if not it.get('apartment') and it['flags'].get('double_bed') and is_bed(rid) and not it.get('mamad'):
            it['parents_bedroom'] = True
            it['parents_reason'] = 'double bed (apartment not identified)'
            for bath in ensuite_of.get(rid, []):
                info[bath]['opens_to_parents_bedroom'] = True

    # outdoor relations: a grass room next to an apartment is a private yard; a paved room next to
    # such a yard is "משטח מרוצף"
    entrance_level = abs(level.Elevation) < 0.05
    for rid, it in info.items():
        it['entrance_level'] = entrance_level
        apts = sorted(set(info[n]['apartment'] for n in neighbours[rid] if info[n].get('apartment')))
        if apts:
            it['touches_apartment'] = apts[0]
        it['apartments_touching'] = apts
        if '_yard_cells' in it:
            fgrass, fpaved, fs, org = it.pop('_yard_cells')
            if len(apts) >= 2:
                # one continuous lawn shared by several apartments: every grass cell belongs to the
                # nearest apartment room bordering the yard, so each apartment gets its own "חצר פרטית"
                refs = [(info[n]['apartment'], info[n]['label_pt']) for n in neighbours[rid]
                        if info[n].get('apartment') and info[n].get('label_pt')]
                parts = dict((a, {}) for a in apts)
                for c, p in fgrass.items():
                    a = min(refs, key=lambda ar: (ar[1][0] - p[0]) ** 2 + (ar[1][1] - p[1]) ** 2)[0]
                    parts[a][c] = p
                it['grass_patches'] = []
                for a in apts:
                    for patch in grid_clusters(parts[a], fs, min_area_m2=YARD_MIN_GRASS_M2, origin=org):
                        patch['apartment'] = a
                        it['grass_patches'].append(patch)
            else:
                it['grass_patches'] = grid_clusters(fgrass, fs, min_area_m2=YARD_MIN_GRASS_M2, origin=org)
            it['paved_patches'] = grid_clusters(fpaved, fs, min_area_m2=YARD_MIN_PAVED_M2, origin=org)
    for rid, it in info.items():
        if any(info[n].get('has_landscape') and info[n].get('touches_apartment') for n in neighbours[rid]):
            it['touches_private_yard'] = True

    for it in info.values():
        it['guess'], it['guess_extras'], it['rule'] = guess_name(it)

    # truth = text notes typed in this view, located inside each room
    unmatched_texts = []
    for tn in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
        txt = (tn.Text or u'').strip()
        if not txt:
            continue
        bb = tn.get_BoundingBox(view)
        if bb is None:
            continue
        c = (bb.Min + bb.Max) * 0.5
        r = room_at(doc, rooms_by_id, c.X, c.Y, z_probe, phase)
        clean = txt.replace(u'\r', u' ').replace(u'\n', u' ').strip()
        if r is None:
            unmatched_texts.append(clean)
        else:
            info[_id(r.Id)]['truth_texts'].append(clean)

    # score
    scored = matched = 0
    for it in info.values():
        names = [x for x in it['truth_texts'] if not x.startswith(u'דירה')]
        it['truth_names'] = names
        truths = [norm(x) for x in names]
        if not truths:
            it['match'] = None
            continue
        if it['guess'] == SERVICE_ROOM:
            it['match'] = None          # the architect picks the name from the fixed list
            it['pending_choice'] = True
            continue
        scored += 1
        guesses = [norm(it['guess'])] + [norm(x) for x in it['guess_extras']]
        # strict: the main guess must equal one of the typed names exactly (ignoring spaces/punctuation)
        ok = guesses[0] in truths
        it['partial_match'] = (not ok) and any(g and (g in t or t in g) for g in guesses for t in truths)
        it['match'] = bool(ok)
        if ok:
            matched += 1

    return {
        'view': view.Name, 'level': level.Name,
        'rooms_total': len(rooms), 'rooms_new_temporary': len(new_ids),
        'rooms_with_truth_text': scored, 'rooms_matched': matched,
        'rooms_pending_choice': sum(1 for x in info.values() if x.get('pending_choice')),
        'unmatched_texts': unmatched_texts,
        'elevator_doors': elevator_log,
        'open_grass_patches': open_grass_patches,
        'rooms': sorted(info.values(), key=lambda x: -x['area_m2']),
    }


def run_dry(doc, view, log, detailed=True):
    """Place rooms temporarily, analyse, and ALWAYS roll back. The model is not changed."""
    level = view.GenLevel
    phase = view_phase(doc, view)
    t = Transaction(doc, u'PermitAgent - ניסוי חדרים (מבוטל)')
    t.Start()
    try:
        new_ids = list(doc.Create.NewRooms2(level, phase))
        doc.Regenerate()
        return _analyse(doc, view, level, phase, new_ids, log, detailed)
    finally:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()


def save_result(doc, result):
    base = os.path.dirname(doc.PathName) if doc.PathName else os.path.join(os.path.expanduser('~'), 'Desktop')
    out_dir = os.path.join(base, u'PermitAgent_exports')
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    safe_view = u''.join(ch for ch in result['view'] if ch not in u'\\/:*?"<>|').strip()
    path = os.path.join(out_dir, u'rooms_%s_%s.json' % (safe_view, stamp))
    for r in result.get('rooms', []):      # the raw plan lines are only needed while placing labels
        for i in r.get('items', []):
            i.pop('plan_segs', None)
    txt = json.dumps(result, ensure_ascii=False, indent=1)
    if not isinstance(txt, type(u'')):
        txt = txt.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(txt)
    return path
