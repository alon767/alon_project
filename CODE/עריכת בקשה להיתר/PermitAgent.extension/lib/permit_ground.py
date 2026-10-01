# -*- coding: utf-8 -*-
"""PermitAgent - ground-floor rules (Alon, 30.09.26, file "עדכונים שעשיתי..." + corrected PDF).

  * fences (ground floor only): a wall not tied to the floor above, 20-200cm above the floor next to it,
    outside the building (no building room on either side). The value + "ג.גדר" sit on the OUTER side,
    with an arrow from "ג.גדר" to the wall; at the ends and every ~6.5m (as in Alon's corrected file).
    A fence lower than 40cm next to a shared garden: the value only.
  * gardens (private yards and shared gardens): a unit "גינון" (only when the grass is more than 30cm
    above the concrete under it, or there is no concrete) / the grass value (2mm, not bold) / the
    concrete value "... פ.ב" - a real spot on the concrete, so it follows the model. Near the corners, a
    metre in from the fences. A yard over 60m2: at least 3; the same concrete level everywhere: at most 3;
    the concrete level changes: one in every zone.
  * a paved patch in a yard: "משטח מרוצף" / relative (NEW) / absolute (+ "פ.ב" over the basement).
  * "קו קומת מרתף": the edge of the basement under the outside, a HIDDEN THIN line + text.
    "קו שינוי גובה פ.ב": where the concrete under the grass steps, the same.
  * "קו קומה מעל": drawn by the agent (its own dashed line style) wherever the floor above reaches beyond
    the walls of this floor, with texts + arrows, all in one Group "קו קומה מעל - <level>".
Only on a working copy; everything is marked (kind "levels") and rebuilt on every run.
"""
import math
import System
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, XYZ, Line, Wall, ElementId,
    SpotDimensionType, TextNoteType, LinePatternElement, GraphicsStyleType, Group, GroupType, Level,
    StorageType, Options, PlanarFace, SpatialElementBoundaryOptions, CurveElement, TextNote, Element,
    SpotDimension,
)
from System.Collections.Generic import List

import permit_rooms
import permit_write
import permit_levels as PL

GARDEN_TYPE_NAME = u'PL_Survay גינון 2 מ"מ'
PB_TYPE_NAME = u'PL_Survay פ.ב 2 מ"מ'
PB_SUFFIX = u' פ.ב'              # (v38) a no-break space: Revit drops a leading normal space ("31.92פ.ב")
BOLD_TEXT_NAME = u'2.0 mm Bold'
FLOOR_ABOVE_STYLE = u'PA_קו קומה מעל'
HIDDEN_THIN_NAME = u'Hidden Thin'
FLOOR_ABOVE_TEXT = u'קו קומה מעל'
BASEMENT_TEXT = u'קו קומת מרתף'
PB_STEP_TEXT = u'קו שינוי גובה פ.ב'
GROUP_PREFIX = u'קו קומה מעל'
TEXT_MM = 2.0

FENCE_MIN_CM, FENCE_MAX_CM = 20.0, 200.0
FENCE_LOW_CM = 40.0              # a lower fence in a shared garden: the value only
FENCE_EVERY_M = 6.5              # measured in Alon's corrected file (~6-7m)
FENCE_END_M = 0.5
FENCE_MIN_GAP_M = 4.0
FENCE_TEXT_OUT_M = 0.55          # the value / "ג.גדר" centre, this far outside the wall line
GARDEN_INSET_M = (1.0, 0.8, 1.3, 1.6, 0.6)   # a metre in from the fences (corner bisector)
GARDEN_BIG_M2 = 60.0
GIN_OVER_CM = 30.0               # "גינון" when the grass is higher than this above the concrete
LINE_SAMPLE_M = 0.5
LINE_LABEL_EVERY_M = 10.0
LINE_MIN_RUN_M = 1.0
VALUE_CHARS = 5
# how many texts a dashed line gets - counted in Alon's corrected file (30.09.26): "קו קומה מעל" ~8 on
# the whole ground floor, "קו קומת מרתף" one in every yard
LABEL_RULES = {
    u'קו קומה מעל': {'gap_m': 9.0, 'min_run_m': 2.0, 'every_m': 14.0},
    u'קו קומת מרתף': {'gap_m': 12.0, 'min_run_m': 3.0, 'every_m': 20.0},
    u'קו שינוי גובה פ.ב': {'gap_m': 8.0, 'min_run_m': 2.0, 'every_m': 20.0},
}
GARDEN_CELL_M = 0.5              # the grid the garden units are chosen on
GARDEN_EDGE_M = (0.75, 1.25)     # a unit ~1m in from the fences / edges of the grass (Alon's file)
SUB_YARD_MIN_M2 = 12.0           # a piece of a yard behind a fence this big is an apartment's own yard


# ------------------------------------------------------------------------------------------ types
def _find_named(doc, cls, name):
    return permit_rooms.type_named(doc, cls, name)


def _set(el, bip_name, names, value, log=None):
    p = None
    bip = getattr(BuiltInParameter, bip_name, None) if bip_name else None
    if bip is not None:
        p = el.get_Parameter(bip)
    if p is None:
        for n in names:
            p = el.LookupParameter(n)
            if p is not None:
                break
    if p is None or p.IsReadOnly:
        if log:
            log(u'type %s: parameter %s not found' % (permit_rooms.elem_name(el), bip_name or names))
        return False
    try:
        if p.StorageType == StorageType.Integer:
            p.Set(int(value))
        elif p.StorageType == StorageType.Double:
            p.Set(float(value))
        else:
            p.Set(value)
        return True
    except Exception as ex:
        if log:
            log(u'type %s: %s not set (%s)' % (permit_rooms.elem_name(el), bip_name or names, ex))
        return False


def ensure_spot_type(doc, base, name, suffix=None, log=None):
    """a copy of `base` with a 2mm regular text (and a suffix) - created once on the working copy"""
    t, created = permit_rooms.ensure_type(doc, SpotDimensionType, base, name, log)
    if not created:
        return t
    _set(t, 'TEXT_SIZE', [u'Text Size', u'גודל טקסט'], TEXT_MM / 304.8, log)
    _set(t, 'TEXT_STYLE_BOLD', [u'Bold'], 0, log)
    _set(t, 'TEXT_STYLE_ITALIC', [u'Italic'], 0, log)
    if suffix:
        _set(t, 'SPOT_ELEV_SINGLE_OR_UPPER_SUFFIX', [u'Single/Upper Value Suffix', u'Elevation Suffix', u'Suffix'],
             suffix, log)
    if log:
        log(u'created spot type "%s"' % name)
    return t


def ensure_bold_text(doc, base, log=None):
    t, created = permit_rooms.ensure_type(doc, TextNoteType, base, BOLD_TEXT_NAME, log)
    if not created:
        return t
    _set(t, 'TEXT_STYLE_BOLD', [u'Bold'], 1, log)
    _set(t, 'TEXT_SIZE', [u'Text Size'], TEXT_MM / 304.8, log)
    if log:
        log(u'created text type "%s"' % BOLD_TEXT_NAME)
    return t


_SUFFIX_LOGGED = [False]


