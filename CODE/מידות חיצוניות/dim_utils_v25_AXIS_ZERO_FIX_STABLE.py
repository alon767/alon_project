# -*- coding: utf-8 -*-
"""
מודול משותף — לוגיקת מידות
אלון מיכאלי אדריכלות ובינוי ערים
גרסה 21 - הגדרות (סגנון מידה נבחר) + מצב "Multiple" חזרתי
"""
import os
import json
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

# ================================================================
# הגדרות משותפות (נשמרות בקובץ JSON) — מצב "Multiple" וסגנון מידה נבחר
# ================================================================
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "autodim_settings.json")
CW_TAG_NAME = "UK_קיר מסך"
DEFAULT_SETTINGS = {
    "multiple": False,
    "dim_style": STYLE,
    "uk_tag_style": UK_TAG_NAME,
    "cw_tag_style": CW_TAG_NAME,   # תגית UK (Base Offset) לקיר מסך
    "wall_dim_offset": 100,   # מרחק קו מידה מהקיר בס"מ: L3 בחזיתות + offset ב"מידות פנימיות עם פתחים"
    "reference_view_ids": [],  # ElementId.IntegerValue של תכניות הקומה (ViewPlan) שנבחרו ב"בחירת מפלסים";
                                # לא ריק => מידות חיצוניות ירוצו אוטומטית על כל התכניות שנבחרו
}

def load_settings():
    try:
        with open(SETTINGS_PATH, "r") as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except:
        return dict(DEFAULT_SETTINGS)

def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f)
    except:
        pass

def get_active_dim_style_name():
    return load_settings().get("dim_style") or STYLE

def get_active_uk_tag_name():
    return load_settings().get("uk_tag_style") or UK_TAG_NAME

def get_active_cw_tag_name():
    return load_settings().get("cw_tag_style") or CW_TAG_NAME

# קיר מקביל נמוך מהגובה הזה נחשב "שקוף": לא נמדד בעצמו, ולא חוסם
# קיר מקביל אחר שנמצא מאחוריו (למשל מעקה/קיר חצי-גובה מול קיר מלא)
LOW_WALL_HEIGHT = 200 * CM

def get_wall_height(wall):
    """גובה קיר בפועל (BoundingBox) — עובד גם כשהקיר מוגבל ע"י Level ולא Unconnected."""
    try:
        bbox = wall.get_BoundingBox(None)
        if bbox is None:
            return None
        return bbox.Max.Z - bbox.Min.Z
    except:
        return None

def is_low_wall(wall):
    # Curtain Wall הוא תמיד אלמנט זיגוג אמיתי (כמו רצועת חלון) — לא
    # מעקה/קיר-חצי-גובה שקוף, גם אם הוא נמוך מ-200 ס"מ. לא לסנן אותו כאן.
    try:
        if wall.WallType.Kind == WallKind.Curtain:
            return False
    except:
        pass
    h = get_wall_height(wall)
    return h is not None and h < LOW_WALL_HEIGHT

# סבילות לזיהוי נורמל ניצב (מאפשר גם חיבורי קיר מוטים קלות)
NORMAL_TOL = 0.1

LINEAR_STYLE_TYPES = (DimensionStyleType.Linear, DimensionStyleType.LinearFixed)

def is_linear_dim_style(dt):
    """True רק עבור סגנונות מידה ליניארית (לא זוויתית/רדיאלית/ספוט וכו')."""
    try:
        return dt.StyleType in LINEAR_STYLE_TYPES
    except:
        return False

def get_dim_style(doc, name):
    for dt in FilteredElementCollector(doc).OfClass(DimensionType):
        if not is_linear_dim_style(dt):
            continue
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

def _add_face_ref_candidate(candidates, face, ref, is_horizontal):
    try:
        n = face.FaceNormal
        if is_horizontal:
            if abs(abs(n.X) - 1.0) > NORMAL_TOL:
                return
        else:
            if abs(abs(n.Y) - 1.0) > NORMAL_TOL:
                return
        # נקודה מרכזית על הפנים לאיתור הקואורדינטה
        bbox = face.GetBoundingBox()
        uv_mid = UV(
            (bbox.Min.U + bbox.Max.U) / 2.0,
            (bbox.Min.V + bbox.Max.V) / 2.0
        )
        pt = face.Evaluate(uv_mid)
        coord = pt.X if is_horizontal else pt.Y
        candidates.append((coord, ref))
    except:
        pass

def _iter_wall_solids(geom_element, depth=0):
    """
    מרחיב רקורסיבית GeometryInstance ומחזיר את כל ה-Solid עם פאות.
    קיר רגיל: Solid נמצא ישירות ברמה העליונה של הגיאומטריה.
    Curtain Wall: הגיאומטריה מורכבת מ-GeometryInstance מקוננים (mullions/panels),
    ולכן צריך לרדת רמה כדי להגיע ל-Solid בפועל.
    """
    if depth > 3:
        return
    for obj in geom_element:
        if isinstance(obj, Solid):
            if obj.Faces.Size > 0:
                yield obj
        elif isinstance(obj, GeometryInstance):
            try:
                inst_geom = obj.GetInstanceGeometry()
            except:
                continue
            for s in _iter_wall_solids(inst_geom, depth + 1):
                yield s

def _debug_geom_summary(wall):
    """
    מחזיר תקציר טקסטואלי של גיאומטריית הקיר — לאבחון בלבד, מוצג בהודעת
    שגיאה כשלא נמצאו refs (למשל Curtain Wall בעייתי).
    """
    try:
        wt = wall.WallType
        kind = wt.Kind if wt else "?"
        opts = Options()
        opts.ComputeReferences = True
        opts.DetailLevel = ViewDetailLevel.Fine
        geom = wall.get_Geometry(opts)
        if geom is None:
            return "kind={} geom=None".format(kind)
        top_solids = 0
        top_instances = 0
        for obj in geom:
            if isinstance(obj, Solid):
                top_solids += 1
            elif isinstance(obj, GeometryInstance):
                top_instances += 1
        nested_solids = 0
        nested_faces = 0
        for s in _iter_wall_solids(geom):
            nested_solids += 1
            try:
                nested_faces += s.Faces.Size
            except:
                pass
        try:
            sc = wall.SuperComponent
            host = "id={}".format(sc.Id.IntegerValue) if sc else "None"
        except Exception as ex2:
            host = "err:{}".format(type(ex2).__name__)
        return "kind={} host={} top_solids={} top_instances={} nested_solids={} nested_faces={}".format(
            kind, host, top_solids, top_instances, nested_solids, nested_faces)
    except Exception as ex:
        return "debug_error: {}: {}".format(type(ex).__name__, str(ex))

def get_dim_face_refs(wall, is_horizontal, return_coords=False):
    """
    מחזיר (ref_min, ref_max) — refs של פנים ניצבות לכיוון המידה.
    לממד אופקי (חזית דרום/צפון): פנים עם נורמל X, ב-X_min וב-X_max של הקיר.
    לממד אנכי (חזית מזרח/מערב): פנים עם נורמל Y, ב-Y_min וב-Y_max של הקיר.
    עובד עבור כל קיר, ללא תלות בכיוונו ביחס לחזית — כולל Curtain Wall
    (שם הגיאומטריה בנויה מ-GeometryInstance מקוננים של mullions/panels
    ולא מ-Solid ישיר, ולכן צריך לרדת רקורסיבית כדי למצוא את פאות הקצה).

    return_coords=True: מחזיר (coord_min, ref_min, coord_max, ref_max) —
    כולל הקואורדינטה בפועל של כל פאה (לא רק ה-Reference), לשימוש בקוד
    שצריך למיין/לסנן פאות מכמה קירות יחד לפי מיקום אמיתי (למשל מניעת
    סגמנט מידה "0" כשקירות סמוכים תורמים פאות כמעט-חופפות — ראו
    _run_axis_dim_once).
    """
    empty = (None, None, None, None) if return_coords else (None, None)
    try:
        opts = Options()
        opts.ComputeReferences = True
        # Curtain Wall: ה-mullions/panels לא נוצרים בגיאומטריה כלל אם
        # DetailLevel לא נקבע במפורש ל-Fine (ברירת המחדל דלה מדי וגורמת
        # ל-get_Geometry להחזיר עץ ריק/חלקי עבור הקיר).
        opts.DetailLevel = ViewDetailLevel.Fine
        geom = wall.get_Geometry(opts)
        if geom is None:
            return empty

        candidates = []  # (coord, Reference)
        for solid in _iter_wall_solids(geom):
            try:
                faces = solid.Faces
            except:
                continue
            for face in faces:
                try:
                    ref = face.Reference
                except:
                    continue
                if ref is None:
                    continue
                _add_face_ref_candidate(candidates, face, ref, is_horizontal)

        if not candidates:
            return empty

        candidates.sort(key=lambda x: x[0])
        c_min, r_min = candidates[0]
        c_max, r_max = candidates[-1]
        if return_coords:
            return c_min, r_min, c_max, r_max
        return r_min, r_max  # ref_min, ref_max
    except:
        return empty

