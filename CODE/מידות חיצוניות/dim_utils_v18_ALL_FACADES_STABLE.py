# -*- coding: utf-8 -*-
"""
מודול משותף — לוגיקת מידות
אלון מיכאלי אדריכלות ובינוי ערים
גרסה 14 - תיקון refs לקירות מקבילים לחזית
"""
import traceback
from Autodesk.Revit.DB import *
from pyrevit import revit, forms

CM      = 0.0328084
L1      = 500 * CM
L2      = 460 * CM
L3      = 360 * CM
UK_DIST = 50  * CM
STYLE   = "2.0cm Rounded"
UK_TAG_NAME = "UK"
TOLERANCE   = 10 * CM

# סבילות לזיהוי נורמל ניצב (מאפשר גם חיבורי קיר מוטים קלות)
NORMAL_TOL = 0.1

def get_dim_style(doc, name):
    for dt in FilteredElementCollector(doc).OfClass(DimensionType):
        try:
            n = dt.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            if n == name:
                return dt
        except:
            pass
    return None

def get_tag_type(doc, type_name):
    for ft in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            n = ft.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME).AsString()
            if n == type_name:
                return ft
        except:
            pass
    return None

def get_dim_face_refs(wall, is_horizontal):
    """
    מחזיר (ref_min, ref_max) — refs של פנים ניצבות לכיוון המידה.
    לממד אופקי (חזית דרום/צפון): פנים עם נורמל X, ב-X_min וב-X_max של הקיר.
    לממד אנכי (חזית מזרח/מערב): פנים עם נורמל Y, ב-Y_min וב-Y_max של הקיר.
    עובד עבור כל קיר, ללא תלות בכיוונו ביחס לחזית.
    """
    try:
        opts = Options()
        opts.ComputeReferences = True
        geom = wall.get_Geometry(opts)
        if geom is None:
            return None, None

        candidates = []  # (coord, Reference)
        for obj in geom:
            if not isinstance(obj, Solid):
                continue
            for face in obj.Faces:
                try:
                    n = face.FaceNormal
                    if is_horizontal:
                        if abs(abs(n.X) - 1.0) > NORMAL_TOL:
                            continue
                    else:
                        if abs(abs(n.Y) - 1.0) > NORMAL_TOL:
                            continue
                    # נקודה מרכזית על הפנים לאיתור הקואורדינטה
                    bbox = face.GetBoundingBox()
                    uv_mid = UV(
                        (bbox.Min.U + bbox.Max.U) / 2.0,
                        (bbox.Min.V + bbox.Max.V) / 2.0
                    )
                    pt = face.Evaluate(uv_mid)
                    coord = pt.X if is_horizontal else pt.Y
                    candidates.append((coord, face.Reference))
                except:
                    pass

        if not candidates:
            return None, None

        candidates.sort(key=lambda x: x[0])
        return candidates[0][1], candidates[-1][1]  # ref_min, ref_max
    except:
        return None, None

def get_opening_side_refs(fi, is_horizontal):
    try:
        r1 = fi.GetReferences(FamilyInstanceReferenceType.Left)
        r2 = fi.GetReferences(FamilyInstanceReferenceType.Right)
        result = []
        if r1 and len(r1) > 0: result.append(r1[0])
        if r2 and len(r2) > 0: result.append(r2[0])
        return result
    except:
        return []

def try_dim(doc, view, line, refs, dim_type):
    if line is None or refs is None or refs.Size < 2:
        return None, "refs={}".format(refs.Size if refs else 0)
    try:
        d = doc.Create.NewDimension(view, line, refs, dim_type) if dim_type \
            else doc.Create.NewDimension(view, line, refs)
        return d, None
    except Exception as ex:
        return None, "{}: {}".format(type(ex).__name__, str(ex))

def make_line(p1, p2):
    if p1.DistanceTo(p2) < 0.01:
        return None
    return Line.CreateBound(p1, p2)

def get_wall_pts(wall):
    loc = wall.Location
    if not isinstance(loc, LocationCurve):
        return None, None
    return loc.Curve.GetEndPoint(0), loc.Curve.GetEndPoint(1)

def is_horiz_wall(wall):
    s, e = get_wall_pts(wall)
    if not s: return False
    return abs(e.X - s.X) > abs(e.Y - s.Y)

