# -*- coding: utf-8 -*-
"""
מודול משותף — תיוג אוטומטי אלומיניום / מסגרות / נגרות
אלון מיכאלי אדריכלות ובינוי ערים
גרסה 2 — משתמש במשפחת Multi-Category Tag קיימת בפרויקט (ISH_Tag_Reshimot)
במקום ליצור משפחות חדשות
"""
import re
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, StorageType,
    LocationPoint, LocationCurve, IndependentTag, TagOrientation,
    Reference, WallType, WallKind, XYZ, FamilySymbol
)
from pyrevit import revit, forms

# משפחת התג הקיימת בפרויקט (Multi-Category Tag) עם 3 טיפוסים שונים —
# אחד לכל קטגוריה. אם שם המשפחה/הטיפוסים משתנה בפרויקט אחר, לעדכן כאן בלבד.
TAG_FAMILY_NAME = "ISH_Tag_Reshimot"
TAG_TYPE_CARPENTRY = "NAGARUT"    # נגרות — עיגול
TAG_TYPE_FRAMES = "MASGERUT"      # מסגרות — מעוין
TAG_TYPE_ALUMINUM = "ALUMINIUM"   # אלומיניום — משושה

# המספור שהתג בפועל מציג לא נלקח מ-Type Mark, אלא מפרמטרים ייעודיים
# בקבוצת "Overall Legend" בטיפוס הפריט (אושר בפועל מול המשתמש 05.07.26) —
# פרמטר נפרד לכל קטגוריה, כדי לאפשר 3 רצפי מספור נפרדים.
PARAM_NUMBER_CARPENTRY = "Resh_Nagarut_Number"
PARAM_NUMBER_FRAMES = "Resh_Masgerut_Number"
PARAM_NUMBER_ALUMINUM = "Resh_Aluminium_Number"


# ================================================================
# עזרי פרמטר המספור (Resh_*_Number)
# ================================================================
def get_number_param(element_type, param_name):
    return element_type.LookupParameter(param_name)


def read_number_int(element_type, param_name):
    """0 מטופל כ"אין ערך" (None) - זה מה ש-clear_number_param כותב באיפוס, ומספר
    אמיתי אף פעם לא 0 (המספור תמיד מתחיל מ-1 ב-get_or_assign_mark)."""
    p = get_number_param(element_type, param_name)
    if p is None or not p.HasValue:
        return None
    try:
        if p.StorageType == StorageType.Integer:
            v = p.AsInteger()
            return v if v != 0 else None
        if p.StorageType == StorageType.Double:
            v = int(round(p.AsDouble()))
            return v if v != 0 else None
        raw = p.AsString()
        if raw:
            return int(raw.strip())
    except Exception:
        pass
    return None


def set_number_param(element_type, param_name, value):
    p = get_number_param(element_type, param_name)
    if p is None or p.IsReadOnly:
        return
    try:
        if p.StorageType == StorageType.Integer:
            p.Set(int(value))
        elif p.StorageType == StorageType.Double:
            p.Set(float(value))
        else:
            p.Set(str(value))
    except Exception:
        pass


def clear_number_param(element_type, param_name):
    """מאפס לערך ש-read_number_int מזהה כ"אין ערך" (0 למספרי, מחרוזת ריקה למחרוזת) -
    אין API אמין ל"למחוק" ערך פרמטר לגמרי, אז זו נקודת ההסכמה הפנימית שלנו."""
    p = get_number_param(element_type, param_name)
    if p is None or p.IsReadOnly:
        return
    try:
        if p.StorageType == StorageType.Integer:
            p.Set(0)
        elif p.StorageType == StorageType.Double:
            p.Set(0.0)
        else:
            p.Set("")
    except Exception:
        pass


def build_mark_cache(type_pool, param_name):
    """מחזיר (cache, max_val) — cache ממפה type_id(int) -> מספר קיים, max_val הגבוה ביותר שנמצא."""
    cache = {}
    max_val = 0
    for t in type_pool:
        v = read_number_int(t, param_name)
        if v is not None:
            cache[t.Id.IntegerValue] = v
            if v > max_val:
                max_val = v
    return cache, max_val


