# -*- coding: utf-8 -*-
"""PermitAgent - level marks on the basement and roof plans (rulebook chapter 3ג, agent 2).

Alon's answers (01.10.26):
  * basement: the method of "פרויקט לדוגמא" - absolute values WITHOUT a symbol ("PL_Survay ללא סימבול")
    on every line where a ramp's slope changes (top, bottom, every break), "X%" with an arrow down the
    slope in every sloped piece, one absolute value in every flat area of the driving lanes (+ every 15m).
    The basement lobby's relative unit is made by "עדכון מפלסים" (run_levels) - not here.
    Ramps are modelled as sloped floors.
  * roof: the spot type is the new one used in the apartments ("** PL_Base_Point - MOST USE NEW").
    The top of the built parapet at its corners (+ the middle of a long side) with "מעקה בנוי" under the
    value; the roof surface with "קומת <level>" under it (as in the submission: "קומת גג טכני").
    The drains are not modelled - no drainage arrows.
Only on a working copy (_AGENT); one TransactionGroup; only this module's own elements (kind "roofbase")
are deleted and rebuilt. Never text on text: every value / label is checked against the drawing and
every text of the view before it stays.
Entry point for "הרצה מלאה": run_roof_basement_view(doc, view, log) -> {'checks': [...]}.
"""
import math
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, Transaction, TransactionGroup, XYZ, Options, PlanarFace,
    SpotDimension, TextNote, TextNoteOptions, HorizontalTextAlignment, VerticalTextAlignment,
    TextNoteLeaderTypes, Wall, CurveElement, UV, Line, ElementTransformUtils, CopyPasteOptions, Transform,
    ElementId,
)
from System.Collections.Generic import List

import permit_rooms
import permit_write
import permit_levels as PL
import permit_ground as PG

VERSION = u'roofbase 2026-10-01 r3 (slopes = copies of a hand-placed Spot Slope, checked against the ramp; r2: r1 PNG:no duplicates of Alon\'s own values / slopes, ramp lines up to 2m above the level, ' \
          u'value exactly on the line, slope text along a drawn arrow; roof: parapets on the outer edge only, value by its symbol)'
KIND = u'roofbase'

SURVEY_H_KEY = u'PL_Survay ללא סימבול הוריזונטלי'
SURVEY_V_KEY = u'PL_Survay ללא סימבול ורטיקלי'
BASEMENT_BELOW_M = -0.5          # a level lower than this (relative) is a basement
ROOF_KW = (u'גג', u'roof')

RAMP_MIN_SLOPE = 2.0             # % - flatter faces are drainage falls, not ramps
BREAK_MIN_LEN_M = 1.5            # a ramp's top / bottom line is at least this long
# (r2) 6mm into the sloped face: r1 used 3cm and gave 27.94 / 28.86 / 29.53 next to Alon's 27.93 / 28.87 / 29.54
BREAK_INSET_FT = 0.02
# (r2) the ramp lines / pieces of this plan: up to 2m above its level. r1 marked the ramp up to the street
# (30.14, 31.67, 15% / 17% / 12.5%) - Alon's plan -01 does not.
ABOVE_MAX_M = 2.0
SLOPE_MIN_M2 = 5.0               # a smaller sloped piece (a curb / transition) gets no "X%"
# (r2) the working copy still holds Alon's own values and slopes (from the submission): where one of his
# stands, the agent adds nothing (no duplicate) - and counts it as "exists"
MANUAL_LINE_M = 1.5              # Alon's value this close to a ramp line, the same height (1.5cm) = the line is marked
MANUAL_PT_M = 6.0                # ... this close to a flat-area / roof-surface point
MANUAL_DZ_FT = 1.5 / 30.48
FLAT_MIN_M2 = 15.0
FLAT_EVERY_M = 15.0
FLAT_MIN_GAP_M = 6.0             # a flat-area value keeps this far from the break-line values
SLOPE_ARROW_M = 1.2              # the arrow under "X%" runs this long down the slope

PARAPET_MIN_CM, PARAPET_MAX_CM = 60.0, 160.0
PARAPET_LONG_M = 15.0
PARAPET_CORNER_GAP_M = 1.0
PARAPET_LABEL = u'מעקה בנוי'
LABEL_GAP_MM = 1.0               # paper mm between the value text and the label under it
ROOF_SURFACE_MIN_M2 = 6.0

ROOM_OK_KW = (u'חני', u'רמפ', u'נתיב')   # basement rooms that may get a flat-area value (parking / ramp)


# --------------------------------------------------------------------------------------------- helpers
def _clean(s):
    return (s or u'').replace(u'‏', u'').replace(u'‎', u'').strip()


def mode_of(doc, view):
    """'basement' / 'roof' / None for this plan"""
    level = view.GenLevel
    if level is None:
        return None
    name = _clean(level.Name).lower() + u' ' + _clean(view.Name).lower()
    if any(k in name for k in ROOF_KW):
        return u'roof'
    if level.Elevation * 0.3048 < BASEMENT_BELOW_M:
        return u'basement'
    return None


def _upfaces(el, opts):
    out = []
    for f in PL._faces(el.get_Geometry(opts)):
        if f.Reference is None:
            continue
        if isinstance(f, PlanarFace):
            if f.FaceNormal.Z > 0.3:
                out.append(f)
        else:
            try:
                bb = f.GetBoundingBox()
                uv = UV((bb.Min.U + bb.Max.U) / 2.0, (bb.Min.V + bb.Max.V) / 2.0)
                if f.ComputeNormal(uv).Z > 0.3:
                    out.append(f)
            except Exception:
                pass
    return out


def _slope_pct(f):
    n = f.FaceNormal
    h = (n.X * n.X + n.Y * n.Y) ** 0.5
    return 100.0 * h / n.Z if n.Z > 1e-6 else 999.0


def _face_pts(f):
    pts = []
    try:
        for loop in f.EdgeLoops:
            for e in loop:
                pts.extend(list(e.Tessellate()))
    except Exception:
        pass
    return pts


