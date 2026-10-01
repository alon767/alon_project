# -*- coding: utf-8 -*-
"""
יצירת Schedule אוטומטי — אלומיניום / מסגרות / נגרות.
Multi-Category Schedule (ElementId.InvalidElementId), מקובץ לפי Type (לא
Itemize), מסונן לפריטים עם Resh_*_Number קיים בלבד. הסינון לפי HasValue הוא
מה שבפועל מגביל את השורות לקטגוריות הרלוונטיות בלי צורך בסינון קטגוריה
מפורש - למשל Schedule מסגרות "תופס" אוטומטית גם דלתות פלדה וגם מסגרת/צינורות
של חלונות Shield, כי רק להם יש ערך ב-Resh_Masgerut_Number.
"""
import os
import traceback

from Autodesk.Revit.DB import (
    ViewSchedule, ElementId, ScheduleFilter, ScheduleFilterType, ScheduleSortGroupField
)
from pyrevit import revit, forms

from alutag_utils import PARAM_NUMBER_ALUMINUM, PARAM_NUMBER_FRAMES, PARAM_NUMBER_CARPENTRY

SCHEDULE_NAME_ALUMINUM = u"ALUMINIUM SCHEDULE (AutoTag+)"
SCHEDULE_NAME_FRAMES = u"MASGEROT SCHEDULE (AutoTag+)"
SCHEDULE_NAME_CARPENTRY = u"NAGAROT SCHEDULE (AutoTag+)"

# סדר השדות המדויק, לפי הדוגמה מפרויקט "דוד אלעזר" — עם Resh_Aluminium_Number
# במקום Type Mark (ראו הסבר בזיכרון הפרויקט מדוע).
FIELD_ORDER_ALUMINUM = [
    (u"Count", False),
    (u"Type Comments", False),
    (u"AR Finish & Color", False),
    (u"AR Glazing", False),
    (u"AR Metal-Work", False),
    (u"Description", False),
    (u"AR Shutter Box Height", False),
    (u"ISH_Width", False),
    (PARAM_NUMBER_ALUMINUM, False),
    (u"AR Element Symbol", False),
    (u"Family and Type", True),   # מוסתר — משמש רק לקיבוץ
    (u"ISH_Height", False),
]

# מסגרות/נגרות - לפי טבלת הייחוס שהמשתמש שלח (מבנה זהה לשתיהן):
# מספר | גודל פתח | תיאור | פירזול | גמר/צבע | הערות | כמות
# "AR Hardware" (פירזול) הוא ניחוש - לא אומת מול הפרויקט כמו שאר השדות.
# שדה שלא נמצא בפועל מדולג ומדווח ב-missing, בלי לעצור את התהליך.
FIELD_ORDER_FRAMES = [
    (u"Count", False),
    (u"Type Comments", False),
    (u"AR Finish & Color", False),
    (u"AR Hardware", False),
    (u"Description", False),
    (u"ISH_Width", False),
    (PARAM_NUMBER_FRAMES, False),
    (u"Family and Type", True),   # מוסתר — משמש רק לקיבוץ
    (u"ISH_Height", False),
]

FIELD_ORDER_CARPENTRY = [
    (u"Count", False),
    (u"Type Comments", False),
    (u"AR Finish & Color", False),
    (u"AR Hardware", False),
    (u"Description", False),
    (u"ISH_Width", False),
    (PARAM_NUMBER_CARPENTRY, False),
    (u"Family and Type", True),
    (u"ISH_Height", False),
]

LOG_PATH = os.path.join(os.path.dirname(__file__), "schedule_log.txt")


def log(msg):
    try:
        text = msg if isinstance(msg, unicode) else unicode(str(msg))
    except Exception:
        text = repr(msg)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(text.encode("utf-8"))
            f.write("\n")
    except Exception:
        pass


def reset_log():
    try:
        with open(LOG_PATH, "w") as f:
            f.write("")
    except Exception:
        pass


def get_schedulable_field_by_name(doc, definition, name):
    target = name.strip().lower()
    for sf in definition.GetSchedulableFields():
        try:
            if sf.GetName(doc).strip().lower() == target:
                return sf
        except Exception:
            continue
    return None


def find_existing_schedule(doc, name):
    from Autodesk.Revit.DB import FilteredElementCollector
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        try:
            if v.Name == name:
                return v
        except Exception:
            continue
    return None


def create_schedule(transaction_title, schedule_name, param_name, field_order):
    reset_log()
    doc = revit.doc

    try:
        with revit.Transaction(transaction_title):
            existing = find_existing_schedule(doc, schedule_name)
            if existing is not None:
                log(u"deleting existing schedule to recreate")
                doc.Delete(existing.Id)
            log(u"creating multi-category schedule...")
            schedule = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId)
            schedule.Name = schedule_name
            definition = schedule.Definition
            definition.IsItemized = False
            log(u"schedule created, adding fields...")

            missing = []
            number_field = None
            family_type_field = None

            for name, hidden in field_order:
                sf = get_schedulable_field_by_name(doc, definition, name)
                if sf is None:
                    missing.append(name)
                    log(u"MISSING schedulable field: {}".format(name))
                    continue
                field = definition.AddField(sf)
                log(u"added field: {}".format(name))
                if hidden:
                    try:
                        field.IsHidden = True
                    except Exception:
                        log(u"could not hide field: {}".format(name))
                if name == param_name:
                    number_field = field
                if name == u"Family and Type":
                    family_type_field = field

            if number_field is not None:
                try:
                    definition.AddSortGroupField(ScheduleSortGroupField(number_field.FieldId))
                    log(u"added sort/group field (primary, ascending): {}".format(param_name))
                except Exception:
                    log(u"FAILED to add sort/group field (number):\n" + traceback.format_exc())

            if family_type_field is not None:
                try:
                    definition.AddSortGroupField(ScheduleSortGroupField(family_type_field.FieldId))
                    log(u"added sort/group field (secondary): Family and Type")
                except Exception:
                    log(u"FAILED to add sort/group field (family/type):\n" + traceback.format_exc())

            if number_field is not None:
                try:
                    definition.AddFilter(ScheduleFilter(number_field.FieldId, ScheduleFilterType.HasValue))
                    log(u"added filter: {} HasValue".format(param_name))
                except Exception:
                    log(u"FAILED to add filter:\n" + traceback.format_exc())

        if missing:
            forms.alert(
                u"ה-Schedule '{}' נוצר, אך השדות הבאים לא נמצאו בפרויקט ולא נוספו:\n{}"
                .format(schedule_name, u"\n".join(missing))
            )
        else:
            forms.alert(u"נוצר Schedule '{}' בהצלחה.".format(schedule_name))

    except Exception:
        tb = traceback.format_exc()
        log(u"FATAL:\n" + tb)
        forms.alert(u"שגיאה ביצירת ה-Schedule. פרטים ב-schedule_log.txt.\n\n" + tb)


def create_aluminum_schedule():
    create_schedule(u"יצירת Schedule אלומיניום", SCHEDULE_NAME_ALUMINUM, PARAM_NUMBER_ALUMINUM, FIELD_ORDER_ALUMINUM)


def create_frames_schedule():
    create_schedule(u"יצירת Schedule מסגרות", SCHEDULE_NAME_FRAMES, PARAM_NUMBER_FRAMES, FIELD_ORDER_FRAMES)


def create_carpentry_schedule():
    create_schedule(u"יצירת Schedule נגרות", SCHEDULE_NAME_CARPENTRY, PARAM_NUMBER_CARPENTRY, FIELD_ORDER_CARPENTRY)