def get_or_assign_mark(doc, elem_type, cache, counter, param_name):
    """counter הוא רשימה בת איבר אחד [max_val] כדי לאפשר עדכון by-reference.
    מחזיר את המספר (int) שהוקצה/קיים לטיפוס הזה."""
    tid = elem_type.Id.IntegerValue
    if tid in cache:
        return cache[tid]
    counter[0] += 1
    mark = counter[0]
    cache[tid] = mark
    set_number_param(elem_type, param_name, mark)
    return mark


# ================================================================
# זיהוי דלתות נגרות/מסגרות — לפי שם ה-Family, ובמקרה שאף אחד מהם לא
# מופיע בשם - לפי הפרמטר "AR Element Symbol" (נ/מ) כברירת מחדל
# ================================================================
AR_ELEMENT_SYMBOL_PARAM = "AR Element Symbol"
LETTER_CARPENTRY = u"נ"
LETTER_FRAMES = u"מ"
LETTER_ALUMINUM = u"א"


def type_family_name(door_type):
    try:
        return door_type.Family.Name or ""
    except Exception:
        return ""


def is_wood(name):
    return "wood" in name.lower()


def is_steel(name):
    return "steel" in name.lower()


def get_element_symbol(door_type):
    try:
        p = door_type.LookupParameter(AR_ELEMENT_SYMBOL_PARAM)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()
    except Exception:
        pass
    return u""


def door_type_category(door_type):
    """מחזיר 'carpentry' / 'frames' / None לפי שם ה-Family, ואם אף אחד לא
    תואם — לפי AR Element Symbol (נ=carpentry, מ=frames)."""
    name = type_family_name(door_type)
    if is_wood(name):
        return "carpentry"
    if is_steel(name):
        return "frames"
    symbol = get_element_symbol(door_type)
    if symbol == LETTER_CARPENTRY:
        return "carpentry"
    if symbol == LETTER_FRAMES:
        return "frames"
    return None


# ================================================================
# עזרי מיקום/יצירת תג
# ================================================================
def get_location_point(element):
    loc = element.Location
    if isinstance(loc, LocationPoint):
        return loc.Point
    if isinstance(loc, LocationCurve):
        curve = loc.Curve
        return curve.Evaluate(0.5, True)
    bbox = element.get_BoundingBox(None)
    if bbox is not None:
        return XYZ(
            (bbox.Min.X + bbox.Max.X) / 2.0,
            (bbox.Min.Y + bbox.Max.Y) / 2.0,
            (bbox.Min.Z + bbox.Max.Z) / 2.0,
        )
    return XYZ(0, 0, 0)


def get_type_name(element_type):
    """element_type.Name זורק חריגה עבור FamilySymbol-ים מסוימים (נצפה בפועל) —
    הדרך האמינה היא הפרמטר המובנה SYMBOL_NAME_PARAM."""
    p = element_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
    if p is not None and p.HasValue:
        val = p.AsString()
        if val:
            return val
    return element_type.Name


def find_tag_type(doc, family_name, type_name):
    """מחפש FamilySymbol של תג בקטגוריית Multi-Category Tags לפי שם Family+Type
    (השוואה case-insensitive + strip — ל-Properties panel יש לעיתים תצוגת ALL-CAPS
    שלא בהכרח תואמת את המחרוזת המקורית)."""
    target_family = family_name.strip().lower()
    target_type = type_name.strip().lower()
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_MultiCategoryTags).WhereElementIsElementType()
    for t in collector:
        try:
            if t.Family.Name.strip().lower() == target_family and get_type_name(t).strip().lower() == target_type:
                if not t.IsActive:
                    t.Activate()
                    doc.Regenerate()
                return t
        except Exception:
            continue
    return None