def set_suffix(sp, text, log=None):
    """"31.92 פ.ב": the suffix on the spot itself. (v36 set it on the type - Revit 2025 has no such type
    parameter: "SPOT_ELEV_SINGLE_OR_UPPER_SUFFIX not found", and the plan showed "31.92" alone.)"""
    tries = []
    bip = getattr(BuiltInParameter, 'SPOT_ELEV_SINGLE_OR_UPPER_SUFFIX', None)
    if bip is not None:
        tries.append(lambda: sp.get_Parameter(bip))
    for n in (u'Single/Upper Value Suffix', u'Single / Upper Value Suffix', u'Suffix', u'סיומת',
              u'סיומת ערך יחיד/עליון'):
        tries.append(lambda n=n: sp.LookupParameter(n))
    for get in tries:
        try:
            p = get()
            if p is not None and not p.IsReadOnly and p.StorageType == StorageType.String:
                p.Set(text)
                return True
        except Exception:
            pass
    try:
        sp.Suffix = text
        return True
    except Exception:
        pass
    if log and not _SUFFIX_LOGGED[0]:
        _SUFFIX_LOGGED[0] = True
        names = []
        for p in sp.Parameters:
            try:
                if p.StorageType == StorageType.String:
                    names.append(p.Definition.Name)
            except Exception:
                pass
        log(u'פ.ב suffix: no writable suffix parameter on the spot. Its text parameters: %s' % u', '.join(names))
    return False


def cover_above_core_ft(doc, floor):
    """thickness (ft) of the layers above the structural core of a floor's type (tiles + fill over the
    concrete). 0 when the type has no core (pavers on soil) or nothing above it."""
    try:
        cs = doc.GetElement(floor.GetTypeId()).GetCompoundStructure()
        if cs is None:
            return 0.0
        first = cs.GetFirstCoreLayerIndex()           # layer 0 is the top of a floor
        if first is None or first <= 0:
            return 0.0
        layers = cs.GetLayers()
        if first >= layers.Count:
            return 0.0
        return sum(layers[i].Width for i in range(first))
    except Exception:
        return 0.0


def set_name(el, name):
    """Element.Name for a type - the property setter misbehaves in IronPython ("failed: Name" in v36)"""
    try:
        Element.Name.__set__(el, name)
        return True
    except Exception:
        pass
    p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM) or el.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
    if p is not None and not p.IsReadOnly:
        p.Set(name)
        return True
    el.Name = name
    return True


def _hidden_pattern(doc):
    pats = list(FilteredElementCollector(doc).OfClass(LinePatternElement))
    for p in pats:
        if (p.Name or u'').strip().lower() == u'hidden':
            return p
    for p in pats:
        if u'hidden' in (p.Name or u'').lower():
            return p
    return None


def line_style(doc, name, weight, log=None, find_kw=None):
    """a line style (subcategory of Lines) - an existing one whose name holds all of `find_kw`, else the
    named one, created with the HIDDEN pattern when missing. -> GraphicsStyle"""
    cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
    subs = list(cat.SubCategories)
    if find_kw:
        for s in subs:
            n = (s.Name or u'').lower()
            if all(k in n for k in find_kw):
                return s.GetGraphicsStyle(GraphicsStyleType.Projection)
    key = permit_rooms._type_key(name)
    for s in subs:
        if permit_rooms._type_key(s.Name) == key:
            return s.GetGraphicsStyle(GraphicsStyleType.Projection)
    try:
        sub = doc.Settings.Categories.NewSubcategory(cat, name)
    except Exception as ex:
        if log:
            log(u'line style "%s" not created: %s' % (name, ex))
        return None
    pat = _hidden_pattern(doc)
    if pat is not None:
        sub.SetLinePatternId(pat.Id, GraphicsStyleType.Projection)
    sub.SetLineWeight(weight, GraphicsStyleType.Projection)
    if log:
        log(u'created line style "%s" (pattern %s)' % (name, pat.Name if pat else u'-'))
    return sub.GetGraphicsStyle(GraphicsStyleType.Projection)


# ---------------------------------------------------------------------------------------- context
class Ctx(object):
    """what the ground rules need from run_levels"""
    pass


def _mmft(ctx, v_mm):
    return v_mm * ctx.scale / 1000.0 / 0.3048


def _value_rect(ctx, cx, cy):
    w = (VALUE_CHARS * permit_write.SPOT_VALUE_CHAR_MM / 2.0 + 0.4) * ctx.scale / 1000.0 / 0.3048
    h = (PL.SURVEY_TEXT_H_MM / 2.0 + 0.4) * ctx.scale / 1000.0 / 0.3048
    return (cx - w, cy - h, cx + w, cy + h)


def _text_rect(ctx, text, cx, cy):
    hw, hh = permit_write._label_box(text, ctx.scale)
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def refresh_texts(ctx):
    """the boxes of every text and spot in the view as they are NOW (the drawing scan was taken before
    the units moved their labels - v36 put "קו קומת מרתף" on "משטח מרוצף" that way)"""
    rects = []
    try:
        ctx.doc.Regenerate()
    except Exception:
        pass
    for t in FilteredElementCollector(ctx.doc, ctx.view.Id).OfClass(TextNote):
        r = note_rect(t, ctx.view, ctx.scale)
        if r is not None:
            rects.append(r)
    for s in FilteredElementCollector(ctx.doc, ctx.view.Id).OfCategory(BuiltInCategory.OST_SpotElevations):
        rects.extend(spot_text_rects(ctx.doc, s, ctx.scale))
    ctx.text_rects = rects
    return rects


def note_rect(t, view, scale):
    """(v39) the box of a text WITHOUT its arrow: a text with a leader ("ג.גדר") has a bounding box that runs
    to the arrow's end - in v38 those boxes covered the whole boundary strip, the basement line got no
    text and the check counted 17 false overlaps"""
    try:
        n = t.LeaderCount
    except Exception:
        n = 0
    if n:
        try:
            hw, hh = permit_write._label_box((t.Text or u'').strip().split(u'\r')[0], scale)
            c = t.Coord
            return (c.X - hw, c.Y - hh, c.X + hw, c.Y + hh)
        except Exception:
            pass
    try:
        bb = t.get_BoundingBox(view)
    except Exception:
        bb = None
    return None if bb is None else (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)


def spot_text_rects(doc, s, scale):
    """(v38) the boxes of a spot's VALUE text (and its symbol) - not its bounding box, which runs from the
    measured point to the moved text and blocked whole stretches in v37 (11 fence texts left out)"""
    mm = scale / 1000.0 / 0.3048
    out = []
    try:
        tp = s.TextPosition
        val = (getattr(s, 'ValueString', u'') or u'')
        suffix = u''
        try:
            p = s.get_Parameter(getattr(BuiltInParameter, 'SPOT_ELEV_SINGLE_OR_UPPER_SUFFIX'))
            suffix = (p.AsString() or u'') if p is not None else u''
        except Exception:
            pass
        n = max(4, len(val) + len(suffix))
        w = n * permit_write.SPOT_VALUE_CHAR_MM * mm / 2.0
        h = 2.5 * mm
        cy = tp.Y + VALUE_ANCHOR_MM * mm
        out.append((tp.X - w, cy - h / 2.0 - 0.3 * mm, tp.X + w, cy + h / 2.0 + 0.3 * mm))
    except Exception:
        pass
    try:
        tname = permit_rooms.elem_name(doc.GetElement(s.GetTypeId())) or u''
        if u'survay' not in tname.lower() and u'survey' not in tname.lower():
            o = s.Origin
            r = permit_write.SPOT_SYMBOL_R_MM * mm
            out.append((o.X - r, o.Y - r, o.X + r, o.Y + r))
    except Exception:
        pass
    return out