def _find_ref_at_coord(walls, coord, is_horizontal, exclude_id, tol=0.05):
    """
    גיבוי לקיר בלי גיאומטריה משלו לחלוטין (למשל Curtain Wall ריק,
    בלי mullions/panels — ראו get_dim_face_refs). מחפש בין שאר הקירות
    את זה שנוגע בדיוק באותה קואורדינטה (קצה משותף) ושואל ממנו ref.
    """
    best = None
    best_d = tol
    for w2 in walls:
        if w2.Id.IntegerValue == exclude_id:
            continue
        s2, e2 = get_wall_pts(w2)
        if not s2:
            continue
        if is_horizontal:
            w2_min, w2_max = min(s2.X, e2.X), max(s2.X, e2.X)
        else:
            w2_min, w2_max = min(s2.Y, e2.Y), max(s2.Y, e2.Y)
        if min(abs(w2_min - coord), abs(w2_max - coord)) > best_d:
            continue
        ref_min, ref_max = get_dim_face_refs(w2, is_horizontal)
        for wc, ref in ((w2_min, ref_min), (w2_max, ref_max)):
            if ref is None:
                continue
            d = abs(wc - coord)
            if d < best_d:
                best_d = d
                best = ref
    return best

def _is_curtain_wall(wall):
    try:
        return wall.WallType.Kind == WallKind.Curtain
    except:
        return False

def _get_wall_host(wall):
    """
    מחזיר את הקיר המארח (host) אם הקיר הזה מוטבע (Embedded Wall) בתוך
    קיר אחר — כמו שקורה בדרך כלל ל-Curtain Wall שמוצב בתוך קיר בסיסי.
    """
    try:
        sc = wall.SuperComponent
        if sc and isinstance(sc, Wall):
            return sc
    except:
        pass
    return None

def _find_containing_host_wall(walls, embed_wall, is_horizontal, tol=0.1):
    """
    גיבוי גיאומטרי ל-_get_wall_host (למקרה ש-SuperComponent לא זמין):
    מחפש קיר לא-Curtain שגבולותיו מכילים לחלוטין את טווח הקיר המוטבע
    ונמצא בערך על אותו קו.
    """
    s, e = get_wall_pts(embed_wall)
    if not s:
        return None
    if is_horizontal:
        e_min, e_max = min(s.X, e.X), max(s.X, e.X)
        e_pos = (s.Y + e.Y) / 2.0
    else:
        e_min, e_max = min(s.Y, e.Y), max(s.Y, e.Y)
        e_pos = (s.X + e.X) / 2.0

    for w2 in walls:
        if w2.Id.IntegerValue == embed_wall.Id.IntegerValue:
            continue
        if _is_curtain_wall(w2):
            continue
        s2, e2 = get_wall_pts(w2)
        if not s2:
            continue
        if is_horizontal:
            w2_min, w2_max = min(s2.X, e2.X), max(s2.X, e2.X)
            w2_pos = (s2.Y + e2.Y) / 2.0
        else:
            w2_min, w2_max = min(s2.Y, e2.Y), max(s2.Y, e2.Y)
            w2_pos = (s2.X + e2.X) / 2.0
        if abs(w2_pos - e_pos) > tol:
            continue
        if w2_min - tol <= e_min and e_max <= w2_max + tol:
            return w2
    return None

def _get_embedded_opening_refs(host_wall, embed_wall, is_horizontal):
    """
    מוצא refs בגיאומטריית הקיר המארח (host_wall) בדיוק בגבולות הקיר
    המוטבע (embed_wall, למשל Curtain Wall) — כמו get_wall_opening_refs
    לפתחי דלת/חלון, רק לפי טווח קואורדינטות (bounding) של הקיר המוטבע
    במקום נקודת מיקום בודדת של משפחה.

    הקואורדינטות של קיר המסך עצמו (e_min/e_max, מתוך ה-Location Curve
    שלו) הן תמיד "אמת המידה" — הן לא תלויות במבנה השכבות/חיבורים של
    הקיר המארח. לכן במקום לאסוף את כל פאות הקיר המארח שנמצאות "איפשהו
    בטווח" ולקחת min/max כללי (שנשבר בשקט אם הקיר המארח הוא Compound
    עם כמה שכבות, או אם קצה אחד "נבלע" בחיבור עם קיר ניצב וכל הפאות
    שנמצאו מרוכזות בקצה השני בלבד — בדיוק מה שיצר את סגמנט המידה "0"
    השגוי) — מחפשים בנפרד את הפאה **הכי קרובה** ל-e_min ואת הפאה הכי
    קרובה ל-e_max, כל אחת בתוך סבילות TOLERANCE משלה. אם לא נמצאה פאה
    קרובה מספיק לאחד הקצוות — מחזירים None בשבילו (לא מנחשים), ונותנים
    לשרשרת הגיבויים (_robust_curtain_wall_opening_refs) להמשיך.
    """
    s, e = get_wall_pts(embed_wall)
    if not s:
        return None, None
    if is_horizontal:
        e_min, e_max = min(s.X, e.X), max(s.X, e.X)
    else:
        e_min, e_max = min(s.Y, e.Y), max(s.Y, e.Y)

    try:
        opts = Options()
        opts.ComputeReferences = True
        best_min, best_min_d = None, TOLERANCE
        best_max, best_max_d = None, TOLERANCE
        for obj in host_wall.get_Geometry(opts):
            if not isinstance(obj, Solid):
                continue
            for face in obj.Faces:
                try:
                    n = face.FaceNormal
                    axis_component = n.X if is_horizontal else n.Y
                    if abs(abs(axis_component) - 1.0) > NORMAL_TOL:
                        continue
                    if face.Reference is None:
                        continue
                    bb  = face.GetBoundingBox()
                    mid = face.Evaluate(UV((bb.Min.U + bb.Max.U) / 2,
                                           (bb.Min.V + bb.Max.V) / 2))
                    coord = mid.X if is_horizontal else mid.Y

                    d_min = abs(coord - e_min)
                    if d_min < best_min_d:
                        best_min_d, best_min = d_min, face.Reference
                    d_max = abs(coord - e_max)
                    if d_max < best_max_d:
                        best_max_d, best_max = d_max, face.Reference
                except:
                    pass
        if best_min and best_max:
            return best_min, best_max
    except:
        pass
    return None, None

def _opening_position(op, is_horizontal):
    """
    מיקום (X או Y) של "פתח" בקו מידה — עובד גם ל-FamilyInstance (חלון/דלת,
    Location.Point) וגם ל-Wall (Curtain Wall מוטבע, Location Curve).
    """
    if isinstance(op, Wall):
        s, e = get_wall_pts(op)
        if not s:
            return 0
        return (s.X + e.X) / 2.0 if is_horizontal else (s.Y + e.Y) / 2.0
    pt = op.Location.Point
    return pt.X if is_horizontal else pt.Y

def _robust_curtain_wall_opening_refs(doc, view, host_wall, cw, is_horizontal, ref_coord):
    """
    refs לגבולות קיר מסך מוטבע כשהוא מופיע כ"פתח" בתוך רשימת הפתחים של
    קיר מארח.

    שיטה ראשית: גיאומטריית הקיר המארח בגבולות קיר המסך
    (_get_embedded_opening_refs — מחפש את הפאה הכי קרובה לכל קצה בנפרד).
    גיבוי: אם זה לא מוצא את שני הקצוות — Reference Plane דטרמיניסטי
    מה-Location Curve של קיר המסך עצמו (תמיד עובד, אבל "מלאכותי" —
    לא פאה אמיתית).
    """
    ref_min, ref_max = _get_embedded_opening_refs(host_wall, cw, is_horizontal)
    if ref_min and ref_max:
        return [ref_min, ref_max]

    s, e = get_wall_pts(cw)
    if not s:
        return []
    coord_min = min(s.X, e.X) if is_horizontal else min(s.Y, e.Y)
    coord_max = max(s.X, e.X) if is_horizontal else max(s.Y, e.Y)
    z = (s.Z + e.Z) / 2.0

    ref_min = _get_or_create_edge_ref_plane(
        doc, view, coord_min, is_horizontal, ref_coord, cw.Id.IntegerValue, "CWL", z)
    ref_max = _get_or_create_edge_ref_plane(
        doc, view, coord_max, is_horizontal, ref_coord, cw.Id.IntegerValue, "CWR", z)

    if ref_min and ref_max:
        return [ref_min, ref_max]
    return []

def _opening_refs(op, is_horizontal, doc=None, view=None, ref_coord=None, host_wall=None):
    """refs לצדי "פתח" — Curtain Wall מוטבע מול FamilyInstance רגיל."""
    if isinstance(op, Wall):
        return _robust_curtain_wall_opening_refs(doc, view, host_wall, op, is_horizontal, ref_coord)
    return get_opening_side_refs(op, is_horizontal)

def _annotate_curtain_wall_heights(d, cw_height_segs):
    """
    לאחר יצירת מידה עם רצף פתחים, מציג Unconnected Height כ-Below text
    (הרוחב עצמו כבר מוצג אוטומטית על קו המידה) על הסגמנט של כל קיר מסך
    מוטבע. cw_height_segs הוא רשימת (seg_idx, height_cm) שנבנתה תוך כדי
    בניית ה-ReferenceArray לפי המיקום האמיתי של ה-refs — לא מחושבת לפי
    הנחה קבועה של "2 refs לכל פתח" (הנחה כזו נשברת בשקט כשפתח תורם 0/1
    refs בלבד, וגורמת גם לסגמנט "0" מיותר וגם לתווית גובה על הסגמנט הלא
    נכון — זה בדיוק הבאג שתוקן כאן).
    """
    if not d or not cw_height_segs:
        return
    try:
        segs = d.Segments
        for seg_idx, h_cm in cw_height_segs:
            if 0 <= seg_idx < segs.Size and h_cm:
                segs.get_Item(seg_idx).Below = str(h_cm)
    except:
        pass