def list_multicategory_tag_types(doc):
    """דיאגנוסטיקה: כל זוגות (Family, Type) הקיימים בפועל תחת Multi-Category Tags —
    לשימוש כשלא נמצא טיפוס תג צפוי, כדי לראות מה ה-API באמת מחזיר."""
    result = []
    collector = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_MultiCategoryTags).WhereElementIsElementType()
    for t in collector:
        try:
            fam_name = t.Family.Name
        except Exception as e1:
            fam_name = u"<שגיאת Family: {}>".format(e1)
        try:
            type_name = get_type_name(t)
        except Exception as e2:
            type_name = u"<שגיאת Name: {}>".format(e2)
        result.append(u"{} / {}".format(fam_name, type_name))
    return result


def get_tagged_ids_in_view(doc, view, tag_type_id):
    """אוסף את כל ה-ElementId-ים שכבר מתויגים בתצוגה ע"י תגית מה-Type הספציפי הזה."""
    tagged = set()
    tags = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_MultiCategoryTags).WhereElementIsNotElementType().ToElements()
    for t in tags:
        try:
            if t.GetTypeId() != tag_type_id:
                continue
            ids = t.GetTaggedLocalElementIds()
            for i in ids:
                tagged.add(i.IntegerValue)
        except Exception:
            pass
    return tagged


CM = 1 / 30.48
TAG_OFFSET_DIST = 50 * CM  # מרחק הטאג מהפריט - 50 ס"מ
_TAG_OFFSET_COMPONENT = TAG_OFFSET_DIST / (2.0 ** 0.5)  # פיצול ל-45 מעלות


def get_tag_head_point(base_point):
    return XYZ(base_point.X + _TAG_OFFSET_COMPONENT, base_point.Y + _TAG_OFFSET_COMPONENT, base_point.Z)


def get_secondary_tag_head_point(base_point):
    """נקודת ראש לתג שני על אותו host (למשל MASGERUT על חלון שכבר מתויג/יתויג
    גם ALUMINIUM ע"י הכפתור השני) - כיוון הפוך (מטה-שמאל) כדי שלא יתנגש ויזואלית
    עם התג הרגיל, בלי תלות בסדר שבו הכפתורים רצים."""
    return XYZ(base_point.X - _TAG_OFFSET_COMPONENT, base_point.Y - _TAG_OFFSET_COMPONENT, base_point.Z)


def create_tag_at_head(doc, view, tag_type_id, element, head_point):
    ref = Reference(element)
    return IndependentTag.Create(
        doc, tag_type_id, view.Id, ref, True,
        TagOrientation.Horizontal, head_point
    )


def create_tag(doc, view, tag_type_id, element, point):
    return create_tag_at_head(doc, view, tag_type_id, element, get_tag_head_point(point))


# ================================================================
# איסוף קירות מסך (Curtain Wall)
# ================================================================
def is_curtain_wall_instance(wall):
    try:
        return wall.WallType.Kind == WallKind.Curtain
    except Exception:
        return False


def is_curtain_wall_type(wall_type):
    try:
        return wall_type.Kind == WallKind.Curtain
    except Exception:
        return False


# ================================================================
# מספור קירות מסך - כל קיר מסך בפרויקט חולק Type אחד גנרי ("ISH- בסיס"),
# והגודל בפועל (אורך/גובה/פריסת מוליונים) הוא ברמת ה-Instance. התג קורא
# את המספר מ-Type בלבד - נבדק בפועל (13.07.26) שהפרמטר Resh_Aluminium_Number
# לא בכלל bound כ-Instance לקטגוריית Walls, ואי אפשר לשנות את זה בלי
# להשפיע גם על חלונות (Type/Instance הוא הגדרה אחת לכל הקטגוריות המחוברות
# לאותו project parameter ברוויט). המשתמש בחר (13.07.26) בפתרון החלופי:
# ליצור/לשכפל אוטומטית Type ייעודי לכל "חתימה" גיאומטרית שונה - ואם קיר
# חדש בדיוק באותו גודל+פריסה כמו קיר קיים איפשהו בפרויקט, להשתמש **באותו
# Type בדיוק** (לא ליצור עוד אחד) כדי שיקבל אוטומטית את אותו מספר.
# ================================================================
def get_curtain_wall_signature(wall):
    """(אורך_ס"מ, גובה_ס"מ, מספר קווי גריד אנכי, מספר קווי גריד אופקי) -
    כל שינוי במידות או בפריסת המוליונים משנה את החתימה."""
    try:
        length_param = wall.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH)
        length_ft = length_param.AsDouble() if length_param is not None else 0.0
    except Exception:
        length_ft = 0.0
    try:
        bbox = wall.get_BoundingBox(None)
        height_ft = (bbox.Max.Z - bbox.Min.Z) if bbox is not None else 0.0
    except Exception:
        height_ft = 0.0

    length_cm = int(round(length_ft / CM))
    height_cm = int(round(height_ft / CM))

    num_u = 0
    num_v = 0
    try:
        grid = wall.CurtainGrid
        if grid is not None:
            num_u = len(list(grid.GetUGridLineIds()))
            num_v = len(list(grid.GetVGridLineIds()))
    except Exception:
        pass

    return (length_cm, height_cm, num_u, num_v)