def run_facade(facade_name, is_horizontal, perp_x, perp_y):
    try:
        doc   = revit.doc
        uidoc = revit.uidoc
        view  = uidoc.ActiveView

        # קבועי מרחק לפי חזית (ניתן לכוונן כל חזית בנפרד)
        l1_dist  = L1
        l2_dist  = L2
        l3_dist  = L3
        uk_dist  = UK_DIST
        if facade_name == "צפון":
            l1_dist  = L1  - 50  * CM
            l2_dist  = L2  - 50  * CM
            uk_dist  = UK_DIST - 100 * CM
        elif facade_name == "מערב":
            l1_dist  = L1  - 50 * CM
            l2_dist  = L2  - 50 * CM
            uk_dist  = -UK_DIST  # 50 ס"מ לכיוון הקיר מ-L3
        elif facade_name == "מזרח":
            uk_dist  = UK_DIST  # 50 ס"מ לכיוון הרחוק מהחזית מ-L3

        if not isinstance(view, (ViewPlan, ViewSection)):
            forms.alert("אנא עבור לתצוגת תכנית קומה.", title="שגיאה")
            return

        all_walls = list(
            FilteredElementCollector(doc, view.Id)
            .OfClass(Wall)
            .WhereElementIsNotElementType()
        )
        if not all_walls:
            forms.alert("לא נמצאו קירות!", title="שגיאה")
            return

        # קצוות המבנה
        all_pts = []
        for w in all_walls:
            s, e = get_wall_pts(w)
            if s: all_pts += [s, e]

        bld_min_x = min(p.X for p in all_pts)
        bld_max_x = max(p.X for p in all_pts)
        bld_min_y = min(p.Y for p in all_pts)
        bld_max_y = max(p.Y for p in all_pts)

        # ================================================
        # מציאת קירות החזית — לוגיקת "חשוף לחזית"
        # קיר מקביל לחזית נכלל אם אין קיר אחר שחוסם אותו מבחוץ
        # קיר ניצב לחזית נכלל אם קצהו נוגע בקיר מקביל חשוף
        # ================================================
        def build_exposed_ids(facade):
            """
            מחזיר set של IDs של קירות מקבילים החשופים לחזית.
            קיר נחשב חשוף אם יש לו לפחות נקודת X אחת (או Y) שאין מולה
            קיר אחר שחוסם אותה מהכיוון החיצוני — בדיקת קו ראייה חלקי.
            """
            parallel_data = []
            for w in all_walls:
                s2, e2 = get_wall_pts(w)
                if not s2: continue
                if facade in ("דרום", "צפון") and not is_horiz_wall(w): continue
                if facade in ("מזרח", "מערב") and is_horiz_wall(w): continue
                parallel_data.append((
                    w.Id.IntegerValue,
                    min(s2.X, e2.X), max(s2.X, e2.X),
                    min(s2.Y, e2.Y), max(s2.Y, e2.Y)
                ))

            def has_sightline(wid, x1, x2, y1, y2):
                """
                בודק אם יש לפחות טווח X (או Y) קטן שאין מולו חוסם.
                אוסף את כל טווחי ה-X שחסומים ע"י קירות חיצוניים יותר,
                ובודק שהם לא מכסים את כל הטווח.
                """
                blocking = []
                for wid2, x12, x22, y12, y22 in parallel_data:
                    if wid2 == wid: continue
                    if facade == "דרום":
                        if y12 >= y1 - TOLERANCE: continue  # לא חיצוני יותר
                        bx1 = max(x12, x1); bx2 = min(x22, x2)
                        if bx2 > bx1 + TOLERANCE: blocking.append((bx1, bx2))
                    elif facade == "צפון":
                        if y22 <= y2 + TOLERANCE: continue
                        bx1 = max(x12, x1); bx2 = min(x22, x2)
                        if bx2 > bx1 + TOLERANCE: blocking.append((bx1, bx2))
                    elif facade == "מזרח":
                        if x22 <= x2 + TOLERANCE: continue
                        by1 = max(y12, y1); by2 = min(y22, y2)
                        if by2 > by1 + TOLERANCE: blocking.append((by1, by2))
                    else:  # מערב
                        if x12 >= x1 - TOLERANCE: continue
                        by1 = max(y12, y1); by2 = min(y22, y2)
                        if by2 > by1 + TOLERANCE: blocking.append((by1, by2))

                if not blocking:
                    return True  # אין חוסמים כלל

                # בדוק אם החוסמים מכסים את כל הטווח
                rng_start = x1 if facade in ("דרום", "צפון") else y1
                rng_end   = x2 if facade in ("דרום", "צפון") else y2
                blocking.sort()
                covered = rng_start
                for b1, b2 in blocking:
                    if b1 > covered + TOLERANCE:
                        return True  # נמצא פתח — קו ראייה פנוי
                    covered = max(covered, b2)
                return covered < rng_end - TOLERANCE

            exposed = set()
            for wid, x1, x2, y1, y2 in parallel_data:
                if has_sightline(wid, x1, x2, y1, y2):
                    exposed.add(wid)
            return exposed

        exposed_parallel_ids = build_exposed_ids(facade_name)

        # קואורדינטות Y (או X) של הקירות המקבילים החשופים — לאיתור ניצבים
        exposed_coords = set()
        for w in all_walls:
            if w.Id.IntegerValue not in exposed_parallel_ids: continue
            s2, e2 = get_wall_pts(w)
            if not s2: continue
            if is_horizontal:
                exposed_coords.add(round(min(s2.Y, e2.Y), 3))
            else:
                exposed_coords.add(round(min(s2.X, e2.X), 3))

        if facade_name == "דרום":
            ref_coord = bld_min_y
        elif facade_name == "צפון":
            ref_coord = bld_max_y
        elif facade_name == "מזרח":
            ref_coord = bld_max_x
        else:
            ref_coord = bld_min_x

        def on_facade(w):
            s, e = get_wall_pts(w)
            if not s: return False
            # קיר מקביל חשוף
            if w.Id.IntegerValue in exposed_parallel_ids:
                return True
            # קיר ניצב שקצהו נוגע בקיר מקביל חשוף
            if is_horizontal and not is_horiz_wall(w):
                for pt in [s, e]:
                    if round(pt.Y, 3) in exposed_coords:
                        return True
            if not is_horizontal and is_horiz_wall(w):
                for pt in [s, e]:
                    if round(pt.X, 3) in exposed_coords:
                        return True
            return False

        facade_walls = [w for w in all_walls if on_facade(w)]

        if not facade_walls:
            forms.alert("לא נמצאו קירות לחזית {}!".format(facade_name), title="שגיאה")
            return

        # span מקצה לקצה
        facade_pts = []
        for w in facade_walls:
            s, e = get_wall_pts(w)
            if s: facade_pts += [s, e]

        if is_horizontal:
            span_start = min(p.X for p in facade_pts)
            span_end   = max(p.X for p in facade_pts)
        else:
            span_start = min(p.Y for p in facade_pts)
            span_end   = max(p.Y for p in facade_pts)

        # ================================================
        # coord_to_ref — קואורדינטות קצות קירות על קו החזית
        # ================================================
        # שלב 1: אסוף קואורדינטות (X או Y) מכל קירות החזית
        # לקירות מקבילים — מוסיפים קצה רק אם אינו מכוסה ע"י קיר חיצוני יותר.
        # כך coord_to_ref מייצג רק גבולות חשופים, ו-L3 מתקצר אוטומטית.
        # ================================================

        # טבלת כל הקירות המקבילים בבנין לצורך בדיקת כיסוי
        all_parallel_info = []
        for w2 in all_walls:
            s2, e2 = get_wall_pts(w2)
            if not s2: continue
            if is_horizontal and not is_horiz_wall(w2): continue
            if not is_horizontal and is_horiz_wall(w2): continue
            all_parallel_info.append((
                w2.Id.IntegerValue,
                min(s2.X, e2.X), max(s2.X, e2.X),
                min(s2.Y, e2.Y), max(s2.Y, e2.Y)
            ))

        def coord_is_externally_covered(coord, w_id, w_outer):
            """
            בודק אם קואורדינטה (X לחזית דרום/צפון, Y לחזית מזרח/מערב)
            מכוסה ע"י קיר מקביל שנמצא חיצוני יותר (קרוב יותר לחוץ).
            """
            for wid2, wx1, wx2, wy1, wy2 in all_parallel_info:
                if wid2 == w_id: continue
                # הקיר השני חייב להיות חיצוני יותר
                if facade_name == "דרום" and wy1 >= w_outer - TOLERANCE: continue
                if facade_name == "צפון" and wy2 <= w_outer + TOLERANCE: continue
                if facade_name == "מזרח" and wx2 <= w_outer + TOLERANCE: continue
                if facade_name == "מערב" and wx1 >= w_outer - TOLERANCE: continue
                # בדוק אם הוא מכסה את הקואורדינטה
                if is_horizontal:
                    if wx1 - TOLERANCE <= coord <= wx2 + TOLERANCE:
                        return True
                else:
                    if wy1 - TOLERANCE <= coord <= wy2 + TOLERANCE:
                        return True
            return False

        facade_endpoint_coords = set()
        for w in facade_walls:
            s, e = get_wall_pts(w)
            if not s: continue
            if is_horizontal:
                if is_horiz_wall(w):
                    w_outer = min(s.Y, e.Y)
                    for xc in [round(min(s.X, e.X), 3), round(max(s.X, e.X), 3)]:
                        if not coord_is_externally_covered(xc, w.Id.IntegerValue, w_outer):
                            facade_endpoint_coords.add(xc)
                else:
                    # קיר ניצב — נקודה דרומית ביותר, רק אם לא מכוסה ע"י קיר מקביל חיצוני
                    pt = s if s.Y < e.Y else e
                    tip_x = round(pt.X, 3)
                    if not coord_is_externally_covered(tip_x, w.Id.IntegerValue, pt.Y):
                        facade_endpoint_coords.add(tip_x)
            else:
                if not is_horiz_wall(w):
                    w_outer = (max(s.X, e.X) if facade_name == "מזרח" else min(s.X, e.X))
                    for yc in [round(min(s.Y, e.Y), 3), round(max(s.Y, e.Y), 3)]:
                        if not coord_is_externally_covered(yc, w.Id.IntegerValue, w_outer):
                            facade_endpoint_coords.add(yc)
                else:
                    if facade_name == "מזרח":
                        pt = s if s.X > e.X else e
                    else:
                        pt = s if s.X < e.X else e
                    tip_y = round(pt.Y, 3)
                    tip_outer = pt.X
                    if not coord_is_externally_covered(tip_y, w.Id.IntegerValue, tip_outer):
                        facade_endpoint_coords.add(tip_y)

        # שלב 2: עבור כל קואורדינטה, מצא ref של פנים ניצבת לקו המידה.
        # מעבד קירות מקבילים ראשונים (ref ברור לפי x_min/x_max),
        # ורק אחר כך קירות ניצבים (בוחר פנים לפי מיקום שמאל/ימין).
        coord_to_ref = {}
        span_mid = (span_start + span_end) / 2.0

        parallel_walls_f  = [w for w in facade_walls
                              if (is_horizontal and     is_horiz_wall(w))
                              or (not is_horizontal and not is_horiz_wall(w))]
        perp_walls_f      = [w for w in facade_walls
                              if (is_horizontal and not is_horiz_wall(w))
                              or (not is_horizontal and     is_horiz_wall(w))]

        for coord in sorted(facade_endpoint_coords):
            # סבב 1 — קירות מקבילים (ref ברור לפי endpoint min/max)
            # לחזיתות אופקיות (דרום/צפון): קירות EW קודם.
            # לחזיתות אנכיות (מזרח/מערב): קירות EW (ניצבים) קודם כי הם
            # מכילים את הפנים החיצונית האמיתית בקצוות הספאן.
            first_pass  = perp_walls_f if not is_horizontal else parallel_walls_f
            second_pass = parallel_walls_f if not is_horizontal else perp_walls_f

            for w in first_pass:
                s, e = get_wall_pts(w)
                if not s: continue
                ref_min, ref_max = get_dim_face_refs(w, is_horizontal)
                if is_horizontal:
                    x_min = round(min(s.X, e.X), 3)
                    x_max = round(max(s.X, e.X), 3)
                    if abs(x_min - coord) < 0.01 and ref_min and coord not in coord_to_ref:
                        coord_to_ref[coord] = ref_min; break
                    if abs(x_max - coord) < 0.01 and ref_max and coord not in coord_to_ref:
                        coord_to_ref[coord] = ref_max; break
                else:
                    # ניצבים (EW) — Y קבוע; בחירת פנים לפי מיקום ביחס ל-NS חשוף
                    wall_y = round(min(s.Y, e.Y), 3)
                    if abs(wall_y - coord) < 0.01:
                        if abs(coord - round(span_start, 3)) < 0.015:
                            chosen = ref_min   # קצה דרומי → פנים דרומית חיצונית
                        elif abs(coord - round(span_end, 3)) < 0.015:
                            chosen = ref_max   # קצה צפוני → פנים צפונית חיצונית
                        else:
                            # קיר ביניים: בדוק איזה NS חשוף גובל בקואורדינטה זו
                            chosen = ref_min   # ברירת מחדל: NS חשוף מדרום
                            for ns_w in parallel_walls_f:
                                if ns_w.Id.IntegerValue not in exposed_parallel_ids:
                                    continue
                                ns_s2, ns_e2 = get_wall_pts(ns_w)
                                if not ns_s2: continue
                                if abs(round(min(ns_s2.Y, ns_e2.Y), 3) - coord) < 0.015:
                                    chosen = ref_max  # NS חשוף מתחיל כאן → מצפון
                                    break
                        if chosen:
                            coord_to_ref[coord] = chosen; break

            if coord in coord_to_ref:
                continue

            # סבב 2 — קירות השני
            for w in second_pass:
                s, e = get_wall_pts(w)
                if not s: continue
                ref_min, ref_max = get_dim_face_refs(w, is_horizontal)
                if is_horizontal:
                    # ניצבים (NS) — X קבוע
                    wall_x = round(min(s.X, e.X), 3)
                    if abs(wall_x - coord) < 0.01:
                        chosen = ref_min if coord <= span_mid else ref_max
                        if chosen:
                            coord_to_ref[coord] = chosen; break
                else:
                    # מקבילים (NS) — Y endpoints
                    y_min = round(min(s.Y, e.Y), 3)
                    y_max = round(max(s.Y, e.Y), 3)
                    if abs(y_min - coord) < 0.01 and ref_min and coord not in coord_to_ref:
                        coord_to_ref[coord] = ref_min; break
                    if abs(y_max - coord) < 0.01 and ref_max and coord not in coord_to_ref:
                        coord_to_ref[coord] = ref_max; break

        sorted_coords = sorted(coord_to_ref.keys())

        # ================================================
        # פתחים — חלונות ודלתות בקירות החזית
        # ================================================
        windows = list(FilteredElementCollector(doc, view.Id)
                      .OfCategory(BuiltInCategory.OST_Windows)
                      .WhereElementIsNotElementType())
        doors   = list(FilteredElementCollector(doc, view.Id)
                      .OfCategory(BuiltInCategory.OST_Doors)
                      .WhereElementIsNotElementType())

        facade_wall_ids = set(w.Id.IntegerValue for w in facade_walls)
        openings_by_wall = {w.Id.IntegerValue: [] for w in facade_walls}
        for fi in windows + doors:
            try:
                if fi.Host and fi.Host.Id.IntegerValue in facade_wall_ids:
                    openings_by_wall[fi.Host.Id.IntegerValue].append(fi)
            except:
                pass

        dim_type = get_dim_style(doc, STYLE)
        tag_type = get_tag_type(doc, UK_TAG_NAME)

        def dim_line_at(offset):
            if is_horizontal:
                p1 = XYZ(span_start, ref_coord + perp_y * offset, 0)
                p2 = XYZ(span_end,   ref_coord + perp_y * offset, 0)
            elif facade_name == "מזרח":
                # כיוון הפוך — כיתוב פונה לכיוון החזית (מערבה)
                p1 = XYZ(ref_coord + perp_x * offset, span_end,   0)
                p2 = XYZ(ref_coord + perp_x * offset, span_start, 0)
            else:
                p1 = XYZ(ref_coord + perp_x * offset, span_start, 0)
                p2 = XYZ(ref_coord + perp_x * offset, span_end,   0)
            return make_line(p1, p2)

        log    = []
        errors = []
        created = 0

        log.append("חזית {}: {} קירות, {} קואורדינטות".format(
            facade_name, len(facade_walls), len(sorted_coords)))

        with revit.Transaction("מידות {} אוטומטיות".format(facade_name)):

            # ===================================================
            # L1 — מידה כללית אחת
            # ===================================================
            ra = ReferenceArray()
            if len(sorted_coords) >= 2:
                r = coord_to_ref.get(sorted_coords[0])
                if r: ra.Append(r)
                r = coord_to_ref.get(sorted_coords[-1])
                if r: ra.Append(r)

            ln = dim_line_at(l1_dist)
            d, err = try_dim(doc, view, ln, ra, dim_type)
            if d: created += 1; log.append("L1 ✓")
            else: errors.append("L1: {}".format(err))

            # ===================================================
            # L2 — מידות בין כל קצות הקירות על קו החזית
            # ===================================================
            ra = ReferenceArray()
            for c in sorted_coords:
                r = coord_to_ref[c]
                if r: ra.Append(r)

            ln = dim_line_at(l2_dist)
            d, err = try_dim(doc, view, ln, ra, dim_type)
            if d: created += 1; log.append("L2 ✓ ({} refs)".format(ra.Size))
            else: errors.append("L2: {}".format(err))

            # ===================================================
            # L3 — קו מידה נפרד לכל קיר מקביל לחזית (עם פתחים)
            # קירות ניצבים לחזית מדולגים (הם תורמים ל-L2 בלבד)
            # ===================================================
            if is_horizontal:
                parallel_walls = [w for w in facade_walls if is_horiz_wall(w)]
                sorted_facade = sorted(parallel_walls,
                    key=lambda w: min(get_wall_pts(w)[0].X, get_wall_pts(w)[1].X))
            else:
                parallel_walls = [w for w in facade_walls if not is_horiz_wall(w)]
                sorted_facade = sorted(parallel_walls,
                    key=lambda w: min(get_wall_pts(w)[0].Y, get_wall_pts(w)[1].Y))

            for w in sorted_facade:
                ops = openings_by_wall.get(w.Id.IntegerValue, [])
                s, e = get_wall_pts(w)

                if is_horizontal:
                    w_min = min(s.X, e.X)
                    w_max = max(s.X, e.X)
                else:
                    w_min = min(s.Y, e.Y)
                    w_max = max(s.Y, e.Y)

                # -------------------------------------------------------
                # גבולות L3: רק החלק החשוף של הקיר
                # מחשבים את הטווח החשוף (ללא כיסוי ע"י קירות חיצוניים יותר),
                # ומסננים את wall_coords לפיו.
                # -------------------------------------------------------
                if is_horizontal:
                    w_outer = min(s.Y, e.Y)
                else:
                    w_outer = (max(s.X, e.X) if facade_name == "מזרח"
                               else min(s.X, e.X))

                # חישוב הטווח החשוף של הקיר
                blocking = []
                for wid2, wx1, wx2, wy1, wy2 in all_parallel_info:
                    if wid2 == w.Id.IntegerValue: continue
                    if facade_name == "דרום" and wy1 >= w_outer - TOLERANCE: continue
                    if facade_name == "צפון" and wy2 <= w_outer + TOLERANCE: continue
                    if facade_name == "מזרח" and wx2 <= w_outer + TOLERANCE: continue
                    if facade_name == "מערב" and wx1 >= w_outer - TOLERANCE: continue
                    if is_horizontal:
                        b1, b2 = max(wx1, w_min), min(wx2, w_max)
                    else:
                        b1, b2 = max(wy1, w_min), min(wy2, w_max)
                    if b2 > b1 + TOLERANCE:
                        blocking.append((b1, b2))

                if blocking:
                    blocking.sort()
                    exposed_parts = []
                    covered = w_min
                    for b1, b2 in blocking:
                        if b1 > covered + TOLERANCE:
                            exposed_parts.append((covered, b1))
                        covered = max(covered, b2)
                    if covered < w_max - TOLERANCE:
                        exposed_parts.append((covered, w_max))
                    exp_min = exposed_parts[0][0] if exposed_parts else w_min
                    exp_max = exposed_parts[-1][1] if exposed_parts else covered
                else:
                    exp_min, exp_max = w_min, w_max

                wall_coords = [c for c in sorted_coords
                               if exp_min - TOLERANCE <= c <= exp_max + TOLERANCE]

                if len(wall_coords) >= 2:
                    dim_start   = wall_coords[0]
                    dim_end     = wall_coords[-1]
                    start_ref   = coord_to_ref[dim_start]
                    end_ref     = coord_to_ref[dim_end]
                else:
                    # גיבוי: refs ישירים מהגיאומטריה של הקיר
                    ref_min, ref_max = get_dim_face_refs(w, is_horizontal)
                    dim_start, dim_end = w_min, w_max
                    start_ref, end_ref = ref_min, ref_max

                if not start_ref or not end_ref:
                    errors.append("L3 קיר {}: לא נמצאו refs".format(w.Id.IntegerValue))
                    continue

                if is_horizontal:
                    lp1 = XYZ(dim_start, ref_coord + perp_y * l3_dist, 0)
                    lp2 = XYZ(dim_end,   ref_coord + perp_y * l3_dist, 0)
                elif facade_name == "מזרח":
                    lp1 = XYZ(ref_coord + perp_x * l3_dist, dim_end,   0)
                    lp2 = XYZ(ref_coord + perp_x * l3_dist, dim_start, 0)
                else:
                    lp1 = XYZ(ref_coord + perp_x * l3_dist, dim_start, 0)
                    lp2 = XYZ(ref_coord + perp_x * l3_dist, dim_end,   0)

                ra = ReferenceArray()
                ra.Append(start_ref)

                if ops:
                    if is_horizontal:
                        sorted_ops = sorted(ops, key=lambda fi: fi.Location.Point.X)
                        sorted_ops = [fi for fi in sorted_ops
                                      if dim_start - TOLERANCE <= fi.Location.Point.X <= dim_end + TOLERANCE]
                    else:
                        sorted_ops = sorted(ops, key=lambda fi: fi.Location.Point.Y)
                        sorted_ops = [fi for fi in sorted_ops
                                      if dim_start - TOLERANCE <= fi.Location.Point.Y <= dim_end + TOLERANCE]
                    for fi in sorted_ops:
                        srefs = get_opening_side_refs(fi, is_horizontal)
                        for sr in srefs:
                            ra.Append(sr)

                ra.Append(end_ref)

                if ra.Size >= 2:
                    ln = make_line(lp1, lp2)
                    d, err = try_dim(doc, view, ln, ra, dim_type)
                    if d:
                        created += 1
                        log.append("L3 קיר {} ({} פתחים, refs={}) ✓".format(
                            w.Id.IntegerValue, len(ops), ra.Size))
                    else:
                        errors.append("L3 קיר {}: {}".format(w.Id.IntegerValue, err))
                else:
                    errors.append("L3 קיר {}: refs לא מספיקים ({})".format(
                        w.Id.IntegerValue, ra.Size))

            # ===================================================
            # UK Tags — תגיות UK לחלונות
            # ===================================================
            for w in sorted_facade:
                for fi in openings_by_wall.get(w.Id.IntegerValue, []):
                    try:
                        if fi.Category.Id.IntegerValue != int(BuiltInCategory.OST_Windows):
                            continue
                        sill_param = fi.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
                        if sill_param is None or sill_param.AsDouble() <= 0:
                            continue
                        wp = fi.Location.Point
                        if is_horizontal:
                            tag_pt = XYZ(wp.X,
                                        ref_coord + perp_y * (l3_dist + uk_dist),
                                        wp.Z)
                        else:
                            tag_pt = XYZ(ref_coord + perp_x * (l3_dist + uk_dist),
                                        wp.Y, wp.Z)

                        tag = IndependentTag.Create(
                            doc, view.Id, Reference(fi),
                            False,
                            TagMode.TM_ADDBY_CATEGORY,
                            TagOrientation.Horizontal,
                            tag_pt
                        )
                        if tag_type:
                            tag.ChangeTypeId(tag_type.Id)
                        created += 1
                        log.append("UK Tag {} ✓".format(fi.Id.IntegerValue))
                    except Exception as ex:
                        errors.append("UK Tag {}: {}: {}".format(
                            fi.Id.IntegerValue, type(ex).__name__, str(ex)))

        msg = "חזית {} — נוצרו {} אלמנטים\n\n".format(facade_name, created)
        msg += "\n".join(log)
        if errors:
            msg += "\n\nשגיאות ({}):\n".format(len(errors))
            msg += "\n".join(errors)

        forms.alert(msg, title="AutoDimensions — {}".format(facade_name))

    except Exception as e:
        forms.alert("שגיאה:\n{}\n\n{}".format(str(e), traceback.format_exc()),
                    title="שגיאה קריטית")