def _poly_area_m2(pts):
    if len(pts) < 3:
        return 0.0
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i].X, pts[i].Y
        x1, y1 = pts[(i + 1) % len(pts)].X, pts[(i + 1) % len(pts)].Y
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0 * 0.0929


def _seg_dist(px, py, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    if L2 < 1e-9:
        return ((px - a[0]) ** 2 + (py - a[1]) ** 2) ** 0.5
    k = max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / L2))
    return ((px - a[0] - k * vx) ** 2 + (py - a[1] - k * vy) ** 2) ** 0.5


def _fmt_pct(s):
    v = round(s * 2.0) / 2.0
    return (u'%d%%' % int(v)) if abs(v - int(v)) < 1e-6 else (u'%.1f%%' % v)


# ------------------------------------------------------------------------------------------- the run
class _Run(object):
    def __init__(self, doc, view, log):
        self.doc, self.view, self.log = doc, view, log
        self.level = view.GenLevel
        self.lz = self.level.ProjectElevation
        self.scale = view.Scale or 100
        self.mm = self.scale / 1000.0 / 0.3048
        self.schema = permit_write._label_schema(create=True)
        self.text_type = permit_write._text_type(doc)
        self.checks = []
        self.notes = []
        self.made = []                 # (rects, what) of everything placed - the overlap check at the end
        self.opts = Options()
        self.opts.ComputeReferences = True
        self.ctx = PG.Ctx()            # what permit_ground._free needs
        self.ctx.doc, self.ctx.view, self.ctx.scale, self.ctx.log = doc, view, self.scale, log
        self.ctx.occ, self.ctx.taken = None, []
        self.manual, self.manual_pct = [], []

    def read_manual(self):
        """Alon's own values (spots that are not the agent's) and slope texts ("%") in this view"""
        for s in FilteredElementCollector(self.doc, self.view.Id).OfCategory(BuiltInCategory.OST_SpotElevations):
            if PL._kind(s, self.schema) is None:
                try:
                    self.manual.append(s.Origin)
                except Exception:
                    pass
        for tn in FilteredElementCollector(self.doc, self.view.Id).OfClass(TextNote):
            if PL._kind(tn, self.schema) is None and u'%' in (tn.Text or u''):
                self.manual_pct.append(tn.Coord)
        for s in FilteredElementCollector(self.doc, self.view.Id).OfCategory(BuiltInCategory.OST_SpotSlopes) \
                .WhereElementIsNotElementType():
            if PL._kind(s, self.schema) is None:
                a = _anchor(s, self.view)
                if a is not None:
                    self.manual_pct.append(a)
        self.log(u'roofbase: %d manual values, %d manual slope texts in the view' % (len(self.manual), len(self.manual_pct)))

    def manual_on_line(self, a, b, z):
        r = MANUAL_LINE_M / 0.3048
        return any(abs(m.Z - z) < MANUAL_DZ_FT and _seg_dist(m.X, m.Y, (a.X, a.Y), (b.X, b.Y)) < r for m in self.manual)

    def manual_near(self, x, y, z, r_m=MANUAL_PT_M):
        r = r_m / 0.3048
        return any(abs(m.Z - z) < MANUAL_DZ_FT and ((m.X - x) ** 2 + (m.Y - y) ** 2) ** 0.5 < r for m in self.manual)

    # ---- elements of the view
    def surfaces(self, cats=('OST_Floors',)):
        """[(element, type name, [upward faces])] of the floors (roofs) visible in this view"""
        out = []
        for cn in cats:
            bic = getattr(BuiltInCategory, cn, None)
            if bic is None:
                continue
            for el in FilteredElementCollector(self.doc, self.view.Id).OfCategory(bic).WhereElementIsNotElementType():
                tname = permit_rooms.elem_name(self.doc.GetElement(el.GetTypeId())) or u''
                if PL._is_landscape(tname):
                    continue
                fs = _upfaces(el, self.opts)
                if fs:
                    out.append((el, tname, fs))
        return out

    def scan(self, elems):
        xs, ys = [], []
        for el in elems:
            bb = el.get_BoundingBox(None)
            if bb is not None:
                xs += [bb.Min.X, bb.Max.X]
                ys += [bb.Min.Y, bb.Max.Y]
        if not xs:
            return
        try:
            self.ctx.occ = permit_write.view_occupancy(
                self.doc, self.view, (min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5),
                cell=0.2, extra_skip=PL.SITE_SURFACE_CATS + ['OST_Roofs'])
        except Exception as ex:
            self.log(u'roofbase: drawing scan failed (%s) - texts only' % ex)
        PG.refresh_texts(self.ctx)

    # ---- creation
    def spot(self, ref, pt, typ, what):
        right = XYZ(1.0, 0, 0)
        try:
            sp = self.doc.Create.NewSpotElevation(self.view, ref, pt, pt + right, pt + right * 2.0, pt, False)
        except Exception as ex:
            self.log(u'%s: spot failed: %s' % (what, ex))
            return None
        if typ is not None:
            try:
                sp.ChangeTypeId(typ.Id)
            except Exception as ex:
                self.log(u'%s: type not set (%s)' % (what, ex))
        PL._mark(sp, self.schema, KIND)
        self.doc.Regenerate()
        return sp

    def spot_rect(self, sp):
        """the value text box of a spot (its own box when it has no symbol - the vertical type too)"""
        try:
            bb = sp.get_BoundingBox(self.view)
        except Exception:
            bb = None
        rs = PG.spot_text_rects(self.doc, sp, self.scale)
        if bb is not None and (bb.Max.X - bb.Min.X) < 12 * self.mm and (bb.Max.Y - bb.Min.Y) < 12 * self.mm:
            pad = 0.3 * self.mm
            return (bb.Min.X - pad, bb.Min.Y - pad, bb.Max.X + pad, bb.Max.Y + pad)
        return rs[0] if rs else None

    def free(self, rects):
        return all(r is not None for r in rects) and PG._free(self.ctx, rects)

    def take(self, rects, what):
        rects = [r for r in rects if r is not None]
        self.ctx.taken.extend(rects)
        self.made.append((rects, what))

    def note(self, pt, text, leader_to=None, align=HorizontalTextAlignment.Center, rotation=None):
        if self.text_type is None:
            return None
        o = TextNoteOptions(self.text_type.Id)
        o.HorizontalAlignment = align
        o.VerticalAlignment = VerticalTextAlignment.Middle
        if rotation is not None:
            o.Rotation = rotation
        tn = TextNote.Create(self.doc, self.view.Id, XYZ(pt.X, pt.Y, 0), text, o)
        if leader_to is not None:
            try:
                kind = TextNoteLeaderTypes.TNLT_STRAIGHT_R if leader_to.X >= pt.X else TextNoteLeaderTypes.TNLT_STRAIGHT_L
                ld = tn.AddLeader(kind)
                ld.End = XYZ(leader_to.X, leader_to.Y, 0)
            except Exception as ex:
                self.log(u'arrow failed for "%s": %s' % (text, ex))
        PL._mark(tn, self.schema, KIND)
        return tn

    def text_rect(self, text, cx, cy):
        hw, hh = permit_write._label_box(text, self.scale)
        return (cx - hw, cy - hh, cx + hw, cy + hh)

    def overlaps(self):
        """my values / texts against each other and against every other text and value of the view, as
        they stand now (after the run) -> (count, [what])"""
        try:
            self.doc.Regenerate()
        except Exception:
            pass
        others = []
        for tn in FilteredElementCollector(self.doc, self.view.Id).OfClass(TextNote):
            if PL._kind(tn, self.schema) != KIND:
                r = PG.note_rect(tn, self.view, self.scale)
                if r is not None:
                    others.append(r)
        for s in FilteredElementCollector(self.doc, self.view.Id).OfCategory(BuiltInCategory.OST_SpotElevations):
            if PL._kind(s, self.schema) != KIND:
                others.extend(PG.spot_text_rects(self.doc, s, self.scale)[:1])
        bad = []
        for i, (rs, what) in enumerate(self.made):
            hit = False
            for r in rs:
                if any(PL._rects_overlap(r, q) for q in others):
                    hit = True
                for j, (rs2, _w2) in enumerate(self.made):
                    if j != i and any(PL._rects_overlap(r, q) for q in rs2):
                        hit = True
            if hit:
                bad.append(what)
        return len(bad), bad