SIZED_WALL_TYPE_SUFFIX_RE = re.compile(r" \d+x\d+-\d+x\d+$")


def get_or_create_sized_curtain_wall_type(doc, wall, signature):
    """מוצא/יוצר WallType ייעודי לחתימה הגיאומטרית הזו. אם כבר קיים Type
    בשם המתאים (משוכפל בעבר, מאותו קיר או קיר אחר) - משתמש בו (ולכן גם
    באותו מספר קיים, כי המספר יושב על ה-Type). אחרת משכפל Type חדש מתוך
    ה-Type הנוכחי של הקיר (שומר את כל שאר תכונות ה-Type)."""
    length_cm, height_cm, num_u, num_v = signature
    current_type = doc.GetElement(wall.GetTypeId())
    base_name = SIZED_WALL_TYPE_SUFFIX_RE.sub("", get_type_name(current_type))
    target_name = u"{} {}x{}-{}x{}".format(base_name, length_cm, height_cm, num_u, num_v)

    all_wall_types = FilteredElementCollector(doc).OfClass(WallType).ToElements()
    for wt in all_wall_types:
        if get_type_name(wt).strip() == target_name.strip():
            return wt

    return current_type.Duplicate(target_name)


# ================================================================
# חלונות "מורכבים" (Shield) - חלון + מסגרת + עד 2 צינורות תריס
# (nested shared families) בתוך אותה משפחת Window יחידה.
# הזיהוי מבני (לפי שם ה-Family של תת-הרכיב) ולא לפי שם משפחת החלון עצמה,
# כדי לא להיות תלוי בשמות ספציפיים כמו "MMD"/"Shield".
# ================================================================
def is_pipe_family_name(name):
    return "pipe" in name.lower()


def collect_pipe_type_pool(doc):
    """כל ה-FamilySymbol-ים בפרויקט ששם ה-Family שלהם מכיל 'pipe' - בלי תלות
    בקטגוריה (הצינורות המקוננים לא בהכרח בקטגוריית Windows)."""
    all_symbols = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
    return [t for t in all_symbols if is_pipe_family_name(type_family_name(t))]


def build_frames_type_pool(doc):
    """כל ה-Types בפרויקט שיכולים לקבל Resh_Masgerut_Number: דלתות פלדה +
    חלונות (מסגרת של חלונות Shield) + צינורות. חובה להשתמש בבריכה המאוחדת
    הזו בכל מקום שבונים cache/counter למספור מסגרות - אחרת לכל פונקציה יש
    מונה עצמאי שרואה רק את ה-Types שהיא עצמה סורקת, ומספרים מתנגשים בין
    קטגוריות (למשל דלת פלדה וטיפוס צינור שמתחילים שניהם באופן עצמאי מ-1)."""
    all_door_types = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsElementType().ToElements()
    frame_door_types = [t for t in all_door_types if door_type_category(t) == "frames"]
    window_type_pool = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsElementType().ToElements())
    pipe_type_pool = collect_pipe_type_pool(doc)
    return frame_door_types + window_type_pool + pipe_type_pool


