# -*- coding: utf-8 -*-
"""PermitAgent - create rooms on the active view's level, name them by the office rules and label the plan.

Labels are text notes of the office's "2.0 mm" text type (the same the architect types by hand):
  * room name     - centre of the room; the living room label sits at the centre of the sofa/armchair/TV group
  * "מטבח"        - centre of the kitchen family (L: centre of the L, line: its centre, 2 lines: between them);
                    a kitchen in a common/service room is labelled "מטבחון"
  * "פ.אוכל"      - centre of every dining table
Update mode: the agent's previous labels in the view (marked with Extensible Storage) and its rooms on
the level (Comments = "PermitAgent") are removed first and everything is rebuilt; names the architect
picked for service rooms / shafts are reused when the space is still there.
Safety:
  * runs only on a working copy - the file name must contain "_AGENT"
  * everything happens inside one TransactionGroup -> a single Ctrl+Z undoes it all
  * only the agent's own rooms / labels are touched; the architect's texts and rooms are left alone
  * every created room gets Comments = "PermitAgent"; every label carries the PermitAgentLabel entity
"""
import math
import System
from Autodesk.Revit.DB import (
    ImportInstance, CategoryType,
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, Transaction, TransactionGroup, XYZ,
    TextNote, TextNoteType, TextNoteOptions, HorizontalTextAlignment, VerticalTextAlignment,
)
from Autodesk.Revit.DB.ExtensibleStorage import SchemaBuilder, Schema, Entity, AccessLevel
from Autodesk.Revit.DB.Architecture import Room

import permit_rooms
import permit_apts

VERSION = u'2026-10-01 v39 (Alon answers of 01.10: paved פ.ב = top minus the build-up above the concrete; fence = real top height; apartment unit moves together up to 80cm. Fixes: paved pieces by the real sampling step, text box without its arrow, no garden outside the lot, every non-Landscape floor as concrete, yard slivers merged, 3m between garden units)'   # written to the trace so we know which code ran
WRITE_MARK = u'_AGENT'
AGENT_COMMENT = u'PermitAgent'
TEXT_TYPE_NAME = u'2.0 mm'
GRASS_SKIP_M2 = 5.0     # grass leftovers smaller than this, without doors, get no room
SHAFT = u'פיר'
LIVING = permit_rooms.LIVING_NAME    # "ח.מגורים" (office dictionary, 29.09.26)
KITCHEN = u'מטבח'
KITCHENETTE = u'מטבחון'
DINING = u'פ.אוכל'
# rooms whose name the architect picks from the fixed list
CHOICE_GUESSES = (permit_rooms.SERVICE_ROOM, SHAFT, u'?')
# names written on two lines in the office drawings ("ח.שינה" / "הורים")
TWO_LINE_SUFFIXES = (u' הורים', u' כללי')
TEXT_HEIGHT_MM = 2.0
# rooms whose label sits in the free space between the sanitary fixtures
WET_NAMES = (u'ח.רחצה', u'ח.רחצה הורים', u'ח.רחצה כללי', u'ש.אורחים', u'שרותים', u'ח.שרות')
# the fixed list the architect picks from (service rooms / shafts)
SERVICE_NAMES = [u'חדר אשפה/דחסנית חכמה', u'חדר אשפה', u'חדר אופניים', u'חדר מיחזור', u'מחסן',
                 u'כ.א.צ מרתפים', u'ח.דיירים',
                 # added from the submissions (Alon, 29.09.26)
                 u'מועדון דיירים משותף', u'חדר ניקיון', u'חדר גז', u'חדר טרנספורמציה', u'אוורור חדר טרפו',
                 u'חדר חשמל', u'מאגר מים', u'חלחול', u'מסתור כביסה', u'חלל כלוא', u'רחבה משותפת',
                 u'רחבה מרוצפת']
SHAFT_NAMES = [u'פיר', u'פיר אוורור', u'אוורור טרפו']
# every text the agent can write - used only to recognise unmarked labels of the first (pre-marking) run
AGENT_VOCABULARY = [u'ח.דיור', u'פיר מעלית', u'פיר מעליות', u'ח. מדרגות', u'מעלית', u'לובי כניסה', u'לובי קומתי', u'חצר פרטית', u'משטח מרוצף',
                    u'גינון משותף', u'מרפסת', u'ח.רחצה הורים', u'ח.רחצה', u'ח.רחצה כללי', u'ש.אורחים',
                    u'שרותים', LIVING, KITCHEN, KITCHENETTE, DINING, u'ח.שינה/ממ"ד', u'ח.שינה הורים',
                    u'ח.שינה', u'ח.שרות', u'ח. דוודים'] + SERVICE_NAMES + SHAFT_NAMES

# Extensible Storage schema that marks the text notes created by PermitAgent (invisible, saved in the model)
LABEL_SCHEMA_GUID = System.Guid('6f1c2a3e-8b4d-4e2a-9c1f-5a7b3d9e2c41')


class Cancelled(Exception):
    pass


def _label_schema(create=False):
    s = Schema.Lookup(LABEL_SCHEMA_GUID)
    if s is None and create:
        sb = SchemaBuilder(LABEL_SCHEMA_GUID)
        sb.SetSchemaName('PermitAgentLabel')
        sb.SetReadAccessLevel(AccessLevel.Public)
        sb.SetWriteAccessLevel(AccessLevel.Public)
        sb.AddSimpleField('Kind', System.String)
        s = sb.Finish()
    return s


def _mark_label(el, schema):
    ent = Entity(schema)
    ent.Set[System.String]('Kind', u'label')
    el.SetEntity(ent)


def _is_label(el, schema):
    """a room/apartment label of the agent (not its spot elevations or "מפלסים" block - those belong to
    the levels tool and survive a rooms update)"""
    if schema is None:
        return False
    try:
        ent = el.GetEntity(schema)
        return ent.IsValid() and ent.Get[System.String]('Kind') == u'label'
    except Exception:
        return False


def is_agent_room(room):
    p = room.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return p is not None and (p.AsString() or u'') == AGENT_COMMENT


def agent_rooms(doc, level=None):
    out = []
    for r in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        if isinstance(r, Room) and is_agent_room(r) and (level is None or r.LevelId == level.Id):
            out.append(r)
    return out


def agent_labels(doc, view):
    schema = _label_schema()
    return [t for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote) if _is_label(t, schema)]


