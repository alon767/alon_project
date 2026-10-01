# -*- coding: utf-8 -*-
"""הרצה מלאה: לכל קומה שנבחרה - חדרים ושמות, מפלסים, ושאר השלבים (מרתף/גג, מידות, חזיתות) - לפי הסדר.
בסוף כל קומה נשמרים בתיקיית "הערות\\auto" תמונה של התכנית וטבלת הבדיקה, כדי שהסוכנים יבדקו בלי לחכות ל-PDF.
רק על עותק עבודה (_AGENT)."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== start full run')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.UI import TaskDialog
    from System.Collections.Generic import List
    from pyrevit import revit, script, forms
    import permit_write
    import permit_fullrun
    trace(u'imports ok, code version: %s' % permit_write.VERSION)

    doc = revit.doc
    uidoc = revit.uidoc
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        TaskDialog.Show(u'הרצה מלאה', u'הכלי רץ רק על עותק עבודה.\nשם הקובץ חייב להכיל "%s".' % permit_write.WRITE_MARK)
        script.exit()

    plans = permit_fullrun.permit_plans(doc)
    if not plans:
        TaskDialog.Show(u'הרצה מלאה', u'לא נמצאו תכניות עם תבנית תצוגה "...להיתר".')
        script.exit()
    label = lambda v: u'%s  |  %s' % (v.Name, v.GenLevel.Name)
    picked = forms.SelectFromList.show(
        [label(v) for v in plans], title=u'הרצה מלאה - בחר קומות (תכניות עם תבנית "...להיתר")',
        button_name=u'הרץ', multiselect=True, width=560, height=600)
    if not picked:
        script.exit()
    views = [v for v in plans if label(v) in picked]
    trace(u'full run: %s' % u', '.join(v.Name for v in views))

    EDIT, SKIP, CANCEL = u'עריכה...', u'דלג (ללא חדר)', u'ביטול הכול'

    def choose_name(r):
        # only for spaces with no name from an earlier run - the same choice as in "עדכון חדרים ושמות".
        # (Alon via agent 2, 01.10.26: the zoom alone is not enough next to other rooms - the room itself is
        # selected (highlighted) while its name is chosen; the selection is cleared at the end of the run)
        try:
            uidoc.ShowElements(ElementId(r['id']))
            uidoc.Selection.SetElementIds(List[ElementId]([ElementId(r['id'])]))
            uidoc.RefreshActiveView()        # the room is still temporary (open transaction) - show the highlight
        except Exception:
            pass
        opts = (permit_write.SHAFT_NAMES + permit_write.SERVICE_NAMES) if r['guess'] == permit_write.SHAFT \
            else (permit_write.SERVICE_NAMES + permit_write.SHAFT_NAMES)
        pick = forms.SelectFromList.show(opts + [EDIT, SKIP, CANCEL], title=u'חלל %d מתוך %d | %.1f מ"ר | הצעה: %s' % (
            r['_index'], r['_total'], r['area_m2'], r['guess']), button_name=u'בחר', multiselect=False, width=520, height=640)
        if pick == CANCEL:
            raise permit_write.Cancelled()
        if pick is None or pick == SKIP:
            return None
        if pick == EDIT:
            txt = forms.ask_for_string(default=u'', prompt=u'שם החלל:', title=u'עריכת שם')
            return txt.strip() if txt and txt.strip() else None
        return pick

    summary = permit_fullrun.run_plans(doc, uidoc, views, trace, choose_name,
                                       stamp=datetime.datetime.now().strftime('%y%m%d_%H%M'))
    try:
        uidoc.Selection.SetElementIds(List[ElementId]())
    except Exception:
        pass

    output = script.get_output()
    output.set_title(u'PermitAgent - הרצה מלאה')
    for name, notes, rows, png, md in summary:
        output.print_md(u'## %s' % name)
        for n in notes:
            output.print_md(u'- ' + n)
        if rows:
            output.print_table([[s, rule, u'%s' % fd, u'%s' % dn, note] for s, rule, fd, dn, note in rows],
                               columns=[u'שלב', u'כלל', u'נמצאו', u'בוצעו', u'הערה'])
        output.print_md(u'_תמונה: %s_' % (png or u'לא נשמרה'))
    output.print_md(u'**התמונות וטבלאות הבדיקה נשמרו ב:** %s  \n'
                    u'אפשר לכתוב לסוכנים "הרצתי" - הם יקראו משם.  \n'
                    u'_כל שלב בכל קומה הוא Undo נפרד._' % permit_fullrun.AUTO_DIR)
    trace(u'full run done: %s' % u', '.join(s[0] for s in summary))
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