def get_pipe_subcomponents(doc, window):
    """מחזיר את תתי-הרכיבים המקוננים (shared) של החלון ששם ה-Family שלהם מכיל
    'pipe' - קיים תמיד במשפחת חלון "מורכבת" (Shield), גם אם מוסתר כרגע בתצוגה."""
    result = []
    try:
        sub_ids = window.GetSubComponentIds()
    except Exception:
        return result
    for sid in sub_ids:
        sub = doc.GetElement(sid)
        if sub is None:
            continue
        try:
            sub_type = doc.GetElement(sub.GetTypeId())
        except Exception:
            continue
        if sub_type is not None and is_pipe_family_name(type_family_name(sub_type)):
            result.append(sub)
    return result


def is_visible_in_view(element, view):
    """True אם לאלמנט יש גאומטריה מוצגת בפועל בתצוגה הזו (bounding box לא ריק) -
    משמש לבדוק אם צינור ספציפי גלוי כרגע, בלי תלות בשם הפרמטר
    Left/Right Pipe Visibility בפרט."""
    try:
        return element.get_BoundingBox(view) is not None
    except Exception:
        return False


# ================================================================
# תיוג מסגרות בתוך חלונות מורכבים (Shield) - חלק מכפתור "מסגרות",
# בנוסף לתיוג דלתות פלדה. לא נוגע בכפתור "אלומיניום" ולא במה שהוא מתייג.
# ================================================================
def run_tag_frames_in_windows():
    doc = revit.doc
    view = doc.ActiveView
    title = u"תיוג מסגרות בחלונות מורכבים"

    tag_type = find_tag_type(doc, TAG_FAMILY_NAME, TAG_TYPE_FRAMES)
    if tag_type is None:
        existing = list_multicategory_tag_types(doc)
        forms.alert(
            u"לא נמצא טיפוס תג '{}' במשפחה '{}'.\n\nמה שנמצא בפועל תחת Multi-Category Tags:\n{}"
            .format(TAG_TYPE_FRAMES, TAG_FAMILY_NAME, u"\n".join(existing) if existing else u"(כלום)")
        )
        return

    cache, max_val = build_mark_cache(build_frames_type_pool(doc), PARAM_NUMBER_FRAMES)
    counter = [max_val]

    window_candidates = list(FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsNotElementType().ToElements())
    already_tagged = get_tagged_ids_in_view(doc, view, tag_type.Id)

    tagged_count = 0
    skipped_count = 0

    with revit.Transaction(title):
        for win in window_candidates:
            pipes = get_pipe_subcomponents(doc, win)
            if not pipes:
                continue  # חלון "רגיל" בלי מסגרת נפרדת - לא באחריות הכפתור הזה

            if win.Id.IntegerValue in already_tagged:
                skipped_count += 1
            else:
                win_type = doc.GetElement(win.GetTypeId())
                get_or_assign_mark(doc, win_type, cache, counter, PARAM_NUMBER_FRAMES)
                head = get_secondary_tag_head_point(get_location_point(win))
                create_tag_at_head(doc, view, tag_type.Id, win, head)
                tagged_count += 1

            for pipe in pipes:
                if pipe.Id.IntegerValue in already_tagged:
                    skipped_count += 1
                    continue
                if not is_visible_in_view(pipe, view):
                    continue
                pipe_type = doc.GetElement(pipe.GetTypeId())
                get_or_assign_mark(doc, pipe_type, cache, counter, PARAM_NUMBER_FRAMES)
                point = get_location_point(pipe)
                create_tag(doc, view, tag_type.Id, pipe, point)
                tagged_count += 1

    forms.alert(u"{}: תויגו {} פריטים, {} כבר היו מתויגים.".format(title, tagged_count, skipped_count))