def _free(ctx, rects, texts_only=False):
    """no drawn line under any rect (site scan), no text of this run and no text of the view on it"""
    for r in rects:
        if not texts_only and ctx.occ is not None and ctx.occ.count(r) > 0:
            return False
        if any(PL._rects_overlap(r, t) for t in ctx.taken):
            return False
        if any(PL._rects_overlap(r, t) for t in (getattr(ctx, 'text_rects', None) or [])):
            return False
    return True


VALUE_ANCHOR_MM = 1.25           # TextPosition is the BOTTOM of the value text (v37 plan: "גינון" sat on "32.42")


def _place_value(ctx, sp, cx, cy, raw=False):
    """move a spot's value text so its centre is (cx, cy) (raw: TextPosition itself = (cx, cy))"""
    try:
        tp = sp.TextPosition
        if not raw:
            cy -= _mmft(ctx, VALUE_ANCHOR_MM)
        sp.TextPosition = XYZ(cx, cy, tp.Z)
        ctx.doc.Regenerate()
        return True
    except Exception as ex:
        ctx.log(u'value text could not be moved: %s' % ex)
        return False


def _is_interior(ctx, room):
    """a room of the building (not a yard / garden / Landscape-floored space)"""
    if room is None:
        return False
    k = permit_rooms._id(room.Id)
    if k not in ctx.interior_cache:
        ctx.interior_cache[k] = PL._landscape_share(room, ctx.lands, ctx.z) < 0.5 and \
            not any(w in (room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'')
                    for w in (PL.YARD_KW, u'גינון'))
    return ctx.interior_cache[k]


def _room_at(ctx, x, y):
    return ctx.doc.GetRoomAtPoint(XYZ(x, y, ctx.z), ctx.phase)


def _building_centre(ctx):
    xs, ys = [], []
    for r in ctx.agent_rooms:
        if _is_interior(ctx, r):
            bb = r.get_BoundingBox(None)
            if bb is not None:
                xs += [bb.Min.X, bb.Max.X]
                ys += [bb.Min.Y, bb.Max.Y]
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


# -------------------------------------------------------------------------------------------- fences
def fences(ctx):
    """-> (fence walls, spots)"""
    doc, z = ctx.doc, ctx.z
    lz = ctx.level.ProjectElevation
    centre = _building_centre(ctx)
    refresh_texts(ctx)
    pts = []
    n_walls = n_spots = 0
    rejected = []
    for w in FilteredElementCollector(doc).OfClass(Wall):
        if not permit_rooms.is_free_top(doc, w, ctx.level):
            continue
        loc = getattr(w.Location, 'Curve', None)
        bb = w.get_BoundingBox(None)
        if loc is None or bb is None or loc.Length * 0.3048 < 0.8:
            continue
        # (v37) a fence may start in the basement (the walls along the ramp, 32.85 in Alon's file):
        # every wall whose top is within reach of this floor counts, whatever its base level
        if w.LevelId != ctx.level.Id and not (bb.Min.Z <= lz + 1.0 and lz - 1.0 <= bb.Max.Z <= lz + 8.0):
            continue
        mid = loc.Evaluate(0.5, True)
        d = loc.ComputeDerivatives(0.5, True).BasisX.Normalize()
        nx, ny = d.Y, -d.X
        off = w.Width / 2.0 + 0.8
        grounds, inside = [], False
        for sgn in (1.0, -1.0):
            # (v37) 30cm off the face: a low wall right by the building (32.95 in Alon's file) is a fence
            qi = XYZ(mid.X + sgn * nx * (w.Width / 2.0 + 0.3 / 0.3048), mid.Y + sgn * ny * (w.Width / 2.0 + 0.3 / 0.3048), z)
            if _is_interior(ctx, _room_at(ctx, qi.X, qi.Y)):
                inside = True
                break
            q = XYZ(mid.X + sgn * nx * (w.Width / 2.0 + 0.8), mid.Y + sgn * ny * (w.Width / 2.0 + 0.8), z)
            hit = PL.floor_top_face(doc, ctx.level, q.X, q.Y)
            if hit is not None:
                grounds.append(hit[1])
        if inside:
            continue                                    # a wall of the building, not a fence
        if not grounds:
            grounds = [lz]
        # (v37) measured from the LOWER side too: a planter's rim stands 10cm over its own soil but
        # ~25cm over the paving (32.15 in Alon's file) - it is a (low) fence
        h_low = (bb.Max.Z - min(grounds)) * 30.48
        h_high = (bb.Max.Z - max(grounds)) * 30.48
        h_cm = max(h_high, 0.0)          # (v38) "lower than 40cm" (value only) is seen from the higher side: a planter rim over its soil
        if not (h_low >= FENCE_MIN_CM and h_high <= FENCE_MAX_CM):
            if len(rejected) < 25 and -50 <= h_low <= 400:
                rejected.append(u'%d (%.0f/%.0f cm)' % (permit_rooms._id(w.Id), h_low, h_high))
            continue
        top = PL._wall_top_face(w)
        if top is None:
            continue
        n_walls += 1
        # the outer side: away from the building; a fence running out from the building -> the freer side
        sgn_out = 1.0
        if centre is not None:
            vx, vy = mid.X - centre[0], mid.Y - centre[1]
            vl = (vx * vx + vy * vy) ** 0.5 or 1.0
            dot = (nx * vx + ny * vy) / vl
            if abs(dot) >= 0.3:
                sgn_out = 1.0 if dot > 0 else -1.0
            elif ctx.occ is not None:
                c1 = ctx.occ.count(_value_rect(ctx, mid.X + nx * 2, mid.Y + ny * 2))
                c2 = ctx.occ.count(_value_rect(ctx, mid.X - nx * 2, mid.Y - ny * 2))
                sgn_out = 1.0 if c1 <= c2 else -1.0
        low_shared = h_cm < FENCE_LOW_CM and not any(
            (lambda r: r is not None and PL.YARD_KW in (r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u''))(
                _room_at(ctx, mid.X + s * nx * off, mid.Y + s * ny * off)) for s in (1.0, -1.0))
        if low_shared:
            # (v38) a planter rim: the value outside the planter (the side without grass), at its corners
            is_grass = lambda x, y: any(k in ((PL._top_floor_at(ctx.all_floors, x, y) or (None, None, u''))[2] or u'').lower()
                                        for k in permit_rooms.LANDSCAPE_KW)
            g1 = is_grass(mid.X + nx * off, mid.Y + ny * off)
            g2 = is_grass(mid.X - nx * off, mid.Y - ny * off)
            if g1 != g2:
                sgn_out = -1.0 if g1 else 1.0
        L = loc.Length
        end = FENCE_END_M / 0.3048
        n = max(1, int(round((L - 2 * end) / (FENCE_EVERY_M / 0.3048)))) if L > 2 * end else 0
        ts = [L / 2.0] if n == 0 else [end + i * (L - 2 * end) / n for i in range(n + 1)]
        for s in ts:
            p = loc.Evaluate(s / L, True)
            if any(((p.X - q.X) ** 2 + (p.Y - q.Y) ** 2) ** 0.5 < FENCE_MIN_GAP_M / 0.3048 for q in pts):
                continue
            # the texts outside the fence, readable. (v38: 11 fence texts were left out in v37 - now they may
            # slide 2m along the wall, then go further out, then to the inner side; over a line as a last
            # resort, never over a text)
            placed = None
            tries = []
            for side, out_m in ((sgn_out, FENCE_TEXT_OUT_M), (sgn_out, FENCE_TEXT_OUT_M + 0.4),
                                (-sgn_out, FENCE_TEXT_OUT_M)):
                for slide in (0.0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0):
                    tries.append((side, out_m, slide))
            for strict in (True, False):
                for side, out_m, slide in tries:
                    ps = s + slide / 0.3048
                    if ps < 0 or ps > L:
                        continue
                    pp = loc.Evaluate(ps / L, True)
                    cx = pp.X + side * nx * out_m / 0.3048
                    cy = pp.Y + side * ny * out_m / 0.3048
                    vr = _value_rect(ctx, cx, cy)
                    gy = cy - _mmft(ctx, 3.8)
                    gr = _text_rect(ctx, u'ג.גדר', cx, gy)
                    rects = [vr] if low_shared else [vr, gr]
                    if _free(ctx, rects, texts_only=not strict):
                        placed = (pp, cx, cy, gy, rects)
                        break
                if placed:
                    break
            if placed is None:
                ctx.skipped.append(u'גדר %d' % permit_rooms._id(w.Id))
                continue
            pp, cx, cy, gy, rects = placed
            sp = ctx.add_spot_ref(top[0], XYZ(pp.X, pp.Y, top[1]), u'גדר %s' % permit_rooms._id(w.Id), ctx.survey_type)
            if sp is None:
                continue
            _place_value(ctx, sp, cx, cy)
            if not low_shared:
                ctx.note_at(XYZ(cx, gy, 0), u'ג.גדר', leader_to=pp)
            ctx.taken.extend(rects)
            pts.append(pp)
            n_spots += 1
    if rejected:
        ctx.log(u'fences: free-standing walls left out by height (from the lower / higher side): %s' % u', '.join(rejected))
    return n_walls, n_spots


