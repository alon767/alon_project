# -*- coding: utf-8 -*-
"""
מידות חיצוניות אוטומטיות
אלון מיכאלי אדריכלות ובינוי ערים
"""
# pylint: disable=import-error
import traceback
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import TaskDialog
from pyrevit import revit, forms

doc   = revit.doc
uidoc = revit.uidoc
view  = uidoc.ActiveView

# =====================================================
# קבועים
# =====================================================
CM    = 0.0328084   # 1 ס"מ בfeet
L1    = 480 * CM
L2    = 440 * CM
L3    = 400 * CM
STYLE = "2.0cm Rounded"

# =====================================================
# ודא שהתצוגה מתאימה
# =====================================================
if not isinstance(view, (ViewPlan, ViewSection)):
    forms.alert(
        "אנא עבור לתצוגת תכנית קומה לפני הפעלת הכלי.",
        title="שגיאה"
    )
    import sys; sys.exit()

# =====================================================
# פונקציות עזר
# =====================================================

def get_dim_style(doc, name):
    for dt in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            n = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            if n == name:
                return dt
        except:
            pass
    return None

def wall_direction(wall):
    loc = wall.Location
    if not isinstance(loc, LocationCurve):
        return None, None
    s = loc.Curve.GetEndPoint(0)
    e = loc.Curve.GetEndPoint(1)
    dx, dy = e.X - s.X, e.Y - s.Y
    ln = (dx**2 + dy**2) ** 0.5
    if ln < 1e-9:
        return None, None
    return XYZ(dx/ln, dy/ln, 0), XYZ(-dy/ln, dx/ln, 0)

def facade_name(wall):
    wd, _ = wall_direction(wall)
    if wd is None:
        return "Unknown"
    return "NS" if abs(wd.X) > abs(wd.Y) else "EW"

def ext_ref(wall):
    try:
        refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior)
        if refs and len(refs) > 0:
            return refs[0]
    except:
        pass
    return None

def int_ref(wall):
    try:
        refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Interior)
        if refs and len(refs) > 0:
            return refs[0]
    except:
        pass
    return None

def opening_ref(fi):
    """Reference לפתח (חלון/דלת)"""
    opts = Options()
    opts.ComputeReferences = True
    opts.View = view
    try:
        geom = fi.get_Geometry(opts)
        for obj in geom:
            if isinstance(obj, GeometryInstance):
                for o2 in obj.GetInstanceGeometry():
                    if isinstance(o2, Solid) and o2.Faces.Size > 0:
                        for face in o2.Faces:
                            if face.Reference:
                                return face.Reference
    except:
        pass
    return None

def make_line(pt_s, pt_e, perp, offset):
    s = XYZ(pt_s.X + perp.X*offset, pt_s.Y + perp.Y*offset, 0)
    e = XYZ(pt_e.X + perp.X*offset, pt_e.Y + perp.Y*offset, 0)
    if s.DistanceTo(e) < 0.01:
        return None
    return Line.CreateBound(s, e)

def create_dim(line, refs, dim_type):
    if line is None or refs is None or refs.Size < 2:
        return None
    if dim_type:
        return doc.Create.NewDimension(view, line, refs, dim_type)
    return doc.Create.NewDimension(view, line, refs)

def sill_cm(win):
    p = win.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
    return round(p.AsDouble() / CM) if p else None

# =====================================================
# איסוף
# =====================================================
walls = list(
    FilteredElementCollector(doc, view.Id)
    .OfClass(Wall)
    .WhereElementIsNotElementType()
)

windows = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_Windows)
    .WhereElementIsNotElementType()
    .Cast[FamilyInstance]()
)

doors = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_Doors)
    .WhereElementIsNotElementType()
    .Cast[FamilyInstance]()
)

# מיפוי פתחים → קיר
openings_by_wall = {w.Id.IntegerValue: [] for w in walls}
for fi in windows + doors:
    if fi.Host and fi.Host.Id.IntegerValue in openings_by_wall:
        openings_by_wall[fi.Host.Id.IntegerValue].append(fi)

dim_type = get_dim_style(doc, STYLE)

# קיבוץ לפי חזית
facades = {}
for w in walls:
    fn = facade_name(w)
    facades.setdefault(fn, []).append(w)

# =====================================================
# יצירת מידות
# =====================================================
created = 0
errors  = []
log     = []

log.append("קירות: {}  חלונות: {}  דלתות: {}".format(
    len(walls), len(windows), len(doors)))
log.append("חזיתות: {}".format(list(facades.keys())))