# ================================================================
# תיוג צינורות בתוך דלתות מורכבות (כמו AR_D_MMD - דלת+מסגרת+עד 2 צינורות,
# אותו רעיון כמו חלון Shield). חלק מכפתור "מסגרות". בשונה מהחלון - גוף הדלת
# עצמו כבר מתויג ונספר כרגיל ע"י run_tag_doors (לפי נגרות/מסגרות), אז כאן
# מטפלים אך ורק בצינורות (תמיד מסגרות, בלי קשר לעץ/פלדה של הדלת עצמה).
# ================================================================
def run_tag_pipes_in_doors():
    doc = revit.doc
    view = doc.ActiveView
    title = u"תיוג צינורות בדלתות מורכבות"

    tag_type = find_tag_type(doc, TAG_FAMILY_NAME, TAG_TYPE_FRAMES)
    if tag_type is None:
        existing = list_multicategory_tag_types(doc)
        forms.alert(
            u"לא נמצא טיפוס תג '{}' במשפחה '{}'.\n\nמה שנמצא בפועל תחת Multi-Category Tags:\n{}"
            .format(TAG_TYPE_FRAMES, TAG_FAMILY_NAME, u"\n".join(existing) if existing else u"(כלום)")
        )
        return

    cache, max_val = build_mark_cache(build_frames_type_pool(doc), PARAM_NUMBER_FRAMES)
    counter = [max_val]

    door_candidates = list(FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType().ToElements())
    already_tagged = get_tagged_ids_in_view(doc, view, tag_type.Id)

    tagged_count = 0
    skipped_count = 0

    with revit.Transaction(title):
        for door in door_candidates:
            for pipe in get_pipe_subcomponents(doc, door):
                if pipe.Id.IntegerValue in already_tagged:
                    skipped_count += 1
                    continue
                if not is_visible_in_view(pipe, view):
                    continue
                pipe_type = doc.GetElement(pipe.GetTypeId())
                get_or_assign_mark(doc, pipe_type, cache, counter, PARAM_NUMBER_FRAMES)
                point = get_location_point(pipe)
                create_tag(doc, view, tag_type.Id, pipe, point)
                tagged_count += 1

    forms.alert(u"{}: תויגו {} פריטים, {} כבר היו מתויגים.".format(title, tagged_count, skipped_count))


# ================================================================
# תיוג דלתות (נגרות / מסגרות) — משותף לשני הכפתורים
# ================================================================
def run_tag_doors(is_carpentry):
    doc = revit.doc
    view = doc.ActiveView

    if is_carpentry:
        tag_type_name = TAG_TYPE_CARPENTRY
        target_category = "carpentry"
        param_name = PARAM_NUMBER_CARPENTRY
        title = u"תיוג נגרות"
    else:
        tag_type_name = TAG_TYPE_FRAMES
        target_category = "frames"
        param_name = PARAM_NUMBER_FRAMES
        title = u"תיוג מסגרות"

    tag_type = find_tag_type(doc, TAG_FAMILY_NAME, tag_type_name)
    if tag_type is None:
        existing = list_multicategory_tag_types(doc)
        forms.alert(
            u"לא נמצא טיפוס תג '{}' במשפחה '{}'.\n\nמה שנמצא בפועל תחת Multi-Category Tags:\n{}"
            .format(tag_type_name, TAG_FAMILY_NAME, u"\n".join(existing) if existing else u"(כלום)")
        )
        return

    if is_carpentry:
        all_types = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsElementType().ToElements()
        type_pool = [t for t in all_types if door_type_category(t) == target_category]
    else:
        # "frames" - Resh_Masgerut_Number משותף גם לחלונות/צינורות (חלונות ודלתות
        # Shield) - חייב בריכה מאוחדת כדי לא להתנגש איתם במספור, ראו build_frames_type_pool.
        type_pool = build_frames_type_pool(doc)
    cache, max_val = build_mark_cache(type_pool, param_name)
    counter = [max_val]

    candidates = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType().ToElements()
    candidates = [e for e in candidates if door_type_category(doc.GetElement(e.GetTypeId())) == target_category]

    already_tagged = get_tagged_ids_in_view(doc, view, tag_type.Id)

    tagged_count = 0
    skipped_count = 0

    with revit.Transaction(title):
        for door in candidates:
            if door.Id.IntegerValue in already_tagged:
                skipped_count += 1
                continue
            elem_type = doc.GetElement(door.GetTypeId())
            get_or_assign_mark(doc, elem_type, cache, counter, param_name)
            point = get_location_point(door)
            create_tag(doc, view, tag_type.Id, door, point)
            tagged_count += 1

    forms.alert(u"{}: תויגו {} פריטים, {} כבר היו מתויגים.".format(title, tagged_count, skipped_count))