def _get_wall_unconnected_height_cm(wall):
    """גובה קיר מפרמטר Unconnected Height, בס\"מ שלם. גיבוי: BoundingBox."""
    try:
        p = wall.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
        if p and p.AsDouble() > 0:
            return int(round(p.AsDouble() / CM))
    except:
        pass
    h = get_wall_height(wall)
    return int(round(h / CM)) if h else None

def _get_wall_base_offset_cm(wall):
    """Base Offset של הקיר, בס\"מ שלם — מקביל ל-Sill Height של חלון."""
    try:
        p = wall.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
        if p and p.AsDouble() > 0:
            return int(round(p.AsDouble() / CM))
    except:
        pass
    return None

def _create_reference_plane(doc, view, p1, p2, p3, name):
    creator = doc.Create
    rp = None
    try:
        rp = creator.NewReferencePlane2(p1, p2, p3, view)
    except:
        try:
            rp = creator.NewReferencePlane(p1, p2, p3, view)
        except:
            rp = None
    if rp:
        try:
            rp.Name = name
        except:
            pass
    return rp

def _get_or_create_edge_ref_plane(doc, view, coord, is_horizontal, ref_coord, wall_id, tag, z=0.0):
    """
    גיבוי אחרון: קיר בלי שום גיאומטריה ובלי שכן צמוד (למשל Curtain Wall
    ריק — ראו get_dim_face_refs / _find_ref_at_coord). יוצר (או מוצא קיים,
    לפי שם ייחודי לקיר — כדי לא ליצור כפילות בהרצות חוזרות) Reference
    Plane קבוע בדיוק בקצה האמיתי של הקיר (מתוך Location Curve), ומחזיר
    את ה-Reference שלו לשימוש במידה.

    חשוב: הגובה (z) חייב לשקף את הגובה **האמיתי** של הקיר בקומה הנוכחית
    (לא Z=0 קבוע!) — גילינו (17.07.26) שקומה 0 (קרוב ל-Z=0) עבדה במקרה,
    בעוד שקומות גבוהות יותר (Z אמיתי הרבה יותר גבוה) קיבלו Reference
    Plane שנוצר רחוק מהגיאומטריה האמיתית של הקיר, ולא נמצא/נראה שם.
    """
    name = "AutoDIM_{}_{}".format(wall_id, tag)
    for rp in FilteredElementCollector(doc).OfClass(ReferencePlane):
        try:
            if rp.Name == name:
                return rp.GetReference()
        except:
            pass
    z_lo, z_hi = z, z + 3
    if is_horizontal:
        p1 = XYZ(coord, ref_coord - 3, z_lo)
        p2 = XYZ(coord, ref_coord + 3, z_lo)
        p3 = XYZ(coord, ref_coord - 3, z_hi)
    else:
        p1 = XYZ(ref_coord - 3, coord, z_lo)
        p2 = XYZ(ref_coord + 3, coord, z_lo)
        p3 = XYZ(ref_coord - 3, coord, z_hi)
    rp = _create_reference_plane(doc, view, p1, p2, p3, name)
    if rp:
        # ReferencePlane שזה עתה נוצר — ה-Reference שלו לא תמיד "גיאומטרי"
        # (תקף לשימוש ב-NewDimension) לפני Regenerate מפורש של המסמך.
        try:
            doc.Regenerate()
        except:
            pass
    return rp.GetReference() if rp else None

def _debug_nearest_coord_gap(walls, coord, is_horizontal, exclude_id):
    """לאבחון בלבד: המרחק (בס"מ) לקצה הקיר הקרוב ביותר לקואורדינטה נתונה."""
    best_d = None
    for w2 in walls:
        if w2.Id.IntegerValue == exclude_id:
            continue
        s2, e2 = get_wall_pts(w2)
        if not s2:
            continue
        if is_horizontal:
            w2_min, w2_max = min(s2.X, e2.X), max(s2.X, e2.X)
        else:
            w2_min, w2_max = min(s2.Y, e2.Y), max(s2.Y, e2.Y)
        d = min(abs(w2_min - coord), abs(w2_max - coord))
        if best_d is None or d < best_d:
            best_d = d
    if best_d is None:
        return "no_neighbors"
    return "{:.1f}cm".format(best_d / CM)

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

def get_wall_opening_refs(wall, fi, is_horizontal):
    """
    מחזיר (ref_left, ref_right) של צלעות חיתוך הפתח בקיר עצמו —
    לא refs של אלמנט הדלת/חלון, אלא הפנים הניצבות לכיוון הקיר
    שנחשפו בגיאומטריית הקיר לאחר חיתוך הפתח.
    מתאים לכל סוגי המשפחות, ללא תלות בהגדרת refs פנימיים.
    """
    try:
        fi_x = fi.Location.Point.X
        fi_y = fi.Location.Point.Y

        # הערכת רוחב הפתח מפרמטרים
        half_w = 1.0  # ברירת מחדל: 1 פוט (כ-30 ס"מ) לכל כיוון
        for pname in ["Rough Width", "Width", "RO Width"]:
            p = fi.LookupParameter(pname)
            if p:
                half_w = p.AsDouble() / 2.0 + TOLERANCE
                break
        if half_w <= TOLERANCE:
            p = fi.get_Parameter(BuiltInParameter.CASEWORK_WIDTH)
            if p: half_w = p.AsDouble() / 2.0 + TOLERANCE

        opts = Options()
        opts.ComputeReferences = True
        candidates = []

        for obj in wall.get_Geometry(opts):
            if not isinstance(obj, Solid):
                continue
            for face in obj.Faces:
                try:
                    n = face.FaceNormal
                    if is_horizontal:
                        # קיר אופקי (X): צלעות פתח הן פנים עם נורמל לאורך X
                        if abs(abs(n.X) - 1.0) > NORMAL_TOL:
                            continue
                        bb  = face.GetBoundingBox()
                        mid = face.Evaluate(UV((bb.Min.U + bb.Max.U) / 2,
                                               (bb.Min.V + bb.Max.V) / 2))
                        if abs(mid.X - fi_x) <= half_w:
                            candidates.append((mid.X, face.Reference))
                    else:
                        # קיר אנכי (Y): צלעות פתח הן פנים עם נורמל לאורך Y
                        if abs(abs(n.Y) - 1.0) > NORMAL_TOL:
                            continue
                        bb  = face.GetBoundingBox()
                        mid = face.Evaluate(UV((bb.Min.U + bb.Max.U) / 2,
                                               (bb.Min.V + bb.Max.V) / 2))
                        if abs(mid.Y - fi_y) <= half_w:
                            candidates.append((mid.Y, face.Reference))
                except:
                    pass

        if len(candidates) >= 2:
            candidates.sort(key=lambda x: x[0])
            return [candidates[0][1], candidates[-1][1]]
    except:
        pass

    # גיבוי: refs של המשפחה עצמה
    return get_opening_side_refs(fi, is_horizontal)

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

# ================================================================
# מניעת כפילות — הרצה חוזרת של אותה חזית על אותה תכנית (למשל בטעות,
# או תוך כדי בדיקות) לא תיצור מידות/תגיות מוכפלות על גבי הקיימות.
# ================================================================
DUP_TOL = 0.05  # פוט (~1.5 ס"מ) — סבילות להתאמת קו מידה קיים

def _existing_dim_line_endpoints(doc, view):
    pts = []
    try:
        for d in FilteredElementCollector(doc, view.Id).OfClass(Dimension):
            try:
                c = d.Curve
                if isinstance(c, Line):
                    pts.append((c.GetEndPoint(0), c.GetEndPoint(1)))
            except:
                pass
    except:
        pass
    return pts

def _dim_line_is_duplicate(existing, p1, p2, tol=DUP_TOL):
    for e1, e2 in existing:
        if (p1.DistanceTo(e1) < tol and p2.DistanceTo(e2) < tol) or \
           (p1.DistanceTo(e2) < tol and p2.DistanceTo(e1) < tol):
            return True
    return False

def _existing_tagged_element_ids(doc, view):
    ids = set()
    try:
        for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
            try:
                for tid in t.GetTaggedLocalElementIds():
                    ids.add(tid.IntegerValue)
            except:
                pass
    except:
        pass
    return ids

# ================================================================
# איפוס מידות (הגדרות.panel > איפוס מידות) — מזהה אלמנטים שכנראה
# נוצרו ע"י AutoDIM לפי הסגנון המוגדר כרגע בהגדרות (dim_style /
# uk_tag_style / cw_tag_style), כדי שאפשר יהיה למחוק אותם בלחיצה.
# לא תיוג ישיר על כל אלמנט (עדיין) — ראו הערה בסקריפט הכפתור.
# ================================================================
def get_autodim_created_element_ids(doc, view):
    ids = []
    dim_type = get_dim_style(doc, get_active_dim_style_name())
    uk_type  = get_tag_type(doc, get_active_uk_tag_name())
    cw_type  = get_tag_type(doc, get_active_cw_tag_name())

    if dim_type:
        for d in FilteredElementCollector(doc, view.Id).OfClass(Dimension):
            try:
                if d.GetTypeId() == dim_type.Id:
                    ids.append(d.Id)
            except:
                pass

    tag_type_ids = set(t.Id.IntegerValue for t in (uk_type, cw_type) if t)
    if tag_type_ids:
        for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
            try:
                if t.GetTypeId().IntegerValue in tag_type_ids:
                    ids.append(t.Id)
            except:
                pass
    return ids