def legacy_labels(doc, view, rooms, text_type):
    """unmarked labels of the first run: agent vocabulary, agent text type, inside one of the agent's rooms"""
    schema = _label_schema()
    vocab = set(permit_rooms.norm(v) for v in AGENT_VOCABULARY)
    z = view.GenLevel.ProjectElevation + 1.0
    out = []
    for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
        if _is_label(t, schema) or t.GetTypeId() != text_type.Id:
            continue
        if permit_rooms.norm(t.Text) not in vocab:
            continue
        bb = t.get_BoundingBox(view)
        if bb is None:
            continue
        c = (bb.Min + bb.Max) * 0.5
        if any(r.IsPointInRoom(XYZ(c.X, c.Y, z)) for r in rooms):
            out.append(t)
    return out


def _level_name(doc, level_id):
    lv = doc.GetElement(level_id)
    return lv.Name if lv is not None else u'?'


def agent_inventory(doc):
    """-> {level name: {'rooms': [room], 'labels': [text note]}} for levels that hold agent rooms or labels.
    A label belongs to the level of the plan view it was placed in."""
    inv = {}
    for r in agent_rooms(doc):
        inv.setdefault(_level_name(doc, r.LevelId), {'rooms': [], 'labels': []})['rooms'].append(r)
    schema = _label_schema()
    if schema is not None:
        for t in FilteredElementCollector(doc).OfClass(TextNote):
            if not _is_label(t, schema):
                continue
            v = doc.GetElement(t.OwnerViewId)
            lv = getattr(v, 'GenLevel', None) if v is not None else None
            nm = lv.Name if lv is not None else u'?'
            inv.setdefault(nm, {'rooms': [], 'labels': []})['labels'].append(t)
    return inv


def delete_agent(doc, level_names, with_labels):
    """deletes the agent's rooms (and optionally its text labels) on the given levels.
    The architect's own rooms and texts are never touched. -> (rooms deleted, labels deleted)"""
    inv = agent_inventory(doc)
    rooms, labels = [], []
    for nm in level_names:
        if nm in inv:
            rooms += inv[nm]['rooms']
            if with_labels:
                labels += inv[nm]['labels']
    if not rooms and not labels:
        return 0, 0
    t = Transaction(doc, u'PermitAgent - מחיקת חדרים' + (u' ותוויות' if with_labels else u''))
    t.Start()
    for e in labels + rooms:
        doc.Delete(e.Id)
    t.Commit()
    return len(rooms), len(labels)


def _text_type(doc):
    best = None
    for t in FilteredElementCollector(doc).OfClass(TextNoteType):
        nm = permit_rooms.elem_name(t)
        if nm == TEXT_TYPE_NAME:
            return t
        if best is None and u'2.0' in nm:
            best = t
    return best


def abort_open(tx):
    """roll back a Transaction / TransactionGroup that is still open (safe to call on any)"""
    try:
        if tx is not None and tx.HasStarted() and not tx.HasEnded():
            tx.RollBack()
    except Exception:
        pass


BOLD_TEXT_TYPE_NAME = u'2.0 mm Bold'


def _bold_text_type(doc, base, log=None):
    """the bold 2.0mm text type (created from the 2.0mm one when missing)"""
    t, created = permit_rooms.ensure_type(doc, TextNoteType, base, BOLD_TEXT_TYPE_NAME, log)
    if t is None:
        return base                                         # never fail the run over the bold type
    if not created:
        return t                                            # the office's own type is used as is
    try:
        t.get_Parameter(BuiltInParameter.TEXT_STYLE_BOLD).Set(1)
    except Exception as ex:
        if log:
            log(u'bold text type: %s' % ex)
    if log:
        log(u'created text type "%s"' % BOLD_TEXT_TYPE_NAME)
    return t


def _label_text(name):
    for suf in TWO_LINE_SUFFIXES:
        if name.endswith(suf):
            return name[:-len(suf)] + u'\r' + suf.strip()
    return name


def _centroid(pts):
    return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]


CHAR_WIDTH = 0.55   # average character width / text height - calibrated on the plan: "23,30,37,44,51" = ~1.4m


def _label_box(text, scale):
    """estimated half width / half height (feet) of a 2.0mm label at the view scale"""
    th = TEXT_HEIGHT_MM / 1000.0 * scale / 0.3048
    lines = text.split(u'\r')
    return max(len(l) for l in lines) * th * CHAR_WIDTH / 2.0, len(lines) * th * 1.3 / 2.0


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _rect_gap(a, b):
    """distance between two axis-aligned rectangles (0 when they touch/overlap)"""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _seg_dist(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = float(dx * dx + dy * dy)
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return ((px - a[0] - t * dx) ** 2 + (py - a[1] - t * dy) ** 2) ** 0.5


def free_spot(room, text, scale):
    """In a wet room: the centre of the free floor between the sanitary fixtures - the label position
    that stays inside the room, covers no fixture (as drawn in the plan, without hidden usage zones)
    and is as far as possible from every fixture and wall. None when the room has no fixtures."""
    fixtures = [i for i in room.get('items', []) if i.get('plumbing') and 'kitchen' not in i['groups']]
    poly = room.get('outline') or []
    if not fixtures or len(poly) < 3:
        return None
    boxes = [i.get('plan_bb') or i['bb'] for i in fixtures]
    edges = [(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly))]
    target = _centroid([[(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0] for b in boxes])
    hw, hh = _label_box(text, scale)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    step = max(hh / 3.0, 0.1)
    best = None
    x = min(xs) + hw
    while x <= max(xs) - hw:
        y = min(ys) + hh
        while y <= max(ys) - hh:
            rect = (x - hw, y - hh, x + hw, y + hh)
            corners = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
            outside = sum(1 for cx, cy in corners if not permit_rooms.point_in_polygons(cx, cy, [poly]))
            cover = sum(_overlap(rect, b) for b in boxes)
            # clearance of the label box from the fixtures and from the walls
            gap_fix = min(_rect_gap(rect, b) for b in boxes)
            gap_wall = min(min(_seg_dist(cx, cy, e[0], e[1]) for cx, cy in corners) for e in edges)
            clear = min(gap_fix, gap_wall)
            dist = ((x - target[0]) ** 2 + (y - target[1]) ** 2) ** 0.5
            score = cover * 1000.0 + outside * 100.0 - clear * 10.0 + dist * 0.1
            if best is None or score < best[0]:
                best = (score, [x, y])
            y += step
        x += step
    return best[1] if best else None


WALL_MARGIN = 0.25      # ft (~8cm) kept between a label and the room outline
SEARCH_RADIUS = 26.0    # ft (~8m) around the preferred point


def _rect_inside(rect, poly, margin):
    """the whole label box (sampled along its sides) lies inside the room, away from the walls"""
    x0, y0, x1, y1 = rect
    pts = []
    for k in range(5):
        f = k / 4.0
        pts += [(x0 + (x1 - x0) * f, y0), (x0 + (x1 - x0) * f, y1),
                (x0, y0 + (y1 - y0) * f), (x1, y0 + (y1 - y0) * f)]
    for px, py in pts:
        if not permit_rooms.point_in_polygons(px, py, [poly]):
            return False
    edges = [(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly))]
    for px, py in pts:
        if min(_seg_dist(px, py, e[0], e[1]) for e in edges) < margin:
            return False
    # a corner of the room poking into the box also means a wall crosses the label
    for vx, vy in poly:
        if x0 < vx < x1 and y0 < vy < y1:
            return False
    return True