# ================================================================
# תיוג אלומיניום (חלונות + קירות מסך) — Type Mark משותף לשניהם
# ================================================================
def run_tag_aluminum():
    doc = revit.doc
    view = doc.ActiveView
    title = u"תיוג אלומיניום"

    tag_type = find_tag_type(doc, TAG_FAMILY_NAME, TAG_TYPE_ALUMINUM)
    if tag_type is None:
        existing = list_multicategory_tag_types(doc)
        forms.alert(
            u"לא נמצא טיפוס תג '{}' במשפחה '{}'.\n\nמה שנמצא בפועל תחת Multi-Category Tags:\n{}"
            .format(TAG_TYPE_ALUMINUM, TAG_FAMILY_NAME, u"\n".join(existing) if existing else u"(כלום)")
        )
        return

    window_candidates = list(FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsNotElementType().ToElements())
    wall_candidates_all = FilteredElementCollector(doc, view.Id).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType().ToElements()
    wall_candidates = [w for w in wall_candidates_all if is_curtain_wall_instance(w)]

    already_tagged = get_tagged_ids_in_view(doc, view, tag_type.Id)

    tagged_count = 0
    skipped_count = 0

    with revit.Transaction(title):
        # שלב מקדים: לכל קיר מסך שעדיין לא מתויג - לוודא שהוא משוייך ל-Type
        # הייעודי לגודל שלו (נמצא קיים או משוכפל חדש). את זה עושים *לפני*
        # בניית ה-cache של המספור, כדי שה-cache ישקף את מצב ה-Types העדכני.
        for wall in wall_candidates:
            if wall.Id.IntegerValue in already_tagged:
                continue
            signature = get_curtain_wall_signature(wall)
            sized_type = get_or_create_sized_curtain_wall_type(doc, wall, signature)
            if wall.GetTypeId() != sized_type.Id:
                wall.ChangeTypeId(sized_type.Id)
        doc.Regenerate()

        # חלונות + קירות מסך חולקים אותו Resh_Aluminium_Number, אז שני
        # המונים חייבים להתחיל מה-max הגלובלי בין שניהם (לא רק מה-max של
        # הקבוצה שלהם) - אחרת חלון חדש בעתיד יכול להתנגש עם מספר קיר מסך
        # קיים שכבר גבוה יותר (אותו לקח בדיוק כמו הבאג שתוקן במסגרות).
        window_type_pool = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsElementType().ToElements())
        window_cache, window_max = build_mark_cache(window_type_pool, PARAM_NUMBER_ALUMINUM)

        all_wall_types = FilteredElementCollector(doc).OfClass(WallType).ToElements()
        curtain_wall_type_pool = [wt for wt in all_wall_types if is_curtain_wall_type(wt)]
        wall_cache, wall_max = build_mark_cache(curtain_wall_type_pool, PARAM_NUMBER_ALUMINUM)

        global_max = max(window_max, wall_max)
        counter_windows = [global_max]

        for win in window_candidates:
            if win.Id.IntegerValue in already_tagged:
                skipped_count += 1
                continue
            elem_type = doc.GetElement(win.GetTypeId())
            get_or_assign_mark(doc, elem_type, window_cache, counter_windows, PARAM_NUMBER_ALUMINUM)
            point = get_location_point(win)
            create_tag(doc, view, tag_type.Id, win, point)
            tagged_count += 1

        # קירות מסך: תמיד אחרונים - המונה שלהם מתחיל **אחרי** שלולאת החלונות
        # למעלה כבר סיימה להריץ (לא לפני!), כדי לכלול גם חלונות חדשים
        # שמספרו באותה ריצה בדיוק.
        counter_walls = [max(counter_windows[0], wall_max)]

        for wall in wall_candidates:
            if wall.Id.IntegerValue in already_tagged:
                skipped_count += 1
                continue
            elem_type = doc.GetElement(wall.GetTypeId())
            get_or_assign_mark(doc, elem_type, wall_cache, counter_walls, PARAM_NUMBER_ALUMINUM)
            point = get_location_point(wall)
            create_tag(doc, view, tag_type.Id, wall, point)
            tagged_count += 1

    forms.alert(u"{}: תויגו {} פריטים, {} כבר היו מתויגים.".format(title, tagged_count, skipped_count))