def get_autodim_reference_plane_ids(doc):
    """
    Reference Plane-ים סינתטיים שה-AutoDIM יצר בתור גיבוי אחרון לגבולות
    קיר מסך/קיר (שם מתחיל ב-"AutoDIM_", ראו _get_or_create_edge_ref_plane).
    Reference Plane הוא אלמנט גלובלי במסמך (לא תלוי-תצוגה) — נאסף פעם
    אחת לכל המסמך, בלי קשר לאיזו תצוגה/scope נבחר לאיפוס. חשוב לכלול
    אותם באיפוס: הם נשמרים/נמצאים-מחדש לפי שם קבוע, כך שאם נשאר אחד
    ישן שנוצר בגובה (Z) שגוי מגרסה קודמת של הקוד, הוא ימשיך להימצא
    ולשמש שוב במקום שיחושב מחדש נכון.
    """
    ids = []
    for rp in FilteredElementCollector(doc).OfClass(ReferencePlane):
        try:
            if rp.Name and rp.Name.startswith("AutoDIM_"):
                ids.append(rp.Id)
        except:
            pass
    return ids

def get_all_dim_and_tag_ids(doc, view):
    """
    כל ה-Dimension וה-IndependentTag בתצוגה, בלי סינון לפי סגנון —
    מחיקה "גורפת" לשימוש כשיש חשד לאלמנטים ישנים שנוצרו בסגנון אחר
    (למשל מסבבי בדיקה מוקדמים) ולכן לא נתפסים ע"י get_autodim_created_element_ids.
    מסוכן יותר — עלול למחוק גם מידות/תגיות שהמשתמש יצר ידנית בתצוגה הזו.
    """
    ids = []
    for d in FilteredElementCollector(doc, view.Id).OfClass(Dimension):
        ids.append(d.Id)
    for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        ids.append(t.Id)
    return ids

def get_wall_pts(wall):
    loc = wall.Location
    if not isinstance(loc, LocationCurve):
        return None, None
    return loc.Curve.GetEndPoint(0), loc.Curve.GetEndPoint(1)

def is_horiz_wall(wall):
    s, e = get_wall_pts(wall)
    if not s: return False
    return abs(e.X - s.X) > abs(e.Y - s.Y)

def get_all_floor_plan_views(doc):
    """
    כל תכניות הקומה בפרויקט (ViewPlan, ViewType.FloorPlan, לא Template,
    לא Dependent View) — ממוינות לפי גובה המפלס ואז לפי שם התכנית.
    בפרויקט אחד יכולים להיות כמה סטים של תכניות לאותו מפלס (למשל תכניות
    "עבודה" מול תכניות "הצגה") — לכן לא מנחשים "את התכנית הנכונה" לפי
    מפלס, אלא מציגים את כל התכניות ונותנים למשתמש לבחור בדיוק אילו.
    """
    views = []
    for v in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            if v.IsTemplate:
                continue
            if v.ViewType != ViewType.FloorPlan:
                continue
            if v.GetPrimaryViewId() != ElementId.InvalidElementId:
                continue  # תצוגה תלויה (Dependent) — מדלגים
            views.append(v)
        except:
            pass

    def _sort_key(v):
        try:
            return (v.GenLevel.Elevation if v.GenLevel else 0, v.Name)
        except:
            return (0, v.Name)
    return sorted(views, key=_sort_key)

def get_reference_views(doc, uidoc):
    """
    מחזיר רשימת ViewPlan שהן היעד להרצת מידות "על כל הקומות" — בדיוק
    התכניות שנבחרו ב"בחירת מפלסים" (reference_view_ids). אם לא נבחרה
    אף תכנית, נופלים חזרה לתצוגה הפעילה בלבד (זהה להרצה רגילה).
    """
    ids = set(load_settings().get("reference_view_ids", []))
    if ids:
        views = []
        for vid in ids:
            try:
                el = doc.GetElement(ElementId(vid))
            except:
                el = None
            if isinstance(el, ViewPlan) and not el.IsTemplate:
                views.append(el)
        if views:
            return views

    cur_view = uidoc.ActiveView
    return [cur_view] if isinstance(cur_view, (ViewPlan, ViewSection)) else []

def run_facade(facade_name, is_horizontal, perp_x, perp_y):
    doc   = revit.doc
    uidoc = revit.uidoc
    settings = load_settings()

    if not settings.get("reference_view_ids"):
        # לא נבחרו תכניות ב"בחירת מפלסים" — הרצה רגילה על התצוגה הפעילה בלבד
        _run_facade_single(doc, uidoc.ActiveView, facade_name, is_horizontal, perp_x, perp_y)
        return

    target_views = get_reference_views(doc, uidoc)
    if not target_views:
        forms.alert("לא נמצאו תכניות קומה מתאימות לבחירת המפלסים שנבחרה.",
                    title="שגיאה")
        return

    total_created = 0
    total_skipped = 0
    total_errors  = []
    total_skip_details = []
    for v in target_views:
        created, errors, skipped, skip_details = _run_facade_single(
            doc, v, facade_name, is_horizontal, perp_x, perp_y, silent=True)
        total_created += created
        total_skipped += skipped
        if errors:
            total_errors += ["[{}] {}".format(v.Name, e) for e in errors]
        if skip_details:
            total_skip_details += ["[{}] {}".format(v.Name, s) for s in skip_details]

    msg = "בוצעו {} מידות/תגיות ב-{} תכניות קומה.".format(total_created, len(target_views))
    if total_skipped:
        msg += "\nדולגו {} (כבר קיימות מקודם — מניעת כפילות):\n{}".format(
            total_skipped, "\n".join(total_skip_details))
    if total_errors:
        msg += "\n\nשגיאות ({}):\n{}".format(len(total_errors), "\n".join(total_errors))
    forms.alert(msg, title="מידות {} — כל הקומות".format(facade_name))