OCC_CELL = 0.15   # ft (~4.6cm) grid for "is anything drawn here?"


class _Occupancy(object):
    """The plan drawing (lines of furniture, fixtures, doors + already placed labels) rasterised on a
    fine grid, with a summed-area table so any rectangle can be tested in constant time."""

    def __init__(self, bounds, cell=None):
        self.cell = cell or OCC_CELL
        self.x0, self.y0 = bounds[0] - 1.0, bounds[1] - 1.0
        self.nx = int((bounds[2] - bounds[0] + 2.0) / self.cell) + 2
        self.ny = int((bounds[3] - bounds[1] + 2.0) / self.cell) + 2
        self.grid = [bytearray(self.nx) for _ in range(self.ny)]
        self.sat = None

    def _cell(self, x, y):
        return int((x - self.x0) / self.cell), int((y - self.y0) / self.cell)

    def _mark(self, i, j):
        if 0 <= i < self.nx and 0 <= j < self.ny:
            self.grid[j][i] = 1

    def add_segment(self, x1, y1, x2, y2):
        # skip lines entirely outside the grid (the whole view is scanned for outdoor labels)
        if max(x1, x2) < self.x0 or max(y1, y2) < self.y0 or \
                min(x1, x2) > self.x0 + self.nx * self.cell or min(y1, y2) > self.y0 + self.ny * self.cell:
            return
        n = int(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / (self.cell / 2.0)) + 1
        for k in range(n + 1):
            f = float(k) / n
            self._mark(*self._cell(x1 + (x2 - x1) * f, y1 + (y2 - y1) * f))

    def add_rect(self, r):
        i0, j0 = self._cell(r[0], r[1])
        i1, j1 = self._cell(r[2], r[3])
        for j in range(max(j0, 0), min(j1, self.ny - 1) + 1):
            row = self.grid[j]
            for i in range(max(i0, 0), min(i1, self.nx - 1) + 1):
                row[i] = 1

    def build(self):
        sat = [[0] * (self.nx + 1) for _ in range(self.ny + 1)]
        for j in range(self.ny):
            run = 0
            row, up, cur = self.grid[j], sat[j], sat[j + 1]
            for i in range(self.nx):
                run += row[i]
                cur[i + 1] = up[i + 1] + run
        self.sat = sat

    def count(self, r):
        """number of drawn cells under the rectangle"""
        i0, j0 = self._cell(r[0], r[1])
        i1, j1 = self._cell(r[2], r[3])
        i0, j0 = max(i0, 0), max(j0, 0)
        i1, j1 = min(i1, self.nx - 1), min(j1, self.ny - 1)
        if i1 < i0 or j1 < j0:
            return 0
        s = self.sat
        return s[j1 + 1][i1 + 1] - s[j0][i1 + 1] - s[j1 + 1][i0] + s[j0][i0]


SKIP_OCC_CATS = [BuiltInCategory.OST_Rooms, BuiltInCategory.OST_Areas, BuiltInCategory.OST_RoomSeparationLines,
                 BuiltInCategory.OST_AreaSchemeLines, BuiltInCategory.OST_Levels,
                 # datum / helper lines crossing the whole plan are not "occupied space"
                 BuiltInCategory.OST_Grids, BuiltInCategory.OST_CLines, BuiltInCategory.OST_VolumeOfInterest]


def view_occupancy(doc, view, bounds, cell=0.3, extra_skip=None, record=None):
    """Everything drawn in the view inside `bounds` (lines of model elements, boxes of annotations),
    rasterised - so outdoor labels land on the paving / grass itself, not on garden furniture, planters,
    trees, curbs, texts or dimensions. CAD imports (e.g. the survey) are ignored.
    extra_skip: more category names to leave out (e.g. the floors / toposolids themselves - their
    sloped surfaces are drawn as a mesh of lines that would cover the whole site)."""
    skip = set(int(c) for c in SKIP_OCC_CATS)
    for n in (extra_skip or []):
        c = getattr(BuiltInCategory, n, None)
        if c is not None:
            skip.add(int(c))
    occ = _Occupancy(bounds, cell)
    for el in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        cat = el.Category
        if cat is None or cat.Id.IntegerValue in skip or isinstance(el, ImportInstance):
            continue
        try:
            bb = el.get_BoundingBox(view)
        except Exception:
            continue
        if bb is None or bb.Max.X < bounds[0] or bb.Min.X > bounds[2] or \
                bb.Max.Y < bounds[1] or bb.Min.Y > bounds[3]:
            continue
        segs = []
        permit_rooms.visible_footprint(doc, el, view, segs)
        drawn = False
        if segs:
            for s in segs:
                occ.add_segment(*s)
            drawn = True
        elif cat.CategoryType == CategoryType.Annotation:
            occ.add_rect((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y))
            drawn = True
        if drawn and record is not None:
            # what was drawn where - to tell which element sits under a label (diagnostics)
            record.append(((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y),
                           u'%s | %s | %d' % (cat.Name, permit_rooms.fam_type_label(doc, el) or el.Name or u'',
                                              permit_rooms._id(el.Id))))
    occ.build()
    return occ


def _patch_bounds(patches):
    xs, ys = [], []
    for p in patches:
        s = p['step']
        ox, oy = p['origin']
        for i, j in p['cells']:
            xs += [ox + i * s, ox + (i + 1) * s]
            ys += [oy + j * s, oy + (j + 1) * s]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