# ------------------------------------------------------------------------------------------ basement
def _breaks_of(run, floors):
    """the ramp lines: horizontal edges (one z) of the sloped faces, merged where two faces share them
    -> lines [{'a','b','z','face','into'}], sloped [(face, slope %, element)]"""
    lo, hi = run.lz - 1.0 / 0.3048, run.lz + ABOVE_MAX_M / 0.3048
    lines, sloped = [], []
    for el, tname, faces in floors:
        for f in faces:
            if not isinstance(f, PlanarFace):
                continue
            s = _slope_pct(f)
            if s < RAMP_MIN_SLOPE or s > 30.0:
                continue
            pts = _face_pts(f)
            if not pts or sum(p.Z for p in pts) / len(pts) > hi or max(p.Z for p in pts) < lo:
                continue
            sloped.append((f, s, el))
            for loop in f.EdgeLoops:
                for e in loop:
                    tp = list(e.Tessellate())
                    if len(tp) < 2:
                        continue
                    if max(p.Z for p in tp) - min(p.Z for p in tp) > 0.5 / 30.48:
                        continue
                    a, b = tp[0], tp[-1]
                    L = ((a.X - b.X) ** 2 + (a.Y - b.Y) ** 2) ** 0.5
                    if L * 0.3048 < BREAK_MIN_LEN_M:
                        continue
                    mx, my = (a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0
                    nx, ny = -(b.Y - a.Y) / L, (b.X - a.X) / L
                    into = None
                    for sg in (1.0, -1.0):
                        if PL._face_z(f, mx + sg * nx * 0.3, my + sg * ny * 0.3, a.Z) is not None:
                            into = (sg * nx, sg * ny)
                            break
                    if into is None:
                        continue
                    z = sum(p.Z for p in tp) / len(tp)
                    if z > hi or z < lo:
                        continue
                    dup = None
                    for ln in lines:
                        if abs(ln['z'] - z) < 1.0 / 30.48 and _seg_dist(mx, my, (ln['a'].X, ln['a'].Y), (ln['b'].X, ln['b'].Y)) < 0.5:
                            dup = ln
                            break
                    if dup is not None:
                        continue
                    lines.append({'a': a, 'b': b, 'z': z, 'face': f, 'into': into, 'len': L, 'type': tname})
    return lines, sloped


def _anchor(el, view=None):
    """the point a spot (slope) measures - its Origin, else the centre of its box"""
    try:
        return el.Origin
    except Exception:
        pass
    try:
        bb = el.get_BoundingBox(view)
        return XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, (bb.Min.Z + bb.Max.Z) / 2.0)
    except Exception:
        return None


def _slope_template(run):
    """(r3) Alon (01.10.26): the slope must be a real Spot Slope, so it follows the model. The Revit API cannot
    create one (only NewSpotElevation / NewSpotCoordinate exist) - so one Spot Slope that Alon placed by hand is
    the template, and the agent copies it onto every ramp piece. -> (element, its view) or (None, None)"""
    best = None
    for s in FilteredElementCollector(run.doc).OfCategory(BuiltInCategory.OST_SpotSlopes).WhereElementIsNotElementType():
        k = PL._kind(s, run.schema)
        if k == KIND:
            continue                                   # never a copy of the agent's own
        v = run.doc.GetElement(s.OwnerViewId)
        if v is None:
            continue
        score = (0 if s.OwnerViewId == run.view.Id else 1)
        if best is None or score < best[0]:
            best = (score, s, v)
    return (best[1], best[2]) if best else (None, None)


def _parse_pct(s):
    try:
        return float((s or u'').replace(u'%', u'').replace(u',', u'.').strip())
    except Exception:
        return None