# ------------------------------------------------------------------------------------------- gardens
def _slab_below(ctx, x, y, ztop):
    """(floor, top z) of the concrete under a garden point: the highest non-Landscape floor below"""
    best = None
    for fl in ctx.slab_floors:
        bb = fl.get_BoundingBox(None)
        if not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y):
            continue
        # (v37) the box only says the slab COULD be there - the height is checked at the point itself
        # (v36 dropped the basement roof because its box rose higher elsewhere -> 29.54 instead of 32.32)
        if bb.Max.Z < ztop - PL.PB_MAX_DEPTH_FT or bb.Min.Z > ztop - 0.1:
            continue
        k = permit_rooms._id(fl.Id)
        if k not in ctx.slab_polys:
            ctx.slab_polys[k] = permit_rooms.floor_polygons(ctx.doc, fl)
        if k not in PL._TOPZ_CACHE:
            PL._TOPZ_CACHE[k] = PL._TopZ(fl)
        tz = PL._TOPZ_CACHE[k].at(x, y)
        if ctx.slab_polys[k]:
            if not permit_rooms.point_in_polygons(x, y, ctx.slab_polys[k]):
                continue
            tz = bb.Max.Z if tz is None else tz
        elif tz is None:
            continue           # (v39) no readable outline: the top face itself must lie over the point
                               # (the C25 at 30.65 under the planters was dropped in v38)
        if ztop - PL.PB_MAX_DEPTH_FT <= tz < ztop - 0.1 and (best is None or tz > best[1]):
            best = (fl, tz)
    return best


def _face_ref(fl, x, y):
    opts = Options()
    opts.ComputeReferences = True
    bb = fl.get_BoundingBox(None)
    best = None
    for f in PL._faces(fl.get_Geometry(opts)):
        if f.Reference is None:
            continue
        zz = PL._face_z(f, x, y, bb.Max.Z)
        if zz is not None and (best is None or zz > best[1]):
            best = (f.Reference, zz)
    return best


def _outline(room):
    try:
        loops = room.GetBoundarySegments(SpatialElementBoundaryOptions())
    except Exception:
        loops = None
    polys = []
    for loop in (loops or []):
        pts = [(s.GetCurve().GetEndPoint(0).X, s.GetCurve().GetEndPoint(0).Y) for s in loop]
        if len(pts) >= 3:
            polys.append(pts)
    if not polys:
        return []
    return max(polys, key=lambda ps: abs(_area2(ps)))


def _area2(ps):
    n = len(ps)
    return sum(ps[i][0] * ps[(i + 1) % n][1] - ps[(i + 1) % n][0] * ps[i][1] for i in range(n))


def _corner_candidates(poly, inside, insets):
    """a point a little inside every convex corner (both sides >= 1m) of `poly`"""
    n = len(poly)
    if n < 3:
        return []
    sgn = 1.0 if _area2(poly) > 0 else -1.0
    out = []
    for i in range(n):
        a, v, b = poly[i - 1], poly[i], poly[(i + 1) % n]
        e1 = (v[0] - a[0], v[1] - a[1])
        e2 = (b[0] - v[0], b[1] - v[1])
        l1 = (e1[0] ** 2 + e1[1] ** 2) ** 0.5
        l2 = (e2[0] ** 2 + e2[1] ** 2) ** 0.5
        if l1 < 1.0 / 0.3048 or l2 < 1.0 / 0.3048:
            continue
        if (e1[0] * e2[1] - e1[1] * e2[0]) * sgn <= 1e-6 * l1 * l2:
            continue
        bx, by = -e1[0] / l1 + e2[0] / l2, -e1[1] / l1 + e2[1] / l2
        bl = (bx * bx + by * by) ** 0.5
        if bl < 1e-6:
            continue
        bx, by = bx / bl, by / bl
        for d in insets:
            # along the bisector the distance to each side is d * sin(half angle); aim at d from both fences
            half = math.acos(max(-1.0, min(1.0, -(e1[0] * e2[0] + e1[1] * e2[1]) / (l1 * l2)))) / 2.0
            dist = (d / 0.3048) / max(math.sin(half), 0.3)
            q = (v[0] + bx * dist, v[1] + by * dist)
            if inside(q[0], q[1]):
                out.append(q)
                break
    return out