def _run_facade_single(doc, view, facade_name, is_horizontal, perp_x, perp_y, silent=False):
    try:
        # קבועי מרחק לפי חזית — L3 נקרא מהגדרות, L2/L1 שומרים מרווח יחסי קבוע
        _l3_cm   = load_settings().get("wall_dim_offset", 100)
        l3_dist  = _l3_cm * CM
        l2_dist  = (_l3_cm + 100) * CM   # תמיד 100 ס"מ מעל L3
        l1_dist  = (_l3_cm + 140) * CM   # תמיד 140 ס"מ מעל L3
        uk_dist  = UK_DIST
        if facade_name == "צפון":
            l1_dist -= 50 * CM
            l2_dist -= 50 * CM
            uk_dist  = UK_DIST - 100 * CM
        elif facade_name == "מערב":
            l1_dist -= 50 * CM
            l2_dist -= 50 * CM
            uk_dist  = -UK_DIST
        elif facade_name == "מזרח":
            uk_dist  = UK_DIST

        if not isinstance(view, (ViewPlan, ViewSection)):
            if silent:
                return 0, ["תצוגה '{}' אינה תכנית קומה".format(view.Name)], 0, []
            forms.alert("אנא עבור לתצוגת תכנית קומה.", title="שגיאה")
            return 0, [], 0, []

        all_walls = list(
            FilteredElementCollector(doc, view.Id)
            .OfClass(Wall)
            .WhereElementIsNotElementType()
        )
        if not all_walls:
            if silent:
                return 0, ["לא נמצאו קירות בתכנית '{}'".format(view.Name)], 0, []
            forms.alert("לא נמצאו קירות!", title="שגיאה")
            return 0, [], 0, []

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
                if is_low_wall(w): continue  # קיר נמוך מ-200 ס"מ: לא נמדד, לא חוסם
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
            if silent:
                return 0, ["לא נמצאו קירות לחזית {} בתכנית '{}'".format(facade_name, view.Name)], 0, []
            forms.alert("לא נמצאו קירות לחזית {}!".format(facade_name), title="שגיאה")
            return 0, [], 0, []

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
            if is_low_wall(w2): continue  # קיר נמוך מ-200 ס"מ: לא נמדד, לא חוסם
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

        # קיר גבוה/רב-קומתי יכול "להיראות" בתצוגה של כמה מפלסים בו-זמנית
        # (17.07.26 — התגלה שקיר מארח אחד אירח 2 קירות מסך שונים בגבהים
        # שונים אך באותה חתימה אופקית, ושניהם נאספו יחד עבור תצוגת מפלס
        # אחד — יצר שרשרת refs חופפת/הפוכה וסגמנט מידה "0" שגוי). לכן
        # מסננים **קירות מסך בלבד** לפי קרבה בגובה (Z) למפלס הפעיל — לא
        # חלונות/דלתות (ניסינו לסנן גם אותם, וזה גרם לחלונות תקינים
        # להיעלם — Z שלהם כנראה לא תמיד קרוב ל-Level.Elevation באותה
        # מידה שקירות מסך הם, אין הוכחה שהם סובלים מאותה בעיה).
        _view_level_z = None
        try:
            if getattr(view, "GenLevel", None):
                _view_level_z = view.GenLevel.Elevation
        except:
            pass
        FLOOR_Z_TOLERANCE = 150 * CM  # חצי גובה קומה טיפוסי בערך

        def _z_matches_floor(z):
            if _view_level_z is None or z is None:
                return True  # אין מידע על מפלס — לא מסננים, עדיף לכלול מאשר לפספס
            return abs(z - _view_level_z) <= FLOOR_Z_TOLERANCE

        for fi in windows + doors:
            try:
                if fi.Host and fi.Host.Id.IntegerValue in facade_wall_ids:
                    openings_by_wall[fi.Host.Id.IntegerValue].append(fi)
            except:
                pass

        # Curtain Wall מוטבע (Embedded) בקיר מארח שגם הוא על החזית —
        # מתווסף כ"פתח" בתוך קו ה-L3 של הקיר המארח (כמו חלון/דלת),
        # ולא מקבל קו מידה נפרד משלו ברשימת sorted_facade למטה.
        embedded_cw_ids = set()
        other_floor_cw_ids = set()  # קיר מסך שנמצא בתצוגה אבל שייך בגובה למפלס אחר — לא נכלל בכלל
        for w in facade_walls:
            if not _is_curtain_wall(w):
                continue
            s2, e2 = get_wall_pts(w)
            cw_z = s2.Z if s2 else None
            if not _z_matches_floor(cw_z):
                other_floor_cw_ids.add(w.Id.IntegerValue)
                continue
            host_wall = _get_wall_host(w) or _find_containing_host_wall(facade_walls, w, is_horizontal)
            if host_wall and host_wall.Id.IntegerValue in facade_wall_ids:
                openings_by_wall.setdefault(host_wall.Id.IntegerValue, []).append(w)
                embedded_cw_ids.add(w.Id.IntegerValue)

        dim_type = get_dim_style(doc, get_active_dim_style_name())
        tag_type = get_tag_type(doc, get_active_uk_tag_name())
        cw_tag_type = get_tag_type(doc, get_active_cw_tag_name())

        existing_dim_lines  = _existing_dim_line_endpoints(doc, view)
        existing_tagged_ids = _existing_tagged_element_ids(doc, view)

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

        errors  = []
        created = 0
        skipped = 0  # דולג כי כבר קיים באותה תצוגה (מניעת כפילות) — לא שגיאה ולא יצירה חדשה
        skip_details = []  # תיאור כל דילוג (איזה קיר/אלמנט) — לאבחון, גם כשאין שגיאות בכלל

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
            if ln is not None and _dim_line_is_duplicate(existing_dim_lines, ln.GetEndPoint(0), ln.GetEndPoint(1)):
                skipped += 1  # כבר קיים — לא יוצרים כפילות
                skip_details.append("L1")
            else:
                d, err = try_dim(doc, view, ln, ra, dim_type)
                if d:
                    created += 1
                    existing_dim_lines.append((ln.GetEndPoint(0), ln.GetEndPoint(1)))
                else:
                    errors.append("L1: {}".format(err))

            # ===================================================
            # L2 — מידות בין כל קצות הקירות על קו החזית
            # ===================================================
            ra = ReferenceArray()
            for c in sorted_coords:
                r = coord_to_ref[c]
                if r: ra.Append(r)

            ln = dim_line_at(l2_dist)
            if ln is not None and _dim_line_is_duplicate(existing_dim_lines, ln.GetEndPoint(0), ln.GetEndPoint(1)):
                skipped += 1  # כבר קיים — לא יוצרים כפילות
                skip_details.append("L2")
            else:
                d, err = try_dim(doc, view, ln, ra, dim_type)
                if d:
                    created += 1
                    existing_dim_lines.append((ln.GetEndPoint(0), ln.GetEndPoint(1)))
                else:
                    errors.append("L2: {}".format(err))

            # ===================================================
            # L3 — קו מידה נפרד לכל קיר מקביל לחזית (עם פתחים)
            # קירות ניצבים לחזית מדולגים (הם תורמים ל-L2 בלבד)
            # ===================================================
            if is_horizontal:
                parallel_walls = [w for w in facade_walls
                                   if is_horiz_wall(w) and w.Id.IntegerValue not in embedded_cw_ids
                                   and w.Id.IntegerValue not in other_floor_cw_ids]
                sorted_facade = sorted(parallel_walls,
                    key=lambda w: min(get_wall_pts(w)[0].X, get_wall_pts(w)[1].X))
            else:
                parallel_walls = [w for w in facade_walls
                                   if not is_horiz_wall(w) and w.Id.IntegerValue not in embedded_cw_ids
                                   and w.Id.IntegerValue not in other_floor_cw_ids]
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
                    dim_start, dim_end = w_min, w_max
                    ref_min, ref_max = None, None

                    # גיבוי 1: Curtain Wall מוטבע (Embedded) בקיר מארח —
                    # refs מגיאומטריית הקיר המארח בגבולות קיר המסך,
                    # בדיוק כמו פתח חלון/דלת.
                    if _is_curtain_wall(w):
                        host_wall = _get_wall_host(w) or _find_containing_host_wall(all_walls, w, is_horizontal)
                        if host_wall:
                            ref_min, ref_max = _get_embedded_opening_refs(host_wall, w, is_horizontal)

                    # גיבוי 1ב: refs ישירים מהגיאומטריה של הקיר עצמו
                    if not ref_min or not ref_max:
                        ref_min, ref_max = get_dim_face_refs(w, is_horizontal)

                    start_ref, end_ref = ref_min, ref_max

                # גיבוי 2: לקיר בלי גיאומטריה משלו ובלי קיר מארח (Curtain
                # Wall ריק וחופשי) — שאול ref מהקיר השכן שנוגע באותה קואורדינטה.
                if not start_ref:
                    start_ref = _find_ref_at_coord(all_walls, dim_start, is_horizontal, w.Id.IntegerValue)
                if not end_ref:
                    end_ref = _find_ref_at_coord(all_walls, dim_end, is_horizontal, w.Id.IntegerValue)

                # גיבוי 3: קיר בלי שום גיאומטריה ובלי שכן צמוד (Curtain Wall
                # ריק ומבודד) — Reference Plane קבוע בדיוק בקצה האמיתי של הקיר.
                if not start_ref:
                    start_ref = _get_or_create_edge_ref_plane(
                        doc, view, dim_start, is_horizontal, ref_coord, w.Id.IntegerValue, "L", (s.Z + e.Z) / 2.0)
                if not end_ref:
                    end_ref = _get_or_create_edge_ref_plane(
                        doc, view, dim_end, is_horizontal, ref_coord, w.Id.IntegerValue, "R", (s.Z + e.Z) / 2.0)

                if not start_ref or not end_ref:
                    gap_start = _debug_nearest_coord_gap(all_walls, dim_start, is_horizontal, w.Id.IntegerValue)
                    gap_end   = _debug_nearest_coord_gap(all_walls, dim_end, is_horizontal, w.Id.IntegerValue)
                    errors.append("L3 קיר {}: לא נמצאו refs [{}] start_gap={} end_gap={}".format(
                        w.Id.IntegerValue, _debug_geom_summary(w), gap_start, gap_end))
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

                sorted_ops = []
                cw_height_segs = []  # (seg_idx, height_cm) — לפי המיקום האמיתי ב-ra, לא הנחה קבועה
                op_diag = []  # לאבחון: כמה refs כל פתח תרם בפועל, אם יוצר קו המידה נכשל
                if ops:
                    # ops עשוי לכלול גם FamilyInstance (חלון/דלת, יש Location.Point)
                    # וגם Wall (Curtain Wall מוטבע, יש Location Curve) — ראו
                    # _opening_position/_opening_refs.
                    sorted_ops = sorted(ops, key=lambda op: _opening_position(op, is_horizontal))
                    sorted_ops = [op for op in sorted_ops
                                  if dim_start - TOLERANCE <= _opening_position(op, is_horizontal) <= dim_end + TOLERANCE]
                    for op in sorted_ops:
                        is_cw = isinstance(op, Wall) and _is_curtain_wall(op)
                        srefs = _opening_refs(op, is_horizontal, doc, view, ref_coord, w)
                        op_diag.append("{}[{}]:{}refs".format(
                            "CW" if is_cw else "FI", op.Id.IntegerValue, len(srefs)))
                        if is_cw and len(srefs) == 2:
                            h_cm = _get_wall_unconnected_height_cm(op)
                            if h_cm:
                                cw_height_segs.append((ra.Size, h_cm))
                        for sr in srefs:
                            ra.Append(sr)

                ra.Append(end_ref)

                if ra.Size >= 2:
                    ln = make_line(lp1, lp2)
                    if ln is not None and _dim_line_is_duplicate(existing_dim_lines, ln.GetEndPoint(0), ln.GetEndPoint(1)):
                        skipped += 1
                        skip_details.append("L3 קיר {} (ops=[{}])".format(w.Id.IntegerValue, ", ".join(op_diag)))
                        continue  # כבר קיים — לא יוצרים כפילות
                    d, err = try_dim(doc, view, ln, ra, dim_type)
                    if d:
                        created += 1
                        existing_dim_lines.append((ln.GetEndPoint(0), ln.GetEndPoint(1)))
                        # Curtain Wall: מציגים את הגובה (Unconnected Height)
                        # כ-Above text על כל סגמנט Curtain Wall מוטבע בתוך
                        # רצף הפתחים (Base Offset מוצג בנפרד ע"י תגית UK
                        # אמיתית — ראו create_cw_uk_tags).
                        _annotate_curtain_wall_heights(d, cw_height_segs)
                        if _is_curtain_wall(w) and not sorted_ops:
                            h_cm = _get_wall_unconnected_height_cm(w)
                            if h_cm:
                                try:
                                    if d.Segments.Size > 0:
                                        d.Segments.get_Item(0).Below = str(h_cm)
                                    else:
                                        d.Below = str(h_cm)
                                except:
                                    pass
                    else:
                        errors.append("L3 קיר {}: {} | refs={} ops=[{}]".format(
                            w.Id.IntegerValue, err, ra.Size, ", ".join(op_diag)))
                else:
                    errors.append("L3 קיר {}: refs לא מספיקים ({})".format(
                        w.Id.IntegerValue, ra.Size))

            # ===================================================
            # UK Tags — תגיות UK לחלונות (Sill Height) ולקירות מסך
            # מוטבעים (Base Offset)
            # ===================================================
            for w in sorted_facade:
                for fi in openings_by_wall.get(w.Id.IntegerValue, []):
                    try:
                        if fi.Id.IntegerValue in existing_tagged_ids:
                            skipped += 1
                            skip_details.append("UK Tag {} (קיר מארח {})".format(
                                fi.Id.IntegerValue, w.Id.IntegerValue))
                            continue  # כבר מתוייג בתצוגה הזו — לא יוצרים כפילות
                        if isinstance(fi, Wall):
                            if not _is_curtain_wall(fi):
                                continue
                            base_param = fi.get_Parameter(BuiltInParameter.WALL_BASE_OFFSET)
                            if base_param is None or base_param.AsDouble() <= 0:
                                continue
                            s2, e2 = get_wall_pts(fi)
                            if not s2:
                                continue
                            wp = XYZ((s2.X + e2.X) / 2.0, (s2.Y + e2.Y) / 2.0, 0)
                            use_type = cw_tag_type
                        else:
                            if fi.Category.Id.IntegerValue != int(BuiltInCategory.OST_Windows):
                                continue
                            sill_param = fi.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
                            if sill_param is None or sill_param.AsDouble() <= 0:
                                continue
                            wp = fi.Location.Point
                            use_type = tag_type

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
                            TagOrientation.Horizontal if is_horizontal else TagOrientation.Vertical,
                            tag_pt
                        )
                        if use_type:
                            tag.ChangeTypeId(use_type.Id)
                        created += 1
                        existing_tagged_ids.add(fi.Id.IntegerValue)
                    except Exception as ex:
                        errors.append("UK Tag {}: {}: {}".format(
                            fi.Id.IntegerValue, type(ex).__name__, str(ex)))

        if errors and not silent:
            forms.alert(
                "שגיאות ({}):\n{}".format(len(errors), "\n".join(errors)),
                title="AutoDimensions — {}".format(facade_name))

        return created, errors, skipped, skip_details

    except Exception as e:
        if silent:
            return 0, ["{}: {}".format(type(e).__name__, str(e))], 0, []
        forms.alert("שגיאה:\n{}\n\n{}".format(str(e), traceback.format_exc()),
                    title="שגיאה קריטית")
        return 0, [], 0, []