def _copy_slope(run, tmpl, tview, target):
    """a copy of the template Spot Slope whose measured point lands on `target` -> the new element or None"""
    o = _anchor(tmpl, tview)
    if o is None:
        return None
    t = XYZ(target.X - o.X, target.Y - o.Y, 0)
    try:
        if tview.Id == run.view.Id:
            ids = list(ElementTransformUtils.CopyElement(run.doc, tmpl.Id, t))
        else:
            ids = list(ElementTransformUtils.CopyElements(tview, List[ElementId]([tmpl.Id]), run.view,
                                                          Transform.CreateTranslation(t), CopyPasteOptions()))
        run.doc.Regenerate()
    except Exception as ex:
        run.log(u'spot slope copy failed: %s' % ex)
        return None
    for i in ids:
        el = run.doc.GetElement(i)
        if el is not None:
            PL._mark(el, run.schema, KIND)
            return el
    return None


def _arrow(run, p0, p1):
    """a drawn arrow from p0 to p1 (detail lines: the shaft and a 2mm head), marked as this module's"""
    try:
        L = ((p1.X - p0.X) ** 2 + (p1.Y - p0.Y) ** 2) ** 0.5
        ux, uy = (p1.X - p0.X) / L, (p1.Y - p0.Y) / L
        hl = 2.0 * run.mm
        segs = [(p0, p1)]
        for sg in (1.0, -1.0):
            c, s_ = math.cos(math.radians(20)), sg * math.sin(math.radians(20))
            bx, by = -(ux * c - uy * s_), -(ux * s_ + uy * c)
            segs.append((p1, XYZ(p1.X + bx * hl, p1.Y + by * hl, 0)))
        for a, b in segs:
            dc = run.doc.Create.NewDetailCurve(run.view, Line.CreateBound(XYZ(a.X, a.Y, 0), XYZ(b.X, b.Y, 0)))
            PL._mark(dc, run.schema, KIND)
        pad = 0.5 * run.mm
        # (a drawn line, not a text: later texts keep off it, the text-on-text check leaves it out)
        run.ctx.taken.append((min(p0.X, p1.X) - pad, min(p0.Y, p1.Y) - pad, max(p0.X, p1.X) + pad, max(p0.Y, p1.Y) + pad))
        return True
    except Exception as ex:
        run.log(u'slope arrow failed: %s' % ex)
        return False


def _view_dir(run, dx, dy):
    """the direction (dx, dy) in the view: True when it reads more vertical than horizontal"""
    r, u = run.view.RightDirection, run.view.UpDirection
    return abs(dx * u.X + dy * u.Y) > abs(dx * r.X + dy * r.Y)