def gardens(ctx):
    """private yards + shared gardens + the paved patches of the yards. -> counts dict"""
    doc, z = ctx.doc, ctx.z
    res = {'zones': 0, 'units': 0, 'pb': 0, 'gin': 0, 'paved': 0, 'pb_zones': 0, 'slabs': set(), 'notes': []}
    garden_type = ensure_spot_type(doc, ctx.survey_type, GARDEN_TYPE_NAME, log=ctx.log)
    pb_type = ensure_spot_type(doc, ctx.survey_type, PB_TYPE_NAME, suffix=PB_SUFFIX, log=ctx.log)
    grass = lambda n: any(k in (n or u'').lower() for k in permit_rooms.LANDSCAPE_KW)
    zones = []
    for r in ctx.exterior:
        nm = (r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u'').strip()
        if PL.YARD_KW in nm or u'גינון' in nm:
            zones.append((nm, r, _outline(r), r.Area * permit_rooms.SQFT_TO_M2))
    # grass with no room around it (the shared garden strips): every grass loop outside all rooms -
    # (v39) but only inside the lot: the outline of the agent's rooms (yards included). v38 marked the
    # sidewalk grass beyond the south boundary.
    rb = [r.get_BoundingBox(None) for r in ctx.agent_rooms]
    rb = [b for b in rb if b is not None]
    lot = (min(b.Min.X for b in rb), min(b.Min.Y for b in rb), max(b.Max.X for b in rb), max(b.Max.Y for b in rb)) \
        if rb else None
    for f in ctx.lands:
        if not grass(f[2]):
            continue
        for loop in f[1]:
            if len(loop) < 3:
                continue
            cx = sum(p[0] for p in loop) / len(loop)
            cy = sum(p[1] for p in loop) / len(loop)
            if _room_at(ctx, cx, cy) is not None:
                continue
            if lot is not None and not (lot[0] <= cx <= lot[2] and lot[1] <= cy <= lot[3]):
                continue                                   # outside the lot (the sidewalk)
            area = abs(_area2(loop)) / 2.0 * permit_rooms.SQFT_TO_M2
            if area >= 1.0:
                zones.append((u'גינון משותף', None, loop, area))
    res['zones'] = len(zones)
    refresh_texts(ctx)
    # the names tool's "גינון משותף" labels: a planter's unit goes right under its label (Alon's file)
    shared_labels = [t for t in FilteredElementCollector(doc, ctx.view.Id).OfClass(TextNote)
                     if PL._kind(t, ctx.schema) == u'label' and u'גינון משותף' in (t.Text or u'')]
    step = GARDEN_CELL_M / 0.3048
    cell_m2 = GARDEN_CELL_M ** 2
    for nm, room, poly, area in zones:
        if room is not None:
            inside = lambda x, y, rr=room: rr.IsPointInRoom(XYZ(x, y, z)) and grass(
                (PL._top_floor_at(ctx.all_floors, x, y) or (None, None, u''))[2])
        else:
            inside = lambda x, y, pp=poly: permit_rooms.point_in_polygons(x, y, [pp]) and grass(
                (PL._top_floor_at(ctx.all_floors, x, y) or (None, None, u''))[2])
        if not poly:
            continue
        ox, oy = min(p[0] for p in poly), min(p[1] for p in poly)
        ni = int((max(p[0] for p in poly) - ox) / step) + 1
        nj = int((max(p[1] for p in poly) - oy) / step) + 1
        cells = [(i, j) for i in range(ni) for j in range(nj)
                 if inside(ox + (i + 0.5) * step, oy + (j + 0.5) * step)]
        if not cells:
            ctx.log(u'garden "%s": no grass inside' % nm)
            continue
        # (v37) one yard room may hold the yards of two apartments - split by the fences inside it
        groups = [g for g in permit_rooms.split_cells(cells, step, (ox, oy), ctx.fence_segs)
                  if len(g) * cell_m2 >= 1.0]
        if room is not None:
            # (v39) inside a yard, slivers between a patio and a fence (3-6m2) got units of their own in v38
            groups = [g for g in groups if len(g) * cell_m2 >= 10.0] or groups[:1]
        for g in groups:
            g_area = len(g) * cell_m2
            lab = None
            if room is None:
                for t in shared_labels:
                    c = t.Coord
                    if any(abs(c.X - (ox + (i + 0.5) * step)) <= step and abs(c.Y - (oy + (j + 0.5) * step)) <= step
                           for i, j in g):
                        lab = t
                        break
            n = _garden_group(ctx, nm, g, step, (ox, oy), g_area, grass, garden_type, pb_type, res, lab)
            res['units'] += n
            res['zones_detail'] = res.get('zones_detail', []) + [u'%s %.0fm2: %d' % (nm, g_area, n)]
    ctx.log(u'gardens: %s' % u'; '.join(res.get('zones_detail', [])))
    return res