FENCE_SEGS = []                  # the ground floor's fences (set by run_write) - they split a yard room
SUB_YARD_MIN_M2 = 12.0           # a piece of grass behind a fence this big is an apartment's own yard


def _two_on_grass(patches, text, scale, occ):
    """"חצר פרטית" twice in every apartment's yard: the yard room's grass is split by the fences inside
    it (one room may hold the yards of two apartments - floor 00, 30.09.26), and each yard gets the name
    at a third and two thirds along the long axis of its grass"""
    patches = [p for p in patches if p.get('cells')]
    if not patches:
        return []
    base = max(patches, key=lambda p: len(p['cells']))
    same = [p for p in patches if p['step'] == base['step'] and list(p['origin']) == list(base['origin'])]
    cells = []
    for p in same:
        cells += [tuple(c) for c in p['cells']]
    s = base['step']
    groups = permit_rooms.split_cells(cells, s, base['origin'], FENCE_SEGS)
    cell_m2 = (s * 0.3048) ** 2
    yards = [g for g in groups if len(g) * cell_m2 >= SUB_YARD_MIN_M2] or groups[:1]
    out = []
    for g in yards:
        out += _two_on_cells(g, s, base['origin'], text, scale, occ)
    return out


def _two_on_cells(cells, s, origin, text, scale, occ):
    ox, oy = origin
    pts = [(ox + (i + 0.5) * s, oy + (j + 0.5) * s) for i, j in cells]
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)            # the long axis
    ux, uy = math.cos(ang), math.sin(ang)
    proj = [(p[0] - mx) * ux + (p[1] - my) * uy for p in pts]
    lo, hi = min(proj), max(proj)
    merged = {'cells': cells, 'step': s, 'origin': origin, 'pt': [mx, my]}
    out = []
    for f in (1.0 / 3.0, 2.0 / 3.0):
        t = lo + (hi - lo) * f
        m = dict(merged)
        m['centroid'] = [mx + ux * t, my + uy * t]
        out.append(patch_spot(m, text, scale, occ)[0])
    return out


def patch_spot(patch, text, scale, occ):
    """Centre for a label that lies fully on the patch (grass / paving) and over nothing drawn,
    as close as possible to the patch centre. -> (point, how)"""
    s = patch['step']
    ox, oy = patch['origin']
    cells = set(tuple(c) for c in patch['cells'])
    hw, hh = _label_box(text, scale)
    cx, cy = patch.get('centroid') or patch['pt']

    def fully_on_patch(rect):
        i0, j0 = int(math.floor((rect[0] - ox) / s)), int(math.floor((rect[1] - oy) / s))
        i1, j1 = int(math.floor((rect[2] - ox) / s)), int(math.floor((rect[3] - oy) / s))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if (i, j) not in cells:
                    return False
        return True

    order = sorted(cells, key=lambda c: (ox + (c[0] + 0.5) * s - cx) ** 2 + (oy + (c[1] + 0.5) * s - cy) ** 2)
    fallback, best = None, None
    for c in order:
        x, y = ox + (c[0] + 0.5) * s, oy + (c[1] + 0.5) * s
        rect = (x - hw, y - hh, x + hw, y + hh)
        if not fully_on_patch(rect):
            continue
        n = 0 if occ is None else occ.count(rect)
        if n == 0:
            return [x, y], 'clear'
        if best is None or n < best:     # e.g. grass under tree symbols: the fewest lines under the text
            fallback, best = [x, y], n
    if fallback:
        return fallback, 'on the surface, least overlap (%d cells)' % best
    return patch['pt'], 'label larger than the patch'


def place_room_name(room, text, scale, start, taken):
    """Room-name label: the clear spot nearest to `start` inside the room (no drawn line under it).
    Office rule for narrow rooms: the text is turned 90 degrees ONLY when the horizontal text has no
    clean place in the room (it would cross the walls or the objects in the room) AND the turned text
    does fit cleanly. Otherwise it stays horizontal.
    -> (point, rotated, how)"""
    poly = room.get('outline') or []
    if len(poly) < 3:
        return start, False, 'no outline'
    # search only as far as the room reaches
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    radius = min(SEARCH_RADIUS, ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5)
    pt, how = place_clear([room], text, scale, start, taken, radius=radius)
    if how == 'clear':
        return pt, False, how
    # horizontal does not fit cleanly: turn it only if the turned text fits cleanly
    rpt, rhow = place_clear([room], text, scale, start, taken, rotated=True, radius=radius)
    if rhow == 'clear':
        return rpt, True, u'rotated 90 - clear'
    if how.startswith(u'least overlap'):
        return pt, False, how            # inside the room, touching as little as possible
    if rhow.startswith(u'least overlap'):
        return rpt, True, u'rotated 90 - ' + rhow
    return start, False, how


def place_clear(rooms, text, scale, start, taken, rotated=False, radius=None, drawn_occ=None):
    """Label centre closest to `start` (the entrance) where the whole label fits inside ONE of `rooms`
    (the apartment's rooms), away from its walls, clear of every element drawn there (furniture,
    fixtures, doors with their swing) and of the labels already placed (`taken` rects).
    If no spot is fully clear: the spot inside a room with the least overlap.
    Returns (point, how) - how is 'clear', 'least overlap' or 'no room fits (kept at the door)'."""
    rooms = [r for r in (rooms or []) if len(r.get('outline') or []) >= 3]
    if not rooms:
        return start, 'no room outlines'
    hw, hh = _label_box(text, scale)
    step = max(hh / 3.0, 0.1)
    if rotated:                  # text turned 90 degrees: its box is tall and narrow
        hw, hh = hh, hw
    radius = radius or SEARCH_RADIUS
    n = int(radius / step)
    cands = []
    for ix in range(-n, n + 1):
        for iy in range(-n, n + 1):
            d = ((ix * step) ** 2 + (iy * step) ** 2) ** 0.5
            if d <= radius:
                cands.append((d, ix, iy))
    cands.sort()
    bounds = [(min(p[0] for p in r['outline']), min(p[1] for p in r['outline']),
               max(p[0] for p in r['outline']), max(p[1] for p in r['outline'])) for r in rooms]
    # what is really drawn: the lines of every element in the apartment + the labels already placed.
    # (a door swing is an arc, so the empty corner of its box stays free - as in the office drawings)
    occ = _Occupancy((min(b[0] for b in bounds), min(b[1] for b in bounds),
                      max(b[2] for b in bounds), max(b[3] for b in bounds)))
    for r in rooms:
        for i in r.get('items', []):
            segs = i.get('plan_segs')
            if segs:
                for s in segs:
                    occ.add_segment(*s)
            else:
                occ.add_rect(i.get('plan_bb') or i['bb'])
    for t in taken:
        occ.add_rect(t)
    occ.build()
    fallback = None
    for d, ix, iy in cands:          # nearest first - the first clear spot wins
        x, y = start[0] + ix * step, start[1] + iy * step
        rect = (x - hw, y - hh, x + hw, y + hh)
        drawn = occ.count(rect)
        if drawn_occ is not None:       # everything drawn in the view (door swings, texts...)
            drawn += drawn_occ.count(rect)
        if drawn > 0 and fallback is not None and drawn >= fallback[0]:
            continue
        for r, bd in zip(rooms, bounds):
            if rect[0] < bd[0] or rect[1] < bd[1] or rect[2] > bd[2] or rect[3] > bd[3]:
                continue
            if not _rect_inside(rect, r['outline'], WALL_MARGIN):
                continue
            if drawn == 0:
                return [x, y], 'clear'
            fallback = (drawn, [x, y], d)
            break
    if fallback:
        return fallback[1], u'least overlap (%d drawn cells under the label)' % fallback[0]
    return start, 'no room fits (kept at the door)'