def _basement(run, out):
    doc = run.doc
    floors = run.surfaces()
    run.scan([el for el, _t, _f in floors])
    st_h = PL._spot_type(doc, SURVEY_H_KEY)
    st_v = PL._spot_type(doc, SURVEY_V_KEY) or st_h
    run.log(u'roofbase basement: %d floors in the view, survey types %s / %s' % (
        len(floors), permit_rooms.elem_name(st_h) if st_h else u'NOT FOUND', permit_rooms.elem_name(st_v) if st_v else u'-'))
    if st_h is None:
        run.notes.append(u'סוג "%s" לא נמצא - נוצר בסוג ברירת המחדל' % SURVEY_H_KEY)

    # 1. ramp lines: an absolute value on every line where the slope changes
    lines, sloped = _breaks_of(run, floors)
    run.log(u'roofbase basement: %d sloped faces, %d ramp lines' % (len(sloped), len(lines)))
    done_lines = manual_lines = 0
    break_pts = []
    for ln in lines:
        a, b = ln['a'], ln['b']
        vert = _view_dir(run, b.X - a.X, b.Y - a.Y)
        typ = st_v if vert else st_h
        absm = PL._absolute_m(doc, ln['z'])
        what = (u'מרתף: קו רמפה %.2f' % absm) if absm is not None else u'מרתף: קו רמפה'
        if run.manual_on_line(a, b, ln['z']):
            manual_lines += 1
            break_pts.append(XYZ((a.X + b.X) / 2.0, (a.Y + b.Y) / 2.0, ln['z']))
            run.log(u'%s: Alon\'s own value stands on the line - nothing added' % what)
            continue
        placed = None
        for k in (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9):
            x = a.X + (b.X - a.X) * k + ln['into'][0] * BREAK_INSET_FT
            y = a.Y + (b.Y - a.Y) * k + ln['into'][1] * BREAK_INSET_FT
            zz = PL._face_z(ln['face'], x, y, ln['z'] + 1.0)
            if zz is None:
                continue
            sp = run.spot(ln['face'].Reference, XYZ(x, y, zz), typ, what)
            if sp is None:
                continue
            r = run.spot_rect(sp)
            if run.free([r]):
                placed = (sp, r, k)
                break
            doc.Delete(sp.Id)
        if placed is None:
            run.notes.append(u'%s: אין מקום נקי על הקו' % what)
            run.log(u'%s: no clear place along the line (%.1fm, %s)' % (what, ln['len'] * 0.3048, ln['type']))
            continue
        sp, r, k = placed
        run.take([r], what)
        break_pts.append(sp.Origin)
        done_lines += 1
        run.log(u'%s: %s at %.0f%% of the line (%.1fm), %s' % (
            what, getattr(sp, 'ValueString', u''), k * 100, ln['len'] * 0.3048, u'vertical' if vert else u'horizontal'))
    run.checks.append((u'מרתף: ערך מוחלט (בלי סימבול) בכל קו שבירת שיפוע ברמפה', len(lines), done_lines + manual_lines,
                       u'העובד הוסיף %d; על %d קווים כבר יש ערך שלך; רמפה = רצפה משופעת מ-%g%% עד %g מ\' מעל הקומה' % (
                           done_lines, manual_lines, RAMP_MIN_SLOPE, ABOVE_MAX_M)))

    # 2. "X%" with an arrow down the slope in every sloped piece. (r2) The text reads along the arrow, as in
    # Alon's plans (r1 wrote it level, above a vertical arrow)
    done_sl = manual_sl = n_sl = done_spot = 0
    spot_fail = []
    tmpl, tview = _slope_template(run)
    run.log(u'spot slope template: %s' % (u'%d in view "%s"' % (permit_rooms._id(tmpl.Id), tview.Name) if tmpl is not None
                                          else u'NONE - slopes as text (Alon: place one Spot Slope by hand)'))
    r_dir, u_dir = run.view.RightDirection, run.view.UpDirection
    for f, s, el in sloped:
        pts = _face_pts(f)
        if _poly_area_m2(pts) < SLOPE_MIN_M2:
            continue
        n_sl += 1
        n = f.FaceNormal
        h = (n.X * n.X + n.Y * n.Y) ** 0.5
        dx, dy = n.X / h, n.Y / h                       # the normal leans down the slope
        cx = sum(p.X for p in pts) / len(pts)
        cy = sum(p.Y for p in pts) / len(pts)
        text = _fmt_pct(s)
        if any(PL._face_z(f, m.X, m.Y, 0.0) is not None for m in run.manual_pct):
            manual_sl += 1
            run.log(u'slope %s: Alon\'s own slope text stands on it - nothing added' % text)
            continue
        # the angle of the arrow in the view, turned to read left-to-right / bottom-to-top
        vx, vy = dx * r_dir.X + dy * r_dir.Y, dx * u_dir.X + dy * u_dir.Y
        ang = math.atan2(vy, vx)
        if ang > math.pi / 2.0 + 1e-6:
            ang -= math.pi
        elif ang <= -math.pi / 2.0 + 1e-6:
            ang += math.pi
        vertical = abs(math.sin(ang)) > 0.7
        hw, hh = permit_write._label_box(text, run.scale)
        half = SLOPE_ARROW_M / 0.3048 / 2.0
        # (r3) a real Spot Slope - a copy of Alon's template - at the first clear place around the centre
        if tmpl is not None:
            got, why = None, u''
            for off in (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5):
                for along in (0.0, 0.8, -0.8):
                    px = cx - dy * off / 0.3048 + dx * along / 0.3048
                    py = cy + dx * off / 0.3048 + dy * along / 0.3048
                    pz = PL._face_z(f, px, py, 0.0)
                    if pz is None:
                        continue
                    el = _copy_slope(run, tmpl, tview, XYZ(px, py, pz))
                    if el is None:
                        why = u'ההעתקה נכשלה'
                        break
                    val = _parse_pct(getattr(el, 'ValueString', None))
                    if val is not None and abs(val - s) > 0.6:
                        # the copy kept the template's ramp (its value is not this ramp's) - not usable
                        run.log(u'slope %s: the copied Spot Slope shows %s - deleted' % (text, val))
                        doc.Delete(el.Id)
                        why = u'העותק הראה %s במקום %s' % (val, text)
                        break
                    bb = el.get_BoundingBox(run.view)
                    r = (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y) if bb is not None else None
                    if r is not None and run.free([r]):
                        got = (el, r, val)
                        break
                    doc.Delete(el.Id)
                if got is not None or why:
                    break
            if got is not None:
                run.take([got[1]], u'Spot Slope %s' % text)
                done_spot += 1
                run.log(u'slope %s: Spot Slope copied (shows %s)' % (text, got[2] if got[2] is not None else u'value not read'))
                continue
            if why:
                spot_fail.append(why)
        ok = None
        side = (hh + 1.0 * run.mm)                      # the text beside the arrow, not on it
        for off in (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5):
            for along in (0.0, 0.8, -0.8):
                px = cx - dy * off / 0.3048 + dx * along / 0.3048
                py = cy + dx * off / 0.3048 + dy * along / 0.3048
                if PL._face_z(f, px, py, 0.0) is None:
                    continue                                 # (planar face: the last argument is not used)
                for sg in (1.0, -1.0):
                    tx, ty = px - dy * side * sg, py + dx * side * sg
                    r = (tx - hh, ty - hw, tx + hh, ty + hw) if vertical else (tx - hw, ty - hh, tx + hw, ty + hh)
                    if run.free([r]):
                        ok = (XYZ(tx, ty, 0), XYZ(px - dx * half, py - dy * half, 0), XYZ(px + dx * half, py + dy * half, 0), r)
                        break
                if ok:
                    break
            if ok:
                break
        if ok is None:
            run.notes.append(u'שיפוע %s: אין מקום נקי' % text)
            continue
        if run.note(ok[0], text, rotation=ang) is not None and _arrow(run, ok[1], ok[2]):
            run.take([ok[3]], u'שיפוע %s' % text)
            done_sl += 1
    if tmpl is None:
        how = u'אין Spot Slope לדוגמה בפרויקט - נכתב טקסט. כדי לקבל Spot Slope: למקם ידנית אחד על רמפה אחת'
    else:
        how = u'Spot Slope (העתק של %d): %d, טקסט: %d%s' % (permit_rooms._id(tmpl.Id), done_spot, done_sl,
                                                          (u' (' + u'; '.join(spot_fail[:2]) + u')') if spot_fail else u'')
    run.checks.append((u'מרתף: שיפוע בכל קטע משופע (Spot Slope)', n_sl, done_spot + done_sl + manual_sl,
                       u'%s; ב-%d כבר יש שיפוע שלך; קטע מעל %g מ"ר' % (how, manual_sl, SLOPE_MIN_M2)))
    out['slope_spots'] = done_spot

    # 3. flat areas of the driving lanes: one absolute value (+ every 15m), away from the ramp values
    rooms = permit_write.agent_rooms(doc, run.level)
    z_in = run.lz + 1.0

    def blocked_room(x, y):
        for rm in rooms:
            if rm.IsPointInRoom(XYZ(x, y, z_in)):
                p = rm.get_Parameter(BuiltInParameter.ROOM_NAME)
                nm = _clean(p.AsString() if p is not None else u'')
                return not any(k in nm for k in ROOM_OK_KW)
        return False

    flat_found = flat_done = flat_manual = 0
    placed_flat = []
    gap = FLAT_MIN_GAP_M / 0.3048
    for el, tname, faces in floors:
        for f in faces:
            if not isinstance(f, PlanarFace) or _slope_pct(f) >= RAMP_MIN_SLOPE:
                continue
            if abs(f.Origin.Z - run.lz) > 0.6 / 0.3048:
                continue
            pts = _face_pts(f)
            area = _poly_area_m2(pts)
            if area < FLAT_MIN_M2:
                continue
            xs, ys = [p.X for p in pts], [p.Y for p in pts]
            step = FLAT_EVERY_M / 0.3048
            seeds = []
            nx_ = max(1, int((max(xs) - min(xs)) / step) + 1)
            ny_ = max(1, int((max(ys) - min(ys)) / step) + 1)
            for i in range(nx_):
                for j in range(ny_):
                    seeds.append((min(xs) + (i + 0.5) * (max(xs) - min(xs)) / nx_,
                                  min(ys) + (j + 0.5) * (max(ys) - min(ys)) / ny_))
            for sx, sy in seeds:
                if run.manual_near(sx, sy, f.Origin.Z, FLAT_EVERY_M / 2.0):
                    flat_found += 1
                    flat_manual += 1
                    continue
                flat_found += 1
                got = None
                why = {'off': 0, 'room': 0, 'gap': 0, 'busy': 0}
                rr, rstep = 0.0, 0.5 / 0.3048
                while rr <= 5.0 / 0.3048 and got is None:
                    for kk in (range(12) if rr > 0 else [0]):
                        aa = 2 * math.pi * kk / 12.0
                        x, y = sx + rr * math.cos(aa), sy + rr * math.sin(aa)
                        zz = PL._face_z(f, x, y, f.Origin.Z + 1.0)
                        if zz is None:
                            why['off'] += 1
                            continue
                        if blocked_room(x, y):
                            why['room'] += 1
                            continue
                        if any(((x - q.X) ** 2 + (y - q.Y) ** 2) ** 0.5 < gap for q in break_pts + placed_flat):
                            why['gap'] += 1
                            continue
                        est = PG._value_rect(run.ctx, x, y)
                        if not run.free([est]):
                            why['busy'] += 1
                            continue
                        sp = run.spot(f.Reference, XYZ(x, y, zz), st_h, u'מרתף: אזור שטוח')
                        if sp is None:
                            continue
                        r = run.spot_rect(sp)
                        if run.free([r]):
                            got = (sp, r)
                            break
                        doc.Delete(sp.Id)
                    rr += rstep
                if got is None:
                    run.log(u'flat area (%s, %.0fm2): no clear place near %s cm (rejected: off the face %d, in a room %d, '
                            u'near a ramp value %d, drawn / text %d)' % (tname, area, [round(sx * 30.48), round(sy * 30.48)],
                                                                         why['off'], why['room'], why['gap'], why['busy']))
                    continue
                run.take([got[1]], u'מרתף: אזור שטוח')
                placed_flat.append(got[0].Origin)
                flat_done += 1
    run.checks.append((u'מרתף: ערך מוחלט באזור שטוח בנתיב (+ כל 15 מ\')', flat_found, flat_done + flat_manual,
                       u'העובד הוסיף %d; ב-%d כבר יש ערך שלך; לא ליד ערכי הרמפה (%g מ\'), לא במחסנים / חדרים טכניים' % (
                           flat_done, flat_manual, FLAT_MIN_GAP_M)))

    # 4. the lobby's relative unit is "עדכון מפלסים" - only reported here
    lobby_spots = [s for s in FilteredElementCollector(doc, run.view.Id).OfClass(SpotDimension)
                   if PL._kind(s, run.schema) == u'spot']
    run.checks.append((u'מרתף: לובי - ערך יחסי (נוצר ב"עדכון מפלסים")', u'-', len(lobby_spots), u''))
    out['spots'] = done_lines + flat_done
    out['slopes'] = done_sl