def _garden_group(ctx, nm, cells, step, origin, area, grass, garden_type, pb_type, res, label=None):
    """the units of one apartment's yard / one garden strip / one planter. -> units placed"""
    doc = ctx.doc
    ox, oy = origin
    cs = set(cells)
    centre = lambda c: (ox + (c[0] + 0.5) * step, oy + (c[1] + 0.5) * step)
    # distance (in cells) from the edge of the grass: the units stand ~1m in from the fences
    dist, front = {}, []
    for c in cells:
        if any((c[0] + d[0], c[1] + d[1]) not in cs for d in ((1, 0), (-1, 0), (0, 1), (0, -1))):
            dist[c] = 0
            front.append(c)
    while front:
        nxt = []
        for c in front:
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                b = (c[0] + d[0], c[1] + d[1])
                if b in cs and b not in dist:
                    dist[b] = dist[c] + 1
                    nxt.append(b)
        front = nxt
    lo_k = int(GARDEN_EDGE_M[0] / GARDEN_CELL_M)
    hi_k = int(GARDEN_EDGE_M[1] / GARDEN_CELL_M + 0.5)
    pool = [c for c in cells if lo_k <= dist[c] <= hi_k]
    if not pool:
        kmax = max(dist.values())
        pool = [c for c in cells if dist[c] == kmax]           # a narrow strip / planter: its middle line
    xs = [centre(c) for c in pool]
    length = max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a in xs[:1] for b in xs) * 0.3048 if xs else 0
    if label is not None:
        want = 1                                               # a planter: one unit, under its name
    elif area > GARDEN_BIG_M2:
        want = 3
    elif area >= 20.0 or length > 10.0:
        want = 2
    else:
        want = 1
    # the concrete under the grass, sampled along the pool (its level changes -> a unit in every zone)
    sample = pool[::max(1, len(pool) // 40)]
    info = []
    for c in sample:
        x, y = centre(c)
        hit = PL.floor_top_face(doc, ctx.level, x, y, grass)
        if hit is None:
            continue
        slab = _slab_below(ctx, x, y, hit[1])
        key = None if slab is None else round(slab[1] * 30.48 / 5.0)   # 5cm steps
        info.append((x, y, hit, slab, key))
    if not info:
        ctx.log(u'garden "%s" (%.0fm2): no grass face found' % (nm, area))
        return 0
    keys = []
    for i in info:
        if i[4] not in keys:
            keys.append(i[4])
    if len(keys) > 1:
        want = max(want, len(keys))
        res['pb_zones'] += len(keys)
    elif label is None:
        want = min(want, 3)                                    # the same concrete everywhere: at most 3
    chosen = []
    if label is not None:
        lx, ly = label.Coord.X, label.Coord.Y
        chosen.append(min(info, key=lambda i: (i[0] - lx) ** 2 + (i[1] - ly) ** 2))
    else:
        for k in keys:
            chosen.append(next(i for i in info if i[4] == k))
        while len(chosen) < min(want, len(info)):
            rest = [i for i in info if i not in chosen]
            chosen.append(max(rest, key=lambda i: min((i[0] - c[0]) ** 2 + (i[1] - c[1]) ** 2 for c in chosen)))
    n = 0
    done = ctx.__dict__.setdefault('garden_pts', [])
    for (x, y, hit, slab, key) in chosen:
        if label is None and any(((x - q[0]) ** 2 + (y - q[1]) ** 2) ** 0.5 < 3.0 / 0.3048 for q in done):
            continue                  # (v39) two units side by side by the west patio in v38
        if _garden_unit(ctx, nm, x, y, hit, slab, garden_type, pb_type, res, label=label):
            done.append((x, y))
            n += 1
    return n


def _garden_unit(ctx, nm, x, y, hit, slab, garden_type, pb_type, res, label=None):
    """"גינון" / grass value / concrete value - one readable unit centred near (x, y).
    label: the planter's "גינון משותף" - the values go right under it (no "גינון" then)"""
    if slab is None:
        # (v38) no concrete found under this grass: list what IS under the point (planters in v37: no "30.65 פ.ב")
        under = []
        for el in FilteredElementCollector(ctx.doc).WhereElementIsNotElementType():
            try:
                cat = el.Category
                if cat is None or cat.Id.IntegerValue not in (int(BuiltInCategory.OST_Floors),
                                                               int(BuiltInCategory.OST_StructuralFoundation),
                                                               int(BuiltInCategory.OST_StructuralFraming),
                                                               int(BuiltInCategory.OST_Roofs)):
                    continue
                bb = el.get_BoundingBox(None)
                if bb is None or not (bb.Min.X <= x <= bb.Max.X and bb.Min.Y <= y <= bb.Max.Y) or bb.Max.Z >= hit[1]:
                    continue
                under.append(u'%s "%s" top %s' % (cat.Name, permit_rooms.elem_name(ctx.doc.GetElement(el.GetTypeId())),
                                                  PL._absolute_m(ctx.doc, bb.Max.Z)))
            except Exception:
                continue
        ctx.log(u'garden "%s": no concrete found under %s cm. Under the point: %s' % (
            nm, [round(x * 30.48), round(y * 30.48)], u'; '.join(under[:8]) or u'nothing'))
    up = _mmft(ctx, 3.6)          # (v38) line spacing of the unit "גינון" / value / פ.ב (Alon's file ~3.5mm)
    gz = hit[1]
    need_gin = label is None and (slab is None or (gz - slab[1]) * 30.48 > GIN_OVER_CM)
    best = None
    if label is not None:
        cx, cy = label.Coord.X, label.Coord.Y - up
        rects = [_value_rect(ctx, cx, cy)] + ([_value_rect(ctx, cx, cy - up)] if slab is not None else [])
        best = (cx, cy, rects)
    for r in ((0.0, 0.3, 0.6, 0.9, 1.2) if best is None else ()):
        for k in (range(8) if r > 0 else [0]):
            a = 2 * math.pi * k / 8.0
            cx, cy = x + r / 0.3048 * math.cos(a), y + r / 0.3048 * math.sin(a)
            rects = [_value_rect(ctx, cx, cy)]
            if need_gin:
                rects.append(_text_rect(ctx, u'גינון', cx, cy + up))
            if slab is not None:
                rects.append(_value_rect(ctx, cx, cy - up))
            if _free(ctx, rects):
                best = (cx, cy, rects)
                break
        if best:
            break
    if best is None:
        # over a drawn line is acceptable as a last resort - over a text never (office rule)
        rects = [_value_rect(ctx, x, y)] + ([_text_rect(ctx, u'גינון', x, y + up)] if need_gin else []) + \
                ([_value_rect(ctx, x, y - up)] if slab is not None else [])
        if not _free(ctx, rects, texts_only=True):
            ctx.skipped.append(u'גינון ב-%s' % nm)
            ctx.log(u'garden "%s": no place without covering a text near %s cm - left out' % (
                nm, [round(x * 30.48), round(y * 30.48)]))
            return False
        best = (x, y, rects)
        ctx.log(u'garden "%s": no fully clear place near %s cm (over lines, not texts)' % (
            nm, [round(x * 30.48), round(y * 30.48)]))
    cx, cy, rects = best
    ref = hit[0]
    sp = ctx.add_spot_ref(ref, XYZ(x, y, gz), u'גינון %s' % nm, garden_type)
    if sp is None:
        return False
    _place_value(ctx, sp, cx, cy)
    if need_gin:
        ctx.note_at(XYZ(cx, cy + up, 0), u'גינון')
        res['gin'] += 1
    if slab is not None:
        fr = _face_ref(slab[0], x, y)
        ok = False
        if fr is not None:
            spb = ctx.add_spot_ref(fr[0], XYZ(x, y, fr[1]), u'פ.ב %s' % nm, pb_type)
            if spb is not None:
                set_suffix(spb, PB_SUFFIX, ctx.log)
                _place_value(ctx, spb, cx, cy - up)
                ok = True
        if not ok:
            # the concrete could not carry a spot in this view - the value as text (not linked)
            v = PL._absolute_m(ctx.doc, slab[1])
            if v is not None:
                ctx.note_at(XYZ(cx, cy - up, 0), u'%.2f פ.ב' % v)
                res['notes'].append(u'פ.ב כטקסט (לא מקושר) ב-%s' % nm)
        res['pb'] += 1
        res['slabs'].add(permit_rooms._id(slab[0].Id))
    ctx.taken.extend(rects)
    return True


# ------------------------------------------------------------------------------------ dashed lines
def _runs_along(poly_edges, keep):
    """sample every edge; consecutive kept samples -> runs [(p0, p1)] (straight pieces)"""
    runs = []
    step = LINE_SAMPLE_M / 0.3048
    for a, b in poly_edges:
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if L < 1e-6:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        n = max(1, int(L / step))
        cur = None
        for i in range(n + 1):
            t = min(L, i * step)
            p = (a[0] + ux * t, a[1] + uy * t)
            if keep(p, (ux, uy)):
                if cur is None:
                    cur = [p, p]
                else:
                    cur[1] = p
            elif cur is not None:
                runs.append(tuple(cur))
                cur = None
        if cur is not None:
            runs.append(tuple(cur))
    return [r for r in runs if ((r[1][0] - r[0][0]) ** 2 + (r[1][1] - r[0][1]) ** 2) ** 0.5 >= LINE_MIN_RUN_M / 0.3048]


def _draw_runs(ctx, runs, style, text, side_fn, ids=None):
    """detail lines + texts with an arrow (how many: LABEL_RULES)"""
    doc, view = ctx.doc, ctx.view
    n_lines = n_text = 0
    for p0, p1 in runs:
        try:
            dc = doc.Create.NewDetailCurve(view, Line.CreateBound(XYZ(p0[0], p0[1], 0), XYZ(p1[0], p1[1], 0)))
            if style is not None:
                dc.LineStyle = style
            PL._mark(dc, ctx.schema, u'levels')
            n_lines += 1
            if ids is not None:
                ids.append(dc.Id)
        except Exception as ex:
            ctx.log(u'%s: line failed: %s' % (text, ex))
            continue
    # (v37) the texts: Alon's file has ~8 "קו קומה מעל" on the whole floor and one "קו קומת מרתף" in a
    # yard - not one per piece of line. The longest pieces are labelled first; a text only where no
    # text of the same kind stands within gap_m; never over another text (left out instead).
    rule = LABEL_RULES.get(text, {'gap_m': 8.0, 'min_run_m': 2.0, 'every_m': LINE_LABEL_EVERY_M})
    gap = rule['gap_m'] / 0.3048
    mine = ctx.line_labels.setdefault(text, [])
    refresh_texts(ctx)
    for p0, p1 in sorted(runs, key=lambda r: -((r[1][0] - r[0][0]) ** 2 + (r[1][1] - r[0][1]) ** 2)):
        L = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
        if L < rule['min_run_m'] / 0.3048:
            continue
        ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        nx, ny = -uy, ux
        k = max(1, int(L / (rule['every_m'] / 0.3048) + 0.5))
        for i in range(k):
            t = (i + 0.5) / k * L
            px, py = p0[0] + ux * t, p0[1] + uy * t
            if any(((px - q[0]) ** 2 + (py - q[1]) ** 2) ** 0.5 < gap for q in mine):
                continue
            s = side_fn((px, py), (nx, ny))
            placed = None
            # (v38) along the WHOLE line (v37 looked 3m each way - the east "קו קומה מעל" got no text)
            slides = [0.0]
            for m in range(1, int(L * 0.3048) + 1):
                slides += [float(m), -float(m)]
            for dist in (0.7, 1.0, 1.3, 0.5, 1.8, 2.5):
                for slide in slides:
                    if not (0.0 <= t + slide / 0.3048 <= L):
                        continue                     # the arrow must still land on this line
                    cx = px + ux * slide / 0.3048 + s * nx * dist / 0.3048
                    cy = py + uy * slide / 0.3048 + s * ny * dist / 0.3048
                    r = _text_rect(ctx, text, cx, cy)
                    if _free(ctx, [r]):
                        placed = (cx, cy, r, slide)
                        break
                if placed:
                    break
            if placed is None:
                for dist in (0.7, 1.0, 1.3):
                    cx, cy = px + s * nx * dist / 0.3048, py + s * ny * dist / 0.3048
                    r = _text_rect(ctx, text, cx, cy)
                    if _free(ctx, [r], texts_only=True):
                        placed = (cx, cy, r, 0.0)        # over a line, never over a text
                        break
            if placed is None:
                ctx.skipped.append(text)
                continue
            # the arrow points at the line right next to the text
            ax, ay = px + ux * placed[3] / 0.3048, py + uy * placed[3] / 0.3048
            mine.append((ax, ay))
            px, py = ax, ay
            tn = ctx.note_at(XYZ(placed[0], placed[1], 0), text, leader_to=XYZ(px, py, 0))
            if tn is not None:
                n_text += 1
                ctx.taken.append(placed[2])
                if ids is not None:
                    ids.append(tn.Id)
    return n_lines, n_text


def _near_wall_fn(ctx, extra_m):
    """f(point) -> True within half a wall + extra_m of a wall of this floor that is part of the building
    (fences excluded - a basement edge along a fence is still drawn)"""
    walls = []
    for w in FilteredElementCollector(ctx.doc).OfClass(Wall):
        if w.LevelId != ctx.level.Id or permit_rooms.is_free_top(ctx.doc, w, ctx.level):
            continue
        c = getattr(w.Location, 'Curve', None)
        if c is not None:
            walls.append((c, w.Width / 2.0 + extra_m / 0.3048))

    def near(p):
        for c, tol in walls:
            try:
                r = c.Project(XYZ(p[0], p[1], c.GetEndPoint(0).Z))
            except Exception:
                continue
            if r is not None and r.Distance <= tol:
                return True
        return False
    return near


def basement_lines(ctx, slab_ids):
    """"קו קומת מרתף" along the edge of the concrete under the gardens (outside the building) and
    "קו שינוי גובה פ.ב" where that concrete steps under the grass. -> (runs, texts, step runs, step texts)"""
    doc = ctx.doc
    style = line_style(doc, HIDDEN_THIN_NAME, 1, ctx.log, find_kw=(u'hidden', u'thin'))
    slabs = [doc.GetElement(ElementId(i)) for i in slab_ids]
    slabs = [s for s in slabs if s is not None]
    polys = {}
    for s in slabs:
        k = permit_rooms._id(s.Id)
        polys[k] = ctx.slab_polys.get(k) or permit_rooms.floor_polygons(doc, s)
    grass = lambda x, y: any(k in ((PL._top_floor_at(ctx.all_floors, x, y) or (None, None, u''))[2] or u'').lower()
                             for k in permit_rooms.LANDSCAPE_KW)
    in_any = lambda x, y: any(permit_rooms.point_in_polygons(x, y, pp) for pp in polys.values())
    off = 0.3

    def slab_z(x, y):
        best = None
        for s in slabs:
            k = permit_rooms._id(s.Id)
            if permit_rooms.point_in_polygons(x, y, polys[k]):
                if k not in PL._TOPZ_CACHE:
                    PL._TOPZ_CACHE[k] = PL._TopZ(s)
                tz = PL._TOPZ_CACHE[k].at(x, y)
                if tz is not None and (best is None or tz > best):
                    best = tz
        return best

    edges = []
    for pp in polys.values():
        for loop in pp:
            for i in range(len(loop)):
                edges.append((loop[i], loop[(i + 1) % len(loop)]))

    def outside_building(p):
        return not _is_interior(ctx, _room_at(ctx, p[0], p[1]))

    near_wall = _near_wall_fn(ctx, 0.6)
    yard_or_grass = lambda p: grass(p[0], p[1]) or (lambda r: r is not None and not _is_interior(ctx, r))(
        _room_at(ctx, p[0], p[1]))

    why = {u'inner': 0, u'building': 0, u'wall': 0, u'not yard': 0, u'kept': 0}

    def keep_edge(p, u):
        nx, ny = -u[1], u[0]
        a = (p[0] + nx * off, p[1] + ny * off)
        b = (p[0] - nx * off, p[1] - ny * off)
        ia, ib = in_any(*a), in_any(*b)
        if ia == ib:
            why[u'inner'] += 1
            return False                         # inside the basement on both sides (or outside on both)
        if not (outside_building(a) and outside_building(b)):
            why[u'building'] += 1
            return False
        # (v37) Alon's file: the line in the yards only (along the south boundary) - not along the
        # building's walls, where v36 put six texts in the west yard. (v38: fences, also those "up to 00 +
        # offset", are not building walls - the south edge runs under the boundary fence)
        if near_wall(p):
            why[u'wall'] += 1
            return False
        if not (yard_or_grass(a) or yard_or_grass(b)):
            why[u'not yard'] += 1
            return False
        why[u'kept'] += 1
        return True

    def keep_step(p, u):
        nx, ny = -u[1], u[0]
        a = (p[0] + nx * off, p[1] + ny * off)
        b = (p[0] - nx * off, p[1] - ny * off)
        za, zb = slab_z(*a), slab_z(*b)
        if za is None or zb is None or abs(za - zb) * 30.48 <= 2.0:
            return False
        return grass(*a) or grass(*b)

    def outer_side(p, n):
        return 1.0 if not in_any(p[0] + n[0] * 1.0, p[1] + n[1] * 1.0) else -1.0

    runs = _runs_along(edges, keep_edge)
    ctx.log(u'basement edge samples (every %.1fm): %s -> %d piece(s) of line' % (
        LINE_SAMPLE_M, u', '.join(u'%s %d' % kv for kv in sorted(why.items())), len(runs)))
    steps = _runs_along(edges, keep_step)
    # a step edge belongs to two slabs - keep one copy
    uniq = []
    for r in steps:
        mx, my = (r[0][0] + r[1][0]) / 2.0, (r[0][1] + r[1][1]) / 2.0
        if not any(abs(mx - (u[0][0] + u[1][0]) / 2.0) < 0.5 and abs(my - (u[0][1] + u[1][1]) / 2.0) < 0.5 for u in uniq):
            uniq.append(r)
    bl, bt = _draw_runs(ctx, runs, style, BASEMENT_TEXT, lambda p, n: -outer_side(p, n))
    sl, st = _draw_runs(ctx, uniq, style, PB_STEP_TEXT, lambda p, n: 1.0)
    return bl, bt, sl, st


def floor_above_line(ctx):
    """"קו קומה מעל": the outline of the floor above wherever it reaches beyond the walls of this floor
    (outside the building rooms), drawn with its own dashed style, texts with arrows, one Group.
    -> (runs, texts, group name or None)"""
    doc, level = ctx.doc, ctx.level
    levels = sorted([l for l in FilteredElementCollector(doc).OfClass(Level)], key=lambda l: l.ProjectElevation)
    floors_by_level = {}
    for fl in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType():
        floors_by_level.setdefault(permit_rooms._id(fl.LevelId), []).append(fl)
    # the first level above that really carries floors (helper levels between the floors are skipped)
    above = next((l for l in levels if l.ProjectElevation > level.ProjectElevation + 1.0
                  and floors_by_level.get(permit_rooms._id(l.Id))), None)
    if above is None:
        return 0, 0, None
    polys = []
    for fl in floors_by_level[permit_rooms._id(above.Id)]:
        pp = permit_rooms.floor_polygons(doc, fl)
        if pp:
            polys.append(pp)
    if not polys:
        return 0, 0, None
    ctx.log(u'קו קומה מעל: the floor above is "%s" (%d floors)' % (above.Name, len(polys)))
    walls = []
    for w in FilteredElementCollector(doc).OfClass(Wall):
        if w.LevelId != level.Id:
            continue
        c = getattr(w.Location, 'Curve', None)
        if c is not None:
            walls.append((c, w.Width / 2.0 + 0.2))
    in_upper = lambda x, y: any(permit_rooms.point_in_polygons(x, y, pp) for pp in polys)

    def near_wall(p):
        q = XYZ(p[0], p[1], 0)
        for c, tol in walls:
            try:
                r = c.Project(XYZ(p[0], p[1], c.GetEndPoint(0).Z))
            except Exception:
                continue
            if r is not None and r.Distance <= tol:
                return True
        return False

    off = 0.3
    lz = level.ProjectElevation
    here = PL.level_floors(ctx.doc, level, landscape=False)

    def built_here(x, y):
        """this floor is BUILT at (x, y): a room of the building, or (no room) a floor of this level at
        the level's height. A yard / the site / a balcony-patio in a yard / the car ramp (it slopes down
        from this floor - its surface is below the level) are open (Alon, 01.10.26)."""
        rm = _room_at(ctx, x, y)
        if rm is not None:
            return _is_interior(ctx, rm)
        f = PL._top_floor_at(here, x, y)
        if f is None:
            return False
        k = permit_rooms._id(f[3].Id)
        if k not in PL._TOPZ_CACHE:
            PL._TOPZ_CACHE[k] = PL._TopZ(f[3])
        tz = PL._TOPZ_CACHE[k].at(x, y)
        tz = f[0].Max.Z if tz is None else tz
        return tz >= lz - 0.3 / 0.3048

    def keep(p, u):
        nx, ny = -u[1], u[0]
        a = (p[0] + nx * off, p[1] + ny * off)
        b = (p[0] - nx * off, p[1] - ny * off)
        ia, ib = in_upper(*a), in_upper(*b)
        if ia == ib:
            return False                          # an inner edge between two slabs of the floor above
        if near_wall(p):
            return False                          # on a wall of this floor - nothing to show
        # (v37) the floor above must really stand out over an OPEN place of this floor. v36 kept every
        # edge that was not between two rooms: the service-balcony niches on 01 (01 is built right there)
        # got texts. Under the ramp the edge is kept - the ramp is not built (Alon, 01.10.26).
        under = a if ia else b
        return not built_here(*under)

    edges = []
    for pp in polys:
        for loop in pp:
            for i in range(len(loop)):
                edges.append((loop[i], loop[(i + 1) % len(loop)]))
    runs = _runs_along(edges, keep)
    # a text on the side away from the floor above (outside), like Alon's file
    side = lambda p, n: 1.0 if not in_upper(p[0] + n[0] * 1.0, p[1] + n[1] * 1.0) else -1.0
    style = line_style(doc, FLOOR_ABOVE_STYLE, 1, ctx.log)
    ids = []
    nl, nt = _draw_runs(ctx, runs, style, FLOOR_ABOVE_TEXT, side, ids)
    gname = None
    if ids:
        try:
            doc.Regenerate()
            grp = doc.Create.NewGroup(List[ElementId](ids))
            gname = u'%s - %s' % (GROUP_PREFIX, level.Name)
            gt_new = doc.GetElement(grp.GetTypeId())
            old = next((gt for gt in FilteredElementCollector(doc).OfClass(GroupType)
                        if permit_rooms._type_key(permit_rooms.elem_name(gt)) == permit_rooms._type_key(gname)
                        and gt.Id != gt_new.Id), None)
            if old is not None:
                try:
                    doc.Delete(old.Id)
                except Exception:
                    pass
            set_name(gt_new, gname)
            PL._mark(grp, ctx.schema, u'levels')
        except Exception as ex:
            ctx.log(u'group "%s" failed: %s' % (GROUP_PREFIX, ex))
            gname = None
    return nl, nt, gname


def delete_old(doc, view, schema):
    """the agent's groups and dashed lines of previous runs in this view"""
    n = 0
    for g in FilteredElementCollector(doc, view.Id).OfClass(Group):
        try:
            if (permit_rooms.elem_name(doc.GetElement(g.GetTypeId())) or u'').startswith(GROUP_PREFIX):
                doc.Delete(g.Id)
                n += 1
        except Exception:
            pass
    for c in FilteredElementCollector(doc, view.Id).OfClass(CurveElement):
        try:
            if PL._kind(c, schema) == u'levels':
                doc.Delete(c.Id)
                n += 1
        except Exception:
            pass
    return n


def text_overlaps(doc, view, schema):
    """pairs of the agent's texts that overlap in the view (the rulebook: never text on text)"""
    boxes = []
    for t in FilteredElementCollector(doc, view.Id).OfClass(TextNote):
        k = PL._kind(t, schema)
        if k not in (u'label', u'levels'):
            continue
        r = note_rect(t, view, view.Scale or 100)
        if r is not None:
            boxes.append((r, (t.Text or u'').strip().split(u'\r')[0]))
    # (v38) the spot values too - v37's check missed "גינון" on "32.42" and "32.05" on "גינון משותף"
    for s in FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_SpotElevations):
        if PL._kind(s, schema) != u'spot':
            continue
        rs = spot_text_rects(doc, s, view.Scale or 100)
        if rs:
            boxes.append((rs[0], u'ערך %s' % (getattr(s, 'ValueString', u'') or u'')))
    pairs = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][0], boxes[j][0]
            ix = min(a[2], b[2]) - max(a[0], b[0])
            iy = min(a[3], b[3]) - max(a[1], b[1])
            if ix > 0.05 and iy > 0.05:
                pairs.append(u'%s / %s' % (boxes[i][1], boxes[j][1]))
    return pairs