with revit.Transaction("מידות חיצוניות אוטומטיות"):

    for fname, fwalls in facades.items():

        # כיוון החזית — לפי הקיר הראשון
        wd, perp = wall_direction(fwalls[0])
        if wd is None:
            continue

        # === L1 — מידה כללית אחת לכל החזית ===
        try:
            all_pts = []
            for w in fwalls:
                lc = w.Location.Curve
                all_pts += [lc.GetEndPoint(0), lc.GetEndPoint(1)]

            def proj(pt): return pt.X*wd.X + pt.Y*wd.Y
            min_pt = min(all_pts, key=proj)
            max_pt = max(all_pts, key=proj)

            ra = ReferenceArray()
            for w in fwalls:
                r = ext_ref(w)
                if r: ra.Append(r)

            ln = make_line(min_pt, max_pt, perp, L1)
            d  = create_dim(ln, ra, dim_type)
            if d:
                created += 1
                log.append("L1 חזית {} ✓".format(fname))
            else:
                errors.append("L1 חזית {}: נכשל (refs={})".format(fname, ra.Size))
        except Exception as ex:
            errors.append("L1 חזית {}: {}: {}".format(fname, type(ex).__name__, str(ex)))

        # === L2 — מידות קירות ללא פתחים ===
        for wall in fwalls:
            try:
                lc = wall.Location.Curve
                s  = lc.GetEndPoint(0)
                e  = lc.GetEndPoint(1)

                ra = ReferenceArray()
                r  = ext_ref(wall)
                if r: ra.Append(r)
                r  = int_ref(wall)
                if r: ra.Append(r)

                ln = make_line(s, e, perp, L2)
                d  = create_dim(ln, ra, dim_type)
                if d:
                    created += 1
                    log.append("L2 קיר {} ✓".format(wall.Id.IntegerValue))
                else:
                    errors.append("L2 קיר {}: נכשל (refs={})".format(
                        wall.Id.IntegerValue, ra.Size))
            except Exception as ex:
                errors.append("L2 קיר {}: {}: {}".format(
                    wall.Id.IntegerValue, type(ex).__name__, str(ex)))

        # === L3 — מידות קירות כולל פתחים ===
        for wall in fwalls:
            try:
                lc  = wall.Location.Curve
                s   = lc.GetEndPoint(0)
                e   = lc.GetEndPoint(1)
                ops = openings_by_wall[wall.Id.IntegerValue]

                ra = ReferenceArray()
                r  = ext_ref(wall)
                if r: ra.Append(r)

                for fi in ops:
                    r = opening_ref(fi)
                    if r: ra.Append(r)

                r = int_ref(wall)
                if r: ra.Append(r)

                ln = make_line(s, e, perp, L3)
                d  = create_dim(ln, ra, dim_type)
                if d:
                    created += 1
                    log.append("L3 קיר {} ({} פתחים) ✓".format(
                        wall.Id.IntegerValue, len(ops)))
                else:
                    errors.append("L3 קיר {}: נכשל (refs={})".format(
                        wall.Id.IntegerValue, ra.Size))
            except Exception as ex:
                errors.append("L3 קיר {}: {}: {}".format(
                    wall.Id.IntegerValue, type(ex).__name__, str(ex)))

        # === תוויות UK=X לחלונות ===
        for wall in fwalls:
            for fi in openings_by_wall[wall.Id.IntegerValue]:
                if fi.Category.Id.IntegerValue != int(BuiltInCategory.OST_Windows):
                    continue
                try:
                    h = sill_cm(fi)
                    if h is None: continue
                    wp = fi.Location.Point
                    lp = XYZ(
                        wp.X + perp.X * (L3 - 20*CM),
                        wp.Y + perp.Y * (L3 - 20*CM),
                        wp.Z
                    )
                    tid  = doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType)
                    opts = TextNoteOptions(tid)
                    opts.HorizontalAlignment = HorizontalTextAlignment.Center
                    TextNote.Create(doc, view.Id, lp, "UK={}".format(h), opts)
                    created += 1
                    log.append("UK={} חלון {} ✓".format(h, fi.Id.IntegerValue))
                except Exception as ex:
                    errors.append("UK חלון {}: {}: {}".format(
                        fi.Id.IntegerValue, type(ex).__name__, str(ex)))

# =====================================================
# סיכום
# =====================================================
msg = "נוצרו {} אלמנטים\n\n".format(created)
msg += "\n".join(log)
if errors:
    msg += "\n\nשגיאות ({}):\n".format(len(errors))
    msg += "\n".join(errors)

forms.alert(msg, title="AutoDimensions — סיכום")