# ---------------------------------------------------------------------------------------------- roof
def _parapets(run, roof_hit, outside=None):
    """the built parapets: walls of this level whose top is free, 60-160cm above the roof next to them,
    with the roof on one side only -> [(wall, curve, top face (ref, z), height cm)]"""
    doc = run.doc
    out = []
    for w in FilteredElementCollector(doc, run.view.Id).OfClass(Wall):
        try:
            c = getattr(w.Location, 'Curve', None)
            bb = w.get_BoundingBox(None)
            if c is None or bb is None or not (run.lz - 1.0 <= bb.Min.Z <= run.lz + 2.0):
                continue
            if not permit_rooms.is_free_top(doc, w, run.level):
                continue
            mid = c.Evaluate(0.5, True)
            d = c.ComputeDerivatives(0.5, True).BasisX.Normalize()
            nx, ny = d.Y, -d.X
            off = w.Width / 2.0 + 0.4
            probes = [(mid.X + sg * nx * off, mid.Y + sg * ny * off) for sg in (1.0, -1.0)]
            sides = [roof_hit(px, py) for px, py in probes]
            roofz = [s for s in sides if s is not None]
            # (r2) the roof on ONE side only, and the other side outside the roof altogether - r1 took the walls
            # around the shafts and the stair (the roof on one side, the shaft "hole" on the other) for parapets
            if len(roofz) != 1:
                continue
            ox, oy = probes[1] if sides[0] is not None else probes[0]
            if outside is not None and not outside(ox, oy):
                continue
            fz = roofz[0]
            hcm = (bb.Max.Z - fz) * 30.48
            if not (PARAPET_MIN_CM <= hcm <= PARAPET_MAX_CM):
                continue
            top = PL._wall_top_face(w)
            if top is None:
                continue
            inside = 1.0 if sides[0] is not None else -1.0
            out.append((w, c, top, hcm, (inside * nx, inside * ny)))
        except Exception as ex:
            run.log(u'parapet check failed for wall %s: %s' % (permit_rooms._id(w.Id), ex))
    return out