DOOR_LABEL_GAP = 0.20 / 0.3048   # 20cm between the door (as drawn) and a regular-floor apartment number


def door_label_spot(door, text, scale):
    """Office rule for a regular (non-typical) floor: the apartment number sits inside the apartment,
    20cm past the entrance door as drawn (leaf + swing), centred on the door."""
    hw, hh = _label_box(text, scale)
    dx, dy = door['dir']
    half = abs(dx) * hw + abs(dy) * hh        # the label's half size along the door direction
    k = door['reach'] + DOOR_LABEL_GAP + half
    return [door['loc'][0] + dx * k, door['loc'][1] + dy * k]


def _rects_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _rect_of(text, pt, scale, rotated=False):
    hw, hh = _label_box(text, scale)
    if rotated:
        hw, hh = hh, hw
    return (pt[0] - hw, pt[1] - hh, pt[0] + hw, pt[1] + hh)


L_KITCHEN_GAP = 0.30 / 0.3048   # 30cm between the "מטבח" text and each arm of an L kitchen


def _kitchen_point(items, text, scale, room_pt=None):
    """Office rules for the "מטבח" label:
    * L kitchen - in the inner corner of the L, the text box 30cm off both arms
    * one line kitchen - in front of the counter (the side facing the room), the text box 30cm off it,
      at the middle of its length
    * two line kitchens - midway between them"""
    kitchens = [i for i in items if 'kitchen' in i['groups']]
    hw, hh = _label_box(text, scale)
    for i in kitchens:
        if i.get('l_corner'):
            (cx, cy), (sx, sy) = i['l_corner']
            return [cx + sx * (L_KITCHEN_GAP + hw), cy + sy * (L_KITCHEN_GAP + hh)]
    if len(kitchens) == 1:
        k = kitchens[0]
        b = (k.get('l_debug') or {}).get('counter_bb') or k.get('plan_bb') or k['bb']
        mx, my = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        ref = room_pt or [mx, my]
        if (b[2] - b[0]) >= (b[3] - b[1]):     # counter runs along x -> the label goes above/below it
            if ref[1] >= my:
                return [mx, b[3] + L_KITCHEN_GAP + hh]
            return [mx, b[1] - L_KITCHEN_GAP - hh]
        if ref[0] >= mx:                        # counter runs along y -> the label goes beside it
            return [b[2] + L_KITCHEN_GAP + hw, my]
        return [b[0] - L_KITCHEN_GAP - hw, my]
    return _centroid([i['pt'] for i in kitchens])


PRIVATE_YARD = u'חצר פרטית'
SHARED_GARDEN = u'גינון משותף'
PAVED = u'משטח מרוצף'
ENTRANCE_LOBBY = u'לובי כניסה'
FLOOR_LOBBY = u'לובי קומתי'
BALCONY = u'מרפסת'
# the spot elevation as drawn (paper mm, "** PL_Base_Point" at 1:100, measured on the plan): the symbol
# circle radius and the top of the value text above the symbol centre. The spot's bounding box is NOT
# used - it also holds the hidden leader points and is much larger than the drawing.
SPOT_SYMBOL_R_MM = 1.8
SPOT_TEXT_TOP_MM = 2.8
SPOT_LABEL_GAP_M = 0.10      # office rule (Alon): 10cm between the value text and the room label above it
SPOT_VALUE_LEFT_MM = 2.9     # the value text starts this far right of the symbol centre
SPOT_VALUE_CHAR_MM = 1.15    # width of one character of the value text


def spot_value_centre_x(origin_x, value, scale):
    """x of the centre of the spot's value text (e.g. "+7.50"), to the right of the symbol"""
    mm = scale / 1000.0 / 0.3048
    n = len((value or u'+0.00').strip())
    return origin_x + (SPOT_VALUE_LEFT_MM + n * SPOT_VALUE_CHAR_MM / 2.0) * mm


def label_over_spot(origin, text, scale, value=None, gap_m=None):
    """centre of the room label right above the spot's value text, centred on that text (office rule).
    gap_m overrides SPOT_LABEL_GAP_M (ground floor service rooms use 20cm - see permit_levels.py)."""
    hw, hh = _label_box(text, scale)
    gap = SPOT_LABEL_GAP_M if gap_m is None else gap_m
    up = SPOT_TEXT_TOP_MM / 1000.0 * scale / 0.3048 + gap / 0.3048
    return [spot_value_centre_x(origin[0], value, scale), origin[1] + up + hh]
SPOT_GAP = 0.15 / 0.3048         # 15cm between the spot elevation symbol and the lobby label above it
OPEN_GARDEN_MIN_M2 = 3.0         # grass outside any room smaller than this gets no label


def _above_entrance_spot(room, text, scale):
    """office rule: "לובי כניסה" sits just above the spot elevation at the entrance (the one nearest
    to the building's exterior door; without one, the one nearest to the room centre)"""
    spots = room.get('spots') or []
    if not spots:
        return None
    ref = (room.get('exterior_door_pts') or [room.get('label_pt')])[0]
    if ref is None:
        ref = spots[0]['origin']
    best = min(spots, key=lambda s: (s['origin'][0] - ref[0]) ** 2 + (s['origin'][1] - ref[1]) ** 2)
    b = best['bb']
    hw, hh = _label_box(text, scale)
    return [(b[0] + b[2]) / 2.0, b[3] + SPOT_GAP + hh]