# ================================================================
# מידות פנימיות — קו חיתוך זמני (ציר Y או ציר X)
# ================================================================
def run_axis_dim(axis):
    """
    axis == "Y": המשתמש מסמן קו מקביל לציר Y (אנכי). כל קיר אופקי
    (הרץ לאורך X) שנחצה בטווח שסומן מקבל מידה — עובי הקיר + המרווח
    בין קיר לקיר, כרשרת מידה רציפה אחת, בסגנון הנבחר בהגדרות.
    axis == "X": אותו דבר בכיוון מנוגד — קו מקביל לציר X, חוצה
    קירות אנכיים (הרצים לאורך Y).

    אם מצב "Multiple" מסומן בהגדרות, הפעולה חוזרת על עצמה (בחירת שתי
    נקודות → יצירת מידה) שוב ושוב, עד שהמשתמש לוחץ Esc.
    """
    try:
        doc   = revit.doc
        uidoc = revit.uidoc
        view  = uidoc.ActiveView

        if not isinstance(view, (ViewPlan, ViewSection)):
            forms.alert("אנא עבור לתצוגת תכנית קומה.", title="שגיאה")
            return

        repeat = load_settings().get("multiple", False)

        while True:
            cancelled = _run_axis_dim_once(doc, uidoc, view, axis)
            if cancelled or not repeat:
                break

    except Exception as e:
        forms.alert("שגיאה:\n{}\n\n{}".format(str(e), traceback.format_exc()),
                    title="שגיאה קריטית")

def _run_axis_dim_once(doc, uidoc, view, axis):
    """
    מבצע סיבוב אחד של מידת ציר (בחירת שתי נקודות → יצירת מידה).
    מחזיר True אם המשתמש ביטל (Esc), אחרת False.
    """
    is_y = (axis == "Y")

    try:
        p1 = uidoc.Selection.PickPoint(
            "בחר נקודת התחלה לקו מידות {}".format(axis))
        p2 = uidoc.Selection.PickPoint(
            "בחר נקודת סיום לקו מידות {}".format(axis))
    except Exception:
        return True  # המשתמש ביטל (Esc)

    # גובה מישור התצוגה (לא בהכרח 0) — נדרש למיקום הקו הזמני בדיוק במישור ה-View
    view_z = view.GenLevel.Elevation if getattr(view, "GenLevel", None) else 0

    # יישור הקו למקביל בדיוק לציר שנבחר
    if is_y:
        cut    = p1.X
        lo, hi = sorted([p1.Y, p2.Y])
        lp1, lp2 = XYZ(cut, lo, 0), XYZ(cut, hi, 0)
    else:
        cut    = p1.Y
        lo, hi = sorted([p1.X, p2.X])
        lp1, lp2 = XYZ(lo, cut, 0), XYZ(hi, cut, 0)

    if hi - lo < TOLERANCE:
        forms.alert("הטווח שנבחר קצר מדי.", title="מידות {}".format(axis))
        return False

    # --- קו זמני להצגה (יימחק בסוף) ---
    temp_pt1 = XYZ(lp1.X, lp1.Y, view_z)
    temp_pt2 = XYZ(lp2.X, lp2.Y, view_z)
    temp_id = None
    with revit.Transaction("קו זמני — מידות {}".format(axis)):
        temp_line = doc.Create.NewDetailCurve(view, Line.CreateBound(temp_pt1, temp_pt2))
        temp_id = temp_line.Id
    uidoc.RefreshActiveView()

    # --- איסוף קירות שנחצו ---
    all_walls = list(
        FilteredElementCollector(doc, view.Id)
        .OfClass(Wall)
        .WhereElementIsNotElementType()
    )

    crossed = []
    for w in all_walls:
        s, e = get_wall_pts(w)
        if not s: continue
        horiz = is_horiz_wall(w)
        if is_y:
            if not horiz: continue  # צריך קיר הרץ לאורך X
            wx1, wx2 = min(s.X, e.X), max(s.X, e.X)
            if not (wx1 - TOLERANCE <= cut <= wx2 + TOLERANCE): continue
            wy = (s.Y + e.Y) / 2.0
            if not (lo - TOLERANCE <= wy <= hi + TOLERANCE): continue
        else:
            if horiz: continue  # צריך קיר הרץ לאורך Y
            wy1, wy2 = min(s.Y, e.Y), max(s.Y, e.Y)
            if not (wy1 - TOLERANCE <= cut <= wy2 + TOLERANCE): continue
            wx = (s.X + e.X) / 2.0
            if not (lo - TOLERANCE <= wx <= hi + TOLERANCE): continue
        crossed.append(w)

    if not crossed:
        with revit.Transaction("מחיקת קו זמני"):
            doc.Delete(temp_id)
        forms.alert("לא נמצאו קירות שנחצו ע\"י הקו.", title="מידות {}".format(axis))
        return False

    # מיון הקירות לפי מיקומם לאורך כיוון החיתוך
    if is_y:
        crossed.sort(key=lambda w: (get_wall_pts(w)[0].Y + get_wall_pts(w)[1].Y) / 2.0)
    else:
        crossed.sort(key=lambda w: (get_wall_pts(w)[0].X + get_wall_pts(w)[1].X) / 2.0)

    # אוספים את כל הפאות (עם קואורדינטה אמיתית) מכל הקירות שנחצו, וממיינים
    # גלובלית לפי הקואורדינטה בפועל — לא לפי סדר הקירות. קודם המיון היה
    # "לפי קיר" (כל קיר תורם min/max ברצף), מה שיצר סגמנט מידה "0" (או
    # מיקום גובה שגוי) כשקירות סמוכים/חופפים (עמוד קטן, זיז, פינה משופעת,
    # שני קירות שכמעט נוגעים) תרמו פאות כמעט-זהות בקואורדינטה — ראו
    # project_autodimensions_memory.md.
    MIN_SEGMENT = 2 * CM  # מתחת לזה, נחשב "אותה פאה" ולא סגמנט אמיתי

    candidates = []  # (coord, Reference)
    for w in crossed:
        c_min, r_min, c_max, r_max = get_dim_face_refs(w, is_horizontal=not is_y, return_coords=True)
        if r_min is not None: candidates.append((c_min, r_min))
        if r_max is not None: candidates.append((c_max, r_max))
    candidates.sort(key=lambda x: x[0])

    ra = ReferenceArray()
    last_coord = None
    for coord, ref in candidates:
        if last_coord is not None and abs(coord - last_coord) < MIN_SEGMENT:
            continue  # פאה כמעט-חופפת לקודמת — מדלגים כדי למנוע סגמנט "0"
        ra.Append(ref)
        last_coord = coord

    dim_type = get_dim_style(doc, get_active_dim_style_name())

    created = 0
    err = None
    with revit.Transaction("מידות {} פנימיות".format(axis)):
        ln = make_line(lp1, lp2)
        d, err = try_dim(doc, view, ln, ra, dim_type)
        if d:
            created = 1
        doc.Delete(temp_id)

    if not created:
        forms.alert("שגיאה ביצירת המידה: {}".format(err),
                    title="מידות {} פנימיות".format(axis))

    return False