def _roof_unit(run, ref, pts, typ, label, what, inward=None, face=None):
    """a spot with a label under its value. (r2) The value stays by its symbol (r1 moved it up to 14mm away,
    and it read as detached): the symbol tries the points `pts` in turn (along the wall / around the
    centre), the value only a few mm. -> True / False"""
    doc = run.doc
    mm = run.mm
    tries = [(0.0, 0.0), (0.0, 3.0 * mm), (0.0, -3.0 * mm)]
    if inward is not None:
        tries.insert(1, (inward[0] * 3 * mm, inward[1] * 3 * mm))
    hw, hh = permit_write._label_box(label, run.scale)
    for pt in pts:
        if face is not None:
            zz = PL._face_z(face, pt.X, pt.Y, pt.Z + 3.0)
            if zz is None:
                continue
            pt = XYZ(pt.X, pt.Y, zz)
        sp = run.spot(ref, pt, typ, what)
        if sp is None:
            continue
        try:
            tp0 = sp.TextPosition
        except Exception:
            tp0 = None
        for dx, dy in tries:
            if dx or dy:
                if tp0 is None:
                    continue
                try:
                    sp.TextPosition = XYZ(tp0.X + dx, tp0.Y + dy, tp0.Z)
                    doc.Regenerate()
                except Exception:
                    continue
            rs = PG.spot_text_rects(doc, sp, run.scale)
            if not rs:
                continue
            v = rs[0]
            lc = ((v[0] + v[2]) / 2.0, v[1] - LABEL_GAP_MM * mm - hh)
            lr = (lc[0] - hw, lc[1] - hh, lc[0] + hw, lc[1] + hh)
            if run.free([v, lr]):
                if run.note(XYZ(lc[0], lc[1], 0), label) is None:
                    doc.Delete(sp.Id)
                    return False
                run.take([v, lr], what)
                run.log(u'%s: at %s cm, text moved %s mm' % (
                    what, [round(pt.X * 30.48), round(pt.Y * 30.48)], [round(dx / mm), round(dy / mm)]))
                return True
        doc.Delete(sp.Id)
    run.notes.append(u'%s: אין מקום נקי' % what)
    run.log(u'%s: no clear place for the value + "%s" - left out' % (what, label))
    return False


def _roof(run, out):
    doc = run.doc
    floors = run.surfaces(('OST_Floors', 'OST_Roofs'))
    walls = list(FilteredElementCollector(doc, run.view.Id).OfClass(Wall))
    run.scan([el for el, _t, _f in floors] + walls)
    stype = PL._spot_type(doc)                                   # "** PL_Base_Point - MOST USE NEW"
    run.log(u'roofbase roof: %d floors / roofs in the view, spot type %s' % (
        len(floors), permit_rooms.elem_name(stype) if stype else u'NOT FOUND'))
    if stype is None:
        run.notes.append(u'סוג "%s" לא נמצא' % PL.SPOT_TYPE_NAME)

    lo, hi = run.lz - 0.4 / 0.3048, run.lz + 0.8 / 0.3048
    roof_faces = []
    for el, tname, faces in floors:
        for f in faces:
            pts = _face_pts(f)
            if not pts:
                continue
            zc = sum(p.Z for p in pts) / len(pts)
            if lo <= zc <= hi:
                roof_faces.append((f, pts, tname))

    def roof_hit(x, y):
        best = None
        for f, pts, _t in roof_faces:
            zz = PL._face_z(f, x, y, run.lz + 3.0)
            if zz is not None and (best is None or zz > best):
                best = zz
        return best

    # the outer outline of every roof face (its largest loop - the shafts are its inner loops)
    outers = []
    for f, pts, _t in roof_faces:
        best = None
        try:
            for loop in f.EdgeLoops:
                lp = []
                for e in loop:
                    lp.extend([(p.X, p.Y) for p in e.Tessellate()])
                if len(lp) >= 3:
                    a2 = abs(sum(lp[i][0] * lp[(i + 1) % len(lp)][1] - lp[(i + 1) % len(lp)][0] * lp[i][1]
                                 for i in range(len(lp))))
                    if best is None or a2 > best[0]:
                        best = (a2, lp)
        except Exception:
            pass
        if best is not None:
            outers.append(best[1])

    def outside(x, y):
        return not any(permit_rooms.point_in_polygons(x, y, [poly]) for poly in outers)

    # 1. the parapet: its top at the corners (+ the middle of a long side), "מעקה בנוי" under the value
    pars = _parapets(run, roof_hit, outside if outers else None)
    run.log(u'roofbase roof: %d roof faces, %d parapet walls (on the outer edge)' % (len(roof_faces), len(pars)))
    ends = []
    for w, c, top, hcm, inward in pars:
        for k in (0, 1):
            p = c.GetEndPoint(k)
            q = c.GetEndPoint(1 - k)
            L = ((q.X - p.X) ** 2 + (q.Y - p.Y) ** 2) ** 0.5 or 1.0
            ends.append({'p': p, 'dir': ((q.X - p.X) / L, (q.Y - p.Y) / L), 'wall': w, 'top': top, 'in': inward,
                         'width': w.Width, 'len': L})
    corners = []
    for i, e in enumerate(ends):
        for j in range(i + 1, len(ends)):
            f2 = ends[j]
            if permit_rooms._id(e['wall'].Id) == permit_rooms._id(f2['wall'].Id):
                continue
            dist = ((e['p'].X - f2['p'].X) ** 2 + (e['p'].Y - f2['p'].Y) ** 2) ** 0.5
            if dist > e['width'] + f2['width'] + 0.1:
                continue
            cosang = abs(e['dir'][0] * f2['dir'][0] + e['dir'][1] * f2['dir'][1])
            if cosang > 0.87:
                continue                                   # a straight joint, not a corner
            cx, cy = (e['p'].X + f2['p'].X) / 2.0, (e['p'].Y + f2['p'].Y) / 2.0
            if any(((cx - q[0]) ** 2 + (cy - q[1]) ** 2) ** 0.5 < PARAPET_CORNER_GAP_M / 0.3048 for q in corners):
                continue
            corners.append((cx, cy))
            # the symbol on the top of the first wall: a wall-width in from the corner, or a little further
            # along the wall when the value has no clear place there
            e['corner'] = True
    par_found = par_done = par_manual = 0
    for e in ends:
        if not e.get('corner'):
            continue
        par_found += 1
        ref, tz = e['top']
        if run.manual_near(e['p'].X, e['p'].Y, tz, PARAPET_CORNER_GAP_M * 1.5):
            par_manual += 1
            continue
        pts = []
        for k_m in (0.0, 0.4, 0.8, 1.2):
            k = e['width'] + k_m / 0.3048
            if k < e['len'] / 2.0:
                pts.append(XYZ(e['p'].X + e['dir'][0] * k, e['p'].Y + e['dir'][1] * k, tz))
        if _roof_unit(run, ref, pts, stype, PARAPET_LABEL, u'גג: מעקה בנוי (פינה)', e['in']):
            par_done += 1
    for w, c, top, hcm, inward in pars:
        if c.Length * 0.3048 < PARAPET_LONG_M:
            continue
        m = c.Evaluate(0.5, True)
        par_found += 1
        if run.manual_near(m.X, m.Y, top[1], 3.0):
            par_manual += 1
            continue
        d = c.ComputeDerivatives(0.5, True).BasisX.Normalize()
        pts = [XYZ(m.X + d.X * s_ / 0.3048, m.Y + d.Y * s_ / 0.3048, top[1]) for s_ in (0.0, 0.8, -0.8, 1.6, -1.6)]
        if _roof_unit(run, top[0], pts, stype, PARAPET_LABEL, u'גג: מעקה בנוי (אמצע צלע)', inward):
            par_done += 1
    run.checks.append((u'גג: ראש מעקה בנוי בפינות (+ אמצע צלע מעל %g מ\'), "מעקה בנוי" מתחת' % PARAPET_LONG_M,
                       par_found, par_done + par_manual,
                       u'העובד הוסיף %d; ב-%d כבר יש ערך שלך; %d קירות מעקה בקצה הגג (%g-%g ס"מ, ראש לא קשור)' % (
                           par_done, par_manual, len(pars), PARAPET_MIN_CM, PARAPET_MAX_CM)))

    # 2. the roof surface: one value in every roof face, "קומת <level>" under it
    label = u'קומת ' + _clean(run.level.Name)
    surf_found = surf_done = surf_manual = 0
    for f, pts, tname in roof_faces:
        if _poly_area_m2(pts) < ROOF_SURFACE_MIN_M2:
            continue
        surf_found += 1
        if any(abs(m.Z - (PL._face_z(f, m.X, m.Y, run.lz + 3.0) or -1e9)) < MANUAL_DZ_FT for m in run.manual):
            surf_manual += 1
            continue
        cx = sum(p.X for p in pts) / len(pts)
        cy = sum(p.Y for p in pts) / len(pts)
        cands = []
        rr = 0.0
        while rr <= 6.0 / 0.3048:
            for kk in (range(12) if rr > 0 else [0]):
                aa = 2 * math.pi * kk / 12.0
                x, y = cx + rr * math.cos(aa), cy + rr * math.sin(aa)
                if PL._face_z(f, x, y, run.lz + 3.0) is None:
                    continue
                if not run.free([PG._value_rect(run.ctx, x + 4 * run.mm, y + 1.5 * run.mm)]):
                    continue
                cands.append(XYZ(x, y, run.lz))
            rr += 0.6 / 0.3048
        if cands and _roof_unit(run, f.Reference, cands[:12], stype, label, u'גג: פני הגג (%s)' % tname, face=f):
            surf_done += 1
    run.checks.append((u'גג: פני הגג בכל משטח, "%s" מתחת' % label, surf_found, surf_done + surf_manual,
                       u'העובד הוסיף %d; ב-%d כבר יש ערך שלך' % (surf_done, surf_manual)))
    run.checks.append((u'גג: ניקוז 1.5% וחצים לנקזים', u'-', 0, u'הנקזים לא ממודלים (אלון 01.10.26) - לא מבוצע'))
    out['spots'] = par_done + surf_done