# ================================================================
# איפוס מספור - כלי תחזוקה כללי, לבחירת המשתמש בין 3 הקטגוריות. מאפס את
# פרמטר המספור (Resh_*_Number) על כל ה-Types הרלוונטים לקטגוריה שנבחרה.
# פעולה הרסנית (מוחקת מספור קיים בפועל) - חובה אישור מהמשתמש לפני ביצוע.
# לא מוחק תגים - יש למחוק אותם בנפרד לפני שממספרים מחדש.
# ================================================================
# (תווית תצוגה, מפתח פנימי, שם פרמטר) - הסדר קובע את סדר האפשרויות בתפריט
RESET_CATEGORIES = [
    (u"נגרות", "carpentry", PARAM_NUMBER_CARPENTRY),
    (u"מסגרות", "frames", PARAM_NUMBER_FRAMES),
    (u"אלומיניום", "aluminum", PARAM_NUMBER_ALUMINUM),
]


def build_type_pool_for_category(doc, category):
    if category == "carpentry":
        all_types = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsElementType().ToElements()
        return [t for t in all_types if door_type_category(t) == "carpentry"]
    if category == "frames":
        return build_frames_type_pool(doc)
    if category == "aluminum":
        # חלונות + קירות מסך: שניהם לפי Type (כל גודל שונה של קיר מסך =
        # Type ייעודי, ראו get_or_create_sized_curtain_wall_type).
        window_type_pool = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsElementType().ToElements())
        all_wall_types = FilteredElementCollector(doc).OfClass(WallType).ToElements()
        curtain_wall_type_pool = [wt for wt in all_wall_types if is_curtain_wall_type(wt)]
        return window_type_pool + curtain_wall_type_pool
    return []


def run_reset_numbering():
    doc = revit.doc

    labels = [c[0] for c in RESET_CATEGORIES]
    selected_label = forms.CommandSwitchWindow.show(labels, message=u"איזה מספור לאפס?")
    if not selected_label:
        return
    label, category, param_name = next(c for c in RESET_CATEGORIES if c[0] == selected_label)

    confirmed = forms.alert(
        u"פעולה זו תאפס את מספור {} ({}) על כל הפריטים הרלוונטיים בפרויקט - כולל "
        u"מספרים שכבר בשימוש בפועל.\n\n"
        u"התגים הקיימים בתצוגה לא יימחקו אוטומטית ע\"י הפעולה הזו - יש למחוק אותם "
        u"בנפרד (בכל התצוגות הרלוונטיות) לפני שמריצים מחדש את כפתור התיוג המתאים.\n\n"
        u"האם להמשיך ולאפס את המספור?"
        .format(label, param_name),
        title=u"איפוס מספור {}".format(label),
        yes=True, no=True
    )
    if not confirmed:
        return

    type_pool = build_type_pool_for_category(doc, category)
    reset_count = 0
    with revit.Transaction(u"איפוס מספור {}".format(label)):
        for t in type_pool:
            if read_number_int(t, param_name) is not None:
                clear_number_param(t, param_name)
                reset_count += 1

    forms.alert(
        u"אופס מספור {} על {} טיפוסים.\n\nעכשיו: למחוק תגים ישנים (אם עוד לא נמחקו) "
        u"ולהריץ מחדש את כפתור התיוג המתאים כדי לקבל מספור נקי בלי התנגשויות."
        .format(label, reset_count)
    )