def plan_labels(room, name, in_apartment, scale=100, outdoor_occ=None, taken=None):
    """-> list of (text, [x, y], rotated) for one room; points are the label centres"""
    items = room.get('items', [])
    labels = []
    living_pts = [i['pt'] for i in items if 'sofa' in i['groups'] or 'tv' in i['groups']]
    kitchen_pts = [i['pt'] for i in items if 'kitchen' in i['groups']]
    dining_pts = [i['pt'] for i in items if i.get('dining_table')]

    kitchen_text = KITCHEN if in_apartment or name == KITCHEN else KITCHENETTE
    kitchen_pt = _kitchen_point(items, kitchen_text, scale, room.get('label_pt')) if kitchen_pts else None

    # yards: one label per grass patch, and "משטח מרוצף" on every paved patch (office rule)
    # each label sits fully on its own grass / paved patch, over nothing drawn (garden furniture etc.)
    if name in (PRIVATE_YARD, SHARED_GARDEN) and room.get('grass_patches'):
        if name == PRIVATE_YARD:
            # office rule (Alon, 30.09.26): "חצר פרטית" exactly twice per yard, spread on the grass (bold)
            for pt in _two_on_grass(room['grass_patches'], name, scale, outdoor_occ):
                labels.append((name, pt, False))
        else:
            for p in room['grass_patches']:
                labels.append((name, patch_spot(p, name, scale, outdoor_occ)[0], False))
        if name == PRIVATE_YARD:
            for p in room.get('paved_patches', []):
                labels.append((PAVED, patch_spot(p, PAVED, scale, outdoor_occ)[0], False))
        return labels

    main_pt = room.get('label_pt')
    special = False          # placed by its own office rule
    rotated = False
    if name == LIVING and living_pts:
        main_pt, special = _centroid(living_pts), True
    if name == KITCHEN and kitchen_pt:
        main_pt, special = kitchen_pt, True
    if name in WET_NAMES:
        spot = free_spot(room, _label_text(name), scale)
        if spot:
            main_pt, special = spot, True
    if name == ENTRANCE_LOBBY:
        spot = _above_entrance_spot(room, _label_text(name), scale)
        if spot:
            main_pt, special = spot, True
    if name in (FLOOR_LOBBY, BALCONY) and room.get('spots'):
        # levels rule: the label sits just above the room's spot elevation
        ref = room.get('label_pt') or room['spots'][0]['origin']
        best = min(room['spots'], key=lambda s: (s['origin'][0] - ref[0]) ** 2 + (s['origin'][1] - ref[1]) ** 2)
        # right above the value text, centred on it (as the levels tool places it)
        main_pt, special = label_over_spot(best['origin'], name, scale, best.get('value')), True
    if main_pt and not special:
        # any other room: the clear spot nearest the room centre - not over the objects in the room
        # (e.g. the bins of a recycling room); turned 90 degrees only if it cannot fit otherwise
        main_pt, rotated, _how = place_room_name(room, _label_text(name), scale, main_pt, taken or [])
    if main_pt:
        labels.append((_label_text(name), main_pt, rotated))

    if kitchen_pt and name != KITCHEN:
        labels.append((kitchen_text, kitchen_pt, False))
    for p in dining_pts:
        labels.append((DINING, p, False))
    return labels