# ================================================================
# עזר — גובה פתח (דלת/חלון) בס"מ שלם
# ================================================================
def _get_opening_height_cm(fi):
    """מחזיר גובה הפתח בס"מ שלם, או None אם לא נמצא."""
    for bip in [BuiltInParameter.DOOR_HEIGHT, BuiltInParameter.WINDOW_HEIGHT]:
        try:
            p = fi.get_Parameter(bip)
            if p and p.AsDouble() > 0:
                return int(round(p.AsDouble() / CM))
        except:
            pass
    for pname in ["Height", "Rough Height", "Opening Height"]:
        try:
            p = fi.LookupParameter(pname)
            if p and p.AsDouble() > 0:
                return int(round(p.AsDouble() / CM))
        except:
            pass
    try:
        head = fi.get_Parameter(BuiltInParameter.INSTANCE_HEAD_HEIGHT_PARAM)
        sill = fi.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
        if head and sill:
            h = head.AsDouble() - sill.AsDouble()
            if h > 0:
                return int(round(h / CM))
    except:
        pass
    return None


def _set_opening_heights_on_dim(d, sorted_openings):
    """
    לאחר יצירת ממד, מגדיר Below text של כל סגמנט פתח לגובה הפתח.
    הסגמנטים בסדר: [קיר-שמאל→פתח1-שמאל], [פתח1-שמאל→פתח1-ימין], [פתח1-ימין→...]
    כלומר כל סגמנט אי-זוגי (1,3,5...) הוא הפתח עצמו.
    """
    if not d or not sorted_openings:
        return
    try:
        segs = d.Segments
        op_idx = 0
        for seg_idx in range(1, segs.Size, 2):
            if op_idx >= len(sorted_openings):
                break
            h_cm = _get_opening_height_cm(sorted_openings[op_idx])
            if h_cm is not None:
                segs.get_Item(seg_idx).Below = str(h_cm)
            op_idx += 1
    except:
        pass


def _get_opening_refs_and_width(wall, fi, is_horizontal):
    """
    סריקה אחת של גיאומטריית הקיר — מחזירה (refs_list, width_ft).
    מחליפה את get_wall_opening_refs + _get_wall_opening_width_ft בקריאה אחת.
    """
    try:
        fi_x = fi.Location.Point.X
        fi_y = fi.Location.Point.Y
        half_w = 1.0
        for pname in ["Rough Width", "Width", "RO Width"]:
            p = fi.LookupParameter(pname)
            if p and p.AsDouble() > 0:
                half_w = p.AsDouble() / 2.0 + TOLERANCE
                break
        if half_w <= TOLERANCE:
            p = fi.get_Parameter(BuiltInParameter.CASEWORK_WIDTH)
            if p: half_w = p.AsDouble() / 2.0 + TOLERANCE

        opts = Options()
        opts.ComputeReferences = True
        candidates = []  # (coord, ref)

        for obj in wall.get_Geometry(opts):
            if not isinstance(obj, Solid): continue
            for face in obj.Faces:
                try:
                    n = face.FaceNormal
                    if is_horizontal:
                        if abs(abs(n.X) - 1.0) > NORMAL_TOL: continue
                        bb  = face.GetBoundingBox()
                        mid = face.Evaluate(UV((bb.Min.U + bb.Max.U) / 2,
                                               (bb.Min.V + bb.Max.V) / 2))
                        if abs(mid.X - fi_x) <= half_w:
                            candidates.append((mid.X, face.Reference))
                    else:
                        if abs(abs(n.Y) - 1.0) > NORMAL_TOL: continue
                        bb  = face.GetBoundingBox()
                        mid = face.Evaluate(UV((bb.Min.U + bb.Max.U) / 2,
                                               (bb.Min.V + bb.Max.V) / 2))
                        if abs(mid.Y - fi_y) <= half_w:
                            candidates.append((mid.Y, face.Reference))
                except:
                    pass

        if len(candidates) >= 2:
            candidates.sort(key=lambda x: x[0])
            refs  = [candidates[0][1], candidates[-1][1]]
            width = candidates[-1][0] - candidates[0][0]
            return refs, width
    except:
        pass

    # גיבוי: refs של המשפחה
    return get_opening_side_refs(fi, is_horizontal), None


def _annotate_opening_heights_by_width(d, sorted_ops):
    """
    מתייג גובה פתח (Below text) על סגמנטים שרוחבם תואם רוחב דלת.
    רוחב_ft נמדד מגיאומטריית הקיר (אותה גיאומטריה כמו ה-refs) → התאמה מדויקת.
    סבילות: ±1 ס"מ לעיגול בלבד.
    """
    if not d or not sorted_ops:
        return
    try:
        segs = d.Segments

        # בנה רשימת (רוחב_ft, גובה_cm)
        door_dims = []
        for fi, w_ft in sorted_ops:
            h_cm = _get_opening_height_cm(fi)
            if h_cm and w_ft:
                door_dims.append((w_ft, h_cm))

        if not door_dims:
            return

        MATCH_TOL = 3 * CM  # פוט — ±3 ס"מ סבילות

        for i in range(segs.Size):
            seg = segs.get_Item(i)
            try:
                seg_val = seg.Value
                if seg_val is None:
                    continue
                for w_ft, h_cm in door_dims:
                    if abs(seg_val - w_ft) <= MATCH_TOL:
                        seg.Below = str(h_cm)
                        break
            except:
                pass
    except:
        pass


# ================================================================
# עזר — פתחים של קיר בודד
# ================================================================
def _get_wall_doors(doc, view, wall):
    """מחזיר דלתות בלבד המתארחות בקיר נתון."""
    result = []
    wall_id = wall.Id.IntegerValue
    for fi in (FilteredElementCollector(doc, view.Id)
               .OfCategory(BuiltInCategory.OST_Doors)
               .WhereElementIsNotElementType()):
        try:
            if fi.Host and fi.Host.Id.IntegerValue == wall_id:
                result.append(fi)
        except:
            pass
    return result


def _get_wall_openings(doc, view, wall):
    """מחזיר רשימת FamilyInstance (חלונות + דלתות) המתארחים בקיר נתון."""
    openings = []
    wall_id = wall.Id.IntegerValue
    for cat in [BuiltInCategory.OST_Windows, BuiltInCategory.OST_Doors]:
        for fi in (FilteredElementCollector(doc, view.Id)
                   .OfCategory(cat)
                   .WhereElementIsNotElementType()):
            try:
                if fi.Host and fi.Host.Id.IntegerValue == wall_id:
                    openings.append(fi)
            except:
                pass
    return openings


# ================================================================
# מידות X/Y עם פתחים — קיר הכי קרוב לדרום/מערב לקו שסומן
# ================================================================
def run_axis_dim_openings(axis):
    try:
        doc   = revit.doc
        uidoc = revit.uidoc
        view  = uidoc.ActiveView
        if not isinstance(view, (ViewPlan, ViewSection)):
            forms.alert("אנא עבור לתצוגת תכנית קומה.", title="שגיאה")
            return
        repeat = load_settings().get("multiple", False)
        while True:
            cancelled = _run_axis_dim_openings_once(doc, uidoc, view, axis)
            if cancelled or not repeat:
                break
    except Exception as e:
        forms.alert("שגיאה:\n{}\n\n{}".format(str(e), traceback.format_exc()),
                    title="שגיאה קריטית")


