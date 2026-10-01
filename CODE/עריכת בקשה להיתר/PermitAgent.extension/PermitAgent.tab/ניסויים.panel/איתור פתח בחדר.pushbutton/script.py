# -*- coding: utf-8 -*-
"""איתור הסיבה שרוויט לא יוצר Room בחלל מסוים. קריאה בלבד - החשודים רק מסומנים (Select)."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_diag_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== start (version 3)')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.UI import TaskDialog
    from System.Collections.Generic import List
    from pyrevit import revit, script
    import permit_diag
    trace(u'imports ok')

    doc = revit.doc
    uidoc = revit.uidoc
    view = revit.active_view
    trace(u'view: %s (%s)' % (view.Name, view.ViewType))

    if getattr(view, 'GenLevel', None) is None:
        TaskDialog.Show(u'איתור פתח בחדר', u'יש להריץ מתוך תכנית קומה.')
        trace(u'not a plan view - stop')
        script.exit()

    TaskDialog.Show(u'איתור פתח בחדר (גרסה 3)',
                    u'אחרי סגירת ההודעה - לחץ פעם אחת בתוך החלל שלא נסגר לחדר (למשל באמצע הסלון).\nEsc לביטול.')
    trace(u'waiting for pick')
    try:
        pick = uidoc.Selection.PickPoint(u'לחץ בתוך החלל שלא נסגר לחדר')
    except Exception as ex:
        trace(u'pick failed/cancelled: %s' % ex)
        TaskDialog.Show(u'איתור פתח בחדר', u'לא נבחרה נקודה (%s).' % ex)
        script.exit()
    trace(u'picked %.2f, %.2f' % (pick.X, pick.Y))

    res = permit_diag.diagnose(doc, view, pick, lambda pct, msg: trace(u'%d%% %s' % (pct, msg)))
    path = permit_diag.save_result(doc, res)
    trace(u'saved %s' % path)

    output = script.get_output()
    output.set_title(u'PermitAgent - איתור פתח בחדר')
    output.print_md(u'**קומה:** {}  |  **גובה חישוב חדר:** {} ס"מ  |  **הנקודה:** {} ס"מ  |  אלמנטים תוחמים שנבדקו: {}'.format(
        res['level'], res['computation_height_cm'], res['pick_cm'], res['bounding_count']))

    leak_ids = []
    if res.get('leaks'):
        output.print_md(u'## איפה החלל "דולף"')
        for k, lk in enumerate(res['leaks']):
            if 'element_a' not in lk:
                output.print_md(u'- כיוון {}°: לא נמצאו אלמנטים משני צידי הכיוון'.format(lk['angles'][0]))
                continue
            (ia, la), (ib, lb) = lk['element_a'], lk['element_b']
            leak_ids += [ia, ib]
            output.print_md(u'- **פתח {}:** ברוחב כ-**{} ס"מ**, במרחק כ-{} ס"מ מהנקודה שלחצת, בין {} ({}) לבין {} ({})'.format(
                k + 1, lk['width_cm'], lk['distance_cm'], output.linkify(ElementId(ia)), la,
                output.linkify(ElementId(ib)), lb))

    if res['gaps']:
        output.print_md(u'### רווחים בין קצוות חופשיים')
        for a, b, g, p in res['gaps']:
            output.print_md(u'- רווח של **{} ס"מ** ליד ({}, {}) - בין {} ל-{}'.format(
                g, p[0], p[1], output.linkify(ElementId(a)), output.linkify(ElementId(b))))

    output.print_md(u'### חשודים (מהקרוב לנקודה)')
    if res['suspects']:
        rows = [[output.linkify(ElementId(i)), r, d] for i, r, d in res['suspects']]
        output.print_table(rows, columns=[u'אלמנט', u'סיבה', u'מרחק מהנקודה (ס"מ)'])
    else:
        output.print_md(u'לא נמצאו קירות חשודים.')

    if res['escaping_ray_angles']:
        output.print_md(u'### כיוונים שבהם אין שום קיר/קו תוחם עד 15 מ\' מהנקודה')
        output.print_md(u'זוויות (0° = ימינה, 90° = למעלה בתכנית לא מסובבת): ' +
                        u', '.join(u'{}°'.format(a) for a in res['escaping_ray_angles']))
    else:
        output.print_md(u'_כל הקרניים מהנקודה פוגשות אלמנט תוחם - החלל סגור גיאומטרית; הבעיה כנראה באחד החשודים למעלה._')

    # select (and zoom to) the leak edges first - they point at the opening; otherwise the suspects
    to_select = leak_ids or res['suspect_ids'][:50]
    if to_select:
        ids = List[ElementId]([ElementId(i) for i in set(to_select)])
        uidoc.Selection.SetElementIds(ids)
        try:
            uidoc.ShowElements(ids)
        except Exception:
            pass
        output.print_md(u'_האלמנטים שבקצוות הפתח סומנו (Select) והתצוגה קפצה אליהם. לחיצה על מספר אלמנט מקפיצה אליו._')
    output.print_md(u'**דוח נשמר:** `{}`'.format(path))
    trace(u'done')
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