def run_write(doc, uidoc, view, choose_name, log, confirm_legacy=None):
    """Create or update rooms + labels on the view's level.
    choose_name(room_info) -> name (unicode), None to skip the room, or raises Cancelled.
    confirm_legacy(count) -> True to also delete unmarked labels left by the first (pre-marking) run."""
    if WRITE_MARK not in (doc.Title or u''):
        raise Exception(u'הכלי רץ רק על עותק עבודה - שם הקובץ חייב להכיל %s' % WRITE_MARK)
    log(u'code version: %s' % VERSION)
    level = view.GenLevel
    phase = permit_rooms.view_phase(doc, view)
    text_type = _text_type(doc)
    if text_type is None:
        raise Exception(u'לא נמצא סוג טקסט "%s" בפרויקט' % TEXT_TYPE_NAME)

    z = level.ProjectElevation + 1.0
    tg = TransactionGroup(doc, u'PermitAgent - עדכון חדרים ושמות')
    tg.Start()
    try:
        # 0. update: remove the agent's previous rooms on this level and its labels in this view,
        #    remembering the names so choices made by the architect are not asked again
        old_rooms = agent_rooms(doc, level)
        # only names the architect chose himself: from the fixed list, or free text outside the rules
        rule_names = set(AGENT_VOCABULARY) - set(SERVICE_NAMES) - set(SHAFT_NAMES)
        remembered = []
        for r in old_rooms:
            loc = r.Location.Point if r.Location else None
            nm = r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u''
            if loc is not None and nm and nm not in rule_names:
                remembered.append((nm, loc.X, loc.Y))
        old_labels = agent_labels(doc, view)
        # the labels survive "מחיקת חדרים", so they remember the architect's choices too
        for tn in old_labels:
            nm = (tn.Text or u'').replace(u'\r', u' ').replace(u'\x0b', u' ').strip()
            nm = u' '.join(nm.split())
            if nm and nm not in rule_names:
                remembered.append((nm, tn.Coord.X, tn.Coord.Y))
        legacy = legacy_labels(doc, view, old_rooms, text_type) if old_rooms else []
        if legacy and not (confirm_legacy and confirm_legacy(len(legacy))):
            legacy = []
        removed_rooms, removed_labels = len(old_rooms), len(old_labels) + len(legacy)
        if old_rooms or old_labels or legacy:
            t = Transaction(doc, u'PermitAgent - ניקוי הרצה קודמת')
            t.Start()
            for e in old_labels + legacy:
                doc.Delete(e.Id)
            for r in old_rooms:
                doc.Delete(r.Id)
            t.Commit()
            log(u'נמחקו %d חדרים ו-%d תוויות מהרצה קודמת' % (removed_rooms, removed_labels))

        # 1. create the rooms (committed inside the group so the UI can zoom to them)
        t = Transaction(doc, u'PermitAgent - יצירת חדרים')
        t.Start()
        new_ids = list(doc.Create.NewRooms2(level, phase))
        t.Commit()
        log(u'נוצרו %d חדרים חדשים' % len(new_ids))
        new_set = set(permit_rooms._id(i) for i in new_ids)

        # 2. name them by the rules (same engine as the experiment)
        res = permit_rooms._analyse(doc, view, level, phase, new_ids, log)
        rooms = [r for r in res['rooms'] if r['id'] in new_set]
        for r in res['rooms']:
            if r.get('grass_patches') is not None:
                log(u'yard room %s (%s, %.1fm2): grass patches %s | paved patches %s' % (
                    r['id'], r.get('guess'), r['area_m2'],
                    [p['area_m2'] for p in r['grass_patches']], [p['area_m2'] for p in r.get('paved_patches', [])]))
            if r.get('guess') == ENTRANCE_LOBBY:
                log(u'entrance lobby %s: spots=%d exterior doors=%d' % (
                    r['id'], len(r.get('spots') or []), len(r.get('exterior_door_pts') or [])))
            for i in r.get('items', []):
                if 'kitchen' in i['groups']:
                    log(u'kitchen in room %s: %s | %s | corner=%s' % (r['id'], i['label'], i.get('l_debug'),
                                                                     i.get('l_corner')))

        # office rule (Alon): the shaft picker (פיר / פיר אוורור / אוורור טרפו...) is for the ground floor
        # only - on every other floor a shaft is simply "פיר". Ground floor = the floor of the entrance lobby
        # (or the level at +0.00).
        ground = any(r.get('guess') == ENTRANCE_LOBBY for r in res['rooms']) or abs(level.Elevation) < 0.5
        skip, names = [], {}
        for r in rooms:
            if r['guess'] == u'גינון משותף' and r['area_m2'] < GRASS_SKIP_M2 and not r['doors']:
                skip.append(r['id'])
            elif r['guess'] not in CHOICE_GUESSES:
                names[r['id']] = r['guess']
        log(u'ground floor: %s (shaft picker %s)' % (ground, u'on' if ground else u'off - shafts named automatically'))

        # 3. the architect picks names for service rooms / shafts / unknown spaces -
        #    unless the same space already got a name from him in a previous run
        by_id = dict((permit_rooms._id(i), i) for i in new_ids)
        pending = []
        reused = auto_shafts = 0
        for r in rooms:
            if r['id'] in skip or r['id'] in names:
                continue
            room_el = doc.GetElement(by_id[r['id']])
            prev = [nm for nm, x, y in remembered
                    if nm not in (permit_rooms.SERVICE_ROOM, u'?') and room_el.IsPointInRoom(XYZ(x, y, z))]
            if prev:
                names[r['id']] = prev[0]       # a name the architect already chose wins
                reused += 1
            elif r['guess'] == SHAFT and not ground:
                names[r['id']] = SHAFT
                auto_shafts += 1
            else:
                pending.append(r)
        log(u'שמות שנלקחו מהרצה קודמת: %d | פירים אוטומטית: %d | ממתינים לבחירה: %d' % (
            reused, auto_shafts, len(pending)))
        for k, r in enumerate(pending):
            r['_index'], r['_total'] = k + 1, len(pending)
            nm = choose_name(r)
            if nm:
                names[r['id']] = nm
            else:
                skip.append(r['id'])

        # 3b. apartment numbers (the floors below are analysed with temporary rooms, rolled back)
        apt_labels, apt_summary = [], []
        try:
            apt_labels, apt_summary = permit_apts.number_labels(doc, view, res['rooms'], log)
        except Exception as ex:
            apt_summary = [u'מספור דירות נכשל: %s' % ex]
            log(u'apartment numbering failed: %s' % ex)

        # 4. write names, mark the rooms, place the 2.0mm labels; drop the skipped rooms
        opts = TextNoteOptions(text_type.Id)
        opts.HorizontalAlignment = HorizontalTextAlignment.Center
        # the placement point is the middle of the text, so small rooms (shafts) keep it off the walls
        opts.VerticalAlignment = VerticalTextAlignment.Middle
        # the same text turned 90 degrees (reads upwards) - for narrow rooms only
        opts_rot = TextNoteOptions(text_type.Id)
        opts_rot.HorizontalAlignment = HorizontalTextAlignment.Center
        opts_rot.VerticalAlignment = VerticalTextAlignment.Middle
        opts_rot.Rotation = math.pi / 2.0
        scale = view.Scale or 100
        schema = _label_schema(create=True)
        t = Transaction(doc, u'PermitAgent - שמות ותוויות')
        t.Start()
        # "חצר פרטית" in bold 2.0mm (office rule, 30.09.26) - the type is created once on the working copy
        bold_type = _bold_text_type(doc, text_type, log)
        opts_bold = TextNoteOptions(bold_type.Id)
        opts_bold.HorizontalAlignment = HorizontalTextAlignment.Center
        opts_bold.VerticalAlignment = VerticalTextAlignment.Middle
        for rid in skip:
            doc.Delete(by_id[rid])
        placed = []
        errors = []
        taken = []   # rects of the labels placed so far (the apartment numbers must not cover them)
        # outdoor labels (yards, paving, shared garden) must sit on the surface itself, over nothing drawn:
        # one scan of the view's drawing around all outdoor patches
        outdoor = list(res.get('open_grass_patches', []))
        for r in rooms:
            if r['id'] in names:
                outdoor += r.get('grass_patches') or []
                outdoor += r.get('paved_patches') or []
        try:
            FENCE_SEGS[:] = permit_rooms.fence_segments(doc, level)
            log(u'fences that split the yards: %d' % len(FENCE_SEGS))
        except Exception as ex:
            FENCE_SEGS[:] = []
            log(u'fence scan failed: %s' % ex)
        outdoor_occ = None
        pb = _patch_bounds(outdoor)
        if pb is not None:
            try:
                outdoor_occ = view_occupancy(doc, view, pb)
            except Exception as ex:
                log(u'outdoor drawing scan failed: %s' % ex)
        for r in rooms:
            if r['id'] not in names:
                continue
            nm = names[r['id']]
            room = doc.GetElement(by_id[r['id']])
            room.get_Parameter(BuiltInParameter.ROOM_NAME).Set(nm)
            cp = room.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if cp is not None and not cp.IsReadOnly:
                cp.Set(AGENT_COMMENT)
            for txt, pt, rot in plan_labels(r, nm, bool(r.get('apartment')), scale, outdoor_occ, taken):
                try:
                    o = opts_rot if rot else (opts_bold if txt == PRIVATE_YARD else opts)
                    tn = TextNote.Create(doc, view.Id, XYZ(pt[0], pt[1], 0), txt, o)
                    _mark_label(tn, schema)
                    placed.append(txt.replace(u'\r', u' '))
                    taken.append(_rect_of(txt, pt, scale, rot))
                    if rot:
                        log(u'label turned 90 degrees (narrow room): %s' % txt.replace(u'\r', u' '))
                except Exception as ex:
                    errors.append(u'%s: %s' % (txt, ex))
        # grass that is in no room (an open shared garden) - labelled from the floors themselves
        for p in res.get('open_grass_patches', []):
            if p['area_m2'] < OPEN_GARDEN_MIN_M2:
                continue
            pt, how = patch_spot(p, SHARED_GARDEN, scale, outdoor_occ)
            log(u'open grass patch %.1fm2 -> %s at %s cm' % (p['area_m2'], how, [round(v * 30.48) for v in pt]))
            try:
                tn = TextNote.Create(doc, view.Id, XYZ(pt[0], pt[1], 0), SHARED_GARDEN, opts)
                _mark_label(tn, schema)
                placed.append(SHARED_GARDEN)
                taken.append(_rect_of(SHARED_GARDEN, pt, scale))
            except Exception as ex:
                errors.append(u'%s: %s' % (SHARED_GARDEN, ex))
        # apartment numbers: checked against EVERYTHING drawn in the view around the entrances (door leaves
        # and swings, fixtures, texts) - the apartment's own item list can miss a door (floor 01)
        apt_occ = None
        if any(u'\r' not in lb[0] for lb in apt_labels):     # needed by the regular-floor labels only
            R = 8.0
            sx = [lb[1][0] for lb in apt_labels]
            sy = [lb[1][1] for lb in apt_labels]
            try:
                doc.Regenerate()
                apt_occ = view_occupancy(doc, view, (min(sx) - R, min(sy) - R, max(sx) + R, max(sy) + R),
                                         cell=OCC_CELL)
            except Exception as ex:
                log(u'apartment drawing scan failed: %s' % ex)
        for txt, start, apt_rooms, door in apt_labels:
            if u'\r' not in txt and door is not None:
                # regular floor (one apartment per label): 20cm past the entrance door, centred on it
                pt = door_label_spot(door, txt, scale)
                rect = _rect_of(txt, pt, scale)
                inside = any(len(r.get('outline') or []) >= 3 and _rect_inside(rect, r['outline'], 0.0)
                             for r in (apt_rooms or []))
                in_apt = lambda q: any(len(r.get('outline') or []) >= 3 and
                                       _rect_inside(_rect_of(txt, q, scale), r['outline'], 0.0)
                                       for r in (apt_rooms or []))
                if inside:
                    how = u'next to the entrance door (20cm, centred)%s' % (
                        u'' if apt_occ is None else u', drawn cells under it: %d' % apt_occ.count(rect))
                else:
                    # apartment 11 (01, 30.09.26): the door's "inside" was the corridor side - the label
                    # went 3.5m into the living room. Try the other side of the door, then small shifts
                    # beside the door; a wide search only as the last resort, and never far from the door.
                    alt = None
                    flip = dict(door)
                    flip['dir'] = (-door['dir'][0], -door['dir'][1])
                    for d in (door, flip):
                        for extra in (0.0, 0.15, 0.3):
                            for side in (0.0, 0.2, -0.2, 0.4, -0.4):
                                q = door_label_spot(d, txt, scale)
                                q = [q[0] + d['dir'][0] * extra / 0.3048 - d['dir'][1] * side / 0.3048,
                                     q[1] + d['dir'][1] * extra / 0.3048 + d['dir'][0] * side / 0.3048]
                                if in_apt(q) and not any(_rects_overlap(_rect_of(txt, q, scale), t) for t in taken):
                                    alt = (q, d is flip, extra, side)
                                    break
                            if alt:
                                break
                        if alt:
                            break
                    if alt is not None:
                        pt = alt[0]
                        how = u'next to the entrance door (%s side, +%dcm, shifted %dcm)' % (
                            u'other' if alt[1] else u'same', alt[2] * 100, alt[3] * 100)
                    else:
                        pt, how = place_clear(apt_rooms, txt, scale, pt, taken, drawn_occ=apt_occ,
                                              radius=1.5 / 0.3048)
                        how = u'door spot not inside the apartment -> ' + how
            else:
                # typical floor (several numbers, wide label): a clear spot near the entrance, checked
                # against the apartment's own drawing only (as approved in v6 - the whole-view scan pushed
                # these wide labels deep into the apartments)
                pt, how = place_clear(apt_rooms, txt, scale, start, taken)
            log(u'apartment label %s: %s (rooms searched: %d, moved %.1f ft from the door point) '
                u'door_pt_cm=%s label_pt_cm=%s' % (
                    txt.split(u'\r')[0], how, len(apt_rooms or []),
                    ((pt[0] - start[0]) ** 2 + (pt[1] - start[1]) ** 2) ** 0.5,
                    [round(v * 30.48) for v in start], [round(v * 30.48) for v in pt]))
            taken.append(_rect_of(txt, pt, scale))
            try:
                tn = TextNote.Create(doc, view.Id, XYZ(pt[0], pt[1], 0), txt, opts)
                _mark_label(tn, schema)
                placed.append(txt.split(u'\r')[0])
            except Exception as ex:
                errors.append(u'%s: %s' % (txt, ex))
        t.Commit()
        tg.Assimilate()
    except Exception:
        # an open transaction must be rolled back before its group - otherwise Revit terminates (30.09.26)
        try:
            abort_open(t)
        except Exception:
            pass
        abort_open(tg)
        raise

    counts = {}
    for p in placed:
        counts[p] = counts.get(p, 0) + 1
    return {
        'created': len(names), 'skipped': len(skip), 'labels': len(placed),
        'removed_rooms': removed_rooms, 'removed_labels': removed_labels, 'reused_names': reused,
        'apartment_summary': apt_summary, 'apartment_labels': [a[0].replace(u'\r', u' / ') for a in apt_labels],
        'label_counts': sorted(counts.items(), key=lambda x: -x[1]),
        'errors': errors, 'text_type': permit_rooms.elem_name(text_type),
        'rooms': [(names[r['id']], r['area_m2'], r['rule']) for r in rooms if r['id'] in names],
    }