def _run_axis_dim_openings_once(doc, uidoc, view, axis):
    """
    קו מידה אחד המשלב:
    - מידות קירות חוצים (כמו run_axis_dim)
    - מידות פתחים בקירות קרובים (עד 200 ס"מ מכל כיוון)
    כל ה-refs מאוחדים במערך אחד — רוויט מסדר לפי מיקום.
    """
    is_x = (axis == "X")

    try:
        p1 = uidoc.Selection.PickPoint("בחר נקודת התחלה לקו חיתוך")
        p2 = uidoc.Selection.PickPoint("בחר נקודת סיום לקו חיתוך")
    except Exception:
        return True  # Esc

    all_walls = list(
        FilteredElementCollector(doc, view.Id)
        .OfClass(Wall)
        .WhereElementIsNotElementType()
    )
    dim_type = get_dim_style(doc, get_active_dim_style_name())

    if is_x:
        cut_y  = (p1.Y + p2.Y) / 2.0
        lo, hi = sorted([p1.X, p2.X])
        lp1 = XYZ(lo, cut_y, 0)
        lp2 = XYZ(hi, cut_y, 0)
    else:
        cut_x  = (p1.X + p2.X) / 2.0
        lo, hi = sorted([p1.Y, p2.Y])
        lp1 = XYZ(cut_x, lo, 0)
        lp2 = XYZ(cut_x, hi, 0)

    if hi - lo < TOLERANCE:
        forms.alert("הטווח שנבחר קצר מדי.", title="מידות {}".format(axis))
        return False

    # איסוף דלתות אחת לכל הקומה (collector אחד במקום N)
    doors_by_wall = {}
    for fi in (FilteredElementCollector(doc, view.Id)
               .OfCategory(BuiltInCategory.OST_Doors)
               .WhereElementIsNotElementType()):
        try:
            wid = fi.Host.Id.IntegerValue
            doors_by_wall.setdefault(wid, []).append(fi)
        except:
            pass

    ra = ReferenceArray()
    OPENING_SEARCH = 200 * CM

    # ── refs מקירות חוצים ─────────────────────────────────────────
    for w in all_walls:
        s, e = get_wall_pts(w)
        if not s: continue
        horiz = is_horiz_wall(w)
        if is_x:
            if horiz: continue
            wy1, wy2 = min(s.Y, e.Y), max(s.Y, e.Y)
            if not (wy1 - TOLERANCE <= cut_y <= wy2 + TOLERANCE): continue
            if not (lo - TOLERANCE <= (s.X+e.X)/2.0 <= hi + TOLERANCE): continue
        else:
            if not horiz: continue
            wx1, wx2 = min(s.X, e.X), max(s.X, e.X)
            if not (wx1 - TOLERANCE <= cut_x <= wx2 + TOLERANCE): continue
            if not (lo - TOLERANCE <= (s.Y+e.Y)/2.0 <= hi + TOLERANCE): continue
        r_min, r_max = get_dim_face_refs(w, is_horizontal=is_x)
        if r_min: ra.Append(r_min)
        if r_max: ra.Append(r_max)

    # ── refs מפתחים בקירות מקבילים במרחק עד 200 ס"מ ──────────────
    sorted_ops   = []
    op_wall_refs = []

    if is_x:
        near = [w for w in all_walls
                if is_horiz_wall(w)
                and get_wall_pts(w)[0] is not None
                and abs((get_wall_pts(w)[0].Y + get_wall_pts(w)[1].Y) / 2.0 - cut_y) <= OPENING_SEARCH + TOLERANCE
                and min(get_wall_pts(w)[0].X, get_wall_pts(w)[1].X) <= hi + TOLERANCE
                and max(get_wall_pts(w)[0].X, get_wall_pts(w)[1].X) >= lo - TOLERANCE]

        for tw in near:
            s, e = get_wall_pts(tw)
            wx1, wx2 = min(s.X, e.X), max(s.X, e.X)
            ref_min, ref_max = get_dim_face_refs(tw, is_horizontal=True)
            if not ref_min or not ref_max: continue
            ops = sorted(doors_by_wall.get(tw.Id.IntegerValue, []),
                         key=lambda fi: fi.Location.Point.X)
            ops_in_range = [fi for fi in ops
                            if wx1 - TOLERANCE <= fi.Location.Point.X <= wx2 + TOLERANCE]
            if not ops_in_range: continue
            op_wall_refs.append(ref_min)
            for fi in ops_in_range:
                refs, w_ft = _get_opening_refs_and_width(tw, fi, is_horizontal=True)
                if len(refs) == 2:
                    sorted_ops.append((fi, w_ft))
                    op_wall_refs.extend(refs)
            op_wall_refs.append(ref_max)

    else:
        near = [w for w in all_walls
                if not is_horiz_wall(w)
                and get_wall_pts(w)[0] is not None
                and abs((get_wall_pts(w)[0].X + get_wall_pts(w)[1].X) / 2.0 - cut_x) <= OPENING_SEARCH + TOLERANCE
                and min(get_wall_pts(w)[0].Y, get_wall_pts(w)[1].Y) <= hi + TOLERANCE
                and max(get_wall_pts(w)[0].Y, get_wall_pts(w)[1].Y) >= lo - TOLERANCE]

        for tw in near:
            s, e = get_wall_pts(tw)
            wy1, wy2 = min(s.Y, e.Y), max(s.Y, e.Y)
            ref_min, ref_max = get_dim_face_refs(tw, is_horizontal=False)
            if not ref_min or not ref_max: continue
            ops = sorted(doors_by_wall.get(tw.Id.IntegerValue, []),
                         key=lambda fi: fi.Location.Point.Y)
            ops_in_range = [fi for fi in ops
                            if wy1 - TOLERANCE <= fi.Location.Point.Y <= wy2 + TOLERANCE]
            if not ops_in_range: continue
            op_wall_refs.append(ref_min)
            for fi in ops_in_range:
                refs, w_ft = _get_opening_refs_and_width(tw, fi, is_horizontal=False)
                if len(refs) == 2:
                    sorted_ops.append((fi, w_ft))
                    op_wall_refs.extend(refs)
            op_wall_refs.append(ref_max)

    for r in op_wall_refs:
        ra.Append(r)

    if ra.Size < 2:
        forms.alert("לא נמצאו refs ליצירת מידה.", title="מידות {} עם פתחים".format(axis))
        return False

    view_z = view.GenLevel.Elevation if getattr(view, "GenLevel", None) else 0
    with revit.Transaction("מידות {} עם פתחים".format(axis)):
        # קו עזר זמני
        temp = doc.Create.NewDetailCurve(
            view, Line.CreateBound(XYZ(lp1.X, lp1.Y, view_z),
                                   XYZ(lp2.X, lp2.Y, view_z)))
        ln = make_line(lp1, lp2)
        d, err = try_dim(doc, view, ln, ra, dim_type)
        doc.Delete(temp.Id)
        if d:
            _annotate_opening_heights_by_width(d, sorted_ops)
        else:
            forms.alert("שגיאה: {}".format(err), title="מידות {} עם פתחים".format(axis))
    return False


# ================================================================
# מדידת קירות — לחיצה על קיר → מידה עם פתחים (דלתות+חלונות)
# ================================================================
def run_wall_dim():
    try:
        doc   = revit.doc
        uidoc = revit.uidoc
        view  = uidoc.ActiveView
        if not isinstance(view, (ViewPlan, ViewSection)):
            forms.alert("אנא עבור לתצוגת תכנית קומה.", title="שגיאה")
            return
        repeat = load_settings().get("multiple", False)
        while True:
            cancelled = _run_wall_dim_once(doc, uidoc, view)
            if cancelled or not repeat:
                break
    except Exception as e:
        forms.alert("שגיאה:\n{}\n\n{}".format(str(e), traceback.format_exc()),
                    title="שגיאה קריטית")


def _run_wall_dim_once(doc, uidoc, view):
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

    class WallFilter(ISelectionFilter):
        def AllowElement(self, element):
            return isinstance(element, Wall)
        def AllowReference(self, reference, position):
            return False

    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, WallFilter(), "לחץ על קיר למדידה עם פתחים")
    except Exception:
        return True  # Esc

    wall = doc.GetElement(ref.ElementId)
    if not isinstance(wall, Wall):
        return False

    s, e = get_wall_pts(wall)
    if not s:
        forms.alert("לא ניתן לקרוא את מיקום הקיר.", title="שגיאה")
        return False

    horiz    = is_horiz_wall(wall)
    openings = _get_wall_openings(doc, view, wall)
    offset   = load_settings().get("wall_dim_offset", 100) * CM
    dim_type = get_dim_style(doc, get_active_dim_style_name())

    if horiz:
        wx1, wx2  = min(s.X, e.X), max(s.X, e.X)
        w_south_y = min(s.Y, e.Y)
        ref_min, ref_max = get_dim_face_refs(wall, is_horizontal=True)
        if not ref_min or not ref_max:
            forms.alert("לא נמצאו refs לקיר.", title="שגיאה")
            return False
        ra = ReferenceArray()
        ra.Append(ref_min)
        sorted_ops = sorted(openings, key=lambda fi: fi.Location.Point.X)
        for fi in sorted_ops:
            for sr in get_wall_opening_refs(wall, fi, is_horizontal=True):
                ra.Append(sr)
        ra.Append(ref_max)
        lp1 = XYZ(wx1, w_south_y - offset, 0)
        lp2 = XYZ(wx2, w_south_y - offset, 0)
    else:
        wy1, wy2 = min(s.Y, e.Y), max(s.Y, e.Y)
        w_west_x = min(s.X, e.X)
        ref_min, ref_max = get_dim_face_refs(wall, is_horizontal=False)
        if not ref_min or not ref_max:
            forms.alert("לא נמצאו refs לקיר.", title="שגיאה")
            return False
        ra = ReferenceArray()
        ra.Append(ref_min)
        sorted_ops = sorted(openings, key=lambda fi: fi.Location.Point.Y)
        for fi in sorted_ops:
            for sr in get_wall_opening_refs(wall, fi, is_horizontal=False):
                ra.Append(sr)
        ra.Append(ref_max)
        lp1 = XYZ(w_west_x - offset, wy1, 0)
        lp2 = XYZ(w_west_x - offset, wy2, 0)

    with revit.Transaction("מידות קיר עם פתחים"):
        ln = make_line(lp1, lp2)
        d, err = try_dim(doc, view, ln, ra, dim_type)
        if d:
            _set_opening_heights_on_dim(d, sorted_ops)
        else:
            forms.alert("שגיאה ביצירת המידה: {}".format(err), title="שגיאה")
    return False