# --------------------------------------------------------------------------------------- entry point
def run_roof_basement_view(doc, view, log):
    """basement / roof plan -> {'checks': [(rule, found, done, note)], ...}; other plans: nothing"""
    md = mode_of(doc, view)
    if md is None:
        return {'checks': [], 'mode': None}
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        raise Exception(u'הכלי רץ רק על עותק עבודה - שם הקובץ חייב להכיל %s' % permit_write.WRITE_MARK)
    log(u'%s | view %s, level %s, mode %s' % (VERSION, view.Name, _clean(view.GenLevel.Name), md))
    run = _Run(doc, view, log)
    out = {'mode': md, 'spots': 0, 'removed': 0}
    tg = TransactionGroup(doc, u'PermitAgent - מרתף / גג')
    tg.Start()
    rb_tx = None
    try:
        rb_tx = Transaction(doc, u'PermitAgent - מרתף / גג')
        rb_tx.Start()
        old = []
        for cls in (SpotDimension, TextNote, CurveElement):
            old += [e for e in FilteredElementCollector(doc, view.Id).OfClass(cls) if PL._kind(e, run.schema) == KIND]
        for e in old:
            try:
                doc.Delete(e.Id)
            except Exception:
                pass
        out['removed'] = len(old)
        doc.Regenerate()
        run.read_manual()
        if md == u'basement':
            _basement(run, out)
        else:
            _roof(run, out)
        n_bad, bad = run.overlaps()
        run.checks.append((u'טקסט על טקסט (הערכים והכיתובים של שלב זה)', len(run.made), n_bad,
                           (u'חופפים: ' + u', '.join(bad[:5])) if bad else u'0 = תקין'))
        if run.notes:
            run.checks.append((u'הערות', u'-', u'-', u' | '.join(run.notes[:6])))
        rb_tx.Commit()
        tg.Assimilate()
    except Exception:
        permit_write.abort_open(rb_tx)
        permit_write.abort_open(tg)
        raise
    for c in run.checks:
        log(u'CHECK | %s | %s | %s | %s' % c)
    out['checks'] = run.checks
    return out
